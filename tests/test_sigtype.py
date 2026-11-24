import unittest

from ginger.core.failure_spec import FailureId
from ginger.errors import TypecheckError
from ginger.parser import parse
from ginger.pipeline import run
from ginger.symbols_builder import build_symbols


def symbols(source):
    return build_symbols(parse(source))


class SigTypeTests(unittest.TestCase):
    def duplicate(self, first, second, definitions=""):
        for left, right in [(first, second), (second, first)]:
            with self.subTest(left=left), self.assertRaisesRegex(
                TypecheckError, "duplicate sigtype contract: 'B' has the same contract as 'A'"
            ):
                symbols(f"sigtype A{left}\nsigtype B{right}\n{definitions}")

    def test_independent_registry(self):
        source = "sigtype Calculation() -> Int { failure { Never } }"
        syms = symbols(source)
        self.assertFalse(syms.sigtypes["Calculation"].failures.unconditional)
        for registry in (syms.types, syms.sigs, syms.funcs, run(source).environment):
            self.assertNotIn("Calculation", registry)

    def test_parameter_order_count_return_and_failure_subsets(self):
        syms = symbols("""
sigtype A(Int, Float) -> Int {}
sigtype B(Float, Int) -> Int {}
sigtype C(Int) -> Int {}
sigtype D(Int) -> Float {}
sigtype E(Int) -> Int { failure { IntegerOverflow } }
sigtype F(Int) -> Int { failure { DivideByZero, IntegerOverflow } }
""")
        self.assertEqual(len(syms.sigtypes), 6)
        self.assertEqual(syms.sigtypes["F"].failures.unconditional,
                         {FailureId.IntegerOverflow, FailureId.DivideByZero})

    def test_forward_type_and_separate_function_namespace(self):
        src = """sigtype Calculation(Bee) -> Bee {}
sig Calculation(Bee) -> Bee {}
func Calculation(value: Bee) { return value }
type Bee"""
        self.assertIn("Calculation", symbols(src).funcs)
        run(src)

    def test_other_namespaces(self):
        syms = symbols("sigtype Addable() -> Int {}\nsigtype Errors(Int) -> Int {}\n"
                       "failureset Errors { IntegerOverflow }")
        self.assertIn("Addable", syms.guarantees)
        self.assertIn("Errors", syms.failuresets)

    def test_duplicate_name(self):
        with self.assertRaisesRegex(TypecheckError, "duplicate sigtype 'A'"):
            symbols("sigtype A() -> Int {}\nsigtype A() -> Float {}")

    def test_exact_duplicate(self):
        self.duplicate("(Int) -> Int { failure { IntegerOverflow } }",
                       "(Int) -> Int { failure { IntegerOverflow } }")

    def test_omitted_failure(self):
        self.duplicate("() -> Int {}", "() -> Int { failure { Never } }")
        with self.assertRaisesRegex(SyntaxError, "at least one"):
            symbols("sigtype A() -> Int { failure {} }")

    def test_failure_order_and_forward_set(self):
        explicit = "() -> Int { failure { IntegerOverflow, DivideByZero } }"
        self.duplicate(explicit, "() -> Int { failure { DivideByZero, IntegerOverflow } }")
        self.duplicate(explicit, "() -> Int { failure { Errors } }",
                       "failureset Errors { DivideByZero, IntegerOverflow }")

    def test_namespace_conflicts(self):
        named = "sigtype Calculation() -> Int {}"
        for other in ["type Calculation", "typegroup Calculation {}"]:
            for source in [other + "\n" + named, named + "\n" + other]:
                with self.subTest(source=source), self.assertRaisesRegex(TypecheckError, "name conflict"):
                    symbols(source)
        for name in ["Int", "Thunk"]:
            with self.assertRaisesRegex(TypecheckError, "name conflict"):
                symbols(f"sigtype {name}() -> Int {{}}")

    def test_validate_original_type_references(self):
        for suffix in ["(Missing) -> Int {}", "() -> Missing {}", "(Box[Missing]) -> Int {}",
                       "() -> Int { failure { Errors(Missing) } }"]:
            with self.subTest(suffix=suffix), self.assertRaisesRegex(TypecheckError, "unknown type 'Missing'"):
                symbols("type Box\nfailureset Errors(T) { IntegerOverflow }\nsigtype A" + suffix)

    def test_unknown_failures(self):
        with self.assertRaisesRegex(TypecheckError, "unknown failure"):
            symbols("sigtype A() -> Int { failure { Missing } }")

    def test_stage_boundaries(self):
        named = "sigtype Calculation() -> Int {}\n"
        for use in ["sig f(Calculation) -> Unit {}"]:
            with self.assertRaisesRegex(TypecheckError, "unknown type 'Calculation'"):
                symbols(named + use)
        with self.assertRaises(TypecheckError):
            run(named + "Calculation()")
        with self.assertRaisesRegex(TypecheckError, "no corresponding sig"):
            run(named + "func Calculation() { return 1 }")
        with self.assertRaises(SyntaxError):
            parse("func f() { " + named + " }")

    def test_alpha_equivalence(self):
        self.duplicate("(T, T) -> T {}", "(U, U) -> U {}")
        symbols("sigtype A(T, T) -> T {}\nsigtype B(T, U) -> T {}\nsigtype C(T, U) -> U {}")

    def test_nested_identity_and_existing_arity(self):
        symbols("type Box\nsigtype A(Box[Box[Int], Float]) -> Int {}\n"
                "sigtype B(Box[Box[Float], Int]) -> Int {}")

    def test_requirements(self):
        self.duplicate("(T) -> T { require T guarantees Addable require T in BoundedArithmetic }",
                       "(U) -> U { require U in BoundedArithmetic require U guarantees Addable }")
        symbols("sigtype A(T) -> T { require T guarantees Addable }\nsigtype B(T) -> T {}")
        for clause in ["U guarantees Addable", "T guarantees Missing", "T in Missing"]:
            with self.assertRaises(TypecheckError):
                symbols(f"sigtype A(T) -> U {{ require {clause} }}")

    def test_symbolic_and_latent_effects(self):
        definition = "sigtype Safe() -> Int {} sigtype Checked() -> Int { failure { IntegerOverflow } } failureset Errors(T) { IntegerOverflow when T in BoundedArithmetic }"
        self.duplicate("(T) -> Thunk[Safe] { failure { Errors(T) } }",
                       "(U) -> Thunk[Safe] { failure { IntegerOverflow when U in BoundedArithmetic } }",
                       definition)
        symbols(definition + """
sigtype A(T,U) -> Thunk[Safe] { failure { Errors(T) } }
sigtype B(T,U) -> Thunk[Checked] { failure { Errors(T) } }
sigtype C(T,U) -> Thunk[Safe] { failure { Errors(U) } }
sigtype D(T,U) -> Thunk[Checked] { failure { Errors(U) } }
""")

    def test_existing_identifier_sigtype(self):
        run("sig sigtype() -> Int {}\nfunc sigtype() { return 1 }\nlet result: Int = sigtype()")
