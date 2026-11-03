import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import ginger.eval as evaluator
from ginger.errors import EvalError, TypecheckError
from ginger.runtime.context import RuntimeContext
from ginger.runtime.failures import FailureStatus
from test_failure_contract import checked, recorded


class FailedReassignmentTests(unittest.TestCase):
    def test_read_and_read_modify_write_stop_without_new_event(self):
        for read in ('print(x)', 'x = add(x,1.0)',
                     'resolve x { DivideByZero { x = add(x,1.0) } }'):
            with self.subTest(read=read):
                context, env, stream = RuntimeContext(), {}, io.StringIO()
                with redirect_stdout(stream), self.assertRaises(EvalError) as raised:
                    evaluator._eval_program_with_context(checked(
                        'var x: Float = 5.0\nx = div(1.0,0.0)\nprint(3)\n' +
                        read + '\nprint(999)')[0], context, env)
                self.assertEqual(stream.getvalue(), '3\n')
                event, = context.failure_history
                self.assertEqual(str(raised.exception),
                    f"cannot read uninitialized binding 'x' (cause event IDs: {(event.event_id,)})")
                self.assertEqual(env['x'].related_event_ids, (event.event_id,))
                self.assertEqual(env['x'].cause_event_id, event.event_id)
                self.assertEqual(context.next_event_id, event.event_id + 1)
                self.assertEqual(event.status, FailureStatus.UNRESOLVED)
                self.assertEqual(context.incomplete_statements[0].related_event_ids, (event.event_id,))

    def test_initialization_and_reassignment_recover_identically(self):
        for prefix in ('var x: Float = ', 'var x: Float = 5.0\nx = '):
            with self.subTest(prefix=prefix):
                env, context, stdout = recorded(checked(prefix +
                    'div(1.0,0.0)\nprint(3)\nx = 8.0\nprint(x)')[0])
                self.assertEqual(stdout, '3\n8.0\n')
                self.assertEqual(env['x'].value, 8.0)
                event, = context.failure_history
                self.assertEqual(event.status, FailureStatus.UNRESOLVED)
                self.assertEqual(context.unresolved_pending(1), (event.event_id,))

    def test_local_read_stops_or_recovers(self):
        for recover in (False, True):
            with self.subTest(recover=recover):
                context, env, stream, locals_seen = RuntimeContext(), {}, io.StringIO(), []
                original = evaluator.eval_block

                def observe(block, env, syms, outer=None):
                    locals_seen.append(env)
                    return original(block, env, syms, outer)

                program = checked(
                    'sig local() -> Int { failure { IntegerOverflow } }\n'
                    'func local() { var y: Int = 7\ny = add(9007199254740991,1)\n' +
                    ('y = 8\n' if recover else '') + 'return y }\nprint(local())')[0]
                with redirect_stdout(stream), patch('ginger.eval.eval_block', side_effect=observe):
                    if recover:
                        evaluator._eval_program_with_context(program, context, env)
                    else:
                        with self.assertRaisesRegex(EvalError, "uninitialized binding 'y'"):
                            evaluator._eval_program_with_context(program, context, env)
                self.assertEqual(stream.getvalue(), '8\n' if recover else '')
                event, = context.failure_history
                self.assertEqual(event.status, FailureStatus.UNRESOLVED)
                self.assertEqual(event.call_id, 2)
                if recover:
                    self.assertEqual(locals_seen[0]['y'].value, 8)
                else:
                    self.assertEqual(locals_seen[0]['y'].related_event_ids, (event.event_id,))

    def test_successful_assignment_and_let_restriction(self):
        env, context, stdout = recorded(checked(
            'var x: Float = 5.0\nx = add(x,1.0)\nprint(x)')[0])
        self.assertEqual(stdout, '6.0\n')
        self.assertEqual(env['x'].value, 6.0)
        self.assertEqual(context.failure_history, ())
        for initializer in ('5.0', 'div(1.0,0.0)'):
            with self.subTest(initializer=initializer), self.assertRaisesRegex(TypecheckError, 'immutable'):
                checked(f'let x: Float = {initializer}\nx = 8.0')
