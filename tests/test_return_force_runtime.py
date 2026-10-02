import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import ginger.eval as evaluator
from ginger.builtin import BUILTINS
from ginger.core.failure_spec import EMPTY_FAILURES, FailureId
from ginger.runtime.failures import FailureStatus
from ginger.runtime.thunk import ThunkValue
from test_failure_contract import checked


class ReturnForceRuntimeTests(unittest.TestCase):
    def run_source(self, source):
        stream = io.StringIO()
        with redirect_stdout(stream):
            result = evaluator.eval_program(checked(source)[0])
        return result, stream.getvalue()

    def test_return_ends_body_and_no_value_cause_reaches_initializer(self):
        result, stdout = self.run_source(
            'sig f() -> Int { failure IntegerOverflow }\n'
            'func f() { return add(9007199254740991,1)\nprint(99)\nreturn 4 }\n'
            'var x: Int = f()\nprint(3)')
        self.assertEqual(stdout, '3\n')
        self.assertIsNone(result.contract_violation)
        self.assertEqual(len(result.failure_history), 1)
        for name in ('x',):
            self.assertIsInstance(result.environment[name], evaluator.UninitializedBinding)
            self.assertEqual(result.environment[name].related_event_ids, (1,))
        self.assertEqual([s.related_event_ids for s in result.incomplete_statements], [(1,)] * 2)
        result, stdout = self.run_source('sig f() -> Int {}\nfunc f() { return 3\nprint(99)\nreturn 4 }\nprint(f())')
        self.assertEqual(stdout, '3\n')

    def test_value_and_unit_with_pending(self):
        result, stdout = self.run_source(
            'sig f() -> Int { failure DivideByZero }\nfunc f() { print(div(1.0,0.0))\nreturn 3 }\n'
            'sig u() -> Unit { failure DivideByZero }\nfunc u() { print(div(1.0,0.0))\nreturn print(2) }\n'
            'var x: Int = f()\nlet unit: Unit = u()\nprint(x)')
        self.assertEqual(stdout, '2\n3\n')
        self.assertEqual(result.environment['x'].value, 3)
        self.assertIsNone(result.environment['unit'].value)
        self.assertEqual(len(result.unresolved_events), 2)

    def test_creation_force_call_and_repeated_evaluation(self):
        source = ('sig make() -> Thunk[Float, DivideByZero] {}\n'
            'func make() { return thunk(div(1.0,0.0)) }\n'
            'sig use(Thunk[Float, DivideByZero]) -> Float { failure DivideByZero }\n'
            'func use(t: Thunk[Float, DivideByZero]) { return force(t) }\n'
            'var t: Thunk[Float, DivideByZero] = make()\n')
        result, _ = self.run_source(source)
        self.assertEqual(result.failure_history, ())
        result, stdout = self.run_source(source + 'print(use(t))\nprint(use(t))\nprint(3)')
        self.assertEqual(stdout, '3\n')
        self.assertEqual([e.call_id for e in result.failure_history], [3, 4])
        self.assertEqual([e.event_id for e in result.failure_history], [1, 2])
        self.assertTrue(all(result.call_frames[e.call_id].function_name == 'use' for e in result.failure_history))

    def test_force_value_and_pending_user_function(self):
        result, stdout = self.run_source('sig f() -> Int { failure DivideByZero }\n'
            'func f() { print(div(1.0,0.0))\nreturn 3 }\n'
            'var t: Thunk[Int, DivideByZero] = thunk(f())\nvar x: Int = force(t)\nprint(x)')
        self.assertEqual(stdout, '3\n')
        self.assertEqual(result.environment['x'].value, 3)
        self.assertEqual(result.call_frames[result.failure_history[0].call_id].function_name, 'f')
        self.assertEqual(len(result.unresolved_events), 1)
        result, _ = self.run_source('var t: Thunk[Int, Never] = thunk(3)\nvar x: Int = force(t)')
        self.assertEqual(result.environment['x'].value, 3)
        self.assertEqual(result.failure_history, ())

    def test_force_catch_and_handler_causality(self):
        result, stdout = self.run_source('var t: Thunk[Float, DivideByZero] = thunk(div(1.0,0.0))\n'
            'try print(force(t))\ncatch DivideByZero print(force(t))\ncatch DivideByZero print(99)\nprint(3)')
        self.assertEqual(stdout, '3\n')
        self.assertEqual([e.status for e in result.failure_history], [FailureStatus.RESOLVED, FailureStatus.UNRESOLVED])
        self.assertEqual(result.failure_history[1].caused_by, 1)
        result, stdout = self.run_source('try print(force(thunk(div(1.0,0.0))))\ncatch DivideByZero print(2)')
        self.assertEqual(stdout, '2\n')
        self.assertEqual(result.unresolved_events, ())

    def test_force_builtin_violation_and_python_error(self):
        result, stdout = self.run_source('sig raw(Float,Float) -> Float { builtin core.float.div }\n'
            'var t: Thunk[Float, Never] = thunk(raw(1.0,0.0))\nprint(force(t))\nprint(99)')
        self.assertEqual(stdout, '')
        self.assertEqual(result.contract_violation.boundary_kind, 'builtin')
        self.assertEqual(result.failure_history, ())
        with patch.dict(BUILTINS, {'core.int.print': lambda x: (_ for _ in ()).throw(TypeError('bug'))}):
            with self.assertRaisesRegex(TypeError, 'bug'):
                self.run_source('var t: Thunk[Unit, Never] = thunk(print(1))\nforce(t)')

    def test_force_and_return_user_function_contract_not_bypassed(self):
        original = evaluator.eval_user_func
        def stale(name, args, syms, env, resolved):
            syms.sig_failures[name] = EMPTY_FAILURES
            return original(name, args, syms, env, resolved)
        with patch('ginger.eval.eval_user_func', side_effect=stale):
            result, stdout = self.run_source('sig f() -> Float { failure DivideByZero }\n'
                'func f() { return div(1.0,0.0)\nprint(99) }\n'
                'var t: Thunk[Float, DivideByZero] = thunk(f())\nprint(force(t))\nprint(99)')
        self.assertEqual(stdout, '')
        self.assertEqual(result.contract_violation.boundary_kind, 'function')
        self.assertEqual(result.contract_violation.event_id, 1)
        self.assertEqual(result.unresolved_events, result.failure_history)

    def test_thunk_potential_contract_checked_for_value_and_no_value(self):
        # Simulate stale runtime metadata without adding any language escape hatch.
        for expression, prefix in [('div(1.0,0.0)', ''), ('f()',
            'sig f() -> Float { failure DivideByZero }\nfunc f() { print(div(1.0,0.0))\nreturn 3.0 }\n')]:
            original = evaluator.eval_expr
            def narrow(expr, env, syms, outer=None):
                result = original(expr, env, syms, outer)
                value = getattr(result.value_result, 'value', None)
                if isinstance(value, ThunkValue):
                    value.potential_failure_contract = EMPTY_FAILURES
                return result
            with patch('ginger.eval.eval_expr', side_effect=narrow):
                result, stdout = self.run_source(prefix + f'var t: Thunk[Float, DivideByZero] = thunk({expression})\nprint(force(t))\nprint(99)')
            self.assertEqual(stdout, '')
            self.assertEqual(result.contract_violation.boundary_kind, 'thunk')
            self.assertEqual(result.contract_violation.event_id, 1)
