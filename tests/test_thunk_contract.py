import unittest

from test_failure_contract import checked, output
from ginger.ast import TypeRef
from ginger.core.catalog_loader import _type_ref
from ginger.core.failure_spec import FailureId
from ginger.diagnostics import Diagnostics
from ginger.errors import TypecheckError
from ginger.symbols_builder import build_symbols
from ginger.typecheck import typecheck_program, same_type

SET = 'failureset CalculationFailure { DivideByZero IOErr }\n'


class ThunkContractTests(unittest.TestCase):
    def test_catalog_contracts(self):
        from ginger.symbols_builder import normalize_types
        typ = _type_ref({'ref': 'Thunk', 'args': [{'ref': 'Int'}],
                         'failures': ['CalculationFailure']})
        resolved = normalize_types(typ, {'CalculationFailure': frozenset({FailureId.IOErr})})
        self.assertEqual(resolved.latent_failures, frozenset({FailureId.IOErr}))
        with self.assertRaises(TypecheckError):
            normalize_types(_type_ref({'ref': 'Thunk', 'args': [{'ref': 'Int'}]}), {})

    def test_valid_contracts_and_normalization(self):
        for spec, names in [('Never', []), ('DivideByZero', ['DivideByZero']),
                            ('DivideByZero, IOErr', ['DivideByZero', 'IOErr']),
                            ('CalculationFailure', ['DivideByZero', 'IOErr']),
                            ('CalculationFailure, PrintErr', ['DivideByZero', 'IOErr', 'PrintErr']),
                            ('CalculationFailure, DivideByZero', ['DivideByZero', 'IOErr'])]:
            with self.subTest(spec=spec):
                program, _ = checked(SET + f'var t: Thunk[Int, {spec}] = thunk(1)')
                env = typecheck_program(program, Diagnostics())
                self.assertEqual(env['t'].ty.args, (TypeRef('Int'),))
                self.assertEqual(env['t'].ty.latent_failures,
                                 frozenset(FailureId(n) for n in names))

    def test_invalid_contracts_in_all_positions(self):
        for typ in ['Thunk[Int]', 'Thunk', 'Thunk[Int, UnknownFailure]',
                    'Thunk[Int, Never, DivideByZero]', 'Thunk[Int, CalculationFailure, Never]',
                    'Thunk[Int, DivideByZero, DivideByZero]', 'Thunk[Int, Never, Never]']:
            for declaration in [f'var t: {typ} = thunk(1)', f'sig f({typ}) -> Unit {{}}',
                                f'sig f() -> {typ} {{}}']:
                with self.subTest(declaration=declaration), self.assertRaises(TypecheckError):
                    checked(SET + declaration)

    def test_creation_and_force_effects_and_runtime(self):
        prefix = 'var t: Thunk[Float, DivideByZero] = thunk(div(1.0,0.0))\n'
        program, diags = checked(prefix)
        self.assertEqual(diags.items, [])
        self.assertEqual(output(program), '')
        for expr in ['force(t)', 'print(force(t))', 'force(thunk(div(1.0,0.0)))']:
            with self.subTest(expr=expr):
                program, diags = checked(prefix + f'try {expr}\ncatch DivideByZero print(7)')
                self.assertEqual(diags.items, [])
                self.assertEqual(output(program), '7\n')
        _, diags = checked(prefix + 'var x: Float = force(t)')
        self.assertEqual([d.message for d in diags], ['unhandled failures: DivideByZero'])

    def test_assignment_bounds_and_declared_binding(self):
        prefix = SET + 'var t: Thunk[Float, CalculationFailure] = thunk(div(1.0,0.0))\n'
        checked(prefix + 't = thunk(1.0)\ntry force(t)\ncatch IOErr print(1)')
        with self.assertRaisesRegex(TypecheckError, 'type mismatch'):
            checked(prefix + 'var narrow: Thunk[Float, DivideByZero] = t')
        with self.assertRaisesRegex(TypecheckError, 'type mismatch'):
            checked('var t: Thunk[Float, Never] = thunk(div(1.0,0.0))')
        checked(SET + 'var narrow: Thunk[Float, DivideByZero] = thunk(div(1.0,0.0))\n'
                'var wide: Thunk[Float, CalculationFailure] = narrow')

    def test_functions_and_transitive_validation(self):
        prefix = SET + '''
sig execute(Thunk[Float, CalculationFailure]) -> Float { failure CalculationFailure }
func execute(t: Thunk[Float, IOErr, DivideByZero]) { return force(t) }
sig make() -> Thunk[Float, DivideByZero] { failure Never }
func make() { return thunk(div(1.0,0.0)) }
sig use() -> Float { failure CalculationFailure }
func use() { return execute(make()) }
'''
        program, diags = checked(prefix + 'try print(use())\ncatch DivideByZero print(2)\ncatch IOErr print(3)')
        self.assertEqual(diags.items, [])
        self.assertEqual(output(program), '2\n')
        with self.assertRaisesRegex(TypecheckError, 'undeclared failures'):
            checked(prefix.replace('sig use() -> Float { failure CalculationFailure }',
                                   'sig use() -> Float { failure Never }'))
        with self.assertRaisesRegex(TypecheckError, 'return type mismatch'):
            checked('sig make() -> Thunk[Float, Never] {}\n'
                    'func make() { return thunk(div(1.0,0.0)) }')

    def test_parameter_bounds_and_exact_declarations(self):
        prefix = SET + 'sig f(Thunk[Int, DivideByZero]) -> Int { failure DivideByZero }\n'
        for spec in ['IOErr', 'CalculationFailure', 'Never']:
            with self.subTest(spec=spec), self.assertRaisesRegex(TypecheckError, 'positional types'):
                checked(prefix + f'func f(t: Thunk[Int, {spec}]) {{ return force(t) }}')
        checked(prefix + 'func f(t: Thunk[Int, DivideByZero]) { return force(t) }\n'
                'var x: Int = f(thunk(1))')
        with self.assertRaisesRegex(TypecheckError, 'type mismatch'):
            checked(prefix + 'var t: Thunk[Int, CalculationFailure] = thunk(1)\nvar x: Int = f(t)')

    def test_nested_thunk_does_not_leak_inner_failures(self):
        program, diags = checked('var t: Thunk[Thunk[Float, DivideByZero], Never] = '
                                 'thunk(thunk(div(1.0,0.0)))\n'
                                 'var inner: Thunk[Float, DivideByZero] = force(t)\n'
                                 'try force(inner)\ncatch DivideByZero print(5)')
        self.assertEqual(diags.items, [])
        self.assertEqual(output(program), '5\n')

    def test_force_argument_evaluation_effects(self):
        _, diags = checked('sig make() -> Thunk[Int, DivideByZero] { failure IOErr }\n'
                           'func make() { return thunk(1) }\nvar x: Int = force(make())')
        self.assertEqual([d.message for d in diags], ['unhandled failures: DivideByZero, IOErr'])

    def test_handled_keeps_boundary_and_argument_effect(self):
        prefix = '@attr.handled\nsig h(Float) -> Unit { failure IOErr }\nfunc h(x: Float) {}\n'
        program, diags = checked(prefix + 'var t: Thunk[Float, DivideByZero] = thunk(div(1.0,0.0))\n'
                                 'try h(force(t))\ncatch DivideByZero print(8)')
        self.assertEqual(output(program), '8\n')
        self.assertTrue(all(d.code == 'FAILURE_CONTRACT_DEFERRED' for d in diags))
        _, diags = checked(prefix + 'sig wrap() -> Thunk[Unit, Never] {}\n'
                           'func wrap() { return thunk(h(1.0)) }')
        self.assertEqual(len(diags.items), 2)
        self.assertTrue(all('handled: h' in d.message for d in diags))

    def test_catch_set_still_rejected(self):
        with self.assertRaisesRegex(TypecheckError, 'unknown failure'):
            checked(SET + 'var t: Thunk[Float, CalculationFailure] = thunk(div(1.0,0.0))\n'
                    'try force(t)\ncatch CalculationFailure print(0)')

    def test_result_types_remain_invariant(self):
        with self.assertRaisesRegex(TypecheckError, 'type mismatch'):
            checked('var t: Thunk[Int, Never] = thunk(1)\nvar u: Thunk[Float, Never] = t')
        self.assertFalse(same_type(TypeRef('Thunk', (TypeRef('Int'),), latent_failures=frozenset()),
                                  TypeRef('Thunk', (TypeRef('Int'),),
                                          latent_failures=frozenset({FailureId.IOErr}))))


if __name__ == '__main__':
    unittest.main()
