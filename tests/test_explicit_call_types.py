import unittest

from ginger.ast import NamedArg, TypeRef
from ginger.core.failure_spec import FailureId
from ginger.diagnostics import Diagnostics
from ginger.errors import TypecheckError
from ginger.lower import lower_program
from ginger.parser import parse
from ginger.pipeline import compile, run
from ginger.typecheck import typecheck_program


IDENTITY = "sig identity[Value](Value) -> Value {} func identity(x:Value) {return x} "


class ExplicitCallTypesTests(unittest.TestCase):
    def test_parse_and_lower(self):
        program = parse("identity[Int](10) identity[Box[Int]](x: 1 + 2) identity(1)")
        for tree in (program, lower_program(program)):
            first, named, inferred = [s.expr for s in tree.items]
            self.assertEqual(first.type_args, (TypeRef("Int"),))
            self.assertEqual(named.type_args, (TypeRef("Box", (TypeRef("Int"),)),))
            self.assertIsInstance(named.args[0], NamedArg)
            self.assertIsNone(inferred.type_args)
        lowered = lower_program(program).items[1].expr
        self.assertFalse(lowered.from_operator)
        self.assertTrue(lowered.args[0].expr.from_operator)
        self.assertIsNone(lowered.args[0].expr.type_args)

    def test_widening_and_operator_resolution(self):
        result = run(IDENTITY + "var a:Float = identity[Float](1.5) "
                     "var b:Float = identity[Float](1) "
                     "var c:Int = identity[Int](1) + add[Int](2,3)")
        self.assertEqual(result.environment["a"].value, 1.5)
        self.assertIsInstance(result.environment["b"].value, float)
        self.assertEqual(result.environment["c"].value, 6)

    def test_forwarded_type_and_reordered_sigtype(self):
        source = ("sigtype First[A,B](A,B) -> A {} sig first[Other,Value]: First[Value,Other] "
                  "func first(x:Value,y:Other) {return x} "
                  "sig outer[Element](Element) -> Element {} "
                  "func outer(x:Element) {return first[Int,Element](x,0)} "
                  "var v:Float = outer[Float](1.5)")
        self.assertEqual(run(source).environment["v"].value, 1.5)

    def test_nested_concrete_and_thunk(self):
        compile(IDENTITY + "type Box[T]\nsig f(Box[Int]) -> Box[Int] {} "
                "func f(x:Box[Int]) {return identity[Box[Int]](x)}")
        source = (IDENTITY + "sigtype Deferred[T]() -> T {} "
                  "var v:Int = force(identity[Thunk[Deferred[Int]]](thunk(10)))")
        self.assertEqual(run(source).environment["v"].value, 10)

    def test_return_only_declaration(self):
        compile("sig create[T]() -> T {failure {Never}}")

    def test_unimplemented_specializations(self):
        for params, result, args in (("", "T", ""), ("Int", "T", "1"), ("", "Box[T]", "")):
            source = f"type Box[T]\nsig make[T]({params}) -> {result} {{}}"
            with self.subTest(params=params, result=result), self.assertRaisesRegex(
                    TypecheckError, "no implementation for function 'make'"):
                compile(source + f" var v:{result.replace('T', 'Int')} = make[Int]({args})")

    def test_require_and_conditional_effects_use_explicit_type(self):
        decl = ("sig calc[T](T,T) -> T {require T guarantees Addable "
                "failure {IntegerOverflow when T in BoundedArithmetic, "
                "FloatOverflow when T in FiniteArithmetic}} "
                "func calc(x:T,y:T) {return add[T](x,y)} ")
        for typ, failure in (("Float", FailureId.FloatOverflow), ("Int", FailureId.IntegerOverflow)):
            env = typecheck_program(parse(decl + f"var v:{typ} = calc[{typ}](1,2)"), Diagnostics())
            self.assertEqual(env["v"].latest_write_failures, {failure})
        with self.assertRaisesRegex(TypecheckError, "does not guarantee Addable"):
            compile(decl + "var v:Int64 = calc[Int64](1,2)")

    def test_forward_typegroup_requirements(self):
        prefix = ("typegroup Group { Int } typegroup Other { Int } "
                  "sig inner[A](A) -> A {require A in Group require A guarantees Addable} "
                  "func inner(x:A) {return x} ")
        def program(requirements, call="inner[B](x)"):
            return (prefix + f"sig outer[B](B) -> B {{{requirements}}} "
                    + f"func outer(x:B) {{return {call}}} ")

        both = "require B guarantees Addable require B in Group"
        self.assertEqual(run(program(both) + "var v:Int = outer[Int](1)").environment["v"].value, 1)
        for requirements in ("", "require B guarantees Addable",
                             "require B guarantees Addable require B in Other", "require B in Group"):
            with self.subTest(requirements=requirements), self.assertRaisesRegex(
                    TypecheckError, "requirement not satisfied"):
                compile(program(requirements))
        with self.assertRaisesRegex(TypecheckError, "requirement not satisfied"):
            compile(program(both) + "outer[Float](1.5)")
        with self.assertRaisesRegex(TypecheckError, "explicit type arguments"):
            compile(program(both, "inner(x)"))

    def test_symbolic_dispatch_requires_a_guarantee_method(self):
        with self.assertRaisesRegex(TypecheckError, "no implementation for function 'make'"):
            compile("sig make[T]() -> T {require T guarantees Addable} "
                    "sig outer[B](B) -> B {require B guarantees Addable} "
                    "func outer(x:B) {return make[B]()}")

    def test_invalid_calls(self):
        for src, error in (
            ("toFloat[Int](10)", "type argument count"),
            ("identity[Int,Float](1)", "type argument count"),
            ("sig first[T,U](T,U) -> T {} first[Int](1,2)", "type argument count"),
            ("identity[Int](1.5)", "type mismatch"),
            ("identity[U](1)", "unknown type variable"),
            ("identity[Value](1)", "unknown type"),
            ("identity[Self](1)", "unbound"),
            ("sigtype Calc() -> Int {} identity[Calc](1)", "computation contract"),
            ("identity[Missing](1)", "unknown type"),
            ("identity[Thunk[Missing]](1)", "unknown sigtype"),
            ("sig plain(Int) -> Int {} plain[Int](1)", "type argument count"),
            ("add(1,2)", "explicit type arguments"),
            ("thunk[Int](1)", "does not accept"),
            ("force[Int](1)", "does not accept"),
            ("identity[Int](wrong:1)", "named arguments"),
        ):
            with self.subTest(src=src), self.assertRaisesRegex(TypecheckError, error):
                compile(IDENTITY + src)

    def test_named_arguments_follow_parameter_names(self):
        source = ("sig first[A,B](A,B) -> A {} func first(left:A,right:B) {return left} "
                  "var v:Float = first[Float,Int](right:2,left:1)")
        value = run(source).environment["v"].value
        self.assertIsInstance(value, float)
        self.assertEqual(value, 1.0)

    def test_named_argument_evaluation_order(self):
        import io
        from contextlib import redirect_stdout

        source = ("sig mark(Int) -> Int {} func mark(x:Int) {print[Int](x) return x} "
                  "sig first[V](V,V) -> V {} func first(left:V,right:V) {return left} "
                  "var v:Int = first[Int](right:mark(2),left:mark(1))")
        output = io.StringIO()
        with redirect_stdout(output):
            result = run(source)
        self.assertEqual(output.getvalue(), "2\n1\n")
        self.assertEqual(result.environment["v"].value, 1)

    def test_contract_application_call_arity(self):
        source = ("sigtype Identity[T](T) -> T {} sig generic[V]: Identity[V] "
                  "func generic(x:V) {return x} sig concrete: Identity[Int] "
                  "func concrete(x:Int) {return x} ")
        for call, message in (("generic(1)", "explicit type arguments"),
                              ("concrete[Int](1)", "type argument count")):
            with self.subTest(call=call), self.assertRaisesRegex(TypecheckError, message):
                compile(source + "var v:Int = " + call)

    def test_missing_type_arguments(self):
        for expr in ("identity(10)", "add(1,2)", "mul(1.5,2.0)", "print(1)",
                     "outer[Int](10)"):
            outer = ("sig outer[V](V) -> V {} func outer(x:V) {return identity(x)} "
                     if expr.startswith("outer") else "")
            with self.subTest(expr=expr), self.assertRaisesRegex(TypecheckError, "explicit type arguments"):
                compile(IDENTITY + outer + expr)

    def test_undeclared_parameters_in_source_and_catalog(self):
        import json
        import tempfile
        from pathlib import Path
        from ginger.ast import Program
        from ginger.core.catalog_loader import load_core_catalog_json
        from ginger.symbols_builder import build_symbols

        for name in ("T", "Value"):
            with self.subTest(name=name), self.assertRaisesRegex(TypecheckError, "unknown type"):
                compile(f"sig old({name}) -> {name} {{}}")
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "catalog.json"
                path.write_text(json.dumps({"sigs": [{"name": "old", "params": [name], "ret": name}]}))
                with self.assertRaisesRegex(TypecheckError, "unknown type"):
                    build_symbols(Program(load_core_catalog_json(path)))

    def test_malformed_calls(self):
        for expr in ("identity[](10)", "identity[Int]", "identity[Int(1)", "identity[Int]("):
            with self.subTest(expr=expr), self.assertRaises(SyntaxError):
                parse(expr)
