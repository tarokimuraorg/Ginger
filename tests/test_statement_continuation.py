import unittest
from unittest.mock import Mock, patch

import ginger.eval as evaluator
from ginger.builtin import BUILTINS
from ginger.core.failure_spec import FailureId
from ginger.errors import EvalError, TypecheckError
from ginger.runtime.failures import FailureContractViolation, FailureStatus, RaisedFailure
from test_failure_contract import checked, recorded


class StatementContinuationTests(unittest.TestCase):
    def run_source(self, source):
        return recorded(checked(source)[0])

    def test_basic_continuation_and_incomplete_cause(self):
        _, context, stdout = self.run_source('print(div(1.0,0.0))\nprint(3)')
        self.assertEqual(stdout, '3\n')
        self.assertEqual(len(context.failure_history), 1)
        event = context.failure_history[0]
        self.assertEqual(event.status, FailureStatus.UNRESOLVED)
        record, = context.incomplete_statements
        self.assertEqual((record.scope, record.statement_index, record.statement_kind),
                         ('<program>', 0, 'ExprStmt'))
        self.assertEqual(record.related_event_ids, (event.event_id,))

    def test_nested_builtin_not_called(self):
        outer = Mock(return_value=42.0)
        with patch.dict(BUILTINS, {'core.float.neg': outer}):
            _, context, stdout = self.run_source('print(neg(div(1.0,0.0)))\nprint(3)')
        outer.assert_not_called()
        self.assertEqual(stdout, '3\n')
        self.assertEqual([e.origin for e in context.failure_history], ['core.float.div'])

    def test_arguments_left_to_right_stop_before_third_and_callee(self):
        calls = []
        def mark(value):
            calls.append(value)
            return float(value)
        callee = Mock(return_value=0.0)
        prefix = ('sig mark(Int) -> Float { builtin core.int.toFloat }\n'
                  'sig combine(Float,Float,Float) -> Float { builtin core.float.add }\n')
        with patch.dict(BUILTINS, {'core.int.toFloat': mark, 'core.float.add': callee}):
            _, context, stdout = self.run_source(prefix +
                'print(combine(mark(1),div(1.0,0.0),mark(3)))\nprint(4)')
        self.assertEqual(calls, [1])
        callee.assert_not_called()
        self.assertEqual(stdout, '4\n')
        self.assertEqual(len(context.failure_history), 1)

    def test_thunk_capture_of_uninitialized_binding_does_not_read_value(self):
        env, context, stdout = self.run_source(
            'var x: Float = div(1.0,0.0)\nvar t: Thunk[Float, Never] = thunk(x)\n'
            'x = 4.0\nprint(x)')
        self.assertEqual(stdout, '4.0\n')
        self.assertIsInstance(env['t'].value.env['x'], evaluator.UninitializedBinding)
        self.assertEqual(len(context.failure_history), 1)
        self.assertEqual([r.related_event_ids for r in context.incomplete_statements], [(1,)])

    def test_uninitialized_later_recovery(self):
        env, context, stdout = self.run_source(
            'var x: Float = div(1.0,0.0)\nx = 4.0\nprint(x)')
        self.assertEqual(stdout, '4.0\n')
        self.assertEqual(env['x'].value, 4.0)
        self.assertEqual([r.related_event_ids for r in context.incomplete_statements], [(1,)])
        self.assertEqual(len(context.failure_history), 1)
        env, _, _ = self.run_source('let x: Float = div(1.0,0.0)\nprint(3)')
        self.assertIsInstance(env['x'], evaluator.UninitializedBinding)
        self.assertFalse(hasattr(env['x'], 'value'))

    def test_failed_assignment_preserves_previous_value(self):
        env, context, stdout = self.run_source('var x: Float = 5.0\nx = div(1.0,0.0)\nprint(x)')
        self.assertEqual(env['x'].value, 5.0)
        self.assertEqual(stdout, '5.0\n')
        self.assertEqual([r.statement_kind for r in context.incomplete_statements], ['AssignStmt'])

    def test_repeated_failure_is_not_collapsed(self):
        _, context, stdout = self.run_source('print(div(1.0,0.0))\nprint(div(2.0,0.0))\nprint(3)')
        self.assertEqual(stdout, '3\n')
        self.assertEqual([e.event_id for e in context.failure_history], [1, 2])
        self.assertTrue(all(e.status is FailureStatus.UNRESOLVED for e in context.failure_history))
        self.assertEqual([r.related_event_ids for r in context.incomplete_statements], [(1,), (2,)])

    def test_fatal_errors_do_not_continue_or_enter_resolve(self):
        for failure in (RaisedFailure(FailureId.IOErr), TypeError('bug'),
                        KeyError('bug'), AssertionError('bug'), EvalError('bug')):
            with self.subTest(failure=failure):
                printer = Mock(side_effect=failure)
                with patch.dict(BUILTINS, {'core.int.print': printer}), self.assertRaises(FailureContractViolation if isinstance(failure, RaisedFailure) else type(failure)):
                    self.run_source('print(1)\nprint(3)')
                printer.assert_called_once_with(1)
        # Caller declaration must not legitimize an undeclared builtin failure.
        source = ('sig f() -> Unit { failure IOErr }\nfunc f() { print(1) }\n'
                  'var x: Unit = f()\nresolve x { IOErr { x = print(2) } }\nprint(3)')
        printer = Mock(side_effect=RaisedFailure(FailureId.IOErr))
        with patch.dict(BUILTINS, {'core.int.print': printer}), self.assertRaises(FailureContractViolation):
            self.run_source(source)
        printer.assert_called_once_with(1)

    def test_user_function_body_continues_with_child_frame(self):
        _, context, stdout = self.run_source(
            'sig f() -> Unit { failure DivideByZero }\n'
            'func f() { print(div(1.0,0.0))\nprint(2) }\nf()\nprint(3)')
        self.assertEqual(stdout, '2\n3\n')
        self.assertEqual(len(context.call_frames), 2)
        self.assertEqual([r.scope for r in context.incomplete_statements], ['f'])

    def test_failed_return_does_not_execute_unreachable_body(self):
        env, context, stdout = self.run_source(
            'sig f() -> Float { failure DivideByZero }\n'
            'func f() { return div(1.0,0.0)\nprint(9)\nreturn 4.0 }\n'
            'var x: Float = f()\nprint(3)')
        self.assertEqual(stdout, '3\n')
        self.assertIsInstance(env['x'], evaluator.UninitializedBinding)
        self.assertEqual(context.incomplete_statements[0].statement_kind, 'ReturnStmt')

    def test_valid_return_after_incomplete_statement_keeps_root_history(self):
        env, context, stdout = self.run_source(
            'sig f() -> Int { failure DivideByZero }\n'
            'func f() { print(div(1.0,0.0))\nreturn 4 }\nvar x: Int = f()\nprint(x)')
        self.assertEqual(env['x'].value, 4)
        self.assertEqual(stdout, '4\n')
        self.assertEqual(len(context.failure_history), 1)



if __name__ == '__main__':
    unittest.main()
