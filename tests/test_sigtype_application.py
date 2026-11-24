import unittest

from ginger.ast import SigRefDecl
from ginger.errors import TypecheckError
from ginger.numeric import INT_MAX
from ginger.parser import parse
from ginger.pipeline import run
from ginger.symbols_builder import build_symbols
from internal_declarations import run as run_internal


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
                              "func calculate(x: Int) { return add(x,1) }\n"
                              "let result: Int = calculate(41)", 42)

    def test_zero_arguments_and_same_name(self):
        result = run("sig A: A\nfunc A() { return 7 }\nsigtype A() -> Int { failure { Never } }\n"
                     "let result: Int = A()")
        self.assertEqual(result.environment["result"].value, 7)

    def test_multiple_parameters_and_failures(self):
        self.equivalent_value("(Int, Int) -> Int { failure { Errors } }",
                              "failureset Errors { DivideByZero, IntegerOverflow }\n"
                              "func calculate(x: Int, y: Int) { return add(x,y) }\n"
                              "let result: Int = calculate(1,2)", 3)

    def test_shared_generic_contract(self):
        source = """sigtype Identity(T) -> T {}
sig first: Identity
sig second: Identity
func first(x: T) { return x }
func second(y: T) { return y }
let a: Int = first(1)
let b: Float = second(2.5)"""
        syms = build_symbols(parse(source))
        self.assertIsNot(syms.sigs["first"].params, syms.sigs["second"].params)
        self.assertIsNot(syms.sigs["first"].params, syms.sigtypes["Identity"].declaration.params)
        env = run(source).environment
        self.assertEqual((env["a"].value, env["b"].value), (1, 2.5))

    def test_conditional_failure_specialization(self):
        contract = ("(T,T) -> T { require T guarantees Addable "
                    "failure { IntegerOverflow when T in BoundedArithmetic, "
                    "FloatOverflow when T in FiniteArithmetic } }")
        body = "func calculate(x: T,y: T) { return add(x,y) }\n"
        self.equivalent_value(contract, body + "let result: Float = calculate(1.0,2.0)", 3.0)
        histories = [run(source).failure_history for source in self.forms(
            contract, body + f"let result: Int = calculate({INT_MAX},1)")]
        self.assertTrue(histories[0])
        self.assertEqual(*histories)
        self.equivalent_error(contract, body + "let result: Int64 = calculate(1i64,2i64)")

    def test_missing_conditional_failure(self):
        self.equivalent_error(
            "(T,T) -> T { require T guarantees Addable "
            "failure { IntegerOverflow when T in BoundedArithmetic } }",
            "func calculate(x: T, y: T) { return add(x,y) }",
        )

    def test_contract_name_is_neither_type_nor_callable(self):
        prefix = "sigtype Calculation(Int) -> Int {}\nsig calculate: Calculation\n"
        for use in ["sig f(Calculation) -> Int {}", "let x: Int = Calculation(1)"]:
            with self.subTest(use=use), self.assertRaises(TypecheckError):
                run(prefix + use)

    def test_requirement_in(self):
        contract = "(T) -> T { require T in BoundedArithmetic }"
        body = "func calculate(x: T) { return x }\n"
        self.equivalent_value(contract, body + "let result: Int = calculate(1)", 1)
        self.equivalent_error(contract, body + "let result: Float = calculate(1.0)")

    def test_guarantee_dispatch(self):
        prefix = ("guarantee G { op(self: Self, other: Self) -> Self }\n"
                  "impl Int guarantees G { op = builtin core.int.add }\n")
        for source in self.forms("(U,U) -> U { require U guarantees G failure { IntegerOverflow } }",
                                 "let result: Int = op(2,3)", "op"):
            self.assertEqual(run_internal(prefix + source).environment["result"].value, 5)
        for source in self.forms("(U) -> U { require U guarantees G }", "", "op"):
            with self.assertRaisesRegex(TypecheckError, "arity mismatch for guarantee method 'op'"):
                run_internal(prefix + source)

    def test_repeated_guarantee_requirements(self):
        syms = build_symbols(parse("sigtype Contract(T) -> T { require T guarantees Addable "
                                   "require T guarantees Addable }\nsig calc: Contract"))
        self.assertEqual(len(syms.sigs["calc"].requires), 2)

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
                             ("x: Int", "add(x,1)")]:
            self.equivalent_error("(Int) -> Int { failure { Never } }",
                                  f"func calculate({params}) {{ return {expr} }}")
        self.equivalent_error("(T) -> T {}", "func calculate(x: U) { return x }")
