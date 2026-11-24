import unittest

from ginger.errors import TypecheckError
from ginger.parser import parse
from ginger.pipeline import compile, run
from ginger.type_identity import type_identity_key


class ThunkSyntaxTests(unittest.TestCase):
    def test_forward_reference_and_identity(self):
        source = 'var t: Thunk[A] = thunk(1) sigtype A() -> Int { failure { Never } }'
        typ = parse(source).items[0].typ
        self.assertEqual(type_identity_key(typ), ('Thunk', ('A',)))
        self.assertEqual(run(source + ' var x: Int = force(t)').environment['x'].value, 1)

    def test_legacy_types_rejected_in_all_positions(self):
        for spelling in ['Thunk[Int]', 'Thunk[Int]<Never>', 'Thunk[Int]<IntegerOverflow>',
                         'Thunk[Int]<IntegerOverflow, DivideByZero>', 'Thunk[A]<IntegerOverflow>',
                         'Thunk[Int, Never]', 'Thunk[Int]<>', 'Thunk[Box[Int]]']:
            for use in ['var t: TYPE = thunk(1)', 'sig f(TYPE) -> Int {}',
                        'sig f() -> TYPE {}', 'sigtype F(Box[TYPE]) -> Int {}',
                        'typegroup Members { TYPE }', 'typegroup Members {} register Members { TYPE }']:
                with self.subTest(spelling=spelling, use=use), self.assertRaises((TypecheckError, SyntaxError)):
                    compile('type Box sigtype A() -> Int {} ' + use.replace('TYPE', spelling))

    def test_inapplicable_contracts(self):
        for declaration, message in [('sigtype A(Int) -> Int {}', 'zero-argument'),
                                     ('sigtype A() -> T {}', 'fully concrete'),
                                     ('sigtype A() -> T { failure { IOErr when T in BoundedArithmetic } }', 'fully concrete'),
                                     ('sig A() -> Int {}', 'unknown sigtype')]:
            with self.subTest(declaration=declaration), self.assertRaisesRegex(TypecheckError, message):
                compile(declaration + ' var t: Thunk[A] = thunk(1)')

    def test_recursive_contracts(self):
        compile('sigtype A() -> Thunk[B] {} sigtype B() -> Thunk[A] {}')
        compile('sigtype A() -> Thunk[A] {}')

    def test_type_annotation_required(self):
        with self.assertRaises(SyntaxError):
            compile('var t = thunk(1)')

    def test_contract_registry_has_no_fallback(self):
        for declaration in ['type Contract', 'typegroup Contract { Int }',
                            'failureset Contract { IOErr }', 'sig Contract() -> Int {}',
                            'guarantee Contract { op(self: Self) -> Self }']:
            with self.subTest(declaration=declaration), self.assertRaisesRegex(TypecheckError, "unknown sigtype 'Contract'"):
                compile(declaration + '\nvar t: Thunk[Contract] = thunk(1)')

    def test_failure_reference_checks_its_thunk_argument(self):
        source = 'failureset Tagged(T) { IOErr when T in BoundedArithmetic }'
        with self.assertRaisesRegex(TypecheckError, "unknown sigtype 'Missing'"):
            compile(source + ' sigtype Consumer() -> Int { failure { Tagged(Thunk[Missing]) } }')

    def test_same_contract_name_in_nested_duplicate_signature(self):
        source = ('type Box\nsigtype Consumer(Box[Thunk[A]]) -> Unit {} '
                  'sigtype Other(Box[Thunk[A]]) -> Unit {} sigtype A() -> Int {}')
        with self.assertRaisesRegex(TypecheckError, 'duplicate sigtype contract'):
            compile(source)

    def test_direct_and_named_function_contracts_accept_thunk(self):
        source = ('sigtype A() -> Float {} sigtype Consumer(Thunk[A]) -> Float {} '
                  'sig read: Consumer func read(t: Thunk[A]) { return force(t) } '
                  'sig make() -> Thunk[A] {} func make() { return thunk(read(thunk(1.5))) } '
                  'var result: Float = force(make())')
        self.assertEqual(run(source).environment['result'].value, 1.5)

    def test_static_bound_survives_safe_initializer(self):
        decl = 'sigtype Checked() -> Int { failure { IntegerOverflow } } '
        body = 'func f() { let t: Thunk[Checked] = thunk(1) return force(t) }'
        with self.assertRaisesRegex(TypecheckError, 'undeclared failures: IntegerOverflow'):
            compile(decl + 'sig f() -> Int {} ' + body)
        result = run(decl + 'var t: Thunk[Checked] = thunk(1) var x: Int = force(t) '
                     'resolve x { IntegerOverflow { x = 0 } }')
        self.assertEqual(result.environment['x'].value, 1)
        self.assertEqual(result.failure_history, ())

    def test_thunk_name_is_not_a_generic_binding(self):
        decl = 'sigtype A() -> Int {} '
        source = ('sig keep(A,Thunk[A]) -> A {} func keep(x: A,t: Thunk[A]) { return x } '
                  'var x: Float = keep(1.5,thunk(1))')
        self.assertEqual(run(decl + source).environment['x'].value, 1.5)
        with self.assertRaisesRegex(TypecheckError, "unknown sigtype 'A'"):
            compile(source)

    def test_require_cannot_supply_a_thunk_result(self):
        with self.assertRaisesRegex(TypecheckError, 'not inferable'):
            compile('sigtype Deferred() -> T { require T guarantees Addable } '
                    'var t: Thunk[Deferred] = thunk(1)')
