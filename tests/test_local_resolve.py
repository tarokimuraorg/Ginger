import unittest
import test_resolve
from test_failure_contract import checked
from ginger.errors import TypecheckError
from ginger.runtime.failures import FailureStatus


class LocalResolveTests(unittest.TestCase):
    public_result = test_resolve.ResolveTests.public_result
    run_source = test_resolve.ResolveTests.run_source

    def program(self, body, failures='Never', ret='Int'):
        return (f'sig f(Int) -> {ret} {{ failure {failures} }}\n'
                f'func f(x: Int) {{ {body} }}\n')

    def test_initializer_never_and_call_isolation(self):
        source = self.program('var y: Int = x + 1\n'
            'resolve y { IntegerOverflow { y = 888 } }\nreturn y')
        result, stdout = self.public_result(source +
            'print(f(1))\nprint(f(9007199254740991))\nprint(f(1))')
        self.assertEqual(stdout, '2\n888\n2\n')
        self.assertFalse(result.unresolved_events)
        self.assertIsNone(result.contract_violation)
        self.assertEqual(result.failure_history[0].status, FailureStatus.RESOLVED)

    def test_reassignment_preserves_old_value(self):
        result, stdout = self.public_result(self.program('var y: Int = 123\ny = x + 1\n'
            'resolve y { IntegerOverflow { print(y)\ny = 888 } }\nreturn y') +
            'print(f(9007199254740991))')
        self.assertEqual(stdout, '123\n888\n')
        self.assertFalse(result.unresolved_events)

    def test_no_target_write_rejected_for_never(self):
        with self.assertRaisesRegex(TypecheckError, 'may propagate undeclared failures: IntegerOverflow'):
            checked(self.program('var y: Int = x + 1\n'
                'resolve y { IntegerOverflow { print(999) } }\nreturn y'))

    def test_failed_handler_requires_original_and_new_failure(self):
        prefix = 'sig risky() -> Int { failure IOErr builtin test.risky }\n'
        body = ('var y: Int = 123\ny = x + 1\n'
                'resolve y { IntegerOverflow { y = risky() } }\nreturn y')
        from unittest.mock import patch
        from ginger.builtin import BUILTINS
        for failures, missing in [('IOErr', 'IntegerOverflow'), ('IntegerOverflow', 'IOErr')]:
            with patch.dict(BUILTINS, {'test.risky': lambda: 1}), self.subTest(failures=failures), self.assertRaisesRegex(TypecheckError, missing):
                checked(prefix + self.program(body, failures))
        from unittest.mock import Mock, patch
        from ginger.builtin import BUILTINS
        from ginger.runtime.failures import RaisedFailure
        from ginger.core.failure_spec import FailureId
        with patch.dict(BUILTINS, {'test.risky': Mock(side_effect=RaisedFailure(FailureId.IOErr))}):
            result, stdout = self.public_result(prefix + self.program(body, 'IOErr failure IntegerOverflow') +
                                               'print(f(9007199254740991))')
        self.assertEqual(stdout, '123\n')
        self.assertEqual(len(result.unresolved_events), 2)
        self.assertEqual(result.failure_history[1].caused_by, result.failure_history[0].event_id)

    def test_partial_handler(self):
        body = 'var y: Float = div(1.0,0.0)\nresolve y { DivideByZero { y = 0.0 } }\nreturn y'
        checked(self.program(body, 'FloatOverflow', 'Float'))
        with self.assertRaisesRegex(TypecheckError, 'FloatOverflow'):
            checked(self.program(body, 'Never', 'Float'))

    def test_latest_failed_write_only(self):
        body = ('var y: Int = 123\ny = x + 1\ny = x + 2\n'
                'resolve y { IntegerOverflow { y = 888 } }\nreturn y')
        with self.assertRaisesRegex(TypecheckError, 'IntegerOverflow'):
            checked(self.program(body))
        result, stdout = self.public_result(self.program(body, 'IntegerOverflow') +
                                           'print(f(9007199254740991))')
        self.assertEqual(stdout, '888\n')
        self.assertEqual([e.status for e in result.failure_history],
                         [FailureStatus.UNRESOLVED, FailureStatus.RESOLVED])

    def test_later_success_no_op_and_history(self):
        result, stdout = self.public_result(self.program('var y: Int = x + 1\ny = add(1,2)\n'
            'resolve y { IntegerOverflow { y = 888 } }\nreturn y', 'IntegerOverflow') +
            'print(f(9007199254740991))')
        self.assertEqual(stdout, '3\n')
        self.assertEqual(len(result.unresolved_events), 1)

    def test_argument_and_callee_direct_causes(self):
        prefix = ('sig calculate(Int) -> Int { failure IntegerOverflow }\n'
                  'func calculate(x: Int) { return x + 1 }\n')
        for expr in ('calculate(x * x)', 'calculate(x)'):
            result, stdout = self.public_result(prefix + self.program(f'var y: Int = {expr}\n'
                'resolve y { IntegerOverflow { y = 888 } }\nreturn y') +
                'print(f(9007199254740991))')
            self.assertEqual(stdout, '888\n')
            self.assertEqual(len(result.failure_history), 1)
            self.assertFalse(result.unresolved_events)

    def test_unrelated_binding_and_statement(self):
        result, stdout = self.public_result(self.program('var y: Int = x + 1\nprint(123)\n'
            'var z: Int = x + 2\nresolve y { IntegerOverflow { y = 888 } }\nreturn y',
            'IntegerOverflow') + 'print(f(9007199254740991))')
        self.assertEqual(stdout, '123\n888\n')
        self.assertEqual([e.status for e in result.failure_history],
                         [FailureStatus.RESOLVED, FailureStatus.UNRESOLVED])

    def test_nested_call_same_binding_name(self):
        inner = self.program('var y: Int = x + 1\nresolve y { IntegerOverflow { y = 888 } }\nreturn y')
        outer = ('sig g(Int) -> Int { failure Never }\nfunc g(x: Int) {\n'
                 'var y: Int = x + 2\nprint(f(x))\n'
                 'resolve y { IntegerOverflow { y = 777 } }\nreturn y }\n')
        result, stdout = self.public_result(inner + outer + 'print(g(9007199254740991))')
        self.assertEqual(stdout, '888\n777\n')
        self.assertFalse(result.unresolved_events)

    def test_thunk_creation_and_force_result(self):
        prefix = 'var t: Thunk[Int]<IntegerOverflow> = thunk(x + 1)\n'
        with self.assertRaisesRegex(TypecheckError, 'latest write'):
            checked(self.program(prefix + 'resolve t { IntegerOverflow { print(1) } }\nreturn 1'))
        result, stdout = self.public_result(self.program(prefix + 'var y: Int = force(t)\n'
            'resolve y { IntegerOverflow { y = 888 } }\nreturn y') + 'print(f(9007199254740991))')
        self.assertEqual(stdout, '888\n')
        self.assertFalse(result.unresolved_events)

    def test_invalid_targets_and_global_access(self):
        for prelude, target in [('', 'unknown'), ('', 'x'), ('let y: Int = x + 1\n', 'y'), ('', 'global')]:
            with self.subTest(target=target), self.assertRaises(TypecheckError):
                checked('var global: Int = add(1,2)\n' + self.program(prelude +
                    f'resolve {target} {{ IntegerOverflow {{ print(1) }} }}\nreturn 1'))

    def test_handler_return_remains_rejected(self):
        with self.assertRaisesRegex(SyntaxError, 'return is not allowed'):
            checked(self.program('var y: Int = x + 1\n'
                'resolve y { IntegerOverflow { return 888 } }\nreturn y'))

    def test_last_handler_write_must_be_failure_free(self):
        with self.assertRaisesRegex(TypecheckError, 'IntegerOverflow'):
            checked(self.program('var y: Int = x + 1\n'
                'resolve y { IntegerOverflow { y = 888\ny = x + 2 } }\nreturn y'))

    def test_successful_callee_pending_is_not_discharged(self):
        prefix = ('sig pending() -> Int { failure IntegerOverflow }\n'
                  'func pending() { print(add(9007199254740991,1))\nreturn 3 }\n')
        body = ('var y: Int = pending()\n'
                'resolve y { IntegerOverflow { y = 888 } }\nreturn y')
        with self.assertRaisesRegex(TypecheckError, 'IntegerOverflow'):
            checked(prefix + self.program(body))
        result, stdout = self.public_result(prefix + self.program(body, 'IntegerOverflow') + 'print(f(1))')
        self.assertEqual(stdout, '3\n')
        self.assertEqual(len(result.unresolved_events), 1)

    def test_generic_conditional_failures_recovered(self):
        source = ('sig safe(T,T) -> T { require T guarantees Addable failure Never }\n'
                  'func safe(a: T,b: T) { var y: T = a + b\n'
                  'resolve y { IntegerOverflow { y = a }\nFloatOverflow { y = b } }\nreturn y }\n')
        result, stdout = self.public_result(source + 'print(safe(9007199254740991,1))\nprint(safe(1e308,1e308))')
        self.assertEqual(stdout, '9007199254740991\n1e+308\n')
        self.assertFalse(result.unresolved_events)

    def test_failureset_is_individual_events(self):
        source = ('failureset Arithmetic { DivideByZero FloatOverflow }\n'
                  'sig quotient() -> Float { failure Arithmetic }\n'
                  'func quotient() { return div(1.0,0.0) }\n')
        result, stdout = self.public_result(source + self.program('var y: Float = quotient()\n'
            'resolve y { DivideByZero { y = 0.0 }\nFloatOverflow { y = 1.0 } }\nreturn y', ret='Float') +
            'print(f(1))')
        self.assertEqual(stdout, '0.0\n')
        self.assertFalse(result.unresolved_events)

    def test_handler_independent_failure_remains_outward(self):
        body = ('var y: Int = x + 1\nresolve y { IntegerOverflow {\n'
                'print(div(1.0,0.0))\ny = 888 } }\nreturn y')
        checked(self.program(body, 'DivideByZero failure FloatOverflow'))
        with self.assertRaisesRegex(TypecheckError, 'DivideByZero'):
            checked(self.program(body))

    def test_pure_latest_write_rejects_handler(self):
        with self.assertRaisesRegex(TypecheckError, 'latest write'):
            checked(self.program('var y: Int = x + 1\ny = 123\n'
                'resolve y { IntegerOverflow { y = 888 } }\nreturn y', 'IntegerOverflow'))

    def test_initializer_without_recovery_keeps_event(self):
        result, stdout = self.public_result(self.program('var y: Int = x + 1\n'
            'resolve y { IntegerOverflow { print(999) } }\nreturn 3', 'IntegerOverflow') +
            'print(f(9007199254740991))')
        self.assertEqual(stdout, '999\n3\n')
        self.assertEqual(len(result.unresolved_events), 1)

    def test_assignment_cause_excludes_callee_pending(self):
        prefix = ('sig pending() -> Int { failure DivideByZero failure FloatOverflow }\n'
                  'func pending() { print(div(1.0,0.0))\nreturn 3 }\n')
        body = ('var y: Int = 123\ny = add(pending(),add(x,1))\n'
                'resolve y { DivideByZero { y = 999 }\nIntegerOverflow { y = 888 } }\nreturn y')
        result, stdout = self.public_result(prefix + self.program(body,
            'DivideByZero failure FloatOverflow failure IntegerOverflow') + 'print(f(9007199254740991))')
        self.assertEqual(stdout, '888\n')
        self.assertEqual([e.status for e in result.failure_history],
                         [FailureStatus.UNRESOLVED, FailureStatus.RESOLVED])

    def test_metadata_is_keyed_by_distinct_calls(self):
        source = self.program('var y: Int = x + 1\n'
            'resolve y { IntegerOverflow { y = 888 } }\nreturn y')
        _, context, stdout = self.run_source(source + 'print(f(9007199254740991))\nprint(f(1))')
        writes = [(key, write) for key, write in context._binding_writes.items() if key[1] == 'y']
        self.assertEqual(len(writes), 2)
        self.assertNotEqual(writes[0][0][0], writes[1][0][0])
        self.assertTrue(all(not write.cause_event_ids for _, write in writes))
        self.assertEqual(stdout, '888\n2\n')

    def test_latest_assignment_replaces_initializer_eligibility(self):
        with self.assertRaisesRegex(TypecheckError, 'latest write'):
            checked(self.program('var y: Float = div(1.0,0.0)\ny = mul(1e308,1e308)\n'
                'resolve y { DivideByZero { y = 0.0 } }\nreturn y', 'FloatOverflow', 'Float'))
