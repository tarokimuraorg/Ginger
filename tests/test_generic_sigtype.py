import unittest

from ginger.core.failure_spec import FailureId
from ginger.errors import TypecheckError
from ginger.numeric import INT_MAX
from ginger.parser import parse
from ginger.pipeline import compile, run
from ginger.symbols_builder import build_symbols


class GenericSigTypeTests(unittest.TestCase):
    deferred = "sigtype Deferred[T]() -> T { failure { Never } } "

    def test_forward_and_nested_specialization(self):
        source = """var a: Thunk[Deferred[Int]] = thunk(10)
var b: Int = force(a)
var outer: Thunk[Deferred[Thunk[Deferred[Int]]]] = thunk(a)
var c: Int = force(force(outer))
"""
        result = run(source + self.deferred)
        self.assertEqual((result.environment["b"].value, result.environment["c"].value), (10, 10))
        self.assertEqual(set(build_symbols(parse(source + self.deferred)).sigtypes), {"Deferred"})

    def test_declarations(self):
        with self.assertRaises(SyntaxError):
            parse("sigtype Bad[T] -> T {}")
        for declaration in ("Bad() -> T", "Bad[T,T]() -> T", "Bad[T](U) -> T",
                            "Bad[T]() -> T[Int]", "Bad[Int]() -> Int", "Bad() -> Self"):
            with self.subTest(declaration=declaration), self.assertRaises(TypecheckError):
                compile("sigtype " + declaration + " {}")

    def test_invalid_applications(self):
        declarations = self.deferred + "sigtype Calculation() -> Int {} sigtype Identity[T](T) -> T {} "
        for name in ("Deferred", "Deferred[Int,Float]", "Deferred[Calculation]",
                     "Calculation[Int]", "Deferred[Missing]", "Identity[Int]", "Deferred[Self]"):
            with self.subTest(name=name), self.assertRaises(TypecheckError):
                compile(declarations + f"sig f(Thunk[{name}]) -> Unit {{}}")

    def test_different_specializations(self):
        with self.assertRaisesRegex(TypecheckError, "type mismatch"):
            compile(self.deferred + "var a: Thunk[Deferred[Int]] = thunk(10) "
                    "var b: Thunk[Deferred[Float]] = a")

    def test_declaration_slots(self):
        compile("sigtype A[T,U]() -> T {} sigtype B[T,U]() -> U {}")
        with self.assertRaisesRegex(TypecheckError, "duplicate sigtype contract"):
            compile("sigtype A[T,U]() -> T {} sigtype B[U,T]() -> U {}")

    def test_requirements(self):
        for clause in ("in BoundedArithmetic", "guarantees Addable"):
            prefix = f"sigtype Deferred[Element]() -> Element {{ require Element {clause} }} "
            compile(prefix + "var a: Thunk[Deferred[Int]] = thunk(1)")
            with self.subTest(clause=clause), self.assertRaisesRegex(TypecheckError, "unsatisfied requirement"):
                compile(prefix + "sig f(Thunk[Deferred[Unit]]) -> Unit {}")

    def test_conditional_failure_is_latent_until_each_force(self):
        prefix = """sigtype Checked[Value]() -> Value {
            failure { IntegerOverflow when Value in BoundedArithmetic }
        }
        """
        source = prefix + f"var t: Thunk[Checked[Int]] = thunk(add[Int]({INT_MAX},1)) "
        self.assertFalse(run(source).failure_history)
        result = run(source + "var a: Int = force(t) var b: Int = force(t)")
        self.assertEqual([e.failure_id for e in result.failure_history], [FailureId.IntegerOverflow] * 2)
        compile(prefix + "sig f(Thunk[Checked[Float]]) -> Float {} "
                "func f(t: Thunk[Checked[Float]]) { return force(t) }")
        with self.assertRaisesRegex(TypecheckError, "undeclared failures"):
            compile(prefix + "sig f(Thunk[Checked[Int]]) -> Int {} "
                    "func f(t: Thunk[Checked[Int]]) { return force(t) }")

    def test_failureset_specializes_nested_type(self):
        src = """type Box[T]
typegroup Boxes { Box[Int] }
failureset Errors(T) { IntegerOverflow when T in Boxes }
sigtype Checked[Element]() -> Int { failure { Errors(Box[Element]) } }
"""
        compile(src + "var x: Thunk[Checked[Int]] = thunk(add[Int](1,2))")
        with self.assertRaisesRegex(TypecheckError, "exceeds failure contract"):
            compile(src + "var x: Thunk[Checked[Float]] = thunk(add[Int](1,2))")
