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
THREE_FAILURES = ('sig risky() -> Float { failure IntegerOverflow failure DivideByZero failure FloatOverflow }\n'
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

    def test_successful_latest_write_is_no_op(self):
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
            'sig f() -> Unit { failure DivideByZero failure FloatOverflow }\n'
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
        prefix = ('sig f() -> Float { failure IntegerOverflow failure DivideByZero failure FloatOverflow }\n'
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
        prefix = ('sig f() -> Int { failure DivideByZero failure FloatOverflow }\n'
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
        env, context, stdout = self.run_source(THREE_FAILURES +
            'resolve x { IntegerOverflow { x = 0\nprint(999) } }\nprint(3)')
        self.assertEqual(stdout, '3\n')
        self.assertIsInstance(env['x'], evaluator.UninitializedBinding)
        event, = context.failure_history
        self.assertEqual(event.failure_id, FailureId.DivideByZero)
        self.assertEqual(event.status, FailureStatus.UNRESOLVED)

    def test_only_matching_handler_runs(self):
        result, stdout = self.public_result(THREE_FAILURES +
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
            'x = add(2,2)\nresolve x { IntegerOverflow { x = 9\nprint(999) } }\nprint(x)')
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
        self.assertEqual(self.warning_messages(THREE_FAILURES +
            'resolve x { IntegerOverflow { x = 0 } }'),
            ['unhandled failures: DivideByZero, FloatOverflow'])

    def test_multiple_resolve_statements_combine_initializer_coverage(self):
        self.assertEqual(self.warning_messages(THREE_FAILURES +
            'resolve x { IntegerOverflow { x = 0 } }\n'
            'resolve x { DivideByZero { x = 1 } }\n'
            'resolve x { FloatOverflow { x = 2 } }'), [])

    def test_resolve_warning_removal_is_limited_to_target_binding(self):
        self.assertEqual(self.warning_messages(OVERFLOW +
            'var y: Int = add(9007199254740991,2)\n'
            'print(add(9007199254740991,3))\n'
            'resolve x { IntegerOverflow { x = 0 } }'),
            ['unhandled failures: IntegerOverflow'] * 2)

    def test_resolve_warning_removal_covers_latest_assignment_only(self):
        self.assertEqual(self.warning_messages(OVERFLOW +
            'x = add(9007199254740991,2)\n'
            'resolve x { IntegerOverflow { x = 0 } }'),
            ['unhandled failures: IntegerOverflow'])

    def test_same_handler_failure_remains_unhandled(self):
        self.assertEqual(self.warning_messages(OVERFLOW +
            'resolve x { IntegerOverflow { x = add(9007199254740991,2) } }'),
            ['unhandled failures: IntegerOverflow'])

    def test_all_handler_effects_remain_in_static_failures(self):
        self.assertEqual(self.warning_messages(THREE_FAILURES +
            'resolve x { IntegerOverflow { print(div(2.0,0.0))\nx = 0 }\n'
            'DivideByZero { print(add(9007199254740991,1))\nx = 1 } }'),
            ['unhandled failures: FloatOverflow', 'unhandled failures: DivideByZero, FloatOverflow',
             'unhandled failures: IntegerOverflow'])

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
            'var t: Thunk[Float]<DivideByZero, FloatOverflow> = thunk(div(1.0,0.0))\n'
            'var x: Float = force(t)\nresolve x { DivideByZero { x = 0 } }\nprint(x)')
        self.assertEqual(stdout, '0.0\n')
        self.assertEqual(result.environment['x'].value, 0.0)
        self.assertIsInstance(result.environment['t'].value, ThunkValue)
        self.assertEqual(result.environment['t'].value.potential_failure_contract,
                         frozenset({FailureId.DivideByZero, FailureId.FloatOverflow}))
        self.assertEqual(result.failure_history[0].status, FailureStatus.RESOLVED)
        self.assertEqual(result.unresolved_events, ())

    def test_repeated_force_produces_distinct_binding_causes(self):
        result, _ = self.public_result(
            'var t: Thunk[Float]<DivideByZero, FloatOverflow> = thunk(div(1.0,0.0))\n'
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


class LatestWriteTests(unittest.TestCase):
    run_source = ResolveTests.run_source
    public_result = ResolveTests.public_result
    warning_messages = ResolveTests.warning_messages

    def test_failed_reassignment_recovery(self):
        env, context, stdout = self.run_source(
            'var x: Float = 10.0\nx = mul(1e308,1e308)\n'
            'resolve x { FloatOverflow { x = 1 } }\nprint(x)')
        self.assertEqual(stdout, '1.0\n')
        self.assertEqual(context.failure_history[0].status, FailureStatus.RESOLVED)
        self.assertEqual(env['x'].value, 1.0)

    def test_failed_reassignment_preserves_value_with_or_without_handler(self):
        for handler, expected in [('', '10.0\n'),
                ('resolve x { FloatOverflow { print(999) } }\n', '999\n10.0\n')]:
            with self.subTest(handler=handler):
                result, stdout = self.public_result(
                    'var x: Float = 10.0\nx = mul(1e308,1e308)\n' + handler + 'print(x)')
                self.assertEqual(stdout, expected)
                self.assertEqual(result.environment['x'].value, 10.0)
                self.assertEqual(result.failure_history[0].status, FailureStatus.UNRESOLVED)

    def test_two_failed_writes_only_latest_resolves(self):
        source = ('var x: Float = 10.0\nx = mul(1e308,1e308)\n'
                  'x = div(1.0,0.0)\nresolve x { DivideByZero { x = 2 } }\nprint(x)')
        result, stdout = self.public_result(source)
        self.assertEqual(stdout, '2.0\n')
        self.assertEqual([e.status for e in result.failure_history],
                         [FailureStatus.UNRESOLVED, FailureStatus.RESOLVED])
        self.assertEqual(self.warning_messages(source), ['unhandled failures: FloatOverflow'] * 2)

    def test_same_failure_events_and_warnings_are_separate(self):
        source = ('var x: Int = 1\nx = add(9007199254740991,1)\n'
                  'x = add(9007199254740991,2)\n'
                  'resolve x { IntegerOverflow { x = 2 } }')
        result, _ = self.public_result(source)
        older, latest = result.failure_history
        self.assertNotEqual(older.event_id, latest.event_id)
        self.assertEqual(older.status, FailureStatus.UNRESOLVED)
        self.assertEqual(latest.status, FailureStatus.RESOLVED)
        self.assertEqual(result.unresolved_events, (older,))
        self.assertEqual(self.warning_messages(source), ['unhandled failures: IntegerOverflow'])

    def test_older_matching_failure_is_not_selected(self):
        result, stdout = self.public_result(
            'var x: Float = 10.0\nx = mul(1e308,1e308)\nx = div(1.0,0.0)\n'
            'resolve x { FloatOverflow { x = 2\nprint(999) } }\nprint(x)')
        self.assertEqual(stdout, '10.0\n')
        self.assertEqual([e.status for e in result.failure_history], [FailureStatus.UNRESOLVED] * 2)

    def test_successful_write_clears_runtime_target_preserves_history(self):
        source = 'var x: Float = 10.0\nx = mul(1e308,1e308)\nx = 20.0\n'
        env, context, _ = self.run_source(source)
        self.assertEqual(env['x'].value, 20.0)
        # Inspect the root frame's retained write record after execution.
        self.assertEqual(context._binding_writes[1, 'x'].cause_event_ids, ())
        self.assertEqual(context.failure_history[0].status, FailureStatus.UNRESOLVED)
        with self.assertRaisesRegex(TypecheckError, 'latest write'):
            checked(source + 'resolve x { FloatOverflow { x = 1 } }')

    def test_latest_assignment_eligibility_replaces_initializer(self):
        with self.assertRaisesRegex(TypecheckError, 'latest write'):
            checked('var x: Float = div(1.0,0.0)\nx = mul(1e308,1e308)\n'
                    'resolve x { DivideByZero { x = 0 } }')

    def test_unrelated_statements_and_bindings_preserve_target(self):
        result, stdout = self.public_result(
            'var x: Int = 1\nvar y: Int = 2\nx = add(9007199254740991,1)\n'
            'print(123)\ny = add(9007199254740991,2)\n'
            'resolve x { IntegerOverflow { x = 3 } }\nprint(x)\nprint(y)')
        self.assertEqual(stdout, '123\n3\n2\n')
        self.assertEqual([e.status for e in result.failure_history],
                         [FailureStatus.RESOLVED, FailureStatus.UNRESOLVED])

    def test_handler_failed_write_becomes_latest_then_recovers(self):
        result, stdout = self.public_result(
            'var x: Float = 10.0\nx = mul(1e308,1e308)\n'
            'resolve x { FloatOverflow { x = div(1.0,0.0) } }\nprint(x)\n'
            'resolve x { DivideByZero { x = 2 } }\nprint(x)')
        self.assertEqual(stdout, '10.0\n2.0\n')
        original, latest = result.failure_history
        self.assertEqual(original.status, FailureStatus.UNRESOLVED)
        self.assertEqual(latest.status, FailureStatus.RESOLVED)
        self.assertEqual(latest.caused_by, original.event_id)

    def test_handler_failed_write_does_not_restore_original_target(self):
        result, stdout = self.public_result(
            'var x: Float = 10.0\nx = mul(1e308,1e308)\n'
            'resolve x { FloatOverflow { x = div(1.0,0.0) } }\n'
            'resolve x { FloatOverflow { x = 2\nprint(999) } }\nprint(x)')
        self.assertEqual(stdout, '10.0\n')
        self.assertEqual([e.status for e in result.failure_history], [FailureStatus.UNRESOLVED] * 2)

    def test_argument_and_callee_failures_on_reassignment(self):
        prefix = ('sig calculate(Float, Float) -> Float { failure DivideByZero failure FloatOverflow }\n'
                  'func calculate(a: Float, b: Float) { print(77)\nreturn div(a,b) }\n'
                  'var x: Float = 10.0\n')
        for expr, failure, expected in [('calculate(mul(1e308,1e308),1)', 'FloatOverflow', '1.0\n'),
                                      ('calculate(1,0)', 'DivideByZero', '77\n1.0\n')]:
            with self.subTest(expr=expr):
                result, stdout = self.public_result(prefix + f'x = {expr}\n'
                    f'resolve x {{ {failure} {{ x = 1 }} }}\nprint(x)')
                self.assertEqual(stdout, expected)
                self.assertEqual(len(result.failure_history), 1)
                self.assertEqual(result.failure_history[0].status, FailureStatus.RESOLVED)

    def test_assignment_direct_cause_excludes_pending_events(self):
        result, stdout = self.public_result(
            'sig f() -> Int { failure DivideByZero failure FloatOverflow }\n'
            'func f() { print(div(1.0,0.0))\nreturn 3 }\n'
            'var x: Int = 10\nx = add(f(),add(9007199254740991,1))\n'
            'resolve x { DivideByZero { x = 9\nprint(999) }\n'
            'IntegerOverflow { x = 0 } }\nprint(x)')
        self.assertEqual(stdout, '0\n')
        self.assertEqual([e.status for e in result.failure_history],
                         [FailureStatus.UNRESOLVED, FailureStatus.RESOLVED])

    def test_thunk_force_does_not_write_thunk_binding(self):
        source = ('var t: Thunk[Int]<IntegerOverflow> = thunk(add(9007199254740991,1))\n'
                  'print(force(t))\n')
        result, _ = self.public_result(source)
        self.assertEqual(len(result.unresolved_events), 1)
        with self.assertRaisesRegex(TypecheckError, 'latest write'):
            checked(source + 'resolve t { IntegerOverflow { print(999) } }')

    def test_force_assignment_can_recover(self):
        result, stdout = self.public_result(
            'var t: Thunk[Int]<IntegerOverflow> = thunk(add(9007199254740991,1))\n'
            'var x: Int = 10\nx = force(t)\n'
            'resolve x { IntegerOverflow { x = 1 } }\nprint(x)')
        self.assertEqual(stdout, '1\n')
        self.assertEqual(result.failure_history[0].status, FailureStatus.RESOLVED)

    def test_handler_alternatives_keep_independent_latest_effects(self):
        source = ('var x: Float = div(1.0,0.0)\n'
                  'resolve x { DivideByZero { x = mul(1e308,1e308) }\n'
                  'FloatOverflow { x = 1 } }\n'
                  'resolve x { FloatOverflow { x = 2 } }\nprint(x)')
        result, stdout = self.public_result(source)
        self.assertEqual(stdout, '2.0\n')
        self.assertEqual([e.status for e in result.failure_history],
                         [FailureStatus.UNRESOLVED, FailureStatus.RESOLVED])
        self.assertEqual(self.warning_messages(source), [])


    def test_intermediate_handler_write_warning_survives(self):
        source = ('var x: Float = div(1.0,0.0)\n'
                  'resolve x { DivideByZero { x = mul(1e308,1e308)\nx = div(1.0,0.0) } }\n'
                  'resolve x { DivideByZero { x = 1 }\nFloatOverflow { x = 2 } }')
        self.assertEqual(self.warning_messages(source), ['unhandled failures: FloatOverflow'])

    def test_call_local_same_name_does_not_replace_root_write(self):
        result, stdout = self.public_result(
            'sig f() -> Int { failure IntegerOverflow }\n'
            'func f() { var x: Int = add(9007199254740991,1)\nreturn 7 }\n'
            'var x: Int = add(9007199254740991,2)\nprint(f())\n'
            'resolve x { IntegerOverflow { x = 3 } }\nprint(x)')
        self.assertEqual(stdout, '7\n3\n')
        self.assertEqual([e.status for e in result.failure_history],
                         [FailureStatus.RESOLVED, FailureStatus.UNRESOLVED])

    def test_uninitialized_binding_uses_latest_assignment_cause(self):
        result, stdout = self.public_result(
            'var x: Float = mul(1e308,1e308)\nx = div(1.0,0.0)\n'
            'resolve x { DivideByZero { x = 2 } }\nprint(x)')
        self.assertEqual(stdout, '2.0\n')
        self.assertEqual([e.status for e in result.failure_history],
                         [FailureStatus.UNRESOLVED, FailureStatus.RESOLVED])


if __name__ == '__main__':
    unittest.main()
