import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import Mock, patch

import ginger.eval as evaluator
from ginger.ast import TypeRef
from ginger.builtin import BUILTINS
from ginger.errors import EvalError
from ginger.runtime.context import RuntimeContext
from ginger.runtime.failures import FailureStatus
from test_failure_contract import checked, recorded


MARK = 'sig mark(Int) -> Float {}\nfunc mark(x: Int) { print(x) return toFloat(x) }\n'
COMBINE = ('sig combine(Float,Float,Float) -> Float {}\n'
           'func combine(a: Float,b: Float,c: Float) { print(99) return %s }\n')


class FailedArgumentTests(unittest.TestCase):
    def test_order_body_and_unresolved_events(self):
        for middle, expected, count in [('mark(2)', '1\n2\n3\n99\n1.0\n4\n', 0),
                                        ('div(1.0,0.0)', '1\n3\n99\n1.0\n4\n', 1)]:
            with self.subTest(middle=middle):
                program, diagnostics = checked(MARK + COMBINE % 'a' +
                    f'print(combine(mark(1),{middle},mark(3)))\nprint(4)')
                _, context, stdout = recorded(program)
                self.assertEqual(stdout, expected)
                self.assertEqual(len(context.failure_history), count)
                self.assertEqual(context.unresolved_pending(1), (1,) if count else ())
                self.assertEqual(context.incomplete_statements, ())
                self.assertEqual(bool(diagnostics.items), bool(count))

    def test_multiple_failures_bind_separate_causes_and_concrete_types(self):
        seen = []
        original = evaluator.eval_block
        def observe(block, env, *args, **kwargs):
            seen.append(env.copy())
            return original(block, env, *args, **kwargs)
        # T must be inferred from failed arguments too; the successful Int widens.
        source = ('sig first(T,T,T) -> T {}\nfunc first(a: T,b: T,c: T) { return a }\n'
                  'var result: Float = first(1,div(1.0,0.0),div(2.0,0.0))')
        with patch('ginger.eval.eval_block', side_effect=observe):
            env, context, _ = recorded(checked(source)[0])
        self.assertEqual(env['result'].value, 1.0)
        self.assertIs(type(env['result'].value), float)
        local = seen[0]
        for name, event_id in [('b', 1), ('c', 2)]:
            binding = local[name]
            self.assertIsInstance(binding, evaluator.UninitializedBinding)
            self.assertEqual(binding.typ, TypeRef('Float'))
            self.assertEqual(binding.related_event_ids, (event_id,))
            self.assertEqual(binding.cause_event_id, event_id)
            self.assertFalse(binding.mutable)
        self.assertEqual(local.type_bindings, {'T': TypeRef('Float')})
        self.assertEqual(context.unresolved_pending(1), (1, 2))
        self.assertEqual([e.status for e in context.failure_history], [FailureStatus.UNRESOLVED] * 2)
        self.assertEqual(context.next_event_id, 3)

    def assert_stopped(self, source, name, expected):
        context, stream = RuntimeContext(), io.StringIO()
        with redirect_stdout(stream), self.assertRaisesRegex(
                EvalError, f"uninitialized binding '{name}'.*cause event IDs: \\(1,\\)"):
            evaluator._eval_program_with_context(checked(source)[0], context)
        self.assertEqual(stream.getvalue(), expected)
        self.assertEqual(context.next_event_id, 2)
        self.assertEqual(context.unresolved_pending(1), (1,))

    def test_parameter_read_and_builtin_consumer_stop(self):
        for body in ['b', 'neg(b)']:
            with self.subTest(body=body):
                builtin = Mock(return_value=0.0)
                with patch.dict(BUILTINS, {'core.float.neg': builtin}):
                    self.assert_stopped(COMBINE % body +
                        'print(combine(1.0,div(1.0,0.0),3.0))\nprint(4)', 'b', '99\n')
                builtin.assert_not_called()

    def test_fatal_argument_read_stops_later_arguments_and_body(self):
        self.assert_stopped(MARK + COMBINE % 'a' +
            'var x: Float = div(1.0,0.0)\nprint(combine(mark(1),x,mark(3)))\nprint(4)', 'x', '1\n')

    def test_failed_argument_keeps_earlier_side_effects(self):
        prefix = ('sig fail() -> Float { failure { DivideByZero, FloatOverflow, FloatUnderflow } }\n'
                  'func fail() { print(2) return div(1.0,0.0) }\n')
        _, context, stdout = recorded(checked(prefix + MARK + COMBINE % 'a' +
            'print(combine(mark(1),fail(),mark(3)))')[0])
        self.assertEqual(stdout, '1\n2\n3\n99\n1.0\n')
        self.assertEqual(context.unresolved_pending(1), (1,))

    def test_successful_value_contract_still_checked_with_failed_argument(self):
        from ginger.runtime.results import EvalResult, Value
        from ginger.runtime.value_contract import ValueContractViolation
        original = evaluator.eval_expr
        def corrupt(expr, *args, **kwargs):
            if getattr(expr, 'value', None) == 42.0:
                return EvalResult(Value(float('nan')))
            return original(expr, *args, **kwargs)
        with patch('ginger.eval.eval_expr', side_effect=corrupt):
            with self.assertRaises(ValueContractViolation):
                recorded(checked(COMBINE % 'a' + 'print(combine(42.0,div(1.0,0.0),3.0))')[0])

    def test_generic_type_inferred_entirely_from_failed_value(self):
        source = ('sig ignore(T) -> Int {}\nfunc ignore(x: T) { return 7 }\n'
                  'print(ignore(div(1.0,0.0)))')
        _, context, stdout = recorded(checked(source)[0])
        self.assertEqual(stdout, '7\n')
        self.assertEqual(context.get_call(2).type_bindings, {'T': TypeRef('Float')})
        self.assertEqual(context.unresolved_pending(1), (1,))

    def test_thunk_snapshot_of_failed_parameter_stops_only_when_forced(self):
        source = ('sig capture(Float) -> Thunk[Float]<Never> {}\n'
                  'func capture(x: Float) { return thunk(x) }\n'
                  'var t: Thunk[Float]<Never> = capture(div(1.0,0.0))\n'
                  'print(5)\nprint(force(t))\nprint(4)')
        self.assert_stopped(source, 'x', '5\n')

    def test_each_parameter_read_reports_its_own_event(self):
        for name, event_id in [('b', 1), ('c', 2)]:
            with self.subTest(name=name):
                context, stream = RuntimeContext(), io.StringIO()
                source = COMBINE % name + 'print(combine(1.0,div(1.0,0.0),div(2.0,0.0)))'
                with redirect_stdout(stream), self.assertRaises(EvalError) as caught:
                    evaluator._eval_program_with_context(checked(source)[0], context)
                self.assertIn(f"binding '{name}' (cause event IDs: ({event_id},))", str(caught.exception))
                self.assertEqual(stream.getvalue(), '99\n')
                self.assertEqual(context.next_event_id, 3)
