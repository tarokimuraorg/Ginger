import unittest

from ginger.ast import TypeRef
from ginger.core.failure_spec import FailureId
from ginger.errors import TypecheckError
from ginger.parser import parse
from ginger.pipeline import compile, run
from ginger.symbols_builder import build_symbols, instantiated_signature_failures


class GenericSigTests(unittest.TestCase):
    identity = "sigtype Identity[T](T) -> T {} "

    def test_order_and_scope(self):
        sig = parse("sig choose[Second, First](First, Second) -> Second {}").items[0]
        self.assertEqual(sig.type_params, ("Second", "First"))
        self.assertEqual(sig.ret, TypeRef("Second"))
        self.assertEqual(run("sig choose[B,A](A,B) -> B {} "
                             "func choose(x:A,y:B) {return y} "
                             "var v:Float = choose[Float,Int](1,2.5)").environment["v"].value, 2.5)

    def test_inferred_calls_and_forward_applications(self):
        for sig in ("sig identity[Value](Value) -> Value {}",
                    "sig identity[Value]: Identity[Value]"):
            source = (sig + " func identity(x: Value) {let y: Value = x return y} "
                      "sig intIdentity: Identity[Int] "
                      "func intIdentity(x:Int) {return identity[Int](x)} "
                      "var v:Int = add[Int](intIdentity(4), 1) " + self.identity)
            with self.subTest(sig=sig):
                self.assertEqual(run(source).environment["v"].value, 5)
                self.assertEqual(set(build_symbols(parse(source)).sigtypes), {"Identity"})

    def test_simultaneous_substitution(self):
        source = ("sigtype First[T,U](T,U) -> T {} sig select[T,U]: First[U,T] "
                  "func select(x:U,y:T) {return x} var v:Int = select[Float,Int](4,2.5)")
        self.assertEqual(run(source).environment["v"].value, 4)

    def test_return_only(self):
        source = "sig create[Value]() -> Value { require Value guarantees Addable }"
        compile(source)
        with self.assertRaisesRegex(TypecheckError, "explicit type arguments"):
            compile(source + " var v:Int = create()")

    def test_require_and_conditional_failure(self):
        clause = ("{require Value guarantees Addable failure {IntegerOverflow when Value in BoundedArithmetic, "
                  "FloatOverflow when Value in FiniteArithmetic}}")
        template = "sigtype Calculation[Value](Value,Value) -> Value " + clause
        for sig in ("sig calculate[Element]: Calculation[Element]",
                    "sig calculate[Element](Element,Element) -> Element " + clause.replace("Value", "Element")):
            source = template + sig + " func calculate(x:Element,y:Element) {return add[Element](x,y)}"
            with self.subTest(sig=sig):
                self.assertEqual(run(source + " var v:Int = calculate[Int](4,5)").environment["v"].value, 9)
                syms = build_symbols(parse(source))
                for typ, expected in (("Int", {FailureId.IntegerOverflow}), ("Float", {FailureId.FloatOverflow})):
                    self.assertEqual(instantiated_signature_failures(
                        syms, "calculate", {"Element": TypeRef(typ)}), expected)
        with self.assertRaisesRegex(TypecheckError, "unsatisfied requirement"):
            compile(template + " sig f: Calculation[Unit]")

    def test_nested_conditional_subject_and_named_thunk_argument(self):
        source = ("type Box[T]\ntypegroup Boxes { Box[Int] } "
                  "sigtype Checked[A](A) -> A {failure {IntegerOverflow when A in Boxes}} "
                  "sig f[Element]: Checked[Box[Element]]")
        syms = build_symbols(parse(source))
        for typ, expected in (("Int", {FailureId.IntegerOverflow}), ("Float", set())):
            self.assertEqual(instantiated_signature_failures(
                syms, "f", {"Element": TypeRef(typ)}), expected)
        source = (self.identity + "sigtype Deferred[T]() -> T {} "
                  "sig f: Identity[Thunk[Deferred[Int]]] "
                  "func f(x:Thunk[Deferred[Int]]) {return x} "
                  "var v:Int = force(f(thunk(42)))")
        self.assertEqual(run(source).environment["v"].value, 42)

    def test_invalid_declarations_and_calls(self):
        for src in ("sig f[T](U) -> T {}", "sig f[T,T](T) -> T {}", "sig f[Int]() -> Int {}",
                    "sig f[T](T) -> T {} func f(x:U) {return x}",
                    "sig f[T](T) -> T {} var x:T = 1", "sig f: Identity",
                    "sig f[T]: Identity[T,Int]", "sig f[T]: Identity[U]",
                    "sig f: Identity[Identity]", "sig f[T]: Missing[T]",
                    "sig f[T](T) -> T {} sig f[U](U) -> U {}",
                    "sig f: Identity[Int] func f(x:Float) {return x}",
                    "sig f[T](T) -> T {require U guarantees Addable}",
                    "sig f[T](T) -> T {failure {IntegerOverflow when U in BoundedArithmetic}}",
                    "sig f: Identity[Int] func f(x:Int) {return x} var v:Int = f[Float](1.5)"):
            with self.subTest(src=src), self.assertRaises(TypecheckError):
                compile(self.identity + src)
