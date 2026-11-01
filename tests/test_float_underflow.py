"""Normal-or-zero Float domain, arithmetic failures, and external boundaries."""
import io
import math
import sys
import unittest
from contextlib import redirect_stdout
from unittest.mock import Mock, patch

import ginger.eval as evaluator
from ginger.ast import FloatLit
from ginger.builtin import BUILTINS
from ginger.core.failure_spec import FailureId
from ginger.errors import TypecheckError
from ginger.runtime.failures import FailureStatus
from ginger.runtime.results import EvalResult, Value
from test_failure_contract import checked


class FloatUnderflowTests(unittest.TestCase):
    def execute(self, source):
        program, diagnostics = checked(source)
        out = io.StringIO()
        with redirect_stdout(out):
            result = evaluator.eval_program(program)
        return result, diagnostics, out.getvalue()

    def test_literal_domain_and_source_spelling(self):
        for spelling in ['1.0', '1e-307', repr(sys.float_info.min), '0.0', '-0.0',
                         '0e-400', '-0e-400', '000.000E-999999', '0e400']:
            with self.subTest(spelling=spelling):
                result, diagnostics, _ = self.execute(f'var x: Float = {spelling}')
                self.assertEqual(result.environment['x'].value, float(spelling))
                self.assertEqual(diagnostics.items, [])
        for spelling in ['5e-324', '1e-400', '2.225073858507201e-308',
                         '0.' + '0' * 400 + '1']:
            for expr in [spelling, f'-{spelling}', f'thunk({spelling})']:
                with self.subTest(expr=expr), self.assertRaisesRegex(TypecheckError, 'normal or zero'):
                    checked(f'print({expr})')

    def test_exact_zero_including_sign(self):
        for expr, expected in [('mul(0.0,0.5)', 0.0), ('mul(-0.0,0.5)', -0.0),
                               ('sub(1.0,1.0)', 0.0), ('add(1e-307,-1e-307)', 0.0),
                               ('div(-0.0,1e308)', -0.0), ('neg(0.0)', -0.0)]:
            with self.subTest(expr=expr):
                result, _, _ = self.execute(f'var x: Float = {expr}')
                self.assertEqual(result.failure_history, ())
                self.assertEqual(math.copysign(1, result.environment['x'].value), math.copysign(1, expected))

    def test_all_operations_positive_negative_subnormal_and_rounded_zero(self):
        lo = sys.float_info.min
        hi = math.nextafter(lo, math.inf)
        cases = [('sub', hi, lo), ('sub', lo, hi), ('add', hi, -lo), ('add', lo, -hi),
                 ('mul', 1e-307, .01), ('mul', -1e-307, .01),
                 ('mul', 1e-307, 1e-307), ('mul', -1e-307, 1e-307),
                 ('div', lo, 2.0), ('div', -lo, 2.0),
                 ('div', 1e-307, 1e308), ('div', -1e-307, 1e308)]
        for op, a, b in cases:
            with self.subTest(op=op, a=a, b=b):
                result, diagnostics, stdout = self.execute(f'var x: Float = {op}({a!r},{b!r})\nprint(7)')
                self.assertIsInstance(result.environment['x'], evaluator.UninitializedBinding)
                self.assertEqual(stdout, '7\n')
                event, = result.failure_history
                self.assertEqual(event.failure_id, FailureId.FloatUnderflow)
                self.assertEqual(event.status, FailureStatus.UNRESOLVED)
                self.assertIsNone(result.value_contract_violation)
                self.assertIsNone(result.contract_violation)
                self.assertIn('FloatUnderflow', diagnostics.items[0].message)
                self.assertEqual(result.incomplete_statements[0].related_event_ids, (event.event_id,))

    def test_normal_boundary_results(self):
        for expr in [f'mul({sys.float_info.min!r},1.0)', f'div({sys.float_info.min!r},1.0)',
                     f'add({sys.float_info.min!r},0.0)', f'sub({sys.float_info.min!r},0.0)']:
            result, _, _ = self.execute(f'var x: Float = {expr}')
            self.assertEqual(result.environment['x'].value, sys.float_info.min)
            self.assertEqual(result.failure_history, ())

    def test_failed_write_preserves_old_value_and_explicit_recovery_history(self):
        result, _, out = self.execute('var x: Float = 9.0\nx = mul(1e-307,0.01)\nprint(x)\n'
            'resolve x { FloatUnderflow { x = 0.0 } }\nprint(x)')
        self.assertEqual(out, '9.0\n0.0\n')
        self.assertEqual(result.failure_history[0].status, FailureStatus.RESOLVED)
        self.assertEqual(result.unresolved_events, ())

    def test_generic_thunk_and_function_local_resolve(self):
        source = ('sig inner(U,U) -> U { require U guarantees Multipliable '
                  'failure IntegerOverflow when U guarantees BoundedArithmetic '
                  'failure FloatOverflow when U guarantees FiniteArithmetic '
                  'failure FloatUnderflow when U guarantees FiniteArithmetic }\n'
                  'func inner(a: U,b: U) { return force(thunk(mul(a,b))) }\n'
                  'sig safe(T,T) -> T { require T guarantees Multipliable failure Never }\n'
                  'func safe(a: T,b: T) { var x: T = inner(a,b)\n'
                  'resolve x { IntegerOverflow { x = a } FloatOverflow { x = a } '
                  'FloatUnderflow { x = a } }\nreturn x }\n'
                  'print(safe(1e-307,0.01))')
        result, diagnostics, out = self.execute(source)
        self.assertEqual(diagnostics.items, [])
        self.assertEqual(out, '1e-307\n')
        self.assertEqual(result.failure_history[0].status, FailureStatus.RESOLVED)
        self.assertIsNone(result.contract_violation)
        for frame in result.call_frames.values():
            if frame.function_name == 'inner':
                self.assertEqual(frame.declared_failure_contract,
                                 frozenset({FailureId.FloatOverflow, FailureId.FloatUnderflow}))

    def test_static_upper_bound_thunk_and_resolve_qualification(self):
        for source in [
            'sig f() -> Float { failure FloatOverflow }\nfunc f() { return mul(1.0,2.0) }',
            'var t: Thunk[Float]<FloatOverflow> = thunk(mul(1.0,2.0))',
            'var x: Float = neg(1.0)\nresolve x { FloatUnderflow { x = 0.0 } }',
            'sig f(T,T) -> T { require T guarantees Addable '
            'failure IntegerOverflow when T guarantees BoundedArithmetic '
            'failure FloatOverflow when T guarantees FiniteArithmetic }\n'
            'func f(a: T,b: T) { return add(a,b) }']:
            with self.subTest(source=source), self.assertRaisesRegex(TypecheckError, 'FloatUnderflow|contract|cannot resolve'):
                checked(source)

    def test_direct_builtin_alias_runtime_contract(self):
        for declared in ['', 'failure FloatUnderflow']:
            result, _, _ = self.execute('sig alias(Float,Float) -> Float { '+ declared +
                ' builtin core.float.mul }\nvar x: Float = alias(1e-307,0.01)')
            if declared:
                self.assertIsNone(result.contract_violation)
                self.assertEqual(result.failure_history[0].failure_id, FailureId.FloatUnderflow)
            else:
                self.assertEqual(result.contract_violation.failure_id, FailureId.FloatUnderflow)
                self.assertEqual(result.failure_history, ())

    def test_repeated_force_records_distinct_underflows(self):
        result, _, out = self.execute(
            'var t: Thunk[Float]<FloatOverflow, FloatUnderflow> = thunk(mul(1e-307,0.01))\n'
            'var x: Float = force(t)\nresolve x { FloatUnderflow { x = 0.0 } }\n'
            'print(x)\nvar y: Float = force(t)')
        self.assertEqual(out, '0.0\n')
        self.assertEqual([e.failure_id for e in result.failure_history], [FailureId.FloatUnderflow] * 2)
        self.assertEqual([e.status for e in result.failure_history],
                         [FailureStatus.RESOLVED, FailureStatus.UNRESOLVED])
        self.assertNotEqual(result.failure_history[0].event_id, result.failure_history[1].event_id)
        self.assertIsInstance(result.environment['y'], evaluator.UninitializedBinding)

    def test_custom_builtin_subnormal_never_reaches_consumers(self):
        for payload in [5e-324, -5e-324]:
            consumer = Mock()
            with patch.dict(BUILTINS, {'test.bad': lambda: payload, 'core.float.cmp': consumer}):
                result, _, out = self.execute('sig bad() -> Float { builtin test.bad }\n'
                    'var x: Float = bad()\nprint(cmp(x,1.0))')
            consumer.assert_not_called()
            self.assertEqual(out, '')
            self.assertEqual(result.value_contract_violation.reason, 'subnormal')
            self.assertEqual(result.failure_history, ())

    def test_subnormal_at_each_consumer_boundary(self):
        original = evaluator.eval_expr
        for payload in [5e-324, -5e-324]:
            def injected(expr, *args, **kwargs):
                if isinstance(expr, FloatLit) and expr.value == 42.0:
                    return EvalResult(Value(payload))
                return original(expr, *args, **kwargs)
            for source, boundary in [
                ('var x: Float = 42.0', 'binding_write'),
                ('print(42.0)', 'call_argument'),
                ('sig f(Float) -> Float {}\nfunc f(a: Float) { return a }\nvar x: Float = f(42.0)', 'call_argument'),
                ('sig f() -> Float {}\nfunc f() { return 42.0 }\nvar x: Float = f()', 'function_return'),
                ('var x: Float = force(thunk(42.0))', 'thunk')]:
                with self.subTest(boundary=boundary, payload=payload), patch('ginger.eval.eval_expr', side_effect=injected):
                    result, _, out = self.execute(source)
                self.assertEqual(out, '')
                self.assertEqual(result.value_contract_violation.reason, 'subnormal')
                self.assertEqual(result.value_contract_violation.boundary_kind, boundary)
                self.assertEqual(result.failure_history, ())
