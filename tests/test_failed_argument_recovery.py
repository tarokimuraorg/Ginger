"""Direct causes across user calls, without changing pending-event ownership."""
import unittest
from unittest.mock import patch

import ginger.eval as evaluator
from ginger.core.failure_spec import FailureId
from ginger.errors import EvalError, TypecheckError
from ginger.runtime.context import RuntimeContext
from ginger.runtime.failures import FailureStatus, FailureContractViolation
from ginger.runtime.results import EvalResult, NoValue, Value
from test_failure_contract import checked, recorded

IDENTITY = 'sig identity(Float) -> Float {}\nfunc identity(x: Float) { return x }\n'
IGNORE = 'sig ignore(Float) -> Float { failure { Never } }\nfunc ignore(x: Float) { return 10.0 }\n'
CONTRACT = 'sigtype CheckedInt() -> Int { failure { IntegerOverflow } }\n'
BINDING = 'var t: Thunk[CheckedInt] = thunk(add[Int](9007199254740991,1))\n'
THUNK = CONTRACT + BINDING
FAILED = 'add[Float](toFloat(force(t)),2.0)'
DIV_FAILURES = 'DivideByZero,FloatOverflow,FloatUnderflow'


class FailedArgumentRecoveryTests(unittest.TestCase):
    def run_source(self, source):
        return recorded(checked(source)[0])

    def assert_events(self, context, resolved=(), count=1):
        self.assertEqual(context.next_event_id, count + 1)
        self.assertEqual([e.event_id for e in context.failure_history], list(range(1, count + 1)))
        self.assertEqual([e.status for e in context.failure_history], [
            FailureStatus.RESOLVED if i in resolved else FailureStatus.UNRESOLVED
            for i in range(1, count + 1)])

    def test_builtin_and_identity_recover_original_overflow(self):
        for expr in (FAILED, f'identity({FAILED})'):
            with self.subTest(expr=expr):
                env, context, out = self.run_source(IDENTITY + THUNK + f'var x: Float = {expr}\n'
                    'resolve x { IntegerOverflow { x = 2.0 } }\nprint[Float](x)')
                self.assertEqual(out, '2.0\n')
                self.assertEqual(env['x'].value, 2.0)
                self.assert_events(context, (1,))
                event = context.get_event(1)
                self.assertEqual((event.failure_id, event.origin, event.call_id),
                                 (FailureId.IntegerOverflow, 'core.int.add', 1))
                self.assertIsNone(event.caused_by)
                self.assertTrue(all(not context.unresolved_pending(i) for i in context.call_frames))

    def test_identity_returns_no_value_and_records_failed_write(self):
        env, context, out = self.run_source(IDENTITY + THUNK + f'var x: Float = identity({FAILED})')
        self.assertIsInstance(env['x'], evaluator.UninitializedBinding)
        self.assertFalse(hasattr(env['x'], 'value'))
        self.assertEqual(env['x'].cause_event_id, 1)
        self.assertEqual(context._binding_writes[1, 'x'].cause_event_ids, (1,))
        self.assertEqual(context._binding_writes[1, 'x'].successful_writes, 0)
        self.assertEqual(out, '')
        self.assert_events(context)
        identity = next(f for f in context.call_frames.values() if f.function_name == 'identity')
        self.assertEqual(identity.pending_event_ids, ())
        self.assertEqual([(s.scope, s.statement_kind) for s in context.incomplete_statements],
                         [('identity', 'ReturnStmt'), ('<program>', 'VarDecl')])

    def test_ignore_success_and_resolve_noop(self):
        env, context, out = self.run_source(IGNORE + THUNK + f'var x: Float = ignore({FAILED})\n'
            'resolve x { IntegerOverflow { x = 2.0\nprint[Int](999) } }\nprint[Float](x)')
        self.assertEqual(out, '10.0\n')
        self.assertEqual(env['x'].value, 10.0)
        self.assertEqual(context._binding_writes[1, 'x'].cause_event_ids, ())
        self.assert_events(context)
        self.assertEqual(context.unresolved_pending(1), (1,))

    def test_later_argument_and_body_side_effect_order(self):
        source = ('sig mark() -> Float {}\nfunc mark() { print[Int](2) return 3.0 }\n'
                  'sig first(Float,Float) -> Float {}\n'
                  'func first(a: Float,b: Float) { print[Int](3) return a\nprint[Int](999) }\n'
                  'print[Int](1)\nvar x: Float = first(div(1.0,0.0),mark())\n'
                  'resolve x { DivideByZero { x = 4.0 } }\nprint[Float](x)')
        _, context, out = self.run_source(source)
        self.assertEqual(out, '1\n2\n3\n4.0\n')
        self.assert_events(context, (1,))

    def two_failures(self, name, expression=False):
        prefix = ('sig choose(Float,Float) -> Float {}\n'
                  f'func choose(a: Float,b: Float) {{ return {name} }}\n')
        call = 'choose(div(1.0,0.0),div(2.0,0.0))'
        suffix = (f'var x: Float = resolve {call} {{ DivideByZero {{ return 7.0 }} }}' if expression else
                  f'var x: Float = {call}\nresolve x {{ DivideByZero {{ x = 7.0 }} }}')
        return self.run_source(prefix + suffix + '\nprint[Float](x)')

    def test_first_failure_is_direct_cause(self):
        _, context, out = self.two_failures('a')
        self.assertEqual(out, '7.0\n')
        self.assert_events(context, (1,), 2)

    def test_second_failure_is_direct_cause(self):
        _, context, out = self.two_failures('b')
        self.assertEqual(out, '7.0\n')
        self.assert_events(context, (2,), 2)

    def test_expression_resolve_uses_selected_parameter_cause(self):
        for name, cause in [('a', 1), ('b', 2)]:
            with self.subTest(name=name):
                _, context, out = self.two_failures(name, expression=True)
                self.assertEqual(out, '7.0\n')
                self.assert_events(context, (cause,), 2)

    def test_successful_argument_pending_is_not_direct_cause(self):
        source = IGNORE + ('sig first(Float,Float) -> Float {}\n'
            'func first(a: Float,b: Float) { return a }\n'
            'var x: Float = first(div(1.0,0.0),ignore(div(2.0,0.0)))\n'
            'resolve x { DivideByZero { x = 7.0 } }\nprint[Float](x)')
        _, context, out = self.run_source(source)
        self.assertEqual(out, '7.0\n')
        self.assert_events(context, (1,), 2)

    def test_body_failure_does_not_replace_parameter_cause(self):
        source = (f'sig first(Float) -> Float {{ failure {{ {DIV_FAILURES} }} }}\n'
            'func first(a: Float) { print[Float](div(2.0,0.0)) return a }\n'
            'var x: Float = first(div(1.0,0.0))\n'
            'resolve x { DivideByZero { x = 7.0 } }')
        _, context, _ = self.run_source(source)
        self.assert_events(context, (1,), 2)
        frame = next(f for f in context.call_frames.values() if f.function_name == 'first')
        self.assertEqual(frame.pending_event_ids, (2,))
        self.assertEqual(context.get_event(2).call_id, frame.call_id)

    def test_multilevel_identity_retains_origin_and_frame_ownership(self):
        source = IDENTITY + ('sig outer(Float) -> Float {}\n'
            'func outer(x: Float) { return identity(x) }\n')
        _, context, out = self.run_source(source + THUNK + f'var x: Float = outer({FAILED})\n'
            'resolve x { IntegerOverflow { x = 2.0 } }\nprint[Float](x)')
        self.assertEqual(out, '2.0\n')
        self.assert_events(context, (1,))
        self.assertEqual(context.get_event(1).call_id, 1)
        self.assertTrue(all(not f.pending_event_ids for f in context.call_frames.values() if f.call_id != 1))

    def test_multilevel_ignore_keeps_success_and_pending(self):
        source = IGNORE + 'sig outer(Float) -> Float {}\nfunc outer(x: Float) { return ignore(x) }\n'
        _, context, out = self.run_source(source + THUNK + f'print[Float](outer({FAILED}))')
        self.assertEqual(out, '10.0\n')
        self.assert_events(context)

    def test_snapshot_read_in_same_frame_remains_fatal(self):
        source = ('sigtype DeferredFloat() -> Float { failure { Never } }\nsig capture(Float) -> Float {}\nfunc capture(x: Float) {\n'
                  'var t: Thunk[DeferredFloat] = thunk(x)\nreturn force(t) }\n'
                  'var result: Float = capture(div(1.0,0.0))')
        context = RuntimeContext()
        with self.assertRaisesRegex(EvalError, "uninitialized binding 'x'"):
            evaluator._eval_program_with_context(checked(source)[0], context)
        self.assert_events(context)
        self.assertIsNone(context.last_contract_violation)

    def test_resolved_parameter_read_remains_fatal(self):
        source = ('sig recover(Float) -> Float {}\nfunc recover(x: Float) {\n'
                  'var y: Float = resolve x { DivideByZero { return 3.0 } }\n'
                  'return x }\nvar result: Float = recover(div(1.0,0.0))')
        context = RuntimeContext()
        with self.assertRaisesRegex(EvalError, "uninitialized binding 'x'"):
            evaluator._eval_program_with_context(checked(source)[0], context)
        self.assert_events(context, (1,))

    def test_resolved_snapshot_does_not_restore_value(self):
        source = ('sigtype DeferredFloat() -> Float { failure { Never } }\nsig capture(Float) -> Thunk[DeferredFloat] {}\nfunc capture(x: Float) {\n'
                  'var t: Thunk[DeferredFloat] = thunk(x)\n'
                  'var y: Float = resolve x { DivideByZero { return 3.0 } }\nreturn t }\n'
                  'var t: Thunk[DeferredFloat] = capture(div(1.0,0.0))\nprint[Float](force(t))')
        context = RuntimeContext()
        with self.assertRaisesRegex(EvalError, "uninitialized binding 'x'"):
            evaluator._eval_program_with_context(checked(source)[0], context)
        self.assert_events(context, (1,))

    def test_failed_local_write_is_not_a_parameter(self):
        source = 'sig copy(Float) -> Float {}\nfunc copy(x: Float) { var y: Float = x\nreturn y }\n'
        context = RuntimeContext()
        with self.assertRaisesRegex(EvalError, "uninitialized binding 'y'"):
            evaluator._eval_program_with_context(checked(source + 'var result: Float = copy(div(1.0,0.0))')[0], context)
        self.assert_events(context)

    def test_wrapper_ignore_never_is_rejected(self):
        source = IGNORE + CONTRACT + ('sig wrapper() -> Float { failure { Never } }\nfunc wrapper() {\n' + BINDING +
            'var x: Float = ignore(toFloat(force(t)))\n'
            'resolve x { IntegerOverflow { x = 2.0 } }\nreturn x }\nvar result: Float = wrapper()')
        with self.assertRaisesRegex(TypecheckError, "func 'wrapper'.*IntegerOverflow"):
            checked(source)
        program, _ = checked(source.replace('sig wrapper() -> Float { failure { Never } }',
                            'sig wrapper() -> Float { failure { IntegerOverflow } }'))
        env, context, _ = recorded(program)
        self.assertEqual(env['result'].value, 10.0)
        self.assert_events(context)

    def test_static_safe_local_identity_resolve_matches_runtime(self):
        source = ('sig keep(Int) -> Int {}\nfunc keep(x: Int) { return x }\n'
            'sig safe() -> Int { failure { Never } }\nfunc safe() {\n'
            'var x: Int = keep(add[Int](9007199254740991,1))\n'
            'resolve x { IntegerOverflow { x = 2 } }\nreturn x }\nprint[Int](safe())')
        program, diagnostics = checked(source)
        self.assertEqual(diagnostics.items, [])
        _, context, out = recorded(program)
        self.assertEqual(out, '2\n')
        self.assert_events(context, (1,))

    def test_multiple_argument_effects_cannot_be_discharged_together(self):
        source = ('sig first(Int,Int) -> Int {}\nfunc first(a: Int,b: Int) { return a }\n'
            'sig wrapper() -> Int {}\nfunc wrapper() {\n'
            'var x: Int = first(add[Int](9007199254740991,1),add[Int](9007199254740991,1))\n'
            'resolve x { IntegerOverflow { x = 2 } }\nreturn x }')
        with self.assertRaisesRegex(TypecheckError, "func 'wrapper'.*IntegerOverflow"):
            checked(source)

    def test_runtime_caller_contract_still_validated(self):
        source = IDENTITY + (f'sig caller() -> Float {{ failure {{ {DIV_FAILURES} }} }}\n'
            'func caller() { return identity(div(1.0,0.0)) }\nvar x: Float = caller()')
        program, _ = checked(source)
        original = evaluator.instantiated_signature_failures
        def wrong_contract(syms, name, bindings):
            return frozenset() if name == 'caller' else original(syms, name, bindings)
        context = RuntimeContext()
        with patch.object(evaluator, 'instantiated_signature_failures', side_effect=wrong_contract):
            with self.assertRaises(FailureContractViolation) as caught:
                evaluator._eval_program_with_context(program, context)
        self.assertEqual(caught.exception.violating_function_or_builtin, 'caller')
        self.assertEqual(caught.exception.event_id, 1)
        self.assert_events(context)

    def test_handler_without_return_retains_original_cause(self):
        _, context, _ = self.run_source('var x: Float = resolve div(1.0,0.0) { DivideByZero {\n'
            'print[Float](div(2.0,0.0)) } }\nresolve x { DivideByZero { x = 3.0 } }')
        self.assert_events(context, (1,), 2)

    def test_failed_handler_return_uses_its_direct_cause(self):
        source = ('sig first(Float,Float) -> Float {}\nfunc first(a: Float,b: Float) { return a }\n'
            'var x: Float = resolve div(1.0,0.0) { DivideByZero {\n'
            'return first(div(2.0,0.0),div(3.0,0.0)) } }\n'
            'resolve x { DivideByZero { x = 4.0 } }')
        _, context, _ = self.run_source(source)
        self.assert_events(context, (2,), 3)
        self.assertEqual(context.get_event(2).caused_by, 1)
        self.assertEqual(context.get_event(3).caused_by, 1)

    def test_result_cause_is_explicit_and_validated(self):
        result = EvalResult(NoValue(), (1, 2), 1)
        self.assertEqual(result.cause_event_id, 1)
        self.assertEqual(EvalResult(Value(3), (1, 2)).cause_event_id, None)
        for value, ids, cause in [(Value(3), (1,), 1), (NoValue(), (2,), 1)]:
            with self.assertRaises(ValueError):
                EvalResult(value, ids, cause)
