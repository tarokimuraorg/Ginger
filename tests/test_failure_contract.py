import io
import json
from contextlib import redirect_stdout
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

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
            checked("sig f() -> Unit {}\nfunc f() { return print(1)\nprint(div(1,2)) }")
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

    def test_handler_same_failure_is_swallowed(self):
        program, diags = checked("try print(div(1.0,0.0))\n"
                                 "catch DivideByZero print(div(2.0,0.0))\nprint(3)")
        self.assertEqual(diags.items, [])
        self.assertEqual(output(program), "3\n")

    def test_handler_different_failure_escapes_sibling(self):
        source = """
sig f() -> Unit { failure IOErr failure DivideByZero }
func f() { print(div(1.0,0.0)) }
sig ioFail(Int) -> Unit { failure IOErr builtin core.int.print }
try f()
catch DivideByZero ioFail(1)
catch IOErr print(2)
"""
        program, diags = checked(source)
        self.assertEqual([d.message for d in diags], ["unhandled failures: IOErr"])

        def fail(_):
            raise RaisedFailure(FailureId.IOErr)

        with patch.dict(BUILTINS, {"core.int.print": fail}):
            with self.assertRaises(RaisedFailure) as raised:
                output(program)
        self.assertEqual(raised.exception.fid, FailureId.IOErr)

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

    def test_handled_boundary_propagates_and_preserves_runtime(self):
        for placement in ("sig", "func"):
            source = ("%ssig h() -> Unit {}\n%sfunc h() { print(div(1.0,0.0)) }\n"
                      "sig caller() -> Unit {}\nfunc caller() { h() }\ncaller()") % (
                          "@attr.handled\n" if placement == "sig" else "",
                          "@attr.handled\n" if placement == "func" else "")
            program, diags = checked(source)
            self.assertEqual(len(diags.items), 2)
            self.assertTrue(all(d.code == "FAILURE_CONTRACT_DEFERRED" for d in diags))
            self.assertTrue(all("handled: h" in d.message for d in diags))
            if placement == "sig":
                self.assertEqual(output(program), "")
            else:
                with self.assertRaises(RaisedFailure):
                    output(program)

    def test_handled_builtin_stays_deferred_and_does_not_gain_catch_permission(self):
        prefix = ("@attr.handled\nsig h(Int) -> Unit { failure IOErr builtin core.int.print }\n"
                  "sig f() -> Unit {}\nfunc f() { h(1) }\n")
        program, diags = checked(prefix + "f()")
        self.assertTrue(any("handled: h" in d.message for d in diags))

        def fail(_):
            raise RaisedFailure(FailureId.IOErr)

        with patch.dict(BUILTINS, {"core.int.print": fail}), self.assertRaises(RaisedFailure):
            output(program)
        with self.assertRaisesRegex(TypecheckError, "cannot catch 'IOErr'"):
            checked(prefix + "try h(1)\ncatch IOErr print(0)")

    def test_boundary_transitivity_cycles_and_unreachable_calls(self):
        source = """
sig a() -> Unit {}
sig b() -> Unit {}
@attr.handled
sig h() -> Unit {}
func a() { b() }
func b() { a()\nh() }
func h() {}
"""
        _, diags = checked(source)
        self.assertEqual(len(diags.items), 3)
        _, diags = checked("@attr.handled\nsig h() -> Unit {}\nfunc h() {}\n"
                           "sig f() -> Unit {}\nfunc f() { return print(1)\nh() }")
        self.assertEqual(len(diags.items), 1)

    def test_thunk_boundary_and_saved_force_catch_limitation(self):
        source = """
sig f(Thunk[Float]) -> Float {}
func f(t: Thunk[Float]) { return force(t) }
sig g(Thunk[Float]) -> Float {}
func g(t: Thunk[Float]) { return f(t) }
"""
        _, diags = checked(source)
        self.assertEqual(len(diags.items), 2)
        self.assertTrue(all("Thunk:" in d.message for d in diags))
        with self.assertRaisesRegex(TypecheckError, "cannot catch 'DivideByZero'"):
            checked("var t: Thunk[Float] = thunk(div(1.0,0.0))\n"
                    "try print(force(t))\ncatch DivideByZero print(0)")

    def test_deferred_functions_still_typechecked(self):
        with self.assertRaisesRegex(TypecheckError, "return type mismatch"):
            checked("sig f(Thunk[Int]) -> Float {}\nfunc f(t: Thunk[Int]) { return force(t) }")

    def test_custom_builtin_declarations_are_still_trusted(self):
        program, _ = checked("sig raw(Float,Float) -> Float { builtin core.float.div }\n"
                             "sig f() -> Float {}\nfunc f() { return raw(1.0,0.0) }\n"
                             "var x: Float = f()")
        with self.assertRaises(RaisedFailure):
            output(program)


