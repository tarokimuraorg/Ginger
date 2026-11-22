import io
import unittest
from contextlib import redirect_stdout

from ginger.errors import EvalError, TypecheckError
from ginger.eval import _eval_program_with_context, UninitializedBinding
from ginger.runtime.context import RuntimeContext
from ginger.runtime.failures import FailureStatus
from test_failure_contract import checked, recorded


class ResolveExpressionTests(unittest.TestCase):
    def run_source(self, source):
        return recorded(checked(source)[0])

    def test_success_skips_handler_and_recovery_reads_old_value(self):
        for operand, expected in [('div(4.0,2.0)', '2.0'), ('div(1.0,0.0)', '8.0')]:
            env, context, out = self.run_source('var x: Float = 8.0\nx = resolve ' + operand +
                ' { DivideByZero { return x\nprint(999) } }\nprint(x)')
            self.assertEqual(out, expected + '\n')
            self.assertTrue(all(e.status is FailureStatus.RESOLVED for e in context.failure_history))
            self.assertEqual(len(context.failure_history), int(expected == '8.0'))

    def test_no_return_continues_then_read_is_fatal(self):
        source = 'var x: Float = 8.0\nx = resolve div(1.0,0.0) { DivideByZero { print(3) } }\nprint(4)'
        env, context, out = self.run_source(source)
        self.assertEqual(out, '3\n4\n')
        self.assertIsInstance(env['x'], UninitializedBinding)
        self.assertEqual(env['x'].related_event_ids, (1,))
        self.assertEqual(context.get_event(1).status, FailureStatus.UNRESOLVED)
        context, env, stream = RuntimeContext(), {}, io.StringIO()
        with redirect_stdout(stream), self.assertRaisesRegex(EvalError, r'cause event IDs: \(1,\)'):
            _eval_program_with_context(checked(source + '\nprint(x)\nprint(999)')[0], context, env)
        self.assertEqual(stream.getvalue(), '3\n4\n')

    def test_failed_return_preserves_both_events(self):
        env, context, out = self.run_source('var x: Float = resolve div(1.0,0.0) {\n'
            'DivideByZero { return div(2.0,0.0)\nprint(999) } }\nprint(4)')
        self.assertEqual(out, '4\n')
        self.assertEqual(env['x'].related_event_ids, (1, 2))
        self.assertEqual(env['x'].cause_event_id, 2)
        self.assertEqual(context.get_event(2).caused_by, 1)
        self.assertTrue(all(e.status is FailureStatus.UNRESOLVED for e in context.failure_history))

    def test_missing_handler(self):
        env, context, _ = self.run_source('let x: Float = resolve div(1.0,0.0) {}')
        self.assertIsInstance(env['x'], UninitializedBinding)
        self.assertEqual(context.get_event(1).status, FailureStatus.UNRESOLVED)

    def test_nested_return_and_function_argument(self):
        source = '''sig calculate() -> Float { failure { DivideByZero, FloatOverflow, FloatUnderflow } }
func calculate() {
    let x: Float = resolve div(1.0,0.0) { DivideByZero {
        let y: Float = resolve div(2.0,0.0) { DivideByZero { return 2.0 } }
        print(3)
        return y
        print(999)
    } }
    print(4)
    return resolve div(x,0.0) { DivideByZero { return x } }
}
print(add(calculate(), resolve div(1.0,0.0) { DivideByZero { return 1.0 } }))'''
        _, context, out = self.run_source(source)
        self.assertEqual(out, '3\n4\n3.0\n')
        self.assertEqual(len(context.failure_history), 4)
        self.assertTrue(all(e.status is FailureStatus.RESOLVED for e in context.failure_history))

    def test_wrong_replacement_type_and_existing_widening(self):
        with self.assertRaisesRegex(TypecheckError, 'type mismatch'):
            checked('let x: Float = resolve div(1.0,0.0) { DivideByZero { return eq(1,1) } }')
        _, _, out = self.run_source('print(resolve div(1.0,0.0) { DivideByZero { return 2 } })')
        self.assertEqual(out, '2.0\n')

    def test_recovered_and_handler_failures_require_contract(self):
        for body, missing in [
            ('return resolve div(1.0,0.0) { DivideByZero { return 0.0 } }', 'DivideByZero'),
            ('return resolve div(1.0,0.0) { DivideByZero { print(add(1,2))\nreturn 0.0 } }', 'IntegerOverflow'),
        ]:
            failures = 'Never' if missing == 'DivideByZero' else 'DivideByZero, FloatOverflow, FloatUnderflow'
            with self.assertRaisesRegex(TypecheckError, missing):
                checked(f'sig f() -> Float {{ failure {{ {failures} }} }}\nfunc f() {{ {body} }}')

    def test_fatal_read_not_caught(self):
        with self.assertRaisesRegex(EvalError, 'uninitialized'):
            self.run_source('var x: Float = div(1.0,0.0)\n'
                'let y: Float = resolve x { DivideByZero { return 0.0 } }')

    def test_thunk_and_function_return_in_handler(self):
        _, context, out = self.run_source('sig f() -> Float { failure { Never } }\n'
            'func f() { return 7.0 }\n'
            'let t: Thunk[Float]<DivideByZero,FloatOverflow,FloatUnderflow> = '
            'thunk(resolve div(1.0,0.0) { DivideByZero { return f() } })\nprint(force(t))')
        self.assertEqual(out, '7.0\n')
        self.assertEqual(context.get_event(1).status, FailureStatus.RESOLVED)

    def test_independent_handler_failure_does_not_prevent_recovery(self):
        _, context, out = self.run_source('print(resolve div(1.0,0.0) { DivideByZero {\n'
            'print(div(2.0,0.0))\nreturn 5.0 } })')
        self.assertEqual(out, '5.0\n')
        self.assertEqual([e.status for e in context.failure_history],
                         [FailureStatus.RESOLVED, FailureStatus.UNRESOLVED])
        self.assertEqual(context.get_event(2).caused_by, 1)

    def test_prior_pending_is_not_resolved(self):
        _, context, out = self.run_source('sig f() -> Float { failure { DivideByZero,FloatOverflow,FloatUnderflow } }\n'
            'func f() { print(div(2.0,0.0))\nreturn 1.0 }\n'
            'print(resolve div(f(),0.0) { DivideByZero { return 9.0 } })')
        self.assertEqual(out, '9.0\n')
        self.assertEqual([e.status for e in context.failure_history],
                         [FailureStatus.UNRESOLVED, FailureStatus.RESOLVED])

    def test_generic_return(self):
        _, context, out = self.run_source('sig f(T,T) -> T { require T guarantees Addable '
            'failure { IntegerOverflow when T in BoundedArithmetic, FloatOverflow when T in FiniteArithmetic } }\n'
            'func f(a: T,b: T) { return resolve add(a,b) { IntegerOverflow { return a } FloatOverflow { return b } } }\n'
            'print(f(9007199254740991,1))')
        self.assertEqual(out, '9007199254740991\n')
        self.assertEqual(context.get_event(1).status, FailureStatus.RESOLVED)

    def test_handler_validation(self):
        for handlers in ['Unknown { return 0.0 }',
                         'DivideByZero { return 0.0 } DivideByZero { return 1.0 }']:
            with self.assertRaises(TypecheckError):
                checked('let x: Float = resolve div(1.0,0.0) { ' + handlers + ' }')
