import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import Mock, patch

import ginger.eval as evaluator
from ginger.builtin import BUILTINS
from ginger.core.failure_spec import FailureId
from ginger.errors import EvalError, TypecheckError
from ginger.runtime.context import RuntimeContext
from ginger.runtime.failures import FailureStatus
from test_failure_contract import checked, recorded


class UninitializedReadTests(unittest.TestCase):
    def stopped(self, source, name='a'):
        context, env, stream = RuntimeContext(), {}, io.StringIO()
        with redirect_stdout(stream), self.assertRaisesRegex(
                EvalError, f"cannot read uninitialized binding '{name}'"):
            evaluator._eval_program_with_context(checked(source)[0], context, env)
        self.assertEqual(stream.getvalue(), '')
        event, = context.failure_history
        self.assertEqual((event.event_id, event.failure_id, event.status),
                         (1, FailureId.DivideByZero, FailureStatus.UNRESOLVED))
        self.assertEqual(context.next_event_id, 2)
        self.assertIsNone(context.last_contract_violation)
        self.assertIsNone(context.current_call_id)
        self.assertEqual([r.related_event_ids for r in context.incomplete_statements], [(1,)])
        return env, context

    def test_independent_statement_continues(self):
        env, context, stdout = recorded(checked(
            'var a: Float = div(1.0,0.0)\nprint(999)')[0])
        self.assertEqual(stdout, '999\n')
        self.assertIsInstance(env['a'], evaluator.UninitializedBinding)
        self.assertEqual(env['a'].related_event_ids, (1,))
        self.assertEqual(len(context.failure_history), 1)
        self.assertEqual(context.failure_history[0].status, FailureStatus.UNRESOLVED)

    def test_read_stops_before_print_and_next_statement(self):
        for keyword in ('var', 'let'):
            with self.subTest(keyword=keyword):
                env, _ = self.stopped(f'{keyword} a: Float = div(1.0,0.0)\nprint(a)\nprint(999)')
                self.assertIsInstance(env['a'], evaluator.UninitializedBinding)
                self.assertEqual(env['a'].mutable, keyword == 'var')
                self.assertEqual(env['a'].typ.name, 'Float')

    def test_dependent_binding_and_chain_are_not_created(self):
        for tail in ('', '\nvar c: Float = add(b,1.0)\nprint(999)'):
            with self.subTest(tail=tail):
                add = Mock(return_value=2.0)
                with patch.dict(BUILTINS, {'core.float.add': add}):
                    env, _ = self.stopped('var a: Float = div(1.0,0.0)\n'
                                          'var b: Float = add(a,1.0)' + tail)
                add.assert_not_called()
                self.assertEqual(set(env), {'a'})

    def test_recovery_and_subsequent_dependency(self):
        for tail, output in [('print(a)', '10.0\n'),
                             ('var b: Float = add(a,1.0)\nprint(b)', '11.0\n')]:
            with self.subTest(tail=tail):
                env, context, stdout = recorded(checked(
                    'var a: Float = div(1.0,0.0)\na = 10.0\n' + tail)[0])
                self.assertEqual(stdout, output)
                self.assertIsInstance(env['a'], evaluator.Cell)
                self.assertEqual(env['a'].value, 10.0)
                if 'b' in env:
                    self.assertEqual(env['b'].value, 11.0)
                self.assertEqual(len(context.failure_history), 1)
                self.assertEqual(context.failure_history[0].status, FailureStatus.UNRESOLVED)
        with self.assertRaisesRegex(TypecheckError, 'immutable'):
            checked('let a: Float = div(1.0,0.0)\na = 10.0')

    def test_public_entry_surfaces_eval_error(self):
        with self.assertRaisesRegex(EvalError, "uninitialized binding 'a'"):
            evaluator.eval_program(checked('var a: Float = div(1.0,0.0)\nprint(a)')[0])

    def test_function_local_stops_without_caller_binding(self):
        locals_seen = []
        original = evaluator.eval_block
        def observe(block, env, syms, outer=None):
            locals_seen.append(env)
            return original(block, env, syms, outer)
        add = Mock(return_value=2.0)
        with patch('ginger.eval.eval_block', side_effect=observe), patch.dict(BUILTINS, {'core.float.add': add}):
            env, context = self.stopped(
                'sig f(Float) -> Float { failure DivideByZero }\n'
                'func f(x: Float) { var a: Float = div(x,0.0)\n'
                'var b: Float = add(a,1.0)\nreturn b }\n'
                'var result: Float = f(1.0)\nprint(999)')
        self.assertEqual(env, {})
        self.assertEqual(set(locals_seen[0]), {'x', 'a'})
        self.assertIsInstance(locals_seen[0]['a'], evaluator.UninitializedBinding)
        self.assertEqual(context.failure_history[0].call_id, 2)
        self.assertEqual(context.unresolved_pending(1), (1,))
        add.assert_not_called()

    def test_thunk_capture_stops_only_when_forced_even_after_recovery(self):
        env, context = self.stopped(
            'var a: Float = div(1.0,0.0)\nvar t: Thunk[Float, Never] = thunk(a)\n'
            'a = 10.0\nprint(force(t))\nprint(999)')
        self.assertEqual(env['a'].value, 10.0)
        self.assertIsInstance(env['t'].value.env['a'], evaluator.UninitializedBinding)
        self.assertEqual(len(context.call_frames), 1)

    def test_resolve_handler_uninitialized_read_is_fatal(self):
        # A handler cannot use the target's missing value to recover it.
        self.stopped('var a: Float = div(1.0,0.0)\n'
                     'resolve a { DivideByZero { a = div(a,1.0) } }\nprint(999)')
