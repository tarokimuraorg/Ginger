import io
import unittest
from contextlib import redirect_stderr
from dataclasses import FrozenInstanceError
from unittest.mock import Mock, patch

import ginger.eval as evaluator
from ginger.ast import TypeRef
from ginger.builtin import BUILTINS
from ginger.core.failure_spec import EMPTY_FAILURES
from ginger.lower import lower_program
from ginger.main import report_result
from ginger.numeric import INT_MIN, INT_MAX, INT64_MIN, INT64_MAX
from ginger.parser import parse
from ginger.runtime.builtin_bridge import invoke_builtin
from ginger.runtime.context import RuntimeContext
from ginger.runtime.results import ExecutionResult, NoValue, Value
from ginger.runtime.thunk import ThunkValue
from ginger.runtime.value_contract import ValueContractViolation


class IntSubclass(int):
    pass


class FloatSubclass(float):
    pass


class TupleSubclass(tuple):
    pass


class StrSubclass(str):
    pass


class Hostile:
    def __repr__(self):
        raise AssertionError('repr must not be called')


class BuiltinValueContractTests(unittest.TestCase):
    def bridge(self, typ, value, context=None):
        context = context or RuntimeContext()
        frame = context.create_call('test')
        with patch.dict(BUILTINS, {'test.value': lambda: value}):
            return invoke_builtin('test.value', [], EMPTY_FAILURES, context, frame.call_id,
                                  return_type=TypeRef(typ))

    def accepts(self, typ, values):
        for value in values:
            with self.subTest(typ=typ, value=value):
                result = self.bridge(typ, value)
                self.assertIsInstance(result.value_result, Value)
                self.assertIs(result.value_result.value, value)
                self.assertEqual(result.related_event_ids, ())

    def rejects(self, typ, values, reason):
        for value in values:
            with self.subTest(typ=typ, python_type=type(value)):
                context = RuntimeContext()
                with self.assertRaises(ValueContractViolation) as caught:
                    self.bridge(typ, value, context)
                violation = caught.exception
                self.assertIs(context.last_value_contract_violation, violation)
                self.assertIsNone(context.last_contract_violation)
                self.assertEqual(violation.expected_type, TypeRef(typ))
                self.assertIs(violation.actual_python_type, type(value))
                self.assertEqual(violation.reason, reason)
                self.assertEqual(violation.implementation, 'test.value')
                self.assertEqual(violation.violating_call_id, 1)
                self.assertEqual(violation.boundary_kind, 'builtin')
                self.assertEqual(context.failure_history, ())
                self.assertEqual(context.next_event_id, 1)

    def test_int_bounds(self):
        self.accepts('Int', [INT_MIN, INT_MAX, 0])
        self.rejects('Int', [INT_MIN - 1, INT_MAX + 1, 10**100], 'out_of_range')

    def test_int_exact_type(self):
        self.rejects('Int', [True, 1.0, None, IntSubclass(1)], 'wrong_python_type')

    def test_int64_symmetric_bounds(self):
        self.accepts('Int64', [INT64_MIN, INT64_MAX, 0])
        self.rejects('Int64', [INT64_MIN - 1, INT64_MAX + 1], 'out_of_range')

    def test_int64_exact_type(self):
        self.rejects('Int64', [True, 1.0, None, IntSubclass(1)], 'wrong_python_type')

    def test_float_finite_only(self):
        self.accepts('Float', [1.5, 0.0, -0.0, 5e-324, 1.7976931348623157e308])
        self.rejects('Float', [float('inf'), float('-inf'), float('nan')], 'non_finite')
        self.rejects('Float', [1, True, None, FloatSubclass(1.5)], 'wrong_python_type')

    def test_bool_exact_type(self):
        self.accepts('Bool', [True, False])
        self.rejects('Bool', [0, 1, None], 'wrong_python_type')

    def test_unit_is_not_no_value(self):
        self.accepts('Unit', [None])
        self.rejects('Unit', [NoValue(), 0, False, ()], 'wrong_python_type')

    def test_ordering(self):
        self.accepts('Ordering', [('Ordering', v) for v in ('Left', 'Flat', 'Right')])
        self.rejects('Ordering', [('Ordering', 'Unknown')], 'invalid_variant')
        self.rejects('Ordering', [('Other', 'Left'), ('Ordering',), ('Ordering', 1),
                                 (StrSubclass('Ordering'), 'Left'),
                                 ('Ordering', StrSubclass('Left'))], 'invalid_shape')
        self.rejects('Ordering', [['Ordering', 'Left'], None,
                                 TupleSubclass(('Ordering', 'Left'))], 'wrong_python_type')

    def evaluate(self, source):
        return evaluator.eval_program(lower_program(parse(source)))

    def test_shared_implementation_uses_concrete_type_across_dispatch_paths(self):
        generic = ('sig opposite(T) -> T { require T guarantees Negatable }\n'
                   'func opposite(x: T) { return neg(x) }\n')
        for typ, arg in [('Int', '1'), ('Int64', '1i64')]:
            for prefix, expr in [
                    (f'sig direct({typ}) -> {typ} {{ builtin core.int.neg }}\n', f'direct({arg})'),
                    ('', f'neg({arg})'), (generic, f'opposite({arg})')]:
                with self.subTest(typ=typ, expr=expr):
                    with patch.dict(BUILTINS, {'core.int.neg': lambda x: INT_MAX + 1}):
                        result = self.evaluate(prefix + f'var x: {typ} = {expr}')
                    self.assertEqual(result.failure_history, ())
                    if typ == 'Int':
                        self.assertEqual(result.value_contract_violation.expected_type, TypeRef('Int'))
                        self.assertNotIn('x', result.environment)
                    else:
                        self.assertIsNone(result.value_contract_violation)
                        self.assertEqual(result.environment['x'].value, INT_MAX + 1)

    def test_opaque_types_preserve_payload_through_generic_impl_dispatch(self):
        from test_parameterized_type_identity import CAPABILITY, implementation, use
        payload = Hostile()
        for typ in ['Box[Int]', 'Box[Float]', 'Box[Box[Int]]', 'Pair[Int, Float]',
                    'MissingType', 'Box[MissingType]', 'String', 'SomeType[Int]']:
            for call in ['op', 'relay']:
                with self.subTest(typ=typ, call=call):
                    with patch.dict(BUILTINS, {'test.make': lambda: payload,
                                               'test.box_int_op': lambda v: v}):
                        result = self.evaluate(CAPABILITY + implementation(typ) + use(typ, call))
                    self.assertIsNone(result.value_contract_violation)
                    self.assertIs(result.environment['result'].value, payload)

    def test_extension_thunk_return_remains_opaque(self):
        expr = parse('var x: Int = 1').items[0].expr
        for payload in [Hostile(), ThunkValue(expr, {}, None, EMPTY_FAILURES)]:
            with patch.dict(BUILTINS, {'test.value': lambda: payload}):
                result = self.evaluate('sig make() -> Thunk[Int]<Never> { builtin test.value }\n'
                                       'var x: Thunk[Int]<Never> = make()')
            self.assertIsNone(result.value_contract_violation)
            self.assertIs(result.environment['x'].value, payload)

    def test_fatal_stops_assignment_resolve_widening_and_later_calls(self):
        source = ('sig bad() -> Int { failure IOErr builtin test.value }\n'
                  'sig f() -> Int { failure IOErr }\n'
                  'func f() { return bad() }\n')
        for statement in ['var x: Int = f()', 'var x: Int = 7\nx = f()',
                          'var x: Float = f()', 'var x: Float = add(f(), 1.0)']:
            with self.subTest(statement=statement):
                later = Mock()
                with patch.dict(BUILTINS, {'test.value': lambda: True, 'core.int.print': later,
                                           'core.float.add': later}), patch(
                        'ginger.eval.widen_value', wraps=evaluator.widen_value) as widen:
                    recovery = ('' if statement.startswith('var x: Int = 7') else
                                '\nresolve x { IOErr { print(2) } }')
                    result = self.evaluate(source + statement + recovery + '\nprint(3)')
                later.assert_not_called()
                self.assertFalse(any(call.args[0] is True for call in widen.call_args_list))
                self.assertIsNotNone(result.value_contract_violation)
                self.assertIsNone(result.contract_violation)
                self.assertEqual(result.failure_history, ())
                self.assertEqual(result.incomplete_statements, ())
                if statement.startswith('var x: Int = 7'):
                    self.assertEqual(result.environment['x'].value, 7)
                else:
                    self.assertNotIn('x', result.environment)
                self.assertTrue(all(not f.pending_event_ids for f in result.call_frames.values()))

    def test_fatal_skips_pending_failure_propagation_and_restores_scope(self):
        source = ('sig bad() -> Int { builtin test.value }\n'
                  'sig f() -> Int { failure DivideByZero failure FloatOverflow }\n'
                  'func f() { var y: Float = div(1.0,0.0)\nreturn bad() }\n'
                  'var x: Int = f()')
        context = RuntimeContext()
        with patch.dict(BUILTINS, {'test.value': lambda: True}):
            with self.assertRaises(ValueContractViolation):
                evaluator._eval_program_with_context(lower_program(parse(source)), context)
        self.assertEqual(len(context.failure_history), 1)
        self.assertEqual(context.get_call(1).pending_event_ids, ())
        self.assertEqual(context.get_call(2).pending_event_ids, (1,))
        self.assertIsNone(context.current_call_id)
        with self.assertRaises(LookupError):
            evaluator._active_runtime.get()

    def test_snapshot_safe_diagnostics_and_cli(self):
        for value in [10**10000, Hostile(), ('Ordering', 'x' * 10000)]:
            context = RuntimeContext()
            with self.assertRaises(ValueContractViolation):
                self.bridge('Int', value, context)
            snapshot = ExecutionResult.from_context({}, context)
            violation = snapshot.value_contract_violation
            self.assertLess(len(str(violation)), 600)
            context.last_value_contract_violation.reason = 'changed'
            self.assertEqual(violation.reason, 'out_of_range' if type(value) is int else 'wrong_python_type')
            with self.assertRaises(FrozenInstanceError):
                violation.reason = 'changed'
            stream = io.StringIO()
            with redirect_stderr(stream):
                self.assertEqual(report_result(snapshot), 1)
            self.assertIn('Value contract violation', stream.getvalue())


if __name__ == '__main__':
    unittest.main()
