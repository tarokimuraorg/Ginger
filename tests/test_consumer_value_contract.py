"""Consumer boundaries exercised through checked source and registered extensions."""
import io
import unittest
from dataclasses import replace
from contextlib import redirect_stdout
from unittest.mock import Mock, patch

import ginger.eval as evaluator
from ginger.ast import TypeRef
from ginger.builtin import BUILTINS
from ginger.core.failure_spec import FailureId
from ginger.lower import lower_program
from ginger.numeric import INT_MAX
from ginger.parser import parse
from ginger.runtime.results import NoValue


class ConsumerValueContractTests(unittest.TestCase):
    def run_source(self, source, extensions):
        # Host-side fault injection precedes the extension adapter. The actual
        # Python extension receives only handles, even in these corruption tests.
        bridge = evaluator.invoke_builtin
        def inject(implementation, args, *rest, **kwargs):
            if implementation in {'test.mutate', 'test.corrupt'}:
                returned = extensions[implementation](*args)
                callback = (lambda handle: handle) if returned is args[0] else (lambda handle: returned)
                with patch.dict(BUILTINS, {implementation: callback}):
                    return bridge(implementation, args, *rest, **kwargs)
            return bridge(implementation, args, *rest, **kwargs)
        out = io.StringIO()
        with patch('ginger.eval.invoke_builtin', side_effect=inject), patch.dict(BUILTINS, extensions), redirect_stdout(out):
            result = evaluator.eval_program(lower_program(parse(source)))
        self.assertEqual(out.getvalue(), '')
        return result

    def corrupt(self, typ, initial, payload, tail, extra='', metadata=True):
        def mutate(t):
            # Test-only host injection into the live mapping; capture is a separate snapshot.
            cell = t.env['x']
            live_env['x'] = evaluator.Cell(payload, cell.mutable,
                TypeRef('Box', (TypeRef('Float'),)) if metadata else cell.typ)
        source = (f'sig corrupt(Thunk[{typ}]<Never>) -> Unit {{ builtin test.corrupt }}\n'
                  f'var x: {typ} = {initial}\n'
                  f'var captured: Thunk[{typ}]<Never> = thunk(x)\n'
                  'corrupt(captured)\n' + extra + tail + '\nvar later: Int = 1')
        original = evaluator.eval_expr
        live_env = None
        def observe_env(expr, env, *args, **kwargs):
            nonlocal live_env
            live_env = env
            return original(expr, env, *args, **kwargs)
        with patch('ginger.eval.eval_expr', side_effect=observe_env):
            return self.run_source(source, {'test.corrupt': mutate})

    def assert_read(self, result, typ):
        v = result.value_contract_violation
        self.assertEqual(v.expected_type, TypeRef(typ))
        self.assertEqual(v.boundary_kind, 'value_read')
        self.assertEqual(result.failure_history, ())
        self.assertNotIn('later', result.environment)

    def test_nonfinite_consumers_and_no_side_effects(self):
        for payload in [float('nan'), float('inf'), float('-inf')]:
            for tail in ['var y: Bool = eq(x,1.0)', 'var y: Bool = lt(x,1.0)',
                         'var y: Ordering = cmp(x,1.0)', 'print(x)',
                         'var y: Float = neg(x)', 'var y: Float = add(x,1.0)',
                         'var y: Float = div(1.0,x)', 'var y: Float = x',
                         'var y: Float = 2.0\ny = x',
                         'var y: Float = identity(x)', 'observe(x)']:
                with self.subTest(payload=payload, tail=tail):
                    observer = Mock(return_value=None)
                    extra = ('sig identity(Float) -> Float {}\n'
                             'func identity(a: Float) { observe(a) return a }\n'
                             'sig observe(Float) -> Unit { builtin test.observe }\n')
                    with patch.dict(BUILTINS, {'test.observe': observer}):
                        result = self.corrupt('Float', '1.0', payload, tail, extra)
                    observer.assert_not_called()
                    self.assert_read(result, 'Float')
                    if 'y = x' in tail:
                        self.assertEqual(result.environment['y'].value, 2.0)
                    else:
                        self.assertNotIn('y', result.environment)

    def test_invalid_int_before_arithmetic_conversion_and_widening(self):
        for payload in [INT_MAX + 1, True, NoValue()]:
            for tail in ['var y: Int = add(x,(-1))', 'var y: Float = toFloat(x)',
                         'var y: Float = x', 'var y: Int64 = x',
                         'var y: Bool = eq(x,1)', 'print(x)']:
                with self.subTest(payload=payload, tail=tail):
                    self.assert_read(self.corrupt('Int', '1', payload, tail), 'Int')

    def test_other_standard_types(self):
        for typ, initial, payload in [('Bool', 'eq(1,1)', 1),
                                      ('Ordering', 'cmp(1,1)', ('Other','Bad')),
                                      ('Unit', 'seed()', 1)]:
            observer = Mock(return_value=None)
            extra = f'sig observe({typ}) -> Unit {{ builtin test.observe }}\n'
            with patch.dict(BUILTINS, {'test.observe': observer, 'test.seed': lambda: None}):
                result = self.corrupt(typ, initial, payload, 'observe(x)',
                                      'sig seed() -> Unit { builtin test.seed }\n' + extra)
            self.assert_read(result, typ)
            observer.assert_not_called()

    def test_captured_comparison_print_and_returned_thunk(self):
        for body, typ in [('eq(x,1.0)', 'Bool'), ('print(x)', 'Unit')]:
            for returned in [False, True]:
                with self.subTest(body=body, returned=returned):
                    def mutate(t):
                        t.env['x'] = replace(t.env['x'], value=float('nan'))
                        return t if returned else None
                    ret = f'Thunk[{typ}]<Never>' if returned else 'Unit'
                    tail = 'var u: ' + ret + ' = mutate(t)' if returned else 'mutate(t)'
                    source = (f'sig mutate(Thunk[{typ}]<Never>) -> {ret} {{ builtin test.mutate }}\n'
                              'var x: Float = 1.0\n'
                              f'var t: Thunk[{typ}]<Never> = thunk({body})\n' + tail +
                              f'\nvar y: {typ} = force(' + ('u' if returned else 't') + ')')
                    result = self.run_source(source, {'test.mutate': mutate})
                    self.assert_read(result, 'Float')

    def test_saved_contract_replacement_before_force_and_custom_call(self):
        # Inject a mismatched host-constructed Thunk after the identifier read.
        from ginger.ast import IdentExpr
        from ginger.runtime.results import Value
        original = evaluator.eval_expr
        def injected(expr, *args, **kwargs):
            result = original(expr, *args, **kwargs)
            if isinstance(expr, IdentExpr) and expr.name == 't':
                result = replace(result, value_result=Value(replace(
                    result.value_result.value,
                    potential_failure_contract={FailureId.FloatOverflow})))
            return result
        for tail, boundary in [('var y: Int = force(t)', 'force_argument'),
                               ('observe(t)', 'call_argument')]:
            observer = Mock(return_value=None)
            source = ('sig observe(Thunk[Int]<Never>) -> Unit { builtin test.observe }\n'
                      'var t: Thunk[Int]<Never> = thunk(1)\n' + tail)
            with patch('ginger.eval.eval_expr', side_effect=injected):
                result = self.run_source(source, {'test.observe': observer})
            self.assertEqual(result.value_contract_violation.reason, 'contract_mismatch')
            self.assertEqual(result.value_contract_violation.boundary_kind, boundary)
            self.assertEqual(result.failure_history, ())
            observer.assert_not_called()

    def test_generic_and_nested_capture(self):
        for typ, initial, payload in [('Float','1.0',float('nan')), ('Int','1',True)]:
            for nested in [False, True]:
                def mutate(t):
                    t.env['a'] = replace(t.env['a'], value=payload)
                source = (f'sig mutate(Thunk[Thunk[{typ}]<Never>]<Never>) -> Unit {{ builtin test.mutate }}\n'
                          'sig capture(T) -> Thunk[Thunk[T]<Never>]<Never> {}\n'
                          'func capture(a: T) { return thunk(thunk(a)) }\n'
                          f'var t: Thunk[Thunk[{typ}]<Never>]<Never> = capture({initial})\n'
                          'mutate(t)\n'
                          f'var inner: Thunk[{typ}]<Never> = force(t)\n'
                          f'var y: {typ} = force(inner)')
                if not nested:
                    source = source.replace('Thunk[Thunk[', 'Thunk[').replace(']<Never>]<Never>', ']<Never>')
                    source = source.replace('thunk(thunk(a))', 'thunk(a)')
                    source = source[:source.index('var inner:')] + f'var y: {typ} = force(t)'
                result = self.run_source(source, {'test.mutate': mutate})
                self.assert_read(result, typ)

    def test_reassignment_uses_checked_target_despite_replaced_metadata(self):
        result = self.corrupt('Int', '1', True, 'x = 2\nvar y: Int = x')
        self.assertIsNone(result.value_contract_violation)
        self.assertEqual(result.environment['x'].typ, TypeRef('Int'))
        self.assertEqual(result.environment['y'].value, 2)

    def test_resolve_recovery_preserves_failure_and_uninitialized_binding(self):
        result = self.corrupt('Float','1.0',float('nan'),
                             'var y: Float = div(1.0,0.0)\n'
                             'resolve y { DivideByZero { y = x } }')
        self.assertEqual(result.value_contract_violation.reason, 'non_finite')
        self.assertIsInstance(result.environment['y'], evaluator.UninitializedBinding)
        self.assertEqual(len(result.failure_history), 1)
        self.assertEqual(result.failure_history[0].status.value, 'unresolved')

    def test_open_world_remains_opaque_through_function_binding_and_argument(self):
        for typ in ['Box[Int]', 'Box[Float]', 'Pair[Int,Float]', 'MissingType',
                    'Box[MissingType]', 'Float[Int]']:
            payload = {'invalid': float('nan')}
            observer = Mock(return_value=None)
            source = (f'sig make() -> {typ} {{ builtin test.make }}\n'
                      'sig identity(T) -> T {}\nfunc identity(a: T) { return a }\n'
                      f'sig observe({typ}) -> Unit {{ builtin test.observe }}\n'
                      f'var x: {typ} = identity(make())\nx = identity(x)\nobserve(x)')
            result = self.run_source(source, {'test.make': lambda: payload, 'test.observe': observer})
            self.assertIsNone(result.value_contract_violation)
            observer.assert_called_once_with(payload)

    def test_arguments_revalidated_after_all_argument_expressions(self):
        # Test-only expression injection bypasses the earlier identifier validator.
        from ginger.ast import IdentExpr
        from ginger.runtime.results import Value
        original = evaluator.eval_expr
        later = Mock(return_value=None)
        def injected(expr, *args, **kwargs):
            result = original(expr, *args, **kwargs)
            if isinstance(expr, IdentExpr) and expr.name == 't':
                result = replace(result, value_result=Value(replace(
                    result.value_result.value,
                    potential_failure_contract={FailureId.FloatOverflow})))
            return result
        for user_function in [False, True]:
            observer = Mock(return_value=None)
            later.reset_mock()
            declaration = ('sig observe(Thunk[Int]<Never>,Unit) -> Unit {}\n'
                           'func observe(a: Thunk[Int]<Never>, b: Unit) { effect() }'
                           if user_function else
                           'sig observe(Thunk[Int]<Never>,Unit) -> Unit { builtin test.observe }')
            source = (declaration + '\nsig effect() -> Unit { builtin test.observe }\n'
                      'sig later() -> Unit { builtin test.later }\n'
                      'var t: Thunk[Int]<Never> = thunk(1)\nobserve(t,later())')
            with patch('ginger.eval.eval_expr', side_effect=injected):
                result = self.run_source(source, {'test.later': later, 'test.observe': observer})
            self.assertEqual(result.value_contract_violation.boundary_kind, 'call_argument')
            self.assertEqual(result.failure_history, ())
            later.assert_called_once_with()
            observer.assert_not_called()

    def test_each_preparation_boundary_with_fault_injected_expression(self):
        # Isolate boundaries otherwise masked by the earlier identifier read check.
        from ginger.ast import IntLit
        from ginger.runtime.results import EvalResult, Value
        original = evaluator.eval_expr
        def injected(expr, *args, **kwargs):
            if isinstance(expr, IntLit) and expr.value == 42:
                return EvalResult(Value(True))
            return original(expr, *args, **kwargs)
        cases = [
            ('var y: Float = 42', 'binding_write'),
            ('var y: Int64 = 42', 'binding_write'),
            ('var y: Bool = eq(42,1)', 'call_argument'),
            ('sig observe(Float) -> Unit { builtin test.observe }\nobserve(42)', 'call_argument'),
            ('sig f(Float) -> Float {}\nfunc f(a: Float) { return a }\nvar y: Float = f(42)', 'call_argument'),
            ('sig f() -> Float {}\nfunc f() { return 42 }\nvar y: Float = f()', 'function_return'),
        ]
        for source, boundary in cases:
            observer = Mock(return_value=None)
            with self.subTest(boundary=boundary, source=source), patch('ginger.eval.eval_expr', side_effect=injected):
                result = self.run_source(source, {'test.observe': observer})
            self.assertEqual(result.value_contract_violation.boundary_kind, boundary)
            self.assertEqual(result.value_contract_violation.expected_type, TypeRef('Int'))
            self.assertEqual(result.failure_history, ())
            self.assertNotIn('y', result.environment)
            observer.assert_not_called()

    def test_force_entry_checks_before_saved_expression(self):
        from ginger.ast import CallExpr
        original = evaluator.eval_expr
        def injected(expr, *args, **kwargs):
            result = original(expr, *args, **kwargs)
            if isinstance(expr, CallExpr) and expr.callee == 'thunk':
                from ginger.runtime.results import Value
                result = replace(result, value_result=Value(replace(
                    result.value_result.value,
                    potential_failure_contract={FailureId.FloatOverflow})))
            return result
        effect = Mock(return_value=None)
        with patch('ginger.eval.eval_expr', side_effect=injected):
            result = self.run_source('sig effect() -> Unit { builtin test.effect }\nforce(thunk(effect()))',
                                     {'test.effect': effect})
        self.assertEqual(result.value_contract_violation.boundary_kind, 'force_argument')
        self.assertEqual(result.value_contract_violation.reason, 'contract_mismatch')
        effect.assert_not_called()

    def test_nested_thunk_comparison_rejects_captured_nan(self):
        def mutate(t):
            t.env['x'] = replace(t.env['x'], value=float('nan'))
        result = self.run_source(
            'sig mutate(Thunk[Thunk[Bool]<Never>]<Never>) -> Unit { builtin test.mutate }\n'
            'var x: Float = 1.0\n'
            'var t: Thunk[Thunk[Bool]<Never>]<Never> = thunk(thunk(eq(x,1.0)))\n'
            'mutate(t)\nvar inner: Thunk[Bool]<Never> = force(t)\n'
            'var y: Bool = force(inner)', {'test.mutate': mutate})
        self.assertIn('inner', result.environment)
        self.assertNotIn('y', result.environment)
        self.assert_read(result, 'Float')

    def test_opaque_to_standard_return_remains_checked(self):
        source = ('sig make() -> Box[Float] { builtin test.make }\n'
                  'sig extract(Box[Float]) -> Float { builtin test.extract }\n'
                  'var y: Float = extract(make())')
        result = self.run_source(source, {'test.make': lambda: [float('nan')],
                                          'test.extract': lambda box: box[0]})
        self.assertEqual(result.value_contract_violation.boundary_kind, 'builtin')
        self.assertEqual(result.value_contract_violation.reason, 'non_finite')
        self.assertNotIn('y', result.environment)
