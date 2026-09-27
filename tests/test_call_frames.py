import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import ginger.eval as evaluator
from ginger.builtin import BUILTINS
from ginger.core.failure_spec import FailureId, failures
from ginger.errors import EvalError
from ginger.runtime.context import RuntimeContext
from ginger.runtime.failures import FailureStatus, RaisedFailure
from ginger.runtime.results import Value, NoValue
from test_failure_contract import checked


class CallFrameTests(unittest.TestCase):
    def setUp(self):
        self.context = RuntimeContext()

    def run_source(self, source):
        stream = io.StringIO()
        with redirect_stdout(stream):
            env = evaluator._eval_program_with_context(checked(source)[0], self.context)
        return env, stream.getvalue()

    def test_root_and_normal_child(self):
        env, stdout = self.run_source('sig f() -> Int {}\nfunc f() { return 3 }\nvar x: Int = f()\nprint(x)')
        self.assertEqual((env['x'].value, stdout), (3, '3\n'))
        root, child = self.context.call_frames.values()
        self.assertEqual((root.function_name, root.parent_call_id), ('<program>', None))
        self.assertEqual(child.parent_call_id, root.call_id)
        self.assertEqual(child.pending_event_ids, ())
        self.assertIsNone(self.context.current_call_id)

    def test_nested_propagation_keeps_one_event_and_original_call(self):
        env, stdout = self.run_source(
            'sig a() -> Int { failure DivideByZero }\n'
            'sig b() -> Int { failure DivideByZero }\n'
            'func a() { return b() }\nfunc b() { print(div(1.0,0.0))\nreturn 3 }\n'
            'var x: Int = a()\nprint(x)')
        self.assertEqual((env['x'].value, stdout), (3, '3\n'))
        self.assertEqual(len(self.context.failure_history), 1)
        event, = self.context.failure_history
        self.assertEqual((event.call_id, event.origin, event.status),
                         (3, 'core.float.div', FailureStatus.UNRESOLVED))
        self.assertEqual([f.parent_call_id for f in self.context.call_frames.values()], [None, 1, 2])
        for frame in self.context.call_frames.values():
            self.assertEqual(frame.pending_event_ids, (event.event_id,))
        self.assertEqual(self.context.get_call(3).declared_failure_contract, failures(FailureId.DivideByZero))
        self.assertEqual(self.context.incomplete_statements[0].call_id, 3)

    def test_value_and_unit_results_keep_pending_ids(self):
        original = evaluator.eval_user_func
        results = []
        def observe(*args):
            result = original(*args)
            results.append(result)
            return result
        with patch('ginger.eval.eval_user_func', side_effect=observe):
            env, stdout = self.run_source(
                'sig f() -> Int { failure DivideByZero }\n'
                'func f() { print(div(1.0,0.0))\nreturn 3 }\n'
                'sig u() -> Unit { failure DivideByZero }\n'
                'func u() { print(div(1.0,0.0)) }\n'
                'var x: Int = add(f(),1)\nlet unit: Unit = u()\nprint(x)')
        self.assertEqual(stdout, '4\n')
        self.assertIsNone(env['unit'].value)
        self.assertEqual([r.value_result for r in results], [Value(3), Value(None)])
        self.assertEqual([r.related_event_ids for r in results], [(1,), (2,)])
        self.assertEqual(self.context.get_call(1).pending_event_ids, (1, 2))

    def test_failed_return_no_value_and_caller_incomplete(self):
        env, stdout = self.run_source(
            'sig f() -> Float { failure DivideByZero }\n'
            'func f() { return div(1.0,0.0)\nprint(9) }\nvar x: Float = f()\nprint(3)')
        self.assertEqual(stdout, '3\n')
        self.assertIsInstance(env['x'], evaluator.UninitializedBinding)
        self.assertEqual([r.call_id for r in self.context.incomplete_statements], [2, 1])
        self.assertEqual(self.context.get_call(1).pending_event_ids, (1,))
        self.assertEqual(self.context.get_call(2).pending_event_ids, (1,))

    def test_repeated_calls_and_top_level_origin(self):
        self.run_source('sig f() -> Unit { failure DivideByZero }\n'
                        'func f() { print(div(1.0,0.0)) }\nf()\nf()\nprint(div(1.0,0.0))')
        self.assertEqual([e.call_id for e in self.context.failure_history], [2, 3, 1])
        self.assertEqual([e.event_id for e in self.context.failure_history], [1, 2, 3])
        self.assertEqual(self.context.get_call(1).pending_event_ids, (1, 2, 3))

    def test_resolved_event_not_propagated_and_frames_retained(self):
        with self.context.call('<program>') as root:
            with self.context.call('f', failures(FailureId.IOErr)) as child:
                event = self.context.register_failure(FailureId.IOErr, origin='test', call_id=child.call_id)
                self.context.resolve(event.event_id)
            self.assertEqual(self.context.current_call_id, root.call_id)
            self.assertEqual(self.context.get_call(root.call_id).pending_event_ids, ())
        self.assertEqual(len(self.context.call_frames), 2)
        self.assertEqual(len(self.context.failure_history), 1)
        self.assertIsNone(self.context.current_call_id)

    def test_fatal_cleanup_preserves_original_error_and_prior_pending(self):
        for error in (TypeError('bug'), EvalError('bug'), RaisedFailure(FailureId.IOErr)):
            with self.subTest(error=error):
                self.context = RuntimeContext()
                seen = []
                def fail(value):
                    seen.append(self.context.current_call_id)
                    raise error
                with patch.dict(BUILTINS, {'core.int.print': fail}):
                    with self.assertRaises(type(error)) as raised:
                        self.run_source('sig f() -> Unit { failure DivideByZero }\n'
                                        'func f() { print(div(1.0,0.0))\nprint(1) }\nf()\nprint(3)')
                self.assertIs(raised.exception, error)
                self.assertEqual(seen, [2])
                self.assertIsNone(self.context.current_call_id)
                self.assertEqual(self.context.get_call(1).pending_event_ids, (1,))
                self.assertEqual(len(self.context.failure_history), 1)

    def test_scope_restores_parent_on_fatal_exit(self):
        with self.context.call('parent') as parent:
            with self.assertRaises(EvalError):
                with self.context.call('child'):
                    raise EvalError('fatal')
            self.assertEqual(self.context.current_call_id, parent.call_id)

    def test_try_keeps_first_handler_and_resolves(self):
        _, stdout = self.run_source('sig f() -> Unit { failure DivideByZero }\n'
                                    'func f() { print(div(1.0,0.0)) }\n'
                                    'try f()\ncatch DivideByZero print(2)\nprint(3)')
        self.assertEqual(stdout, '2\n3\n')
        self.assertEqual(self.context.failure_history[0].call_id, 2)
        self.assertEqual(self.context.failure_history[0].status, FailureStatus.RESOLVED)
        self.assertEqual(self.context.get_call(1).pending_event_ids, (1,))

    def test_recursion_has_distinct_frames_and_unwinds_safely(self):
        # Ginger has recursion but no conditional base case. A bounded host
        # builtin raises after three real recursive invocations, not RecursionError.
        seen = []
        def tick(value):
            seen.append(self.context.current_call_id)
            if len(seen) == 3:
                raise EvalError('bounded recursion stop')
        with patch.dict(BUILTINS, {'core.int.print': tick}), self.assertRaises(EvalError):
            self.run_source('sig f() -> Unit {}\nfunc f() { print(1)\nf() }\nf()')
        self.assertEqual(seen, [2, 3, 4])
        self.assertEqual([f.parent_call_id for f in self.context.call_frames.values()], [None, 1, 2, 3])
        self.assertIsNone(self.context.current_call_id)


if __name__ == '__main__':
    unittest.main()
