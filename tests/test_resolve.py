import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import Mock, patch

import ginger.eval as evaluator
from ginger.builtin import BUILTINS
from ginger.core.failure_spec import EMPTY_FAILURES, FailureId
from ginger.errors import EvalError, TypecheckError
from ginger.lower import lower_program
from ginger.parser import parse
from ginger.runtime.context import RuntimeContext
from ginger.runtime.failures import FailureContractViolation, FailureStatus, RaisedFailure
from ginger.runtime.thunk import ThunkValue
from test_failure_contract import checked, recorded


OVERFLOW = 'var x: Int = add(9007199254740991,1)\n'
DIVISION = 'var x: Float = div(1.0,0.0)\n'
TWO_FAILURES = ('sig risky() -> Float { failure IntegerOverflow failure DivideByZero }\n'
                'func risky() { return div(1.0,0.0) }\n'
                'var x: Float = risky()\n')


class ResolveTests(unittest.TestCase):
    def run_source(self, source):
        return recorded(checked(source)[0])

    def public_result(self, source):
        stream = io.StringIO()
        with redirect_stdout(stream):
            result = evaluator.eval_program(checked(source)[0])
        return result, stream.getvalue()

    def warning_messages(self, source):
        return [d.message for d in checked(source)[1] if d.code == 'UNHANDLED_FAILURES']

    def test_parser_uses_target_and_existing_statement_blocks(self):
        from ginger.ast import AssignStmt, BlockStmt, ResolveHandler, ResolveStmt
        program = lower_program(parse(
            'var x: Int = add(1,2)\n'
            'resolve x { IntegerOverflow { x = 0\nprint(x) } }'))
        statement = program.items[1]
        self.assertIsInstance(statement, ResolveStmt)
        self.assertEqual(statement.target, 'x')
        self.assertEqual(len(statement.handlers), 1)
        handler = statement.handlers[0]
        self.assertIsInstance(handler, ResolveHandler)
        self.assertEqual(handler.failure_name, 'IntegerOverflow')
        self.assertIsInstance(handler.body, BlockStmt)
        self.assertIsInstance(handler.body.stmts[0], AssignStmt)

    def test_initialized_binding_is_no_op(self):
        env, context, stdout = self.run_source(
            'var x: Int = add(1,2)\n'
            'resolve x { IntegerOverflow { print(999)\nx = 0 } }\nprint(x)')
        self.assertEqual(stdout, '3\n')
        self.assertEqual(env['x'].value, 3)
        self.assertEqual(context.failure_history, ())

    def test_initialized_binding_does_not_process_other_events(self):
        env, context, stdout = self.run_source(
            'var y: Int = add(9007199254740991,1)\n'
            'var x: Int = add(1,2)\n'
            'resolve x { IntegerOverflow { print(999)\nx = 0 } }\nprint(x)')
        self.assertEqual(stdout, '3\n')
        self.assertIsInstance(env['y'], evaluator.UninitializedBinding)
        event, = context.failure_history
        self.assertEqual(event.status, FailureStatus.UNRESOLVED)
        self.assertEqual(env['y'].related_event_ids, (event.event_id,))

    def test_successful_function_result_with_pending_failure_is_no_op(self):
        env, context, stdout = self.run_source(
            'sig f() -> Int { failure IntegerOverflow }\n'
            'func f() { print(add(9007199254740991,1))\nreturn 3 }\n'
            'var x: Int = f()\n'
            'resolve x { IntegerOverflow { x = 0\nprint(999) } }\nprint(x)')
        self.assertEqual(stdout, '3\n')
        self.assertEqual(env['x'].value, 3)
        self.assertEqual(context.failure_history[0].status, FailureStatus.UNRESOLVED)

    def test_initialized_unit_value_with_pending_failure_is_no_op(self):
        env, context, stdout = self.run_source(
            'sig f() -> Unit { failure DivideByZero }\n'
            'func f() { print(div(1.0,0.0)) }\nvar x: Unit = f()\n'
            'resolve x { DivideByZero { x = print(999) } }\nprint(3)')
        self.assertEqual(stdout, '3\n')
        self.assertIsInstance(env['x'], evaluator.Cell)
        self.assertIsNone(env['x'].value)
        self.assertEqual(context.failure_history[0].status, FailureStatus.UNRESOLVED)

    def test_unit_recovery_counts_as_valid_binding(self):
        result, stdout = self.public_result('var x: Unit = print(div(1.0,0.0))\n'
            'resolve x { DivideByZero { x = print(2) } }')
        self.assertEqual(stdout, '2\n')
        self.assertIsInstance(result.environment['x'], evaluator.Cell)
        self.assertIsNone(result.environment['x'].value)
        self.assertEqual(result.failure_history[0].status, FailureStatus.RESOLVED)
        self.assertEqual(result.unresolved_events, ())

    def test_matching_failure_recovers_binding_and_preserves_public_history(self):
        result, stdout = self.public_result(OVERFLOW +
            'resolve x { IntegerOverflow { x = 0 } }\nprint(x)')
        self.assertEqual(stdout, '0\n')
        self.assertEqual(result.environment['x'].value, 0)
        self.assertEqual(len(result.failure_history), 1)
        event, = result.failure_history
        self.assertEqual(event.failure_id, FailureId.IntegerOverflow)
        self.assertEqual(event.status, FailureStatus.RESOLVED)
        self.assertEqual(result.unresolved_events, ())
        self.assertEqual(result.call_frames[1].pending_event_ids, (event.event_id,))
        self.assertEqual(result.incomplete_statements[0].related_event_ids, (event.event_id,))

    def test_handler_completion_without_target_recovery_keeps_event_unresolved(self):
        result, stdout = self.public_result(OVERFLOW +
            'resolve x { IntegerOverflow { print(1) } }\nprint(3)')
        self.assertEqual(stdout, '1\n3\n')
        self.assertIsInstance(result.environment['x'], evaluator.UninitializedBinding)
        self.assertEqual(result.failure_history[0].status, FailureStatus.UNRESOLVED)
        self.assertEqual(result.unresolved_events, result.failure_history)
        self.assertEqual(result.environment['x'].related_event_ids, (1,))

    def test_other_binding_with_same_failure_is_not_resolved(self):
        result, stdout = self.public_result(OVERFLOW +
            'var y: Int = add(9007199254740991,2)\n'
            'resolve x { IntegerOverflow { x = 0 } }\nprint(x)')
        self.assertEqual(stdout, '0\n')
        self.assertEqual(result.environment['x'].value, 0)
        self.assertIsInstance(result.environment['y'], evaluator.UninitializedBinding)
        original, other = result.failure_history
        self.assertEqual(original.status, FailureStatus.RESOLVED)
        self.assertEqual(other.status, FailureStatus.UNRESOLVED)
        self.assertEqual(result.environment['y'].related_event_ids, (other.event_id,))
        self.assertEqual(result.unresolved_events, (other,))

    def test_previous_same_failure_is_not_resolved(self):
        _, context, stdout = self.run_source(
            'print(add(9007199254740991,1))\n' + OVERFLOW +
            'resolve x { IntegerOverflow { x = 0 } }\nprint(x)')
        self.assertEqual(stdout, '0\n')
        self.assertEqual([e.status for e in context.failure_history],
                         [FailureStatus.UNRESOLVED, FailureStatus.RESOLVED])
        self.assertEqual(context.unresolved_pending(1), (1,))

    def test_failed_return_cause_excludes_earlier_failed_statement(self):
        prefix = ('sig f() -> Float { failure IntegerOverflow failure DivideByZero }\n'
                  'func f() { print(add(9007199254740991,1))\nreturn div(1.0,0.0) }\n'
                  'var x: Float = f()\n')
        env, _, _ = self.run_source(prefix)
        self.assertEqual(env['x'].related_event_ids, (2, 1))
        result, stdout = self.public_result(prefix +
            'resolve x { IntegerOverflow { print(999)\nx = 9 }\n'
            'DivideByZero { x = 0 } }\nprint(x)')
        self.assertEqual(stdout, '0.0\n')
        self.assertEqual([e.status for e in result.failure_history],
                         [FailureStatus.UNRESOLVED, FailureStatus.RESOLVED])
        self.assertEqual(result.unresolved_events, (result.failure_history[0],))

    def test_failed_argument_cause_excludes_successful_argument_pending_events(self):
        prefix = ('sig f() -> Int { failure DivideByZero }\n'
                  'func f() { print(div(1.0,0.0))\nreturn 3 }\n'
                  'var x: Int = add(f(),add(9007199254740991,1))\n')
        env, _, _ = self.run_source(prefix)
        self.assertEqual(env['x'].related_event_ids, (1, 2))
        result, stdout = self.public_result(prefix +
            'resolve x { DivideByZero { print(999)\nx = 9 }\n'
            'IntegerOverflow { x = 0 } }\nprint(x)')
        self.assertEqual(stdout, '0\n')
        self.assertEqual([e.status for e in result.failure_history],
                         [FailureStatus.UNRESOLVED, FailureStatus.RESOLVED])
        self.assertEqual(result.unresolved_events, (result.failure_history[0],))

    def test_unmatched_actual_failure_does_nothing(self):
        env, context, stdout = self.run_source(TWO_FAILURES +
            'resolve x { IntegerOverflow { x = 0\nprint(999) } }\nprint(3)')
        self.assertEqual(stdout, '3\n')
        self.assertIsInstance(env['x'], evaluator.UninitializedBinding)
        event, = context.failure_history
        self.assertEqual(event.failure_id, FailureId.DivideByZero)
        self.assertEqual(event.status, FailureStatus.UNRESOLVED)

    def test_only_matching_handler_runs(self):
        result, stdout = self.public_result(TWO_FAILURES +
            'resolve x {\nIntegerOverflow { print(999)\nx = 0 }\n'
            'DivideByZero { print(2)\nx = 1 }\n}\nprint(x)')
        self.assertEqual(stdout, '2\n1.0\n')
        self.assertEqual(result.environment['x'].value, 1.0)
        self.assertEqual(result.failure_history[0].status, FailureStatus.RESOLVED)

    def test_handler_can_recover_with_existing_var_widening(self):
        env, context, stdout = self.run_source(DIVISION +
            'resolve x { DivideByZero { x = 4 } }\nprint(x)')
        self.assertEqual(stdout, '4.0\n')
        self.assertEqual(env['x'].value, 4.0)
        self.assertEqual(context.failure_history[0].status, FailureStatus.RESOLVED)

    def test_second_resolve_after_recovery_is_no_op(self):
        env, context, stdout = self.run_source(OVERFLOW +
            'resolve x { IntegerOverflow { x = 4 } }\n'
            'resolve x { IntegerOverflow { x = 9\nprint(999) } }\nprint(x)')
        self.assertEqual(stdout, '4\n')
        self.assertEqual(env['x'].value, 4)
        self.assertEqual(len(context.failure_history), 1)
        self.assertEqual(context.failure_history[0].status, FailureStatus.RESOLVED)

    def test_later_assignment_before_resolve_does_not_resolve_history(self):
        env, context, stdout = self.run_source(OVERFLOW +
            'x = 4\nresolve x { IntegerOverflow { x = 9\nprint(999) } }\nprint(x)')
        self.assertEqual(stdout, '4\n')
        self.assertEqual(env['x'].value, 4)
        self.assertEqual(context.failure_history[0].status, FailureStatus.UNRESOLVED)

    def test_duplicate_handler_failure_is_rejected(self):
        with self.assertRaises(TypecheckError):
            checked(OVERFLOW + 'resolve x { IntegerOverflow { x = 0 }\n'
                    'IntegerOverflow { x = 1 } }')

    def test_unknown_never_and_failureset_handlers_are_rejected(self):
        prefix = 'failureset CalculationFailure { IntegerOverflow DivideByZero }\n'
        for failure in ('UnknownFailure', 'Never', 'CalculationFailure'):
            with self.subTest(failure=failure), self.assertRaises(TypecheckError):
                checked(prefix + OVERFLOW + f'resolve x {{ {failure} {{ x = 0 }} }}')

    def test_invalid_targets_are_rejected(self):
        sources = [
            'resolve x { IntegerOverflow { x = 0 } }',
            'let x: Int = add(9007199254740991,1)\n'
            'resolve x { IntegerOverflow { print(0) } }',
            'var x: Int = 1\nresolve x { IntegerOverflow { x = 0 } }',
            'resolve x { IntegerOverflow { x = 0 } }\n' + OVERFLOW,
        ]
        for source in sources:
            with self.subTest(source=source), self.assertRaises(TypecheckError):
                checked(source)

    def test_ineligible_known_failure_is_rejected(self):
        with self.assertRaises(TypecheckError):
            checked(OVERFLOW + 'resolve x { DivideByZero { x = 0 } }')

    def test_handler_failure_does_not_add_target_eligibility(self):
        with self.assertRaises(TypecheckError):
            checked(OVERFLOW + 'resolve x { IntegerOverflow { print(div(1.0,0.0)) }\n'
                    'DivideByZero { x = 0 } }')

    def test_function_resolve_is_rejected(self):
        with self.assertRaises((SyntaxError, TypecheckError)):
            checked('sig f() -> Unit { failure IntegerOverflow }\nfunc f() {\n' +
                    OVERFLOW + 'resolve x { IntegerOverflow { x = 0 } }\n}')

    def test_handler_assignment_type_and_mutability_rules_still_apply(self):
        for body in ('x = 1.0', 'let y: Int = 1\ny = 2'):
            with self.subTest(body=body), self.assertRaises(TypecheckError):
                checked(OVERFLOW + f'resolve x {{ IntegerOverflow {{ {body} }} }}')

    def test_matching_resolve_removes_initializer_warning(self):
        self.assertEqual(self.warning_messages(OVERFLOW +
            'resolve x { IntegerOverflow { x = 0 } }'), [])

    def test_handler_presence_removes_warning_even_without_runtime_recovery(self):
        source = OVERFLOW + 'resolve x { IntegerOverflow { print(1) } }'
        self.assertEqual(self.warning_messages(source), [])
        result, _ = self.public_result(source)
        self.assertEqual(result.unresolved_events, result.failure_history)

    def test_partial_resolve_leaves_only_other_initializer_warning(self):
        self.assertEqual(self.warning_messages(TWO_FAILURES +
            'resolve x { IntegerOverflow { x = 0 } }'),
            ['unhandled failures: DivideByZero'])

    def test_multiple_resolve_statements_combine_initializer_coverage(self):
        self.assertEqual(self.warning_messages(TWO_FAILURES +
            'resolve x { IntegerOverflow { x = 0 } }\n'
            'resolve x { DivideByZero { x = 1 } }'), [])

    def test_resolve_warning_removal_is_limited_to_target_binding(self):
        self.assertEqual(self.warning_messages(OVERFLOW +
            'var y: Int = add(9007199254740991,2)\n'
            'print(add(9007199254740991,3))\n'
            'resolve x { IntegerOverflow { x = 0 } }'),
            ['unhandled failures: IntegerOverflow'] * 2)

    def test_resolve_warning_removal_does_not_cover_target_assignment_effects(self):
        self.assertEqual(self.warning_messages(OVERFLOW +
            'x = add(9007199254740991,2)\n'
            'resolve x { IntegerOverflow { x = 0 } }'),
            ['unhandled failures: IntegerOverflow'])

    def test_same_handler_failure_remains_unhandled(self):
        self.assertEqual(self.warning_messages(OVERFLOW +
            'resolve x { IntegerOverflow { x = add(9007199254740991,2) } }'),
            ['unhandled failures: IntegerOverflow'])

    def test_all_handler_effects_remain_in_static_failures(self):
        self.assertEqual(self.warning_messages(TWO_FAILURES +
            'resolve x { IntegerOverflow { print(div(2.0,0.0))\nx = 0 }\n'
            'DivideByZero { print(add(9007199254740991,1))\nx = 1 } }'),
            ['unhandled failures: DivideByZero', 'unhandled failures: IntegerOverflow'])

    def test_handler_new_failure_keeps_original_unresolved_and_records_cause(self):
        source = ('sig ioFail() -> Int { failure IOErr builtin test.ioFail }\n' +
                  OVERFLOW + 'resolve x { IntegerOverflow { x = ioFail() } }\nprint(3)')
        failed_builtin = Mock(side_effect=RaisedFailure(FailureId.IOErr))
        with patch.dict(BUILTINS, {'test.ioFail': failed_builtin}):
            result, stdout = self.public_result(source)
            messages = self.warning_messages(source)
        failed_builtin.assert_called_once_with()
        self.assertEqual(stdout, '3\n')
        self.assertIsInstance(result.environment['x'], evaluator.UninitializedBinding)
        original, new = result.failure_history
        self.assertEqual([e.failure_id for e in result.failure_history],
                         [FailureId.IntegerOverflow, FailureId.IOErr])
        self.assertEqual([e.status for e in result.failure_history], [FailureStatus.UNRESOLVED] * 2)
        self.assertEqual(new.caused_by, original.event_id)
        self.assertEqual(result.unresolved_events, result.failure_history)
        self.assertEqual(result.environment['x'].related_event_ids, (original.event_id,))
        self.assertEqual(result.incomplete_statements[-1].statement_kind, 'AssignStmt')
        self.assertEqual(result.incomplete_statements[-1].related_event_ids, (new.event_id,))
        self.assertEqual(messages, ['unhandled failures: IOErr'])

    def test_handler_same_failure_does_not_rerun_resolve(self):
        result, stdout = self.public_result(OVERFLOW +
            'resolve x { IntegerOverflow { x = add(9007199254740991,2)\nprint(1) } }\nprint(3)')
        self.assertEqual(stdout, '1\n3\n')
        self.assertIsInstance(result.environment['x'], evaluator.UninitializedBinding)
        self.assertEqual(len(result.failure_history), 2)
        self.assertEqual([e.status for e in result.failure_history], [FailureStatus.UNRESOLVED] * 2)
        self.assertEqual(result.failure_history[1].caused_by, result.failure_history[0].event_id)

    def test_handler_failure_then_recovery_resolves_only_original_event(self):
        result, stdout = self.public_result(OVERFLOW +
            'resolve x { IntegerOverflow { print(div(1.0,0.0))\nx = 4 } }\nprint(x)')
        self.assertEqual(stdout, '4\n')
        self.assertEqual(result.environment['x'].value, 4)
        original, new = result.failure_history
        self.assertEqual(original.status, FailureStatus.RESOLVED)
        self.assertEqual(new.status, FailureStatus.UNRESOLVED)
        self.assertEqual(new.caused_by, original.event_id)
        self.assertEqual(result.unresolved_events, (new,))

    def test_new_failure_after_recovery_does_not_undo_valid_binding(self):
        result, stdout = self.public_result(OVERFLOW +
            'resolve x { IntegerOverflow { x = 4\nx = add(9007199254740991,2) } }\nprint(x)')
        self.assertEqual(stdout, '4\n')
        self.assertEqual(result.environment['x'].value, 4)
        self.assertEqual([e.status for e in result.failure_history],
                         [FailureStatus.RESOLVED, FailureStatus.UNRESOLVED])

    def test_handler_context_is_restored_before_following_failure(self):
        _, context, _ = self.run_source(OVERFLOW +
            'resolve x { IntegerOverflow { x = 0 } }\nprint(div(1.0,0.0))')
        self.assertIsNone(context.failure_history[1].caused_by)

    def test_force_failure_can_be_resolved_from_initializer_binding(self):
        result, stdout = self.public_result(
            'var t: Thunk[Float, DivideByZero] = thunk(div(1.0,0.0))\n'
            'var x: Float = force(t)\nresolve x { DivideByZero { x = 0 } }\nprint(x)')
        self.assertEqual(stdout, '0.0\n')
        self.assertEqual(result.environment['x'].value, 0.0)
        self.assertIsInstance(result.environment['t'].value, ThunkValue)
        self.assertEqual(result.environment['t'].value.potential_failure_contract,
                         frozenset({FailureId.DivideByZero}))
        self.assertEqual(result.failure_history[0].status, FailureStatus.RESOLVED)
        self.assertEqual(result.unresolved_events, ())

    def test_repeated_force_produces_distinct_binding_causes(self):
        result, _ = self.public_result(
            'var t: Thunk[Float, DivideByZero] = thunk(div(1.0,0.0))\n'
            'var x: Float = force(t)\nvar y: Float = force(t)\n'
            'resolve x { DivideByZero { x = 0 } }')
        self.assertEqual([e.event_id for e in result.failure_history], [1, 2])
        self.assertEqual([e.status for e in result.failure_history],
                         [FailureStatus.RESOLVED, FailureStatus.UNRESOLVED])
        self.assertEqual(result.environment['y'].related_event_ids, (2,))

    def test_fatal_initializer_builtin_violation_does_not_enter_resolve(self):
        source = ('sig raw() -> Int { failure IntegerOverflow builtin test.raw }\n'
                  'var x: Int = raw()\nresolve x { IntegerOverflow { x = 0\nprint(2) } }\nprint(3)')
        printer = Mock()
        with patch.dict(BUILTINS, {'test.raw': Mock(side_effect=RaisedFailure(FailureId.IOErr)),
                                  'core.int.print': printer}):
            result, stdout = self.public_result(source)
        printer.assert_not_called()
        self.assertEqual(stdout, '')
        self.assertNotIn('x', result.environment)
        self.assertEqual(result.failure_history, ())
        self.assertEqual(result.contract_violation.failure_id, FailureId.IOErr)
        self.assertEqual(result.contract_violation.boundary_kind, 'builtin')

    def test_fatal_function_boundary_violation_does_not_enter_resolve(self):
        original = evaluator.eval_user_func
        def stale(name, args, syms, env, resolved):
            syms.sig_failures[name] = EMPTY_FAILURES
            return original(name, args, syms, env, resolved)
        with patch('ginger.eval.eval_user_func', side_effect=stale):
            result, stdout = self.public_result(
                'sig f() -> Int { failure IntegerOverflow }\n'
                'func f() { return add(9007199254740991,1) }\nvar x: Int = f()\n'
                'resolve x { IntegerOverflow { x = 0\nprint(2) } }\nprint(3)')
        self.assertEqual(stdout, '')
        self.assertNotIn('x', result.environment)
        self.assertEqual(result.contract_violation.boundary_kind, 'function')
        self.assertEqual(result.unresolved_events, result.failure_history)

    def test_fatal_handler_errors_keep_original_unresolved_and_restore_context(self):
        source = OVERFLOW + 'resolve x { IntegerOverflow { print(2)\nx = 0 } }\nprint(3)'
        for error in (RaisedFailure(FailureId.IOErr), EvalError('bug'), TypeError('bug'),
                      KeyError('bug'), AssertionError('bug')):
            with self.subTest(error=error):
                context, env = RuntimeContext(), {}
                printer = Mock(side_effect=error)
                expected = FailureContractViolation if isinstance(error, RaisedFailure) else type(error)
                with patch.dict(BUILTINS, {'core.int.print': printer}), self.assertRaises(expected):
                    evaluator._eval_program_with_context(checked(source)[0], context, env)
                printer.assert_called_once_with(2)
                self.assertIsInstance(env['x'], evaluator.UninitializedBinding)
                self.assertEqual(len(context.failure_history), 1)
                self.assertEqual(context.failure_history[0].status, FailureStatus.UNRESOLVED)
                self.assertIsNone(context.current_call_id)
                with context.call('later') as frame:
                    new = context.register_failure(FailureId.IOErr, origin='later', call_id=frame.call_id)
                self.assertIsNone(new.caused_by)

    def test_python_initializer_errors_are_not_failure_events(self):
        for error in (EvalError('bug'), ZeroDivisionError('bug'), TypeError('bug')):
            with self.subTest(error=error):
                context, env = RuntimeContext(), {}
                printer = Mock()
                broken = Mock(side_effect=error)
                source = ('sig broken() -> Int { failure IntegerOverflow builtin test.broken }\n'
                          'var x: Int = broken()\n'
                          'resolve x { IntegerOverflow { x = 0\nprint(2) } }')
                with patch.dict(BUILTINS, {'test.broken': broken, 'core.int.print': printer}), \
                        self.assertRaises(type(error)) as caught:
                    evaluator._eval_program_with_context(checked(source)[0], context, env)
                self.assertIs(caught.exception, error)
                printer.assert_not_called()
                self.assertEqual(context.failure_history, ())
                self.assertNotIn('x', env)


