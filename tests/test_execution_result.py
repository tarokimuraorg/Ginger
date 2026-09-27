import io
import unittest
from contextlib import redirect_stdout, redirect_stderr
from dataclasses import FrozenInstanceError
from unittest.mock import patch

import ginger.eval as evaluator
from ginger.builtin import BUILTINS
from ginger.core.failure_spec import EMPTY_FAILURES, FailureId
from ginger.main import main
from ginger.pipeline import run, execute, compile
from ginger.runtime.context import RuntimeContext
from ginger.runtime.failures import FailureStatus, FailureContractViolation
from ginger.runtime.results import ExecutionResult


class ExecutionResultTests(unittest.TestCase):
    def capture(self, source):
        stream = io.StringIO()
        with redirect_stdout(stream):
            result = run(source)
        self.assertIsInstance(result, ExecutionResult)
        return result, stream.getvalue()

    def test_normal_public_entry_points(self):
        for entry in (execute, evaluator.eval_program):
            result = entry(compile('var x: Int = 3'))
            self.assertEqual(result.environment['x'].value, 3)
            self.assertEqual(result.failure_history, ())
            self.assertEqual(result.unresolved_events, ())
            self.assertIsNone(result.contract_violation)

    def test_unresolved_multiple_events_and_incomplete_bindings(self):
        result, stdout = self.capture('var x: Float = div(1.0,0.0)\n'
                                      'print(div(2.0,0.0))\nvar y: Int = 3\nprint(y)')
        self.assertEqual(stdout, '3\n')
        self.assertIsInstance(result.environment['x'], evaluator.UninitializedBinding)
        self.assertEqual(result.environment['y'].value, 3)
        self.assertEqual([e.event_id for e in result.unresolved_events], [1, 2])
        self.assertIsNone(result.contract_violation)
        record = result.incomplete_statements[0]
        self.assertEqual((record.scope, record.statement_index, record.statement_kind,
                          record.call_id, record.related_event_ids), ('<program>', 0, 'VarDecl', 1, (1,)))
        self.assertEqual(result.call_frames[1].pending_event_ids, (1, 2))

    def test_resolved_and_caused_by(self):
        result, _ = self.capture('try print(div(1.0,0.0))\ncatch DivideByZero print(2)')
        self.assertEqual(result.failure_history[0].status, FailureStatus.RESOLVED)
        self.assertEqual(result.unresolved_events, ())
        self.assertEqual(result.call_frames[1].pending_event_ids, (1,))
        result, _ = self.capture('try print(div(1.0,0.0))\ncatch DivideByZero print(div(2.0,0.0))')
        original, new = result.failure_history
        self.assertEqual(new.caused_by, original.event_id)
        self.assertEqual(result.unresolved_events, (new,))

    def test_nested_frame_path(self):
        result, _ = self.capture('sig a() -> Float { failure DivideByZero }\n'
            'sig b() -> Float { failure DivideByZero }\nfunc a() { return b() }\n'
            'func b() { return div(1.0,0.0) }\nvar x: Float = a()')
        event, = result.failure_history
        child = result.call_frames[event.call_id]
        parent = result.call_frames[child.parent_call_id]
        root = result.call_frames[parent.parent_call_id]
        self.assertEqual([child.function_name, parent.function_name, root.function_name], ['b', 'a', '<program>'])
        self.assertTrue(all(f.pending_event_ids == (event.event_id,) for f in (child, parent, root)))

    def test_builtin_violation_preserves_environment_and_stops(self):
        result, stdout = self.capture('sig raw(Float,Float) -> Float { builtin core.float.div }\n'
            'var x: Int = 4\nvar y: Float = raw(1.0,0.0)\nx = 9\nprint(x)')
        self.assertEqual(stdout, '')
        self.assertEqual(result.environment['x'].value, 4)
        self.assertNotIn('y', result.environment)
        self.assertEqual(result.failure_history, ())
        self.assertEqual(result.incomplete_statements, ())
        self.assertIsNone(result.contract_violation.event_id)
        self.assertEqual(result.contract_violation.boundary_kind, 'builtin')

    def test_function_violation_keeps_event_and_stopped_environment(self):
        original = evaluator.eval_user_func
        def stale(name, args, syms, env, resolved):
            syms.sig_failures[name] = EMPTY_FAILURES
            return original(name, args, syms, env, resolved)
        with patch('ginger.eval.eval_user_func', side_effect=stale):
            result, stdout = self.capture('sig f() -> Int { failure DivideByZero }\n'
                'func f() { print(div(1.0,0.0))\nreturn 3 }\n'
                'var before: Int = 7\nvar x: Int = f()\nprint(99)')
        self.assertEqual(stdout, '')
        self.assertEqual(result.environment['before'].value, 7)
        self.assertNotIn('x', result.environment)
        self.assertEqual(result.contract_violation.event_id, result.failure_history[0].event_id)
        self.assertEqual(result.unresolved_events, result.failure_history)
        self.assertEqual(result.contract_violation.violating_function_or_builtin, 'f')

    def test_snapshot_independent_of_runtime(self):
        context = RuntimeContext()
        env = evaluator._eval_program_with_context(compile('var x: Float = div(1.0,0.0)'), context)
        result = ExecutionResult.from_context(env, context)
        context.resolve(1)
        context.create_call('later')
        env.clear()
        self.assertIn('x', result.environment)
        self.assertEqual(len(result.unresolved_events), 1)
        self.assertEqual(len(result.call_frames), 1)
        with self.assertRaises(TypeError):
            result.call_frames[2] = None
        with self.assertRaises(TypeError):
            result.environment['y'] = None
        try:
            context.fail_contract(failure_id=FailureId.IOErr, origin='custom', violating_call_id=1,
                violating_function_or_builtin='custom', declared_contract=EMPTY_FAILURES, boundary_kind='builtin')
        except FailureContractViolation:
            snapshot = ExecutionResult.from_context({}, context)
        context.last_contract_violation.origin = 'changed'
        self.assertEqual(snapshot.contract_violation.origin, 'custom')
        with self.assertRaises(FrozenInstanceError):
            snapshot.contract_violation.origin = 'changed'

    def test_python_and_compile_errors_still_raise(self):
        error = TypeError('implementation bug')
        with patch.dict(BUILTINS, {'core.int.print': lambda x: (_ for _ in ()).throw(error)}):
            with self.assertRaises(TypeError) as caught:
                run('print(1)')
        self.assertIs(caught.exception, error)
        from ginger.errors import TypecheckError
        with self.assertRaises(TypecheckError):
            run('var x: Int = 1.0')
        with self.assertRaises(SyntaxError):
            run('var =')

    def test_cli_diagnostics_and_exit_codes(self):
        cases = [('', 0, ''),
                 ('try print(div(1.0,0.0))\ncatch DivideByZero print(2)', 0, ''),
                 ('print(div(1.0,0.0))\nprint(div(2.0,0.0))\nprint(3)', 0, 'Unresolved failure: DivideByZero'),
                 ('sig raw(Float,Float) -> Float { builtin core.float.div }\nprint(raw(1.0,0.0))\nprint(3)', 1, 'Failure contract violation')]
        for source, code, diagnostic in cases:
            with self.subTest(source=source):
                out, err = io.StringIO(), io.StringIO()
                with patch('ginger.main.run', side_effect=lambda _: run(source)), redirect_stdout(out), redirect_stderr(err):
                    self.assertEqual(main(), code)
                self.assertIn(diagnostic, err.getvalue())
                self.assertNotIn('Traceback', err.getvalue())
                if 'Unresolved' in diagnostic:
                    self.assertEqual(out.getvalue(), '3\n')
                    self.assertEqual(err.getvalue().count('Unresolved failure:'), 2)
                    self.assertIn('event: #2', err.getvalue())
                elif code:
                    self.assertEqual(out.getvalue(), '')
                else:
                    self.assertEqual(err.getvalue(), '')
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            self.assertEqual(main(), 0)
        self.assertEqual(out.getvalue(), '2\n3\n')
        self.assertEqual(err.getvalue(), '')
