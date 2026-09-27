import unittest
from dataclasses import FrozenInstanceError

from ginger.core.failure_spec import FailureId
from ginger.runtime.context import CallFrame, RuntimeContext, IncompleteStatement
from ginger.runtime.failures import FailureEvent, FailureStatus
from ginger.runtime.results import EvalResult, ExecutionResult, NoValue, Value


class RuntimeFoundationTests(unittest.TestCase):
    def setUp(self):
        self.context = RuntimeContext()
        self.frame = self.context.create_call(
            'calculate', declared_failure_contract=frozenset({FailureId.DivideByZero}))

    def failure(self, fid=FailureId.DivideByZero):
        return self.context.register_failure(fid, origin='calculate:div', call_id=self.frame.call_id)

    def test_distinct_occurrences_and_registration_order(self):
        first, second = self.failure(), self.failure()
        third = self.failure(FailureId.IntegerOverflow)
        self.assertEqual([e.event_id for e in self.context.failure_history], [1, 2, 3])
        self.assertEqual(self.context.failure_history, (first, second, third))
        self.assertEqual(first.failure_id, second.failure_id)
        self.assertNotEqual(first.event_id, second.event_id)
        self.assertEqual(self.context.next_event_id, 4)
        self.assertEqual(self.context.get_call(self.frame.call_id).pending_event_ids, (1, 2, 3))
        # Registration does not enforce contracts during Phase 2.
        self.assertNotIn(third.failure_id, self.frame.declared_failure_contract)

    def test_resolution_preserves_identity_origin_and_history_order(self):
        first, second = self.failure(), self.failure()
        resolved = self.context.resolve(first.event_id)
        self.assertEqual(resolved.status, FailureStatus.RESOLVED)
        self.assertEqual((resolved.event_id, resolved.origin, resolved.call_id),
                         (first.event_id, first.origin, first.call_id))
        self.assertEqual(self.context.failure_history, (resolved, second))
        self.assertEqual(self.context.unresolved_pending(self.frame.call_id), (second.event_id,))
        self.assertIs(self.context.resolve(first.event_id), resolved)
        self.assertEqual(self.context.next_event_id, 3)
        # Previously acquired immutable snapshots stay unchanged.
        self.assertEqual(first.status, FailureStatus.UNRESOLVED)
        with self.assertRaises(FrozenInstanceError):
            resolved.event_id = 99
        with self.assertRaises(ValueError):
            FailureEvent(1, FailureId.IOErr, 'origin', 1, status='resovled')

    def test_parent_child_calls_and_shared_event_reference(self):
        child = self.context.create_call('child', parent_call_id=self.frame.call_id)
        self.assertNotEqual(child.call_id, self.frame.call_id)
        self.assertEqual(child.parent_call_id, self.frame.call_id)
        event = self.context.register_failure(FailureId.IOErr, origin='child', call_id=child.call_id)
        self.assertEqual(self.context.get_call(self.frame.call_id).pending_event_ids, ())
        self.context.add_pending(self.frame.call_id, event.event_id)
        self.context.add_pending(self.frame.call_id, event.event_id)
        self.assertEqual(len(self.context.failure_history), 1)
        self.assertEqual(self.context.get_call(self.frame.call_id).pending_event_ids, (event.event_id,))
        self.assertEqual(self.context.get_call(child.call_id).pending_event_ids, (event.event_id,))
        self.context.resolve(event.event_id)
        self.assertTrue(all(not self.context.unresolved_pending(frame.call_id) for frame in self.context.call_frames.values()))
        with self.assertRaises(ValueError):
            self.context.add_pending(child.call_id, event.event_id)

    def test_invalid_references_do_not_allocate_ids(self):
        with self.assertRaises(KeyError):
            self.context.create_call('missing', parent_call_id=99)
        with self.assertRaises(KeyError):
            self.context.register_failure(FailureId.IOErr, origin='missing', call_id=99)
        with self.assertRaises(KeyError):
            self.context.add_pending(self.frame.call_id, 99)
        with self.assertRaises(KeyError):
            self.context.resolve(99)
        self.assertEqual(self.context.next_event_id, 1)
        self.assertEqual(self.context.next_call_id, 2)
        self.assertEqual(self.context.failure_history, ())

    def test_contexts_are_isolated_and_frames_copy_contracts(self):
        other = RuntimeContext()
        self.failure()
        self.assertEqual(other.failure_history, ())
        self.assertEqual(other.next_event_id, 1)
        contract = {FailureId.IOErr}
        frame = CallFrame(8, None, 'standalone', contract)
        contract.clear()
        self.assertEqual(frame.declared_failure_contract, frozenset({FailureId.IOErr}))
        with self.assertRaises(TypeError):
            self.context.call_frames[99] = frame

    def test_unit_and_no_value_are_distinct(self):
        unit, missing = Value(None), NoValue()
        self.assertIsNone(unit.value)
        self.assertNotEqual(unit, missing)
        self.assertIsInstance(unit, Value)
        self.assertIsInstance(missing, NoValue)

    def test_eval_result_keeps_values_and_event_references(self):
        event = self.failure()
        for value in (Value(None), Value(3), NoValue()):
            with self.subTest(value=value):
                ids = [event.event_id]
                result = EvalResult(value, ids)
                ids.clear()
                self.assertEqual(result.value_result, value)
                self.assertEqual(result.related_event_ids, (event.event_id,))
        with self.assertRaises(TypeError):
            EvalResult(None)

    def test_execution_result_retains_resolved_and_unresolved_history(self):
        first, second = self.failure(), self.failure()
        self.context.resolve(first.event_id)
        environment = {'x': 3}
        record = IncompleteStatement(self.frame.call_id, 'calculate', 0, 'ExprStmt', (second.event_id,))
        result = ExecutionResult(environment, self.context.failure_history, [record])
        environment['x'] = 4
        self.assertEqual(result.environment['x'], 3)
        self.assertEqual(len(result.failure_history), 2)
        self.assertEqual(result.unresolved_events, (second,))
        self.assertEqual(result.incomplete_statements, (record,))
        self.context.resolve(second.event_id)
        self.assertEqual(result.unresolved_events, (second,))  # Snapshot, not live context.
        self.assertEqual(ExecutionResult({}, self.context.failure_history).unresolved_events, ())


if __name__ == '__main__':
    unittest.main()
