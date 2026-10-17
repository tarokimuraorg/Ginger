import io
import math
import sys
import unittest
from contextlib import redirect_stdout
from unittest.mock import Mock, patch

from test_failure_contract import checked
from ginger.ast import TypeRef
from ginger.builtin import BUILTINS, builtin_failure_contract
from ginger.core.failure_spec import EMPTY_FAILURES, FailureId, failures
from ginger.errors import TypecheckError
from ginger.eval import UninitializedBinding, eval_program
from ginger.numeric import INT_MIN, INT_MAX
from ginger.runtime.builtin_bridge import invoke_builtin
from ginger.runtime.context import RuntimeContext
from ginger.runtime.failures import FailureStatus
from ginger.runtime.results import NoValue
from ginger.runtime.value_contract import ValueContractViolation
from ginger.symbols_builder import build_symbols


FLOAT_OVERFLOW = failures(FailureId.FloatOverflow)
MAX = sys.float_info.max


def literal(value):
    # The language has decimal literals, without exponent notation.
    text = format(abs(value), '.1074f').rstrip('0')
    if text.endswith('.'):
        text += '0'
    return f'(-{text})' if math.copysign(1, value) < 0 else text


class FloatFiniteTests(unittest.TestCase):
    def execute(self, source):
        program, diagnostics = checked(source)
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            result = eval_program(program)
        return result, diagnostics, stdout.getvalue()

    def test_finite_literals_include_max_subnormal_and_signed_zero(self):
        for value in [MAX, -MAX, 5e-324, -5e-324, 0.0, -0.0]:
            with self.subTest(value=value):
                result, diagnostics, _ = self.execute(f'var x: Float = {literal(value)}')
                actual = result.environment['x'].value
                self.assertIs(type(actual), float)
                self.assertTrue(math.isfinite(actual))
                self.assertEqual(actual, value)
                self.assertEqual(math.copysign(1, actual), math.copysign(1, value))
                self.assertEqual(diagnostics.items, [])

    def test_nonfinite_decimal_literals_rejected_before_execution(self):
        huge = '9' * 400 + '.0'
        for expression in [huge, f'(-{huge})', f'cmp({huge},1.0)', f'thunk({huge})']:
            with self.subTest(expression=expression), self.assertRaisesRegex(TypecheckError, 'Float literal must be finite'):
                checked(f'print({expression})')

    def test_nan_inf_remain_identifiers_not_literals(self):
        for name in ['nan', 'inf', 'infinity', 'NaN']:
            with self.subTest(name=name), self.assertRaises(TypecheckError):
                checked(f'var x: Float = {name}')
            result, _, _ = self.execute(f'var {name}: Float = 1.0\nvar x: Float = {name}')
            self.assertEqual(result.environment['x'].value, 1.0)

    def test_custom_nonfinite_return_is_fatal_and_cannot_reach_comparison_or_resolve(self):
        source = ('sig bad() -> Float { failure FloatOverflow builtin test.bad }\n'
                  'var before: Int = 7\nvar x: Float = bad()\n'
                  'resolve x { FloatOverflow { x = 0.0 } }\nprint(cmp(x,1.0))')
        for value in [float('nan'), float('inf'), float('-inf')]:
            comparison = Mock()
            with self.subTest(value=value), patch.dict(BUILTINS, {'test.bad': lambda: value,
                                                                'core.float.cmp': comparison}):
                result, _, stdout = self.execute(source)
            comparison.assert_not_called()
            self.assertEqual(stdout, '')
            self.assertEqual(result.environment['before'].value, 7)
            self.assertNotIn('x', result.environment)
            self.assertEqual(result.value_contract_violation.reason, 'non_finite')
            self.assertIsNone(result.contract_violation)
            self.assertEqual(result.failure_history, ())
            self.assertEqual(result.unresolved_events, ())

    def test_normal_arithmetic_results(self):
        for operation, a, b, expected in [('add', 1.0, 2.0, 3.0), ('sub', 1.0, 2.0, -1.0),
                                         ('mul', 2.0, 3.0, 6.0), ('div', 1.0, 2.0, 0.5)]:
            with self.subTest(operation=operation):
                result, _, _ = self.execute(f'var x: Float = {operation}({literal(a)},{literal(b)})')
                self.assertEqual(result.environment['x'].value, expected)
                self.assertEqual(result.failure_history, ())

    def test_overflow_both_directions_has_event_no_value_and_continuation(self):
        for operation, a, b in [('add', MAX, MAX), ('add', -MAX, -MAX),
                                 ('sub', MAX, -MAX), ('sub', -MAX, MAX),
                                 ('mul', MAX, 2.0), ('mul', -MAX, 2.0),
                                 ('div', MAX, 5e-324), ('div', -MAX, 5e-324)]:
            with self.subTest(operation=operation, a=a):
                context = RuntimeContext()
                frame = context.create_call('test')
                outcome = invoke_builtin(f'core.float.{operation}', [a, b], FLOAT_OVERFLOW,
                                         context, frame.call_id, return_type=TypeRef('Float'))
                self.assertIsInstance(outcome.value_result, NoValue)
                self.assertEqual(outcome.related_event_ids, (1,))
                result, _, stdout = self.execute(
                    f'var x: Float = {operation}({literal(a)},{literal(b)})\nprint(7)')
                self.assertIsInstance(result.environment['x'], UninitializedBinding)
                self.assertEqual(stdout, '7\n')
                event, = result.failure_history
                self.assertEqual(event.failure_id, FailureId.FloatOverflow)
                self.assertEqual(event.origin, f'core.float.{operation}')
                self.assertEqual(event.status, FailureStatus.UNRESOLVED)
                self.assertEqual(result.unresolved_events, (event,))
                self.assertEqual(result.incomplete_statements[0].related_event_ids, (event.event_id,))
                self.assertIsNone(result.value_contract_violation)
                self.assertIsNone(result.contract_violation)

    def test_division_signed_zero_has_priority(self):
        for zero in [0.0, -0.0]:
            result, _, _ = self.execute(f'var x: Float = div({literal(MAX)},{literal(zero)})')
            self.assertEqual([e.failure_id for e in result.failure_history], [FailureId.DivideByZero])
            self.assertIsInstance(result.environment['x'], UninitializedBinding)

    def test_invalid_external_operands_are_not_numeric_overflow(self):
        for operation in ['add', 'sub', 'mul', 'div']:
            for value in [float('nan'), float('inf'), float('-inf')]:
                for operands in [(value, 1.0), (1.0, value)] if operation != 'div' else [(value, 1.0)]:
                    with self.subTest(operation=operation, operands=operands):
                        context = RuntimeContext()
                        frame = context.create_call('external')
                        with self.assertRaises(ValueContractViolation):
                            invoke_builtin(f'core.float.{operation}', list(operands), FLOAT_OVERFLOW,
                                           context, frame.call_id, return_type=TypeRef('Float'))
                        self.assertEqual(context.failure_history, ())

    def test_underflow_and_rounding_are_valid(self):
        for expression, expected in [('mul(' + literal(5e-324) + ',0.5)', 0.0),
                                     ('mul(' + literal(-5e-324) + ',0.5)', -0.0),
                                     ('add(0.1,0.2)', 0.1 + 0.2)]:
            result, _, _ = self.execute(f'var x: Float = {expression}')
            self.assertEqual(result.environment['x'].value, expected)
            self.assertEqual(math.copysign(1, result.environment['x'].value), math.copysign(1, expected))
            self.assertIsNone(result.value_contract_violation)
            self.assertEqual(result.failure_history, ())

    def test_neg_and_int_conversions_have_empty_contracts(self):
        for value in [MAX, -MAX, 5e-324, -0.0]:
            result, diagnostics, _ = self.execute(f'var x: Float = neg({literal(value)})')
            self.assertEqual(result.environment['x'].value, -value)
            self.assertEqual(diagnostics.items, [])
        for value in [INT_MIN, INT_MAX]:
            text = str(value) if value >= 0 else f'(-{-value})'
            result, diagnostics, _ = self.execute(f'var i: Int = {text}\nvar x: Float = i\nvar y: Float = toFloat(i)')
            self.assertEqual(diagnostics.items, [])
            for name in ['x', 'y']:
                self.assertIs(type(result.environment[name].value), float)
                self.assertTrue(math.isfinite(result.environment[name].value))
        for implementation in ['core.float.neg', 'core.int.toFloat']:
            self.assertEqual(builtin_failure_contract(EMPTY_FAILURES, implementation, direct_builtin=False), EMPTY_FAILURES)

    def test_marker_registration_is_independent_of_capabilities(self):
        program, _ = checked('')
        syms = build_symbols(program)
        self.assertEqual(syms.guarantees['FiniteArithmetic'].methods, [])
        for name in ['Float', 'Int', 'Int64']:
            self.assertEqual('FiniteArithmetic' in syms.type_guarantees[name], name == 'Float')
            self.assertEqual('BoundedArithmetic' in syms.type_guarantees[name], name == 'Int')
        for operation, guarantee in [('add', 'Addable'), ('sub', 'Subtractable'), ('mul', 'Multipliable')]:
            with self.assertRaisesRegex(TypecheckError, 'does not guarantee ' + guarantee):
                checked('register Int64 guarantees FiniteArithmetic\n' + f'var x: Int64 = {operation}(1i64,2i64)')

    def test_direct_alias_does_not_inherit_implementation_contract(self):
        for operation, a, b in [('add', MAX, MAX), ('sub', MAX, -MAX),
                                 ('mul', MAX, 2.0), ('div', MAX, 5e-324)]:
            for declaration in ['', 'failure FloatOverflow']:
                result, _, _ = self.execute(
                    f'sig alias(Float,Float) -> Float {{ {declaration} builtin core.float.{operation} }}\n'
                    f'var x: Float = alias({literal(a)},{literal(b)})')
                if declaration:
                    self.assertIsNone(result.contract_violation)
                    self.assertEqual(result.failure_history[0].failure_id, FailureId.FloatOverflow)
                else:
                    self.assertEqual(result.contract_violation.failure_id, FailureId.FloatOverflow)
                    self.assertEqual(result.failure_history, ())

    def test_nested_generic_thunk_overflow_keeps_concrete_frame_contracts(self):
        source = (
            'sig inner(U,U) -> U { require U guarantees Addable '
            'failure IntegerOverflow when U guarantees BoundedArithmetic '
            'failure FloatOverflow when U guarantees FiniteArithmetic }\n'
            'func inner(a: U,b: U) { return force(thunk(add(a,b))) }\n'
            'sig outer(T,T) -> T { require T guarantees Addable '
            'failure IntegerOverflow when T guarantees BoundedArithmetic '
            'failure FloatOverflow when T guarantees FiniteArithmetic }\n'
            'func outer(a: T,b: T) { return inner(a,b) }\n'
            f'var x: Float = outer({literal(MAX)},{literal(MAX)})\n'
            'resolve x { FloatOverflow { x = 0.0 } }\nprint(x)')
        result, diagnostics, stdout = self.execute(source)
        self.assertEqual(diagnostics.items, [])
        self.assertEqual(stdout, '0.0\n')
        self.assertIsNone(result.contract_violation)
        self.assertEqual(result.failure_history[0].status, FailureStatus.RESOLVED)
        for frame in result.call_frames.values():
            if frame.function_name in ['inner', 'outer']:
                self.assertEqual(frame.declared_failure_contract, FLOAT_OVERFLOW)

    def test_generic_body_requires_both_symbolic_clauses(self):
        for clause, missing in [
                ('failure IntegerOverflow when T guarantees BoundedArithmetic', 'FloatOverflow'),
                ('failure FloatOverflow when T guarantees FiniteArithmetic', 'IntegerOverflow')]:
            with self.assertRaisesRegex(TypecheckError, 'undeclared failures: ' + missing):
                checked(f'sig f(T,T) -> T {{ require T guarantees Addable {clause} }}\n'
                        'func f(a: T,b: T) { return add(a,b) }')

    def test_resolve_and_repeated_thunk_force_recover_overflow(self):
        for operation, a, b in [('add', MAX, MAX), ('sub', MAX, -MAX),
                                 ('mul', MAX, 2.0), ('div', MAX, 5e-324)]:
            contract = 'FloatOverflow, DivideByZero' if operation == 'div' else 'FloatOverflow'
            expression = f'{operation}({literal(a)},{literal(b)})'
            for delayed in [False, True]:
                prefix = f'var t: Thunk[Float]<{contract}> = thunk({expression})\n' if delayed else ''
                expr = 'force(t)' if delayed else expression
                result, _, stdout = self.execute(prefix + f'var x: Float = {expr}\n'
                    'resolve x { FloatOverflow { x = 0.0 } }\nprint(x)\n' + f'var y: Float = {expr}')
                self.assertEqual(stdout, '0.0\n')
                self.assertEqual([e.status for e in result.failure_history],
                                 [FailureStatus.RESOLVED, FailureStatus.UNRESOLVED])
                self.assertIsInstance(result.environment['y'], UninitializedBinding)
                if delayed:
                    expected = FLOAT_OVERFLOW | (failures(FailureId.DivideByZero) if operation == 'div' else EMPTY_FAILURES)
                    self.assertEqual(result.environment['t'].value.potential_failure_contract, expected)


if __name__ == '__main__':
    unittest.main()
