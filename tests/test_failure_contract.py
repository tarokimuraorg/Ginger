import io
import json
from contextlib import redirect_stdout
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from ginger.ast import Program
from ginger.builtin import BUILTINS
from ginger.core.catalog_loader import load_core_catalog_json
from ginger.core.failure_spec import FailureId, EMPTY_FAILURES
from ginger.diagnostics import Diagnostics
from ginger.errors import TypecheckError
from ginger.eval import eval_program
from ginger.lower import lower_program
from ginger.parser import parse
from ginger.runtime.failures import RaisedFailure
from ginger.symbols_builder import build_symbols
from ginger.typecheck import typecheck_program


def checked(source):
    program = lower_program(parse(source))
    diagnostics = Diagnostics()
    typecheck_program(program, diagnostics)
    return program, diagnostics


def output(program):
    stream = io.StringIO()
    with redirect_stdout(stream):
        eval_program(program)
    return stream.getvalue()


def recorded(program):
    from ginger.eval import _eval_program_with_context
    from ginger.runtime.context import RuntimeContext
    context = RuntimeContext()
    stream = io.StringIO()
    with redirect_stdout(stream):
        env = _eval_program_with_context(program, context)
    return env, context, stream.getvalue()


class FailureContractTests(unittest.TestCase):
    def test_upper_bounds(self):
        for failures in ("", "failure Never", "failure IOErr"):
            with self.subTest(failures=failures):
                checked(f"sig f() -> Unit {{ {failures} }}\nfunc f() {{}}")
        for failures in ("failure DivideByZero", "failure IOErr failure DivideByZero"):
            with self.subTest(failures=failures):
                checked(f"sig f() -> Float {{ {failures} }}\n"
                        "func f() { return div(1.0, 0.0) }")

    def test_undeclared_in_return_statement_and_nested_calls(self):
        bodies = [
            ("Float", "return div(1.0, 0.0)"),
            ("Unit", "print(div(1.0, 0.0))"),
            ("Unit", "print(div(div(1.0, 2.0), 0.0))"),
        ]
        for ret, body in bodies:
            with self.subTest(body=body):
                with self.assertRaisesRegex(TypecheckError,
                        "undeclared failures: DivideByZero; declared failures: Never"):
                    checked(f"sig f() -> {ret} {{}}\nfunc f() {{ {body} }}")

    def test_multiple_missing_and_declared_order(self):
        source = """
sig risky() -> Unit { failure PrintErr failure DivideByZero failure IOErr }
func risky() {}
sig f() -> Unit { failure TimeErr failure IOErr }
func f() { risky() }
"""
        with self.assertRaisesRegex(TypecheckError,
                "undeclared failures: DivideByZero, PrintErr; declared failures: IOErr, TimeErr"):
            checked(source)

    def test_user_call_propagation(self):
        source = """
sig g() -> Float { failure DivideByZero }
func g() { return div(1.0, 0.0) }
sig f() -> Unit { %s }
func f() { print(g()) }
"""
        with self.assertRaisesRegex(TypecheckError, "func 'f'.*DivideByZero"):
            checked(source % "")
        checked(source % "failure DivideByZero")

    def test_unreachable_effects_but_not_types_are_ignored(self):
        checked("sig f() -> Unit {}\nfunc f() { return print(1)\nprint(div(1.0,0.0)) }")
        with self.assertRaisesRegex(TypecheckError, "division expects Float"):
            checked("sig f() -> Unit {}\nfunc f() { return print(1)\nprint(div(eq(1,1),2)) }")
        with self.assertRaisesRegex(TypecheckError, "inconsistent return types"):
            checked("sig f() -> Int {}\nfunc f() { return 1\nreturn 2.0 }")

    def test_recursion_uses_signatures(self):
        checked("sig f() -> Unit {}\nfunc f() { f() }")
        source = """
sig f() -> Unit { %s }
sig g() -> Unit { failure IOErr }
func f() { g() }
func g() { f() }
"""
        checked(source % "failure IOErr")
        with self.assertRaisesRegex(TypecheckError, "func 'f'.*IOErr"):
            checked(source % "")
        with self.assertRaisesRegex(TypecheckError, "DivideByZero"):
            checked("sig f() -> Unit {}\nfunc f() { f()\nprint(div(1.0,0.0)) }")

    def test_catch_eligibility(self):
        program, diags = checked("try print(div(1.0,0.0))\ncatch DivideByZero print(0)")
        self.assertEqual(output(program), "0\n")
        self.assertEqual(diags.items, [])
        for name, message in [("IOErr", "cannot catch 'IOErr'"),
                              ("Missing", "unknown failure 'Missing'"),
                              ("Never", "unknown failure 'Never'")]:
            with self.subTest(name=name), self.assertRaisesRegex(TypecheckError, message):
                checked(f"try print(div(1.0,0.0))\ncatch {name} print(0)")
        with self.assertRaisesRegex(TypecheckError, "cannot catch 'DivideByZero'"):
            checked("try print(1)\ncatch DivideByZero print(0)")

    def test_catch_original_set_and_duplicate_first_match(self):
        source = """
sig f() -> Unit { failure IOErr failure DivideByZero }
func f() { print(div(1.0,0.0)) }
try f()
catch IOErr print(1)
catch DivideByZero print(2)
catch DivideByZero print(3)
"""
        program, diags = checked(source)
        self.assertEqual(diags.items, [])
        self.assertEqual(output(program), "2\n")

    def test_handler_same_failure_skips_sibling_and_continues(self):
        program, diags = checked("try print(div(1.0,0.0))\n"
                                 "catch DivideByZero print(div(2.0,0.0))\n"
                                 "catch DivideByZero print(2)\nprint(3)")
        self.assertEqual([d.message for d in diags], ["unhandled failures: DivideByZero"])
        _, context, stdout = recorded(program)
        self.assertEqual(stdout, "3\n")
        self.assertEqual([e.failure_id for e in context.failure_history], [FailureId.DivideByZero] * 2)

    def test_handler_different_failure_escapes_sibling(self):
        source = """
sig f() -> Unit { failure IOErr failure DivideByZero }
func f() { print(div(1.0,0.0)) }
sig ioFail(Int) -> Unit { failure IOErr builtin core.int.print }
try f()
catch DivideByZero ioFail(1)
catch IOErr print(2)
print(3)
"""
        program, diags = checked(source)
        self.assertEqual([d.message for d in diags], ["unhandled failures: IOErr"])

        original = BUILTINS['core.int.print']
        def fail(value):
            if value == 1:
                raise RaisedFailure(FailureId.IOErr)
            return original(value)
        handler = Mock(side_effect=fail)
        with patch.dict(BUILTINS, {"core.int.print": handler}):
            _, context, stdout = recorded(program)
        self.assertEqual(stdout, '3\n')
        self.assertEqual([call.args for call in handler.call_args_list], [(1,), (3,)])
        self.assertEqual([e.failure_id for e in context.failure_history],
                         [FailureId.DivideByZero, FailureId.IOErr])

    def test_source_declarations(self):
        for clause in ("", "failure Never"):
            program, _ = checked(f"sig f() -> Unit {{ {clause} }}")
            self.assertEqual(build_symbols(program).sig_failures["f"], EMPTY_FAILURES)
        for clause in ("failure Never failure IOErr", "failure IOErr failure Never",
                       "failure IOErr failure IOErr", "failure Never failure Never",
                       "failure IOErr[Int]"):
            with self.subTest(clause=clause), self.assertRaises(SyntaxError):
                checked(f"sig f() -> Unit {{ {clause} }}")

    def catalog_program(self, failures):
        sig = {"name": "f", "params": [], "ret": "Unit"}
        if failures is not None:
            sig["failures"] = failures
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "catalog.json"
            path.write_text(json.dumps({"sigs": [sig]}))
            return Program(load_core_catalog_json(path))

    def test_catalog_normalization(self):
        for failures in (None, [], ["Never"]):
            with self.subTest(failures=failures):
                self.assertEqual(build_symbols(self.catalog_program(failures)).sig_failures["f"],
                                 EMPTY_FAILURES)
        for failures in (["Never", "IOErr"], ["IOErr", "Never"],
                         ["IOErr", "IOErr"], ["Never", "Never"], ["Missing"]):
            with self.subTest(failures=failures), self.assertRaises(TypecheckError):
                build_symbols(self.catalog_program(failures))

    def test_standard_div_contract(self):
        self.assertEqual(build_symbols(parse("")).sig_failures["div"],
                         frozenset({FailureId.DivideByZero}))

    def test_handled_is_rejected_on_sig_func_and_builtin(self):
        sources = [
            "@attr.handled\nsig h() -> Unit {}",
            "@attr.handled\nfunc h() {}",
            "sig h() -> Unit {}\n@attr.handled\nfunc h() {}",
            "@attr.handled\nsig h(Int) -> Unit { builtin core.int.print }",
        ]
        for source in sources:
            with self.subTest(source=source), self.assertRaisesRegex(TypecheckError, "unknown attr '@attr.handled'"):
                checked(source)
        from ginger.ast import SigDecl, TypeRef
        with self.assertRaisesRegex(TypecheckError, "unknown attr '@attr.handled'"):
            build_symbols(Program([SigDecl('h', [], TypeRef('Unit'), [], attrs=['handled'])]))

    def test_attributes_preserved_and_invalid_placements_rejected(self):
        program, diags = checked("@attr.io\nsig h() -> Unit {}\n@attr.io\nfunc h() {}\nh()")
        self.assertEqual(diags.items, [])
        self.assertEqual(output(program), "")
        for declaration in ["guarantee G {}", "typegroup G {}", "register Int guarantees G",
                            "impl Int guarantees G {}", "failureset F { IOErr }",
                            "var x: Int = 1", "try print(1)", "catch IOErr print(1)"]:
            for attr in ('handled', 'io'):
                with self.subTest(declaration=declaration, attr=attr), self.assertRaisesRegex(
                        SyntaxError, "attributes must precede a sig or func"):
                    parse(f"@attr.{attr}\n{declaration}")

    def test_function_contracts_are_never_deferred_by_dependencies(self):
        prefix = "sig h() -> Unit {}\nfunc h() {}\n"
        with self.assertRaisesRegex(TypecheckError, "undeclared failures: DivideByZero"):
            checked(prefix + "sig f() -> Unit {}\nfunc f() { h()\nprint(div(1.0,0.0)) }")
        prefix = ("sig h() -> Unit { failure DivideByZero }\n"
                  "func h() { print(div(1.0,0.0)) }\n"
                  "sig middle() -> Unit { failure DivideByZero }\nfunc middle() { h() }\n")
        with self.assertRaisesRegex(TypecheckError, "undeclared failures: DivideByZero"):
            checked(prefix + "sig caller() -> Unit {}\nfunc caller() { middle() }")
        program, diags = checked(prefix + "middle()\nprint(3)")
        self.assertEqual([d.code for d in diags], ['UNHANDLED_FAILURES'])
        _, context, stdout = recorded(program)
        self.assertEqual(stdout, '3\n')
        self.assertEqual([e.failure_id for e in context.failure_history], [FailureId.DivideByZero])

    def test_builtin_contract_is_visible_and_catchable(self):
        prefix = "sig h(Int) -> Unit { failure IOErr builtin core.int.print }\n"
        with self.assertRaisesRegex(TypecheckError, "undeclared failures: IOErr"):
            checked(prefix + "sig f() -> Unit {}\nfunc f() { h(1) }")
        program, diags = checked(prefix + "h(1)")
        self.assertEqual([d.message for d in diags], ['unhandled failures: IOErr'])
        def fail(_):
            raise RaisedFailure(FailureId.IOErr)
        with patch.dict(BUILTINS, {'core.int.print': fail}):
            _, context, _ = recorded(program)
        self.assertEqual([e.failure_id for e in context.failure_history], [FailureId.IOErr])
        program, diags = checked(prefix + "try h(1)\ncatch IOErr print(0)")
        self.assertEqual(diags.items, [])
        original_print = BUILTINS['core.int.print']
        def fail_once(value):
            if value == 1:
                raise RaisedFailure(FailureId.IOErr)
            return original_print(value)
        with patch.dict(BUILTINS, {'core.int.print': fail_once}):
            self.assertEqual(output(program), '0\n')

    def test_thunk_contract_validation_and_saved_force(self):
        _, diags = checked("sig f(Thunk[Float, DivideByZero]) -> Float { failure DivideByZero }\n"
                           "func f(t: Thunk[Float, DivideByZero]) { return force(t) }")
        self.assertEqual(diags.items, [])
        program, diags = checked("var t: Thunk[Float, DivideByZero] = thunk(div(1.0,0.0))\n"
                                 "try print(force(t))\ncatch DivideByZero print(0)")
        self.assertEqual(diags.items, [])
        self.assertEqual(output(program), "0\n")

    def test_thunk_return_type_still_checked(self):
        with self.assertRaisesRegex(TypecheckError, "return type mismatch"):
            checked("sig f(Thunk[Float, Never]) -> Int {}\nfunc f(t: Thunk[Float, Never]) { return force(t) }")

    def test_custom_builtin_declarations_are_still_trusted(self):
        program, _ = checked("sig raw(Float,Float) -> Float { builtin core.float.div }\n"
                             "sig f() -> Float {}\nfunc f() { return raw(1.0,0.0) }\n"
                             "var x: Float = f()")
        with self.assertRaises(RaisedFailure):
            output(program)