class RemovedSyntaxTests(unittest.TestCase):
    def test_try_and_catch_sources_are_rejected_with_migration_hint(self):
        sources = [
            'try print(div(1.0,0.0))\ncatch DivideByZero print(0)',
            'catch IntegerOverflow print(0)',
            'sig f() -> Unit {}\nfunc f() { try print(1) }',
            'sig f() -> Unit {}\nfunc f() { catch IntegerOverflow print(0) }',
            OVERFLOW + 'resolve x { IntegerOverflow { try print(1) } }',
            OVERFLOW + 'resolve x { IntegerOverflow { catch IntegerOverflow print(1) } }',
        ]
        for source in sources:
            with self.subTest(source=source), self.assertRaisesRegex(
                    SyntaxError, 'try/catch has been removed; use resolve <binding>'):
                checked(source)

    def test_removed_keywords_cannot_be_reused_as_identifiers(self):
        for keyword in ('try', 'catch'):
            sources = [
                f'var {keyword}: Int = 1',
                f'let {keyword}: Int = 1',
                f'sig {keyword}() -> Unit {{}}',
                f'func {keyword}() {{}}',
                f'sig f(Int) -> Int {{}}\nfunc f({keyword}: Int) {{ return 1 }}',
                f'var x: Int = {keyword}',
            ]
            for source in sources:
                with self.subTest(source=source), self.assertRaises(SyntaxError):
                    parse(source)


if __name__ == '__main__':
    unittest.main()
