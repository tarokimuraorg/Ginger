import itertools
import unittest
from unittest.mock import patch

from test_failure_contract import checked, output
from ginger.ast import TypeRef
from ginger.builtin import BUILTINS
from ginger.core.failure_spec import FailureId, EMPTY_FAILURES, failures
from ginger.diagnostics import Diagnostics
from ginger.errors import TypecheckError
from ginger.eval import eval_program, eval_block
from ginger.numeric import INT_MAX
from ginger.runtime.failures import RaisedFailure
from ginger.symbols_builder import build_symbols, normalize_types
from ginger.typecheck import effect_expr, reconcile_type_evidence, typecheck_program


IDENTITY = 'sig identity(T) -> T {}\nfunc identity(x: T) { return x }\n'
FIRST = 'sig first(T,T) -> T {}\nfunc first(a: T,b: T) { return a }\n'
OVERFLOW = failures(FailureId.IntegerOverflow)


class CallInferenceTests(unittest.TestCase):
    def annotated(self, source):
        program, _ = checked(source)
        syms = build_symbols(program)
        program = normalize_types(program, syms.failuresets)
        diags = Diagnostics()
        bindings = typecheck_program(program, diags, syms=syms)
        return program, syms, bindings, diags

    def test_exact_inference_and_expression_types(self):
        cases = [('identity(1)', 'Int', 1), ('identity(1.0)', 'Float', 1.0),
                 ('identity(1i64)', 'Int64', 1), ('add(1,2)', 'Int', 3),
                 ('add(1.0,2.0)', 'Float', 3.0), ('neg(1)', 'Int', -1),
                 ('neg(1.0)', 'Float', -1.0), ('(-1)', 'Int', -1)]
        for expr, name, value in cases:
            with self.subTest(expr=expr):
                program, syms, _, _ = self.annotated(IDENTITY + f'var x: {name} = {expr}')
                call = program.items[-1].expr
                self.assertEqual(syms.expression_types[id(call)], TypeRef(name))
                self.assertEqual(syms.resolved_calls[id(call)].return_type, TypeRef(name))
                cell = eval_program(program)['x']
                self.assertEqual(cell.value, value)
                self.assertIs(type(cell.value), type(value))

    def test_print_without_expected_type(self):
        for expr, expected, effects in [('add(1,2)', '3\n', OVERFLOW),
                                        ('add(1.0,2.0)', '3.0\n', EMPTY_FAILURES),
                                        ('add(1,2.0)', '3.0\n', EMPTY_FAILURES),
                                        ('add(1.0,2)', '3.0\n', EMPTY_FAILURES),
                                        ('neg(1)', '-1\n', EMPTY_FAILURES),
                                        ('neg(1.0)', '-1.0\n', EMPTY_FAILURES),
                                        ('(-1)', '-1\n', EMPTY_FAILURES)]:
            with self.subTest(expr=expr):
                program, syms, bindings, _ = self.annotated(f'print({expr})')
                self.assertEqual(effect_expr(program.items[-1].expr, bindings, syms), effects)
                self.assertEqual(output(program), expected)
        with self.assertRaises(SyntaxError):
            checked('print(-1)')

    def test_mixed_arguments_share_static_runtime_and_effect_resolution(self):
        for op in ['add', 'sub', 'mul']:
            for args in ['1,2.0', '1.0,2']:
                with self.subTest(op=op, args=args):
                    program, syms, bindings, diags = self.annotated(f'var x: Float = {op}({args})')
                    call = program.items[0].expr
                    resolution = syms.resolved_calls[id(call)]
                    self.assertEqual(resolution.type_bindings, {'T': TypeRef('Float')})
                    self.assertEqual(resolution.parameter_types, (TypeRef('Float'),) * 2)
                    self.assertEqual(resolution.implementation, f'core.float.{op}')
                    self.assertEqual(effect_expr(call, bindings, syms), EMPTY_FAILURES)
                    self.assertEqual(diags.items, [])
                    seen = []
                    original = BUILTINS[f'core.float.{op}']
                    def inspect(a, b):
                        seen.extend([type(a), type(b)])
                        return original(a, b)
                    with patch.dict(BUILTINS, {f'core.float.{op}': inspect,
                                              f'core.int.{op}': lambda *args: self.fail('wrong implementation')}):
                        eval_program(program)
                    self.assertEqual(seen, [float, float])

    def test_expected_type_only_applies_after_call_resolution(self):
        for target, expr, impl in [('Float', 'add(1,2)', 'core.int.add'),
                                   ('Float', 'identity(1)', None),
                                   ('Int64', 'identity(1)', None)]:
            program, syms, bindings, _ = self.annotated(IDENTITY + f'var x: {target} = {expr}')
            call = program.items[-1].expr
            self.assertEqual(syms.expression_types[id(call)], TypeRef('Int'))
            self.assertEqual(syms.resolved_calls[id(call)].type_bindings, {'T': TypeRef('Int')})
            self.assertEqual(syms.resolved_calls[id(call)].implementation, impl)
            self.assertEqual(bindings['x'].ty, TypeRef(target))
            self.assertEqual(effect_expr(call, bindings, syms), OVERFLOW if impl else EMPTY_FAILURES)
            cell = eval_program(program)['x']
            self.assertEqual(cell.typ, TypeRef(target))
            self.assertIs(type(cell.value), float if target == 'Float' else int)
        with self.assertRaisesRegex(TypecheckError, "type mismatch: expected TypeRef\\(name='Int'.*got TypeRef\\(name='Float'"):
            checked('var x: Int = add(1,2.0)')
        program, _ = checked(f'var x: Float = add({INT_MAX},1)')
        with self.assertRaises(RaisedFailure) as raised:
            eval_program(program)
        self.assertEqual(raised.exception.fid, FailureId.IntegerOverflow)

    def test_unresolved_variables_not_filled_from_any_outer_boundary(self):
        for declaration, call in [('sig make() -> T {}', 'make()'),
                                  ('sig strange(Int) -> T {}', 'strange(1)'),
                                  ('sig make() -> SomeType[T] {}', 'make()')]:
            for context in [f'var x: Int = {call}', f'print({call})',
                            f'sig f() -> Int {{}}\nfunc f() {{ return {call} }}',
                            f'sig f() -> Int {{}}\nfunc f() {{ let x: Int = {call}\nreturn x }}',
                            f'var t: Thunk[Int, Never] = thunk({call})']:
                with self.subTest(declaration=declaration, context=context), self.assertRaisesRegex(
                        TypecheckError, "cannot determine type variable 'T'"):
                    checked(declaration + '\n' + context)

    def test_observed_candidates_only_and_order_independent(self):
        for names, result in [(('Int', 'Int'), 'Int'), (('Int', 'Float'), 'Float'),
                              (('Int', 'Int64'), 'Int64'), (('Int', 'Int', 'Float'), 'Float')]:
            for order in set(itertools.permutations(names)):
                self.assertEqual(reconcile_type_evidence('T', [TypeRef(t) for t in order]), TypeRef(result))
        for order in itertools.permutations(['Int', 'Int64', 'Float']):
            with self.assertRaisesRegex(TypecheckError, 'cannot reconcile'):
                reconcile_type_evidence('T', [TypeRef(t) for t in order])
        with patch('ginger.typecheck.can_widen', return_value=True), self.assertRaisesRegex(TypecheckError, 'ambiguous evidence'):
            reconcile_type_evidence('T', [TypeRef('Int'), TypeRef('Float')])

    def test_int64_unification_then_guarantee_validation(self):
        for args in ['1,2i64', '1i64,2']:
            program, syms, _, _ = self.annotated(FIRST + f'var x: Int64 = first({args})')
            self.assertEqual(syms.resolved_calls[id(program.items[-1].expr)].type_bindings['T'], TypeRef('Int64'))
            self.assertEqual(eval_program(program)['x'].value, 1)
            with self.assertRaisesRegex(TypecheckError, 'Int64.*does not guarantee Addable'):
                checked(f'print(add({args}))')
        for args in ['1i64,2.0', '1.0,2i64']:
            with self.assertRaisesRegex(TypecheckError, "cannot reconcile Float and Int64 for type variable 'T'"):
                checked(FIRST + f'print(first({args}))')

    def test_recursive_typeref_and_thunk_runtime(self):
        source = ('sig unwrap(Thunk[T, Never]) -> T {}\n'
                  'func unwrap(t: Thunk[T, Never]) { return force(t) }\n'
                  'print(unwrap(thunk(1)))\nprint(unwrap(thunk(2i64)))')
        self.assertEqual(output(checked(source)[0]), '1\n2\n')
        program, syms, _, _ = self.annotated(source)
        unwrap = program.items[-1].expr.args[0].expr
        self.assertEqual(syms.resolved_calls[id(unwrap)].parameter_types,
                         (TypeRef('Thunk', (TypeRef('Int64'),), latent_failures=EMPTY_FAILURES),))
        generic = 'sig extract(SomeType[T]) -> T {}\nsig box() -> SomeType[Int] {}\n'
        program, syms, _, _ = self.annotated(generic + 'var x: Int = extract(box())')
        self.assertEqual(syms.resolved_calls[id(program.items[-1].expr)].type_bindings['T'], TypeRef('Int'))
        with self.assertRaisesRegex(TypecheckError, 'type mismatch'):
            checked(generic + 'var x: Int = extract(thunk(1))')

    def test_recursive_matching_does_not_add_constructor_variance(self):
        prefix = 'sig pair(Thunk[T, Never],T) -> T {}\n'
        with self.assertRaisesRegex(TypecheckError, 'type mismatch'):
            checked(prefix + 'var x: Float = pair(thunk(1),2.0)')
        checked(prefix + 'var x: Float = pair(thunk(1.0),2)')
        prefix = 'sig take(Thunk[T, Never]) -> T {}\n'
        with self.assertRaisesRegex(TypecheckError, 'type mismatch'):
            checked(prefix + 'var x: Int = take(thunk(add(1,2)))')
        checked('sig take(Thunk[T, IntegerOverflow]) -> T {}\nvar x: Int = take(thunk(1))')

    def test_user_function_parameters_are_coerced_to_resolved_type(self):
        frames = []
        def inspect(block, env, syms, outer):
            frames.append(env)
            return eval_block(block, env, syms, outer)
        with patch('ginger.eval.eval_block', side_effect=inspect):
            program, _ = checked(FIRST + 'print(first(1,2.0))\nprint(first(1,2i64))')
            self.assertEqual(output(program), '1.0\n1\n')
        self.assertEqual(frames[0]['a'].typ, TypeRef('Float'))
        self.assertIs(type(frames[0]['a'].value), float)
        self.assertEqual(frames[1]['a'].typ, TypeRef('Int64'))
        self.assertIs(type(frames[1]['a'].value), int)

    def test_nested_calls_and_symbolic_user_function_frames(self):
        source = IDENTITY + ('sig relay(U) -> U {}\nfunc relay(x: U) { let y: U = identity(x)\nreturn y }\n'
                             'print(add(identity(1),2))\nprint(relay(2.0))\nprint(relay(3i64))')
        self.assertEqual(output(checked(source)[0]), '3\n2.0\n3\n')
        source = ('sig wrap(T) -> Thunk[T, Never] {}\n'
                  'func wrap(x: T) { let y: T = x\nreturn thunk(y) }\n'
                  'print(force(wrap(1)))\nprint(force(wrap(2.0)))')
        self.assertEqual(output(checked(source)[0]), '1\n2.0\n')

    def test_integer_overflow_effect_and_catch_eligibility(self):
        program, diags = checked(f'var x: Int = {INT_MAX}\ntry add(x,1)\ncatch IntegerOverflow print(999)')
        self.assertEqual(diags.items, [])
        self.assertEqual(output(program), '999\n')
        for args in ['1.0,2.0', '1,2.0', '1.0,2']:
            with self.assertRaisesRegex(TypecheckError, 'no declared or inferred IntegerOverflow'):
                checked(f'try add({args})\ncatch IntegerOverflow print(999)')
        program, syms, bindings, _ = self.annotated('print(add(add(1,2),3.0))')
        outer = program.items[0].expr.args[0].expr
        self.assertEqual(syms.resolved_calls[id(outer)].implementation, 'core.float.add')
        self.assertEqual(effect_expr(outer, bindings, syms), OVERFLOW)

    def test_returns_and_function_upper_bounds(self):
        body = 'func f() { return add(1,2) }\n'
        source = 'sig f() -> Float { failure IntegerOverflow }\n' + body
        self.assertEqual(output(checked(source + 'print(f())')[0]), '3.0\n')
        with self.assertRaisesRegex(TypecheckError, 'undeclared failures: IntegerOverflow'):
            checked('sig f() -> Float {}\n' + body)
        source = 'sig f() -> Float {}\nfunc f() { return add(1,2.0) }\nprint(f())'
        self.assertEqual(output(checked(source)[0]), '3.0\n')

    def test_local_initializer_preserves_call_type_before_widening(self):
        source = ('sig f() -> Float { failure IntegerOverflow }\n'
                  'func f() { var y: Float = add(1,2)\ny = add(1,2.0)\nreturn y }\nprint(f())')
        program, syms, _, _ = self.annotated(source)
        body = syms.funcs['f'].body.stmts
        self.assertEqual(syms.expression_types[id(body[0].expr)], TypeRef('Int'))
        self.assertEqual(syms.expression_types[id(body[1].expr)], TypeRef('Float'))
        self.assertEqual(output(program), '3.0\n')

    def test_thunk_latent_effect_uses_selected_implementation(self):
        source = ('let a: Thunk[Int, IntegerOverflow] = thunk(add(1,2))\n'
                  'let b: Thunk[Float, Never] = thunk(add(1,2.0))\n')
        program, _, bindings, diags = self.annotated(source)
        self.assertEqual(bindings['a'].ty.latent_failures, OVERFLOW)
        self.assertEqual(bindings['b'].ty.latent_failures, EMPTY_FAILURES)
        self.assertEqual(diags.items, [])
        self.assertEqual(output(program), '')
        with self.assertRaisesRegex(TypecheckError, 'type mismatch'):
            checked('var t: Thunk[Float, IntegerOverflow] = thunk(add(1,2))')

    def test_structural_matching_and_body_are_not_inference_sources(self):
        for source in ['sig f(T) -> T {}\nfunc f(x: U) { return x }',
                       'sig f(T) -> T {}\nfunc f(x: T) { return 1 }']:
            with self.assertRaises(TypecheckError):
                checked(source)

    def test_independent_variables_and_typegroup_requirements(self):
        source = ('typegroup Floating = Float\n'
                  'sig choose(T,U) -> U { require U in Floating }\n'
                  'func choose(a: T,b: U) { return b }\nprint(choose(1i64,2.0))')
        self.assertEqual(output(checked(source)[0]), '2.0\n')
        with self.assertRaisesRegex(TypecheckError, 'requirement not satisfied'):
            checked(source.replace('choose(1i64,2.0)', 'choose(1.0,2i64)'))

    def test_existing_generic_body_guarantee_limit_is_explicit(self):
        with self.assertRaisesRegex(TypecheckError, 'does not guarantee Addable'):
            checked('sig twice(T) -> T { require T guarantees Addable failure IntegerOverflow }\n'
                    'func twice(x: T) { return add(x,x) }')
