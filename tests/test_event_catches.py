import unittest
from unittest.mock import Mock, patch

from ginger.builtin import BUILTINS
from ginger.core.failure_spec import FailureId
from ginger.errors import EvalError
from ginger.eval import _eval_program_with_context
from ginger.runtime.catches import handle_try_events
from ginger.runtime.context import RuntimeContext
from ginger.runtime.failures import FailureContractViolation, FailureStatus as Status, RaisedFailure
from test_failure_contract import checked, recorded


class EventCatchTests(unittest.TestCase):
    def run_source(self, source):
        return recorded(checked(source)[0])

    def test_previous_same_failure_is_excluded(self):
        _, context, stdout = self.run_source('print(div(1.0,0.0))\n'
            'try print(div(2.0,0.0))\ncatch DivideByZero print(2)\nprint(3)')
        self.assertEqual(stdout, '2\n3\n')
        self.assertEqual([e.status for e in context.failure_history], [Status.UNRESOLVED, Status.RESOLVED])
        self.assertEqual(context.get_call(1).pending_event_ids, (1, 2))
        self.assertEqual(context.unresolved_pending(1), (1,))

    def test_old_binding_cause_stops_before_handler(self):
        context, env = RuntimeContext(), {}
        printer = Mock()
        with patch.dict(BUILTINS, {'core.int.print': printer}), self.assertRaisesRegex(
                EvalError, "uninitialized binding 'x'"):
            _eval_program_with_context(checked('var x: Float = div(1.0,0.0)\n'
                'try print(div(x,1.0))\ncatch DivideByZero print(9)\nprint(3)')[0], context, env)
        printer.assert_not_called()
        self.assertEqual(len(context.failure_history), 1)
        self.assertEqual(context.failure_history[0].status, Status.UNRESOLVED)

    def test_each_event_in_occurrence_order_with_normal_value(self):
        _, context, stdout = self.run_source(
            'sig f() -> Int { failure DivideByZero failure IntegerOverflow }\n'
            'func f() { print(div(1.0,0.0))\nprint(add(9007199254740991,1))\n'
            'print(div(2.0,0.0))\nreturn 4 }\n'
            'try print(f())\ncatch DivideByZero print(1)\ncatch IntegerOverflow print(2)\n'
            'catch DivideByZero print(9)')
        self.assertEqual(stdout, '4\n1\n2\n1\n')
        self.assertEqual(len(context.failure_history), 3)
        self.assertTrue(all(e.status is Status.RESOLVED for e in context.failure_history))
        self.assertEqual(context.get_call(2).pending_event_ids, (1, 2, 3))
        self.assertEqual(context.unresolved_pending(2), ())

    def test_unmatched_event_remains(self):
        _, context, _ = self.run_source(
            'sig f() -> Unit { failure DivideByZero failure IntegerOverflow }\n'
            'func f() { print(div(1.0,0.0))\nprint(add(9007199254740991,1)) }\n'
            'try f()\ncatch DivideByZero print(1)')
        self.assertEqual([e.status for e in context.failure_history], [Status.RESOLVED, Status.UNRESOLVED])
        self.assertEqual(context.unresolved_pending(1), (2,))

    def test_handler_new_same_failure_no_recatch(self):
        _, context, stdout = self.run_source('try print(div(1.0,0.0))\n'
            'catch DivideByZero print(div(2.0,0.0))\ncatch DivideByZero print(9)\nprint(3)')
        self.assertEqual(stdout, '3\n')
        self.assertEqual([e.status for e in context.failure_history], [Status.RESOLVED, Status.UNRESOLVED])
        self.assertEqual([e.caused_by for e in context.failure_history], [None, 1])
        self.assertEqual(context.incomplete_statements[-1].statement_kind, 'CatchStmt')
        self.assertEqual(context.incomplete_statements[-1].related_event_ids, (2,))

    def test_handler_new_different_failure_not_added_to_targets(self):
        _, context, stdout = self.run_source(
            'sig f() -> Unit { failure DivideByZero failure IntegerOverflow }\n'
            'func f() { print(div(1.0,0.0)) }\n'
            'sig h() -> Unit { failure IntegerOverflow }\n'
            'func h() { print(add(9007199254740991,1)) }\n'
            'try f()\ncatch DivideByZero h()\ncatch IntegerOverflow print(9)\nprint(3)')
        self.assertEqual(stdout, '3\n')
        self.assertEqual([e.failure_id for e in context.failure_history],
                         [FailureId.DivideByZero, FailureId.IntegerOverflow])
        self.assertEqual([e.status for e in context.failure_history], [Status.RESOLVED, Status.UNRESOLVED])
        self.assertEqual(context.failure_history[1].caused_by, 1)

    def test_fatal_handler_keeps_target_unresolved_and_restores_context(self):
        source = ('sig h() -> Unit {}\nfunc h() { print(2) }\n'
                  'try print(div(1.0,0.0))\ncatch DivideByZero h()\nprint(3)')
        for error in (TypeError('bug'), EvalError('bug'), RaisedFailure(FailureId.IOErr)):
            with self.subTest(error=error):
                context = RuntimeContext()
                printer = Mock(side_effect=error)
                with patch.dict(BUILTINS, {'core.int.print': printer}), self.assertRaises(FailureContractViolation if isinstance(error, RaisedFailure) else type(error)):
                    _eval_program_with_context(checked(source)[0], context)
                printer.assert_called_once_with(2)
                self.assertIsNone(context.current_call_id)
                self.assertEqual(context.failure_history[0].status, Status.UNRESOLVED)
                with context.call('later') as frame:
                    event = context.register_failure(FailureId.IOErr, origin='later', call_id=frame.call_id)
                self.assertIsNone(event.caused_by)

    def test_runtime_helper_resolves_before_callee_exit(self):
        context = RuntimeContext()
        with context.call('root') as root:
            with context.call('callee') as child:
                event = context.register_failure(FailureId.IOErr, origin='test', call_id=child.call_id)
                handle_try_events(context, (event.event_id,), [('IOErr', lambda: None)])
                self.assertEqual(context.get_call(child.call_id).pending_event_ids, (event.event_id,))
            self.assertEqual(context.unresolved_pending(root.call_id), ())
            self.assertEqual(context.get_call(root.call_id).pending_event_ids, ())
        self.assertEqual(len(context.failure_history), 1)

    def test_normal_try_does_not_run_handler(self):
        _, context, stdout = self.run_source('try print(div(4.0,2.0))\ncatch DivideByZero print(9)\nprint(3)')
        self.assertEqual(stdout, '2.0\n3\n')
        self.assertEqual(context.failure_history, ())


if __name__ == '__main__':
    unittest.main()
