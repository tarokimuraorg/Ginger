from test_failure_contract import recorded
from ginger.eval import UninitializedBinding
import unittest
from unittest.mock import patch

from test_failure_contract import checked, output
from ginger.ast import TypeRef
from ginger.builtin import BUILTINS, call_builtin
from ginger.core.failure_spec import FailureId, EMPTY_FAILURES, failures
from ginger.diagnostics import Diagnostics
from ginger.errors import TypecheckError
from ginger.eval import eval_program
from ginger.numeric import INT_MIN, INT_MAX
from ginger.runtime.failures import FailureContractViolation, RaisedFailure
from ginger.symbols_builder import build_symbols, normalize_types
from ginger.typecheck import effect_expr, typecheck_program


OVERFLOW = failures(FailureId.IntegerOverflow)
# Concrete parameter boundaries already support these calls. ReturnStmt itself
# must never provide a type argument to a generic call.
INT_IDENTITY = 'sig keepInt(Int) -> Int {}\nfunc keepInt(x: Int) { return x }\n'
FLOAT_IDENTITY = 'sig keepFloat(Float) -> Float {}\nfunc keepFloat(x: Float) { return x }\n'


def literal(value):
    return str(value) if value >= 0 else f'(-{-value})'


class IntegerOverflowTests(unittest.TestCase):
    def annotated(self, source):
        program, _ = checked(source)
        syms = build_symbols(program)
        program = normalize_types(program, syms.failuresets)
        diags = Diagnostics()
        bindings = typecheck_program(program, diags, syms=syms)
        return program, syms, bindings, diags

    def test_runtime_overflow_in_both_directions(self):
        cases = [('add', INT_MAX, 1), ('add', INT_MIN, -1),
                 ('sub', INT_MIN, 1), ('sub', INT_MAX, -1),
                 ('mul', INT_MAX, 2), ('mul', INT_MIN, 2),
                 ('mul', INT_MAX, -2), ('mul', INT_MIN, -2)]
        for op, a, b in cases:
            with self.subTest(op=op, a=a, b=b):
                source = f'var a: Int = {literal(a)}\nvar b: Int = {literal(b)}\nvar y: Int = {op}(a,b)\nprint(999)'
                program, diags = checked(source)
                self.assertEqual([d.message for d in diags], ['unhandled failures: IntegerOverflow'])
                env, context, stdout = recorded(program)
                self.assertIsInstance(env['y'], UninitializedBinding)
                self.assertEqual(stdout, '999\n')
                self.assertEqual(context.failure_history[0].failure_id, FailureId.IntegerOverflow)
                with self.assertRaises(RaisedFailure) as raised:
                    call_builtin(f'core.int.{op}', a, b)
                self.assertEqual(raised.exception.fid, FailureId.IntegerOverflow)

    def test_success_boundaries_and_result_types(self):
        for op, a, b, expected in [('add', 1, 2, 3), ('sub', 1, 2, -1), ('mul', 2, 3, 6),
                                  ('add', INT_MAX, 0, INT_MAX), ('add', INT_MIN, 0, INT_MIN),
                                  ('sub', INT_MIN, 0, INT_MIN), ('sub', INT_MAX, 0, INT_MAX),
                                  ('mul', INT_MAX, 1, INT_MAX), ('mul', INT_MIN, 1, INT_MIN)]:
            with self.subTest(op=op, a=a):
                program, _ = checked(f'var x: Int = {op}({literal(a)},{literal(b)})')
                cell = eval_program(program).environment['x']
                self.assertEqual(cell.value, expected)
                self.assertEqual(cell.typ, TypeRef('Int'))
                self.assertIs(type(cell.value), int)

    def test_negation_has_no_overflow_effect(self):
        for value in [INT_MIN, INT_MAX]:
            for expr in [f'neg({literal(value)})', '(-x)']:
                program, syms, bindings, diags = self.annotated(
                    f'var x: Int = {literal(value)}\nvar y: Int = {expr}')
                self.assertEqual(diags.items, [])
                self.assertEqual(effect_expr(program.items[-1].expr, bindings, syms), EMPTY_FAILURES)
                self.assertEqual(eval_program(program).environment['y'].value, -value)
        checked(INT_IDENTITY + 'sig f(Int) -> Int { failure Never }\n'
                'func f(x: Int) { return keepInt(neg(x)) }')

    def test_literal_overflow_remains_static(self):
        for typ, text in [('Int', '9007199254740992'), ('Int64', '9223372036854775808i64')]:
            for expr in [text, f'(-{text})']:
                with self.subTest(expr=expr), self.assertRaisesRegex(TypecheckError, 'literal out of range'):
                    checked(f'var x: {typ} = {expr}')

    def test_static_effect_specializes_on_int_impl(self):
        for op in ['add', 'sub', 'mul']:
            for typ, args, expected in [('Int', '1,2', OVERFLOW), ('Float', '1.0,2.0', EMPTY_FAILURES)]:
                program, syms, bindings, _ = self.annotated(f'var x: {typ} = {op}({args})')
                self.assertEqual(effect_expr(program.items[0].expr, bindings, syms), expected)
                self.assertEqual(syms.sig_failures[op], EMPTY_FAILURES)

    def test_function_upper_bound_and_transitive_effects(self):
        for op in ['add', 'sub', 'mul']:
            source = INT_IDENTITY + f'sig f(Int) -> Int {{ %s }}\nfunc f(x: Int) {{ return keepInt({op}(x,1)) }}'
            checked(source % 'failure IntegerOverflow')
            for contract in ['', 'failure Never', 'failure IOErr']:
                with self.subTest(op=op, contract=contract), self.assertRaisesRegex(
                        TypecheckError, 'undeclared failures: IntegerOverflow'):
                    checked(source % contract)
        prefix = INT_IDENTITY + 'sig f(Int) -> Int { failure IntegerOverflow }\nfunc f(x: Int) { return keepInt(add(x,1)) }\n'
        with self.assertRaisesRegex(TypecheckError, 'undeclared failures: IntegerOverflow'):
            checked(prefix + 'sig g(Int) -> Int {}\nfunc g(x: Int) { return f(x) }')
        checked('sig f(Int) -> Int { failure IntegerOverflow }\nfunc f(x: Int) { return x }')

    def test_catch_and_unhandled_propagation(self):
        prefix = INT_IDENTITY + 'sig f(Int) -> Int { failure IntegerOverflow }\nfunc f(x: Int) { return keepInt(add(x,1)) }\n'
        program, diags = checked(prefix + f'try print(f({INT_MAX}))\ncatch IntegerOverflow print(7)')
        self.assertEqual(diags.items, [])
        self.assertEqual(output(program), '7\n')
        program, _ = checked(prefix + f'print(f({INT_MAX}))')
        _, context, stdout = recorded(program)
        self.assertEqual(stdout, '')
        self.assertEqual(context.failure_history[0].failure_id, FailureId.IntegerOverflow)

    def test_float_runtime_effect_and_catch_eligibility(self):
        for op, result in [('add', 3.0), ('sub', -1.0), ('mul', 2.0)]:
            source = FLOAT_IDENTITY + f'sig f(Float) -> Float {{ failure Never }}\nfunc f(x: Float) {{ return keepFloat({op}(x,2.0)) }}\n'
            program, diags = checked(source + 'var x: Float = f(1.0)')
            self.assertEqual(diags.items, [])
            self.assertEqual(eval_program(program).environment['x'].value, result)
            with self.assertRaisesRegex(TypecheckError, 'no declared or inferred IntegerOverflow'):
                checked(source + 'try f(1.0)\ncatch IntegerOverflow print(0)')
        self.assertEqual(call_builtin('core.float.mul', 1e308, 2.0), float('inf'))

    def test_thunk_creation_latent_force_and_failureset(self):
        prefix = ('failureset ArithmeticFailure { IntegerOverflow }\n'
                  f'var t: Thunk[Int, ArithmeticFailure] = thunk(add({INT_MAX},1))\n')
        program, syms, bindings, diags = self.annotated(prefix)
        self.assertEqual(diags.items, [])
        self.assertEqual(bindings['t'].ty.latent_failures, OVERFLOW)
        self.assertEqual(effect_expr(program.items[-1].expr, bindings, syms), EMPTY_FAILURES)
        self.assertEqual(output(program), '')
        program, diags = checked(prefix + 'try force(t)\ncatch IntegerOverflow print(9)')
        self.assertEqual(diags.items, [])
        self.assertEqual(output(program), '9\n')
        _, diags = checked(prefix + 'var x: Int = force(t)')
        self.assertEqual([d.message for d in diags], ['unhandled failures: IntegerOverflow'])
        with self.assertRaisesRegex(TypecheckError, 'type mismatch'):
            checked(f'var t: Thunk[Int, Never] = thunk(add({INT_MAX},1))')
        checked(INT_IDENTITY + 'failureset ArithmeticFailure { IntegerOverflow }\n'
                'sig f(Int) -> Int { failure ArithmeticFailure }\nfunc f(x: Int) { return keepInt(add(x,1)) }')

    def test_widening_is_pure_but_propagates_argument_failure(self):
        program, diags = checked('var x: Int = 1\nvar y: Int64 = x\nvar z: Float = x')
        self.assertEqual(diags.items, [])
        env = eval_program(program).environment
        self.assertEqual(env['y'].typ, TypeRef('Int64'))
        self.assertIs(type(env['z'].value), float)
        prefix = INT_IDENTITY + 'sig f(Int) -> Int { failure IntegerOverflow }\nfunc f(x: Int) { return keepInt(add(x,1)) }\n'
        for typ in ['Int64', 'Float']:
            program, _ = checked(prefix + f'var x: {typ} = f({INT_MAX})')
            self.assertIsInstance(eval_program(program).environment['x'], UninitializedBinding)

    def test_int64_arithmetic_stays_disabled(self):
        for op in ['add', 'sub', 'mul', 'div']:
            with self.subTest(op=op), self.assertRaises(TypecheckError):
                checked(f'var x: Int64 = {op}(1i64,2i64)')

    def test_custom_direct_builtin_contract_remains_trusted(self):
        source = 'sig custom() -> Int { builtin test.custom }\nvar x: Int = custom()'
        with patch.dict(BUILTINS, {'test.custom': lambda: INT_MAX + 1}):
            program, diags = checked(source)
            self.assertEqual(diags.items, [])
            self.assertEqual(eval_program(program).environment['x'].value, INT_MAX + 1)

    def test_user_function_override_does_not_inherit_builtin_effect(self):
        program, diags = checked('func add(a: T,b: T) { return a }\nvar x: Int = add(1,2)')
        self.assertEqual(diags.items, [])
        self.assertEqual(eval_program(program).environment['x'].value, 1)

    def test_direct_float_catch_rejected_but_argument_effect_preserved(self):
        identity = 'sig identity(Float) -> Float {}\nfunc identity(x: Float) { return x }\n'
        for op in ['add', 'sub', 'mul']:
            with self.subTest(op=op), self.assertRaisesRegex(
                    TypecheckError, 'no declared or inferred IntegerOverflow'):
                checked(identity + f'try identity({op}(1.0,2.0))\ncatch IntegerOverflow print(0)')
        prefix = f'var t: Thunk[Int, IntegerOverflow] = thunk(add({INT_MAX},1))\n'
        program, diags = checked(identity + prefix +
                                 'try identity(add(toFloat(force(t)),2.0))\ncatch IntegerOverflow print(8)')
        self.assertEqual(diags.items, [])
        self.assertEqual(output(program), '8\n')

    def test_failed_initialization_and_assignment_do_not_store_result(self):
        import ginger.eval as evaluator
        for statement in ['var y: Int = add(x,1)', 'y = add(x,1)']:
            program, _ = checked(f'var x: Int = {INT_MAX}\nvar y0: Int = 5\n'
                                 + ('var y: Int = 7\n' if statement.startswith('y =') else '')
                                 + statement)
            environments = []
            original = evaluator.eval_expr
            def observe(expr, env, syms, outer=None):
                environments.append(env)
                return original(expr, env, syms, outer)
            with patch('ginger.eval.eval_expr', side_effect=observe):
                eval_program(program)
            env = environments[0]
            self.assertEqual(env['x'].value, INT_MAX)
            if statement.startswith('y ='):
                self.assertEqual(env['y'].value, 7)
            else:
                self.assertIsInstance(env['y'], UninitializedBinding)

    def test_direct_builtin_alias_still_needs_its_own_failure_declaration(self):
        source = ('sig alias(Int,Int) -> Int { builtin core.int.add }\n'
                  f'var x: Int = alias({INT_MAX},1)')
        program, diags = checked(source)
        self.assertEqual(diags.items, [])
        result = eval_program(program)
        self.assertEqual(result.contract_violation.failure_id, FailureId.IntegerOverflow)

    def test_return_infers_from_arguments_only(self):
        for typ, arg in [('Int', '1'), ('Float', '1.0')]:
            for op in ['add', 'sub', 'mul', 'neg']:
                args = arg if op == 'neg' else f'{arg},{arg}'
                source = (f'sig f() -> {typ} {{ failure IntegerOverflow }}\n'
                          f'func f() {{ return {op}({args}) }}')
                with self.subTest(typ=typ, op=op):
                    checked(source)
        with self.assertRaisesRegex(TypecheckError, "cannot determine type variable 'T'"):
            checked('sig make() -> T {}\nsig f() -> Int {}\nfunc f() { return make() }')
