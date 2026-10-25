import itertools
import unittest
from unittest.mock import Mock, patch

from test_failure_contract import checked, output
from ginger.ast import TypeRef
from ginger.builtin import BUILTINS
from ginger.eval import eval_program
from ginger.errors import TypecheckError
from ginger.core.failure_spec import EMPTY_FAILURES
from ginger.typecheck import compatible


RETURNS = {'cmp': 'Ordering', 'balance': 'Ordering', 'slope': 'Slope',
           'eq': 'Bool', 'lt': 'Bool', 'gt': 'Bool'}


class ComparisonApiTests(unittest.TestCase):
    def evaluate(self, source):
        program, diagnostics = checked(source)
        return eval_program(program)

    def matrix(self, literals):
        for (a, av), (b, bv) in itertools.product(literals, repeat=2):
            with self.subTest(a=a, b=b):
                a = f'({a})' if a.startswith('-') else a
                b = f'({b})' if b.startswith('-') else b
                source = '\n'.join(f'var {name}Result: {ret} = {name}({a},{b})'
                                   for name, ret in RETURNS.items())
                result = self.evaluate(source + f'\nvar reverse: Bool = lt({b},{a})')
                values = {name: result.environment[name + 'Result'].value for name in RETURNS}
                tag = 'Left' if av > bv else 'Flat' if av == bv else 'Right'
                self.assertEqual(values['cmp'], ('Ordering', tag))
                self.assertEqual(values['balance'], values['cmp'])
                self.assertEqual(values['slope'], ('Slope', {'Left': 'Falling', 'Flat': 'Flat', 'Right': 'Rising'}[tag]))
                self.assertEqual((values['eq'], values['lt'], values['gt']), (av == bv, av < bv, av > bv))
                self.assertEqual(sum((values['eq'], values['lt'], values['gt'])), 1)
                self.assertEqual(values['gt'], result.environment['reverse'].value)
                self.assertEqual(result.failure_history, ())
                self.assertIsNone(result.value_contract_violation)
                for frame in result.call_frames.values():
                    self.assertEqual(frame.declared_failure_contract, EMPTY_FAILURES)

    def test_basic_and_swap(self):
        self.matrix([('1', 1), ('2', 2)])

    def test_int_boundaries(self):
        self.matrix([(str(v), v) for v in [-9007199254740991, 0, 9007199254740991]])

    def test_int64_boundaries(self):
        self.matrix([(f'{v}i64', v) for v in [-9223372036854775807, 0, 9223372036854775807]])

    def test_float_extremes_and_signed_zero(self):
        self.matrix([(str(v), v) for v in [-1e308, -0.0, 0.0, 1e308]])

    def test_widening_matches_cmp(self):
        for name, ret in RETURNS.items():
            self.evaluate(f'var x: {ret} = {name}(1,2.0)')
            self.evaluate(f'var x: {ret} = {name}(1,2i64)')
            for args in ['1i64,2.0', 'eq(1,1),1']:
                with self.subTest(name=name, args=args), self.assertRaises(TypecheckError):
                    checked(f'var x: {ret} = {name}({args})')

    def test_nominal_types(self):
        for source, target in [('Ordering', 'Slope'), ('Slope', 'Ordering')]:
            self.assertFalse(compatible(TypeRef(source), TypeRef(target)))
            call = 'slope' if source == 'Slope' else 'balance'
            with self.assertRaises(TypecheckError):
                checked(f'var x: {target} = {call}(1,1)')

    def test_generic_dispatch(self):
        for name, ret in RETURNS.items():
            prefix = (f'sig relay(T,T) -> {ret} {{ require T guarantees Ord }}\n'
                      f'func relay(a: T,b: T) {{ return {name}(a,b) }}\n')
            for args in ['1,2', '1i64,2i64', '1.0,2.0']:
                direct = self.evaluate(f'var x: {ret} = {name}({args})')
                generic = self.evaluate(prefix + f'var x: {ret} = relay({args})')
                self.assertEqual(generic.environment['x'].value, direct.environment['x'].value)

    def test_custom_cmp_only_impl(self):
        prefix = ('impl Box guarantees Ord { cmp = builtin test.compare }\n'
                  'sig make() -> Box { builtin test.make }\n')
        for name, ret in [('balance', 'Ordering'), ('slope', 'Slope'), ('gt', 'Bool')]:
            for generic in [False, True]:
                relay = (f'sig relay(T,T) -> {ret} {{ require T guarantees Ord }}\n'
                         f'func relay(a: T,b: T) {{ return {name}(a,b) }}\n') if generic else ''
                impl = Mock(return_value=('Ordering', 'Left'))
                with patch.dict(BUILTINS, {'test.make': lambda: object(), 'test.compare': impl}):
                    result = self.evaluate(prefix + relay + f'var x: {ret} = {"relay" if generic else name}(make(),make())')
                impl.assert_called_once()
                self.assertEqual(result.environment['x'].value,
                                 {'balance': ('Ordering', 'Left'), 'slope': ('Slope', 'Falling'), 'gt': True}[name])
                self.assertFalse(any(f.function_name == 'cmp' for f in result.call_frames.values()))

    def test_invalid_canonical_result_is_validated_before_projection(self):
        for name, ret in [('balance', 'Ordering'), ('slope', 'Slope'), ('gt', 'Bool')]:
            with patch.dict(BUILTINS, {'core.int.cmp': lambda a, b: ('Slope', 'Flat')}):
                result = self.evaluate(f'var x: {ret} = {name}(1,1)')
            self.assertEqual(result.value_contract_violation.expected_type, TypeRef('Ordering'))
            self.assertEqual(result.failure_history, ())
            self.assertNotIn('x', result.environment)

    def test_invalid_operands_remain_fatal(self):
        for name, ret in [('balance', 'Ordering'), ('slope', 'Slope'), ('gt', 'Bool')]:
            for typ, bad, literal in [('Int', True, '1'), ('Int64', True, '1i64'),
                                      ('Float', float('nan'), '1.0'),
                                      ('Float', float('inf'), '1.0'),
                                      ('Float', float('-inf'), '1.0')]:
                impl = Mock()
                with patch.dict(BUILTINS, {'test.bad': lambda: bad, 'core.int.cmp': impl, 'core.float.cmp': impl}):
                    result = self.evaluate(f'sig bad() -> {typ} {{ builtin test.bad }}\nvar x: {ret} = {name}(bad(),{literal})')
                impl.assert_not_called()
                self.assertIsNotNone(result.value_contract_violation)
                self.assertEqual(result.failure_history, ())

    def test_custom_slope_return_and_print(self):
        for value in [('Slope', 'Rising'), ('Slope', 'Flat'), ('Slope', 'Falling'), ('Ordering', 'Flat'), 'Flat', None]:
            with patch.dict(BUILTINS, {'test.slope': lambda: value}):
                result = self.evaluate('sig make() -> Slope { builtin test.slope }\nvar x: Slope = make()')
            if type(value) is tuple and value[0] == 'Slope':
                self.assertEqual(result.environment['x'].value, value)
            else:
                self.assertIsNotNone(result.value_contract_violation)
                self.assertNotIn('x', result.environment)
                self.assertEqual(result.failure_history, ())
        program, _ = checked('print(slope(1,2))\nprint(slope(1,1))\nprint(slope(2,1))')
        self.assertEqual(output(program), 'Rising\nFlat\nFalling\n')
