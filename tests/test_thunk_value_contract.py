import unittest
from dataclasses import replace
from unittest.mock import Mock, patch

import ginger.eval as evaluator
from ginger.ast import IdentExpr, TypeRef
from ginger.builtin import BUILTINS, call_builtin
from ginger.core.failure_spec import EMPTY_FAILURES, FailureId
from ginger.lower import lower_program
from ginger.numeric import INT_MIN, INT_MAX, INT64_MIN, INT64_MAX
from ginger.parser import parse
from ginger.runtime.thunk import ThunkValue


class ThunkValueContractTests(unittest.TestCase):
    def thunk(self, payload, saved=EMPTY_FAILURES):
        return ThunkValue(IdentExpr('payload'),
                          {'payload': evaluator.Cell(payload, False)}, None, saved)

    def run_source(self, source, thunk):
        with patch.dict(BUILTINS, {'test.make': lambda: thunk}):
            return evaluator.eval_program(lower_program(parse(source)))

    def source(self, typ='Float', contract='Never', tail=None):
        return (f'sig make() -> Thunk[{typ}]<{contract}> {{ builtin test.make }}\n' +
                (tail if tail is not None else f'var x: {typ} = force(make())'))

    def test_return_shape_and_safe_diagnostic(self):
        for value in [object(), 7, None, 'fake']:
            with self.subTest(value=value):
                result = self.run_source(self.source(), value)
                v = result.value_contract_violation
                self.assertEqual(v.reason, 'wrong_python_type')
                self.assertEqual(v.boundary_kind, 'builtin')
                self.assertEqual(v.implementation, 'test.make')
                self.assertIs(v.actual_python_type, type(value))
                self.assertEqual(result.failure_history, ())
                self.assertNotIn('x', result.environment)

    def test_saved_contract_normalization_and_subset(self):
        overflow = frozenset({FailureId.FloatOverflow})
        divide = frozenset({FailureId.DivideByZero})
        for public, saved, valid in [
                ('Never', EMPTY_FAILURES, True),
                ('FloatOverflow', overflow, True),
                ('FloatOverflow, DivideByZero', overflow, True),
                ('Calculation', overflow, True),
                ('Never', overflow, False), ('FloatOverflow', divide, False)]:
            with self.subTest(public=public, saved=saved):
                source = 'failureset Calculation { FloatOverflow DivideByZero }\n'
                source += self.source(contract=public, tail=f'var t: Thunk[Float]<{public}> = make()')
                result = self.run_source(source, self.thunk(1.0, saved))
                self.assertEqual(result.value_contract_violation is None, valid)
                if not valid:
                    self.assertEqual(result.value_contract_violation.reason, 'contract_mismatch')
                self.assertEqual(result.failure_history, ())

    def test_return_does_not_evaluate_expression(self):
        thunk = ThunkValue(IdentExpr('missing'), {}, None, EMPTY_FAILURES)
        result = self.run_source(self.source(tail='var t: Thunk[Float]<Never> = make()'), thunk)
        self.assertIsNone(result.value_contract_violation)
        self.assertIs(result.environment['t'].value, thunk)

    def check_values(self, typ, good, bad):
        for value in good + bad:
            with self.subTest(typ=typ, value=value):
                result = self.run_source(self.source(typ), self.thunk(value))
                valid = any(value is item for item in good)
                self.assertEqual(result.value_contract_violation is None, valid)
                self.assertEqual(result.failure_history, ())
                self.assertIsNone(result.contract_violation)
                if valid:
                    self.assertIs(result.environment['x'].value, value)
                else:
                    v = result.value_contract_violation
                    self.assertEqual(v.expected_type, TypeRef(typ))
                    self.assertEqual((v.boundary_kind, v.implementation), ('thunk', 'force'))
                    self.assertEqual(v.violating_call_id, 1)
                    self.assertIs(v.actual_python_type, type(value))
                    self.assertTrue(v.actual_value_repr)
                    self.assertNotIn('x', result.environment)

    def test_float(self):
        self.check_values('Float', [0.0, 1.5],
                          [float('nan'), float('inf'), float('-inf'), 1, True, None])

    def test_int(self):
        self.check_values('Int', [INT_MIN, INT_MAX], [True, INT_MAX + 1, 1.0])

    def test_int64(self):
        self.check_values('Int64', [INT64_MIN, INT64_MAX],
                          [INT64_MIN - 1, INT64_MAX + 1, True])

    def test_bool(self):
        self.check_values('Bool', [True, False], [1, 0, None])

    def test_unit(self):
        self.check_values('Unit', [None], [False, 0, ()])

    def test_ordering(self):
        self.check_values('Ordering', [('Ordering', x) for x in ['Left', 'Flat', 'Right']],
                          [('Ordering',), ('Other', 'Left'), ('Ordering', 'Bad'), None])

    def test_nested_one_layer_at_a_time(self):
        for payload in [1.0, float('nan'), float('inf')]:
            inner = self.thunk(payload)
            outer = self.thunk(inner)
            prefix = self.source('Thunk[Float]<Never>', tail='var t: Thunk[Float]<Never> = force(make())')
            result = self.run_source(prefix, outer)
            self.assertIsNone(result.value_contract_violation)
            self.assertIs(result.environment['t'].value, inner)
            result = self.run_source(prefix + '\nvar x: Float = force(t)', outer)
            self.assertEqual(result.value_contract_violation is None, payload == 1.0)
        inner = ThunkValue(IdentExpr('missing'), {}, None, EMPTY_FAILURES)
        self.assertIsNone(self.run_source(prefix, self.thunk(inner)).value_contract_violation)
        for inner in [object(), self.thunk(1.0, frozenset({FailureId.FloatOverflow}))]:
            result = self.run_source(prefix, self.thunk(inner))
            self.assertEqual(result.value_contract_violation.boundary_kind, 'thunk')

    def test_generic_concrete_result(self):
        generic = ('sig use(Thunk[T]<Never>) -> T {}\n'
                   'func use(t: Thunk[T]<Never>) { return force(t) }\n')
        for typ, value, valid in [('Int', 1, True), ('Float', 1.0, True),
                                   ('Int', 1.0, False), ('Float', 1, False),
                                   ('Float', float('nan'), False)]:
            with self.subTest(typ=typ, value=value):
                result = self.run_source(generic + self.source(typ, tail=f'var x: {typ} = use(make())'),
                                         self.thunk(value))
                self.assertEqual(result.value_contract_violation is None, valid)
                if not valid:
                    self.assertEqual(result.value_contract_violation.expected_type, TypeRef(typ))

    def test_open_world_payloads(self):
        payload = object()
        for typ in ['Box[Int]', 'Box[Float]', 'Box[Box[Int]]', 'Pair[Int, Float]',
                    'MissingType', 'Box[MissingType]']:
            with self.subTest(typ=typ):
                result = self.run_source(self.source(typ), self.thunk(payload))
                self.assertIsNone(result.value_contract_violation)
                self.assertIs(result.environment['x'].value, payload)
                self.assertIsNotNone(self.run_source(self.source(typ), payload).value_contract_violation)

    def test_fatal_prevents_assignment_resolve_and_continuation(self):
        for statement in ['var x: Float = force(make())', 'var x: Float = 7.0\nx = force(make())',
                          'var x: Float = add(force(make()), 1.0)']:
            later = Mock()
            source = self.source(contract='FloatOverflow', tail=statement)
            if '= 7.0' not in statement:
                source += '\nresolve x { FloatOverflow { x = 0.0 } }'
            source += '\nprint(3)'
            with patch.dict(BUILTINS, {'core.int.print': later, 'core.float.add': later}):
                result = self.run_source(source, self.thunk(float('nan')))
            later.assert_not_called()
            self.assertEqual(result.value_contract_violation.reason, 'non_finite')
            self.assertEqual(result.failure_history, ())
            if '= 7.0' in statement:
                self.assertEqual(result.environment['x'].value, 7.0)
            else:
                self.assertNotIn('x', result.environment)

    def test_standard_thunk_success_and_no_value(self):
        for expr in ['1.5', 'div(1.0, 0.0)']:
            result = evaluator.eval_program(lower_program(parse(
                f'var t: Thunk[Float]<DivideByZero, FloatOverflow> = thunk({expr})\n'
                'var x: Float = force(t)')))
            self.assertIsNone(result.value_contract_violation)
            self.assertIsNone(result.contract_violation)
            self.assertEqual(len(result.failure_history), int(expr.startswith('div')))

    def test_direct_builtin_remains_raw(self):
        payload = object()
        with patch.dict(BUILTINS, {'test.make': lambda: payload}):
            self.assertIs(call_builtin('test.make'), payload)

    def test_narrowed_public_contract_rejected_before_evaluation(self):
        original = evaluator.expression_type

        def narrow_public(expr, env, syms, outer=None):
            typ = original(expr, env, syms, outer)
            if isinstance(expr, IdentExpr) and expr.name == 't':
                return replace(typ, latent_failures=EMPTY_FAILURES)
            return typ

        with patch('ginger.eval.expression_type', side_effect=narrow_public):
            result = evaluator.eval_program(lower_program(parse(
                'var t: Thunk[Float]<DivideByZero, FloatOverflow> = thunk(div(1.0, 0.0))\n'
                'var x: Float = force(t)')))
        self.assertIsNone(result.contract_violation)
        self.assertEqual(result.value_contract_violation.reason, 'contract_mismatch')
        self.assertEqual(result.value_contract_violation.boundary_kind, 'value_read')
        self.assertEqual(result.failure_history, ())
