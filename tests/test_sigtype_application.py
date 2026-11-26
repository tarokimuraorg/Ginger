import unittest

from ginger.ast import RequireGuarantees, SigRefDecl
from ginger.core.failure_spec import FailureId
from ginger.numeric import INT_MAX
from ginger.runtime.failures import FailureStatus
from internal_declarations import run as run_internal
from ginger.errors import TypecheckError
from ginger.parser import parse
from ginger.pipeline import run
from ginger.symbols_builder import build_symbols


class SigTypeApplicationTests(unittest.TestCase):
    def forms(self, contract, body, name="calculate"):
        return [
            f"sig {name}{contract}\n{body}",
            f"sig {name}: Calculation\n{body}\nsigtype Calculation{contract}",
        ]

    def equivalent_value(self, contract, body, expected):
        for source in self.forms(contract, body):
            with self.subTest(source=source):
                self.assertEqual(run(source).environment["result"].value, expected)

    def equivalent_error(self, contract, body):
        messages = []
        for source in self.forms(contract, body):
            with self.subTest(source=source), self.assertRaises(TypecheckError) as error:
                run(source)
            messages.append(str(error.exception))
        self.assertEqual(*messages)

    def test_ast_and_forward_reference(self):
        self.assertEqual(parse("sig calculate: A").items, [SigRefDecl("calculate", "A")])
        self.equivalent_value("(Int) -> Int { failure { IntegerOverflow } }",
                              "func calculate(x: Int) { return add[Int](x,1) }\n"
                              "let result: Int = calculate(41)", 42)

    def test_zero_arguments_and_same_name(self):
        result = run("sig A: A\nfunc A() { return 7 }\nsigtype A() -> Int { failure { Never } }\n"
                     "let result: Int = A()")
        self.assertEqual(result.environment["result"].value, 7)

    def test_multiple_parameters_and_failures(self):
        self.equivalent_value("(Int, Int) -> Int { failure { Errors } }",
                              "failureset Errors { DivideByZero, IntegerOverflow }\n"
                              "func calculate(x: Int, y: Int) { return add[Int](x,y) }\n"
                              "let result: Int = calculate(1,2)", 3)

    def test_shared_contract(self):
        source = """sigtype Identity(Int) -> Int {}
sig first: Identity
sig second: Identity
func first(x: Int) { return x }
func second(y: Int) { return y }
let a: Int = first(1)
let b: Int = second(2)"""
        syms = build_symbols(parse(source))
        self.assertIsNot(syms.sigs["first"].params, syms.sigs["second"].params)
        self.assertIsNot(syms.sigs["first"].params, syms.sigtypes["Identity"].declaration.params)
        env = run(source).environment
        self.assertEqual((env["a"].value, env["b"].value), (1, 2))

    def test_generic_contract_requires_specialization(self):
        with self.assertRaisesRegex(TypecheckError, "requires type arguments"):
            run("sigtype Identity[T](T) -> T {} sig first: Identity")

    def test_contract_name_is_neither_type_nor_callable(self):
        prefix = "sigtype Calculation(Int) -> Int {}\nsig calculate: Calculation\n"
        for use in ["sig f(Calculation) -> Int {}", "let x: Int = Calculation(1)"]:
            with self.subTest(use=use), self.assertRaises(TypecheckError):
                run(prefix + use)

    def generic_forms(self, contract, name="calculate"):
        return (f"sig {name}[T]{contract}",
                f"sigtype Calculation[T]{contract} sig {name}[T]: Calculation[T]")

    def test_conditional_failure_history(self):
        contract = ("(T,T) -> T {require T guarantees Addable "
                    "failure {IntegerOverflow when T in BoundedArithmetic, "
                    "FloatOverflow when T in FiniteArithmetic}}")
        histories = []
        for declaration in self.generic_forms(contract):
            source = declaration + " func calculate(x:T,y:T) {return add[T](x,y)} "
            result = run(source + "let f:Float = calculate[Float](1.0,2.0) "
                         + f"let i:Int = calculate[Int]({INT_MAX},1)")
            self.assertEqual(result.environment["f"].value, 3.0)
            self.assertEqual([(e.failure_id, e.status) for e in result.failure_history],
                             [(FailureId.IntegerOverflow, FailureStatus.UNRESOLVED)])
            histories.append(result.failure_history)
            with self.assertRaisesRegex(TypecheckError, "does not guarantee Addable"):
                run(source + "calculate[Int64](1i64,2i64)")
        self.assertEqual(*histories)

    def test_missing_conditional_failure(self):
        contract = ("(T,T) -> T {require T guarantees Addable "
                    "failure {IntegerOverflow when T in BoundedArithmetic}}")
        for declaration in self.generic_forms(contract):
            with self.subTest(declaration=declaration), self.assertRaisesRegex(
                    TypecheckError, "undeclared failures: FloatOverflow when T in FiniteArithmetic"):
                run(declaration + " func calculate(x:T,y:T) {return add[T](x,y)}")

    def test_guarantee_dispatch_and_arity(self):
        prefix = ("guarantee G {op(self:Self, other:Self) -> Self} "
                  "impl Int guarantees G {op = builtin core.int.add} ")
        for declaration in self.generic_forms(
                "(T,T) -> T {require T guarantees G failure {IntegerOverflow}}", "op"):
            result = run_internal(prefix + declaration + " let result:Int = op[Int](2,3)")
            self.assertEqual(result.environment["result"].value, 5)
        for declaration in self.generic_forms("(T) -> T {require T guarantees G}", "op"):
            with self.assertRaisesRegex(TypecheckError, "arity mismatch for guarantee method 'op'"):
                run_internal(prefix + declaration)

    def test_repeated_guarantee_requirements(self):
        def contract(first, second):
            return (f"({first},{second}) -> {first} {{require {first} guarantees Addable "
                    f"require {second} guarantees Addable require {first} guarantees Addable}}")

        for source in ("sig calc[A,B]" + contract("A", "B"),
                       "sigtype Calculation[X,Y]" + contract("Y", "X")
                       + " sig calc[A,B]: Calculation[B,A]"):
            requirements = build_symbols(parse(source)).sigs["calc"].requires
            self.assertEqual(requirements, [RequireGuarantees(name, "Addable") for name in "ABA"])

    def test_typegroup_requirement_equivalence(self):
        for declaration in self.generic_forms("(T) -> T {require T in BoundedArithmetic}"):
            source = declaration + " func calculate(x:T) {return x} "
            self.assertEqual(run(source + "let v:Int = calculate[Int](1)").environment["v"].value, 1)
            with self.assertRaisesRegex(TypecheckError, "requirement not satisfied"):
                run(source + "calculate[Float](1.0)")

    def test_no_namespace_fallback(self):
        for name in ["MissingContract", "Int", "add", "Addable", "Errors", "BoundedArithmetic", "A"]:
            with self.subTest(name=name), self.assertRaisesRegex(TypecheckError, f"unknown sigtype '{name}'"):
                run(f"failureset Errors {{ IntegerOverflow }}\nsig calculate: {name}")

    def test_duplicate_applications(self):
        definitions = "sigtype A(Int) -> Int {}\nsigtype B(Int) -> Int { failure { IntegerOverflow } }\n"
        direct = "sig calculate(Float) -> Float {}"
        for first, second in [("sig calculate: A", "sig calculate: B"),
                              ("sig calculate: A", "sig calculate: A"),
                              (direct, "sig calculate: A"), ("sig calculate: A", direct)]:
            with self.subTest(first=first, second=second), self.assertRaisesRegex(
                    TypecheckError, "function 'calculate' has multiple signature declarations"):
                run(definitions + first + "\n" + second)

    def test_implementation_mismatches(self):
        for params, expr in [("", "1"), ("x: Float", "1"), ("x: Int", "1.5"),
                             ("x: Int", "add[Int](x,1)")]:
            self.equivalent_error("(Int) -> Int { failure { Never } }",
                                  f"func calculate({params}) {{ return {expr} }}")
        self.equivalent_error("(Int) -> Int {}", "func calculate(x: Float) { return x }")
