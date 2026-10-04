import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import Mock, patch

import ginger.eval as evaluator
from ginger.builtin import BUILTINS
from ginger.core.failure_spec import EMPTY_FAILURES, FailureId
from ginger.runtime.context import RuntimeContext
from ginger.runtime.failures import FailureContractViolation, FailureStatus, RaisedFailure
from test_failure_contract import checked


class ContractViolationTests(unittest.TestCase):
    def execute(self, source, context):
        return evaluator._eval_program_with_context(checked(source)[0], context)

    def test_function_boundary_checks_value_unit_and_no_value(self):
        for typ, body in [('Int', 'print(div(1.0,0.0))\nreturn 3'),
                          ('Unit', 'print(div(1.0,0.0))'),
                          ('Float', 'return div(1.0,0.0)')]:
            with self.subTest(typ=typ):
                context = RuntimeContext()
                original = evaluator.eval_user_func
                # Simulate stale checked metadata only at the internal runtime
                # boundary; ordinary source remains statically checked.
                def stale(name, args, syms, env, resolved):
                    syms.sig_failures[name] = EMPTY_FAILURES
                    return original(name, args, syms, env, resolved)
                with patch('ginger.eval.eval_user_func', side_effect=stale), self.assertRaises(FailureContractViolation) as caught:
                    self.execute(f'sig f() -> {typ} {{ failure DivideByZero }}\n'
                                 f'func f() {{ {body} }}\nlet result: {typ} = f()\nprint(99)', context)
                v = caught.exception
                self.assertEqual((v.event_id, v.violating_call_id, v.violating_function_or_builtin), (1, 2, 'f'))
                self.assertEqual(v.declared_contract, EMPTY_FAILURES)
                self.assertIs(context.last_contract_violation, v)
                self.assertEqual(context.failure_history[0].status, FailureStatus.UNRESOLVED)
                self.assertEqual(context.unresolved_pending(1), ())
                self.assertIsNone(context.current_call_id)

    def test_nested_contracts_checked_at_each_responsible_function(self):
        source = ('sig a() -> Float { failure DivideByZero }\n'
                  'sig b() -> Float { failure DivideByZero }\n'
                  'func a() { return b() }\nfunc b() { return div(1.0,0.0) }\nlet result: Float = a()')
        for missing in ('a', 'b', None):
            with self.subTest(missing=missing):
                context = RuntimeContext()
                original = evaluator.eval_user_func
                def stale(name, args, syms, env, resolved):
                    if name == missing:
                        syms.sig_failures[name] = EMPTY_FAILURES
                    return original(name, args, syms, env, resolved)
                with patch('ginger.eval.eval_user_func', side_effect=stale):
                    if missing:
                        with self.assertRaises(FailureContractViolation) as caught:
                            self.execute(source, context)
                        self.assertEqual(caught.exception.violating_function_or_builtin, missing)
                    else:
                        self.execute(source, context)
                        self.assertTrue(all(context.unresolved_pending(i) == (1,) for i in (1, 2, 3)))
                self.assertEqual(len(context.failure_history), 1)
                self.assertEqual(context.failure_history[0].call_id, 3)
                self.assertIsNone(context.current_call_id)

    def test_resolved_history_excluded_from_function_contract(self):
        context = RuntimeContext()
        with context.call('<program>') as root:
            with context.call('f') as child:
                event = context.register_failure(FailureId.IOErr, origin='custom', call_id=child.call_id)
                context.resolve(event.event_id)
                context.validate_function_exit(child.call_id)
            self.assertEqual(context.unresolved_pending(root.call_id), ())
        self.assertEqual(context.failure_history[0].status, FailureStatus.RESOLVED)

    def test_builtin_violation_diagnostic_and_no_incomplete_or_event(self):
        context = RuntimeContext()
        printer = Mock(side_effect=RaisedFailure(FailureId.IOErr))
        with patch.dict(BUILTINS, {'core.int.print': printer}), self.assertRaises(FailureContractViolation) as caught:
            self.execute('print(1)\nprint(3)', context)
        v = caught.exception
        self.assertEqual((v.failure_id, v.origin, v.violating_call_id, v.event_id),
                         (FailureId.IOErr, 'core.int.print', 1, None))
        for text in ('Failure contract violation', 'IOErr', 'builtin core.int.print', 'declared failures: {}'):
            self.assertIn(text, str(v))
        self.assertEqual(context.failure_history, ())
        self.assertEqual(context.incomplete_statements, ())
        self.assertIsNone(context.current_call_id)
        printer.assert_called_once_with(1)

    def test_declared_root_failure_continues(self):
        context = RuntimeContext()
        stream = io.StringIO()
        with redirect_stdout(stream):
            self.execute('print(div(1.0,0.0))\nprint(3)', context)
        self.assertEqual(stream.getvalue(), '3\n')
        self.assertIsNone(context.last_contract_violation)
        self.assertEqual(context.unresolved_pending(1), (1,))