class ExistingRegressionTests(unittest.TestCase):
    def test_parameter_and_return_typerefs(self):
        checked("sig identity(Int) -> Int {}\nfunc identity(x: Int) { return x }")
        checked("sig identity(Thunk[Int, Never]) -> Thunk[Int, Never] {}\n"
                "func identity(x: Thunk[Int, Never]) { return x }")
        with self.assertRaisesRegex(TypecheckError, "return type mismatch"):
            checked("sig identity(Thunk[Int, Never]) -> Thunk[Float, Never] {}\n"
                    "func identity(x: Thunk[Int, Never]) { return x }")

    def test_parameter_order_structure_and_declaration_order(self):
        checked("sig f(Int,Float) -> Unit {}\nfunc f(a: Int,b: Float) {}")
        for src in ("sig f(Int,Float) -> Unit {}\nfunc f(a: Float,b: Int) {}",
                    "sig f(Thunk[Int, Never]) -> Unit {}\nfunc f(a: Thunk[Float, Never]) {}",
                    "sig f(Int) -> Unit {}\nfunc f(a: Int,b: Int) {}"):
            with self.subTest(src=src), self.assertRaisesRegex(TypecheckError, "positional types"):
                checked(src)
        with self.assertRaisesRegex(TypecheckError, "no corresponding sig"):
            checked("func f() {}\nsig f() -> Unit {}")

    def test_thunk_force_arity_and_expected_type(self):
        for call in ("thunk()", "thunk(1,2)", "force()", "force(1,2)"):
            with self.subTest(call=call), self.assertRaisesRegex(TypecheckError, "argument count mismatch"):
                checked(f"var t: Thunk[Int, Never] = {call}")
        with self.assertRaisesRegex(TypecheckError, "type mismatch"):
            checked("var t: Thunk[Float, Never] = thunk(1.0)\nvar x: Int = force(t)")
        with self.assertRaisesRegex(TypecheckError, "force expects Thunk"):
            checked("var x: Int = force(1)")

    def test_type_mismatch_diagnostics(self):
        for src in ("var b: Bool = eq(1,1)\nvar x: Float = b", "var x: Int = 1.0",
                    "var x: Float = 1.0\nvar y: Int = x"):
            with self.subTest(src=src), self.assertRaisesRegex(TypecheckError, "type mismatch: expected"):
                checked(src)

    def test_samples_outputs_and_warnings(self):
        expected = {
            "Scene_1": ("9\n4.5\n-9\n", ["unhandled failures: IntegerOverflow"] * 2
                        + ["unhandled failures: DivideByZero"]),
            "Scene_2": ("2.0\n", []),
            "Scene_3": ("0\n", []),
            "Scene_4": ("1\n2\n", []),
        }
        root = Path(__file__).resolve().parents[1] / "ginger" / "scripts"
        for name, (stdout, warnings) in expected.items():
            with self.subTest(name=name):
                program, diags = checked((root / f"{name}.ginger").read_text())
                self.assertEqual(output(program), stdout)
                self.assertEqual([d.message for d in diags], warnings)


if __name__ == "__main__":
    unittest.main()
