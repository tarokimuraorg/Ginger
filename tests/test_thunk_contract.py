import unittest

from test_failure_contract import checked, output
from ginger.ast import TypeRef
from ginger.core.catalog_loader import _type_ref
from ginger.core.failure_spec import FailureId
from ginger.diagnostics import Diagnostics
from ginger.errors import TypecheckError
from ginger.typecheck import typecheck_program, same_type

SET = 'failureset CalculationFailure { DivideByZero, FloatOverflow, FloatUnderflow, IOErr }\n'


class ThunkContractTests(unittest.TestCase):
    def test_catalog_contracts(self):
        from ginger.symbols_builder import normalize_types
        typ = _type_ref({'ref': 'Thunk', 'sigtype': 'Deferred'})
        self.assertEqual(typ.sigtype_name, 'Deferred')
        with self.assertRaises(TypecheckError):
            normalize_types(_type_ref({'ref': 'Thunk', 'args': [{'ref': 'Int'}]}), {})

    def test_valid_contracts_and_normalization(self):
        for spec, names in [('Never', []), ('DivideByZero', ['DivideByZero']),
                            ('DivideByZero, IOErr', ['DivideByZero', 'IOErr']),
                            ('CalculationFailure', ['DivideByZero', 'FloatOverflow', 'FloatUnderflow', 'IOErr']),
                            ('CalculationFailure, PrintErr', ['DivideByZero', 'FloatOverflow', 'FloatUnderflow', 'IOErr', 'PrintErr']),
                            ('CalculationFailure, DivideByZero', ['DivideByZero', 'FloatOverflow', 'FloatUnderflow', 'IOErr'])]:
            with self.subTest(spec=spec):
                program, _ = checked(SET + f'sigtype Deferred() -> Int {{ failure {{ {spec} }} }}\nvar t: Thunk[Deferred] = thunk(1)')
                env = typecheck_program(program, Diagnostics())
                self.assertEqual(env['t'].ty.sigtype_name, 'Deferred')
                self.assertEqual(env['t'].ty.latent_failures,
                                 frozenset(FailureId(n) for n in names))

    def test_invalid_contracts_in_all_positions(self):
        for typ in ['Thunk[Int]', 'Thunk', 'Thunk[Int]<UnknownFailure>',
                    'Thunk[Int]<Never, DivideByZero>', 'Thunk[Int]<CalculationFailure, Never>',
                    'Thunk[Int]<DivideByZero, DivideByZero>', 'Thunk[Int]<Never, Never>']:
            for declaration in [f'var t: {typ} = thunk(1)', f'sig f({typ}) -> Unit {{}}',
                                f'sig f() -> {typ} {{}}']:
                with self.subTest(declaration=declaration), self.assertRaises((SyntaxError, TypecheckError)):
                    checked(SET + declaration)

    def test_creation_and_force_effects_and_runtime(self):
        prefix = 'sigtype DeferredDivision() -> Float { failure { DivideByZero, FloatOverflow, FloatUnderflow } }\nvar t: Thunk[DeferredDivision] = thunk(div(1.0,0.0))\n'
        program, diags = checked(prefix)
        self.assertEqual(diags.items, [])
        self.assertEqual(output(program), '')
        for expr, typ in [('force(t)', 'Float'), ('print(force(t))', 'Unit')]:
            with self.subTest(expr=expr):
                recovery = 'x = print(7)' if typ == 'Unit' else 'x = 0\nprint(7)'
                program, diags = checked(prefix + f'var x: {typ} = {expr}\n'
                    f'resolve x {{ DivideByZero {{ {recovery} }} }}')
                self.assertEqual([d.message for d in diags], ['unhandled failures: FloatOverflow, FloatUnderflow'])
                self.assertEqual(output(program), '7\n')
        _, diags = checked(prefix + 'var x: Float = force(t)')
        self.assertEqual([d.message for d in diags], ['unhandled failures: DivideByZero, FloatOverflow, FloatUnderflow'])

    def test_assignment_bounds_and_declared_binding(self):
        prefix = SET + 'sigtype DeferredFloatCalculationFailure() -> Float { failure { CalculationFailure } }\nvar t: Thunk[DeferredFloatCalculationFailure] = thunk(div(1.0,0.0))\n'
        checked(prefix + 't = thunk(1.0)\nvar x: Float = force(t)\n'
                'resolve x { IOErr { x = 1 } }')
        with self.assertRaisesRegex(TypecheckError, 'type mismatch'):
            checked(prefix + 'sigtype DeferredDivision() -> Float { failure { DivideByZero, FloatOverflow, FloatUnderflow } }\nvar narrow: Thunk[DeferredDivision] = t')
        with self.assertRaisesRegex(TypecheckError, 'type mismatch'):
            checked('sigtype DeferredFloat() -> Float { failure { Never } }\nvar t: Thunk[DeferredFloat] = thunk(div(1.0,0.0))')
        with self.assertRaisesRegex(TypecheckError, "type mismatch"):
            checked(SET + 'sigtype DeferredDivision() -> Float { failure { DivideByZero, FloatOverflow, FloatUnderflow } }\nsigtype DeferredFloatCalculationFailure() -> Float { failure { CalculationFailure } }\nvar narrow: Thunk[DeferredDivision] = thunk(div(1.0,0.0))\n'
                'var wide: Thunk[DeferredFloatCalculationFailure] = narrow')

    def test_functions_and_transitive_validation(self):
        prefix = SET + '''sigtype DeferredFloatCalculationFailure() -> Float { failure { CalculationFailure } }\nsigtype DeferredDivision() -> Float { failure { DivideByZero, FloatOverflow, FloatUnderflow } }\n
sig execute(Thunk[DeferredFloatCalculationFailure]) -> Float { failure { CalculationFailure } }
func execute(t: Thunk[DeferredFloatCalculationFailure]) { return force(t) }
sig make() -> Thunk[DeferredFloatCalculationFailure] { failure { Never } }
func make() { return thunk(div(1.0,0.0)) }
sig use() -> Float { failure { CalculationFailure } }
func use() { return execute(make()) }
'''
        program, diags = checked(prefix + 'var x: Float = use()\n'
            'resolve x { DivideByZero { x = 2\nprint(2) }\nIOErr { x = 3\nprint(3) } }')
        self.assertEqual([d.message for d in diags], ['unhandled failures: FloatOverflow, FloatUnderflow'])
        self.assertEqual(output(program), '2\n')
        with self.assertRaisesRegex(TypecheckError, 'undeclared failures'):
            checked(prefix.replace('sig use() -> Float { failure { CalculationFailure } }',
                                   'sig use() -> Float { failure { Never } }'))
        with self.assertRaisesRegex(TypecheckError, 'exceeds failure contract'):
            checked('sigtype DeferredFloat() -> Float { failure { Never } }\nsig make() -> Thunk[DeferredFloat] {}\n'
                    'func make() { return thunk(div(1.0,0.0)) }')

    def test_parameter_bounds_and_exact_declarations(self):
        prefix = SET + 'sigtype DeferredIntDivideByZero() -> Int { failure { DivideByZero } }\nsig f(Thunk[DeferredIntDivideByZero]) -> Int { failure { DivideByZero } }\n'
        for spec in ['IOErr', 'CalculationFailure', 'Never']:
            with self.subTest(spec=spec), self.assertRaisesRegex(TypecheckError, 'positional types'):
                checked(prefix + f'sigtype Other() -> Int {{ failure {{ {spec} }} }}\nfunc f(t: Thunk[Other]) {{ return force(t) }}')
        checked(prefix + 'func f(t: Thunk[DeferredIntDivideByZero]) { return force(t) }\n'
                'var x: Int = f(thunk(1))')
        with self.assertRaisesRegex(TypecheckError, 'type mismatch'):
            checked(prefix + 'sigtype DeferredIntCalculationFailure() -> Int { failure { CalculationFailure } }\nvar t: Thunk[DeferredIntCalculationFailure] = thunk(1)\nvar x: Int = f(t)')

    def test_nested_thunk_does_not_leak_inner_failures(self):
        program, diags = checked('sigtype DeferredDivision() -> Float { failure { DivideByZero, FloatOverflow, FloatUnderflow } }\nsigtype DeferredDivisionThunk() -> Thunk[DeferredDivision] { failure { Never } }\nvar t: Thunk[DeferredDivisionThunk] = '
                                 'thunk(thunk(div(1.0,0.0)))\n'
                                 'var inner: Thunk[DeferredDivision] = force(t)\n'
                                 'var x: Float = force(inner)\n'
                                 'resolve x { DivideByZero { x = 5\nprint(5) } }')
        self.assertEqual([d.message for d in diags], ['unhandled failures: FloatOverflow, FloatUnderflow'])
        self.assertEqual(output(program), '5\n')

    def test_force_argument_evaluation_effects(self):
        _, diags = checked('sigtype DeferredIntDivideByZero() -> Int { failure { DivideByZero } }\nsig make() -> Thunk[DeferredIntDivideByZero] { failure { IOErr } }\n'
                           'func make() { return thunk(1) }\nvar x: Int = force(make())')
        self.assertEqual([d.message for d in diags], ['unhandled failures: DivideByZero, IOErr'])

    def test_callee_and_argument_effects_are_preserved(self):
        prefix = 'sig h(Float) -> Unit { failure { IOErr } }\nfunc h(x: Float) {}\n'
        program, diags = checked(prefix + 'sigtype DeferredDivision() -> Float { failure { DivideByZero, FloatOverflow, FloatUnderflow } }\nvar t: Thunk[DeferredDivision] = thunk(div(1.0,0.0))\n'
                                 'var x: Unit = h(force(t))\n'
                                 'resolve x { DivideByZero { x = print(8) } }')
        # h ignores its failed argument and successfully writes Unit; resolve is a no-op.
        from test_failure_contract import recorded
        env, context, stdout = recorded(program)
        self.assertEqual(stdout, '')
        self.assertIsNone(env['x'].value)
        self.assertEqual(context.unresolved_pending(1), (1,))
        self.assertEqual([d.message for d in diags], ['unhandled failures: FloatOverflow, FloatUnderflow, IOErr'])
        with self.assertRaises(TypecheckError):
            checked(prefix + 'sigtype DeferredUnit() -> Unit { failure { Never } }\nsig wrap() -> Thunk[DeferredUnit] {}\n'
                    'func wrap() { return thunk(h(1.0)) }')
        _, diags = checked(prefix + 'sigtype DeferredUnitIOErr() -> Unit { failure { IOErr } }\nsig wrap() -> Thunk[DeferredUnitIOErr] {}\n'
                           'func wrap() { return thunk(h(1.0)) }')
        self.assertEqual(diags.items, [])

    def test_resolve_set_still_rejected(self):
        with self.assertRaisesRegex(TypecheckError, 'unknown failure'):
            checked(SET + 'sigtype DeferredFloatCalculationFailure() -> Float { failure { CalculationFailure } }\nvar t: Thunk[DeferredFloatCalculationFailure] = thunk(div(1.0,0.0))\n'
                    'var x: Float = force(t)\nresolve x { CalculationFailure { x = 0 } }')

    def test_result_types_remain_invariant(self):
        with self.assertRaisesRegex(TypecheckError, 'type mismatch'):
            checked('sigtype DeferredInt() -> Int { failure { Never } }\nsigtype DeferredFloat() -> Float { failure { Never } }\nvar t: Thunk[DeferredInt] = thunk(1)\nvar u: Thunk[DeferredFloat] = t')
        self.assertFalse(same_type(TypeRef('Thunk', sigtype_name='Safe'),
                                   TypeRef('Thunk', sigtype_name='Checked')))


if __name__ == '__main__':
    unittest.main()