class ExistingRegressionTests(unittest.TestCase):
    def test_parameter_and_return_typerefs(self):
        checked("sig identity(Int) -> Int {}\nfunc identity(x: Int) { return x }")
        checked("sig identity(Thunk[Int]) -> Thunk[Int] {}\n"
                "func identity(x: Thunk[Int]) { return x }")
        with self.assertRaisesRegex(TypecheckError, "return type mismatch"):
            checked("sig identity(Thunk[Int]) -> Thunk[Float] {}\n"
                    "func identity(x: Thunk[Int]) { return x }")

    def test_parameter_order_structure_and_declaration_order(self):
        checked("sig f(Int,Float) -> Unit {}\nfunc f(a: Int,b: Float) {}")
        for src in ("sig f(Int,Float) -> Unit {}\nfunc f(a: Float,b: Int) {}",
                    "sig f(Thunk[Int]) -> Unit {}\nfunc f(a: Thunk[Float]) {}",
                    "sig f(Int) -> Unit {}\nfunc f(a: Int,b: Int) {}"):
            with self.subTest(src=src), self.assertRaisesRegex(TypecheckError, "positional types"):
                checked(src)
        with self.assertRaisesRegex(TypecheckError, "no corresponding sig"):
            checked("func f() {}\nsig f() -> Unit {}")

    def test_thunk_force_arity_and_expected_type(self):
        for call in ("thunk()", "thunk(1,2)", "force()", "force(1,2)"):
            with self.subTest(call=call), self.assertRaisesRegex(TypecheckError, "argument count mismatch"):
                checked(f"var t: Thunk[Int] = {call}")
        with self.assertRaisesRegex(TypecheckError, "type mismatch"):
            checked("var t: Thunk[Int] = thunk(1)\nvar x: Float = force(t)")
        with self.assertRaisesRegex(TypecheckError, "force expects Thunk"):
            checked("var x: Int = force(1)")

    def test_type_mismatch_diagnostics(self):
        for src in ("var x: Float = 1", "var x: Int = 1.0",
                    "var x: Int = 1\nvar y: Float = x"):
            with self.subTest(src=src), self.assertRaisesRegex(TypecheckError, "type mismatch: expected"):
                checked(src)

    def test_samples_outputs_and_warnings(self):
        expected = {
            "Code": ("4\n", 0),
            "Scene_3": ("-1\n-1.0\n1\n", 0),
            "Scene_5": ("0.5\n2.0\n", 2),
            "Scene_6": ("Left\n", 0),
            "Scene_7": ("3\n4\n2\n-1\n6\n12\n4.0\n2.0\n", 2),
            "Scene_8": ("true\nfalse\ntrue\nfalse\nfalse\n", 0),
            "Scene_9": ("3\n", 0),
        }
        root = Path(__file__).resolve().parents[1] / "ginger" / "script"
        for name, (stdout, warning_count) in expected.items():
            with self.subTest(name=name):
                program, diags = checked((root / f"{name}.ginger").read_text())
                self.assertEqual(output(program), stdout)
                self.assertEqual([d.message for d in diags],
                                 ["unhandled failures: DivideByZero"] * warning_count)


if __name__ == "__main__":
    unittest.main()
