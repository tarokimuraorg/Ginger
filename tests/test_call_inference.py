from test_failure_contract import recorded
from ginger.eval import UninitializedBinding
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
from ginger.symbols_builder import build_symbols, normalize_types
from ginger.typecheck import effect_expr, reconcile_type_evidence, typecheck_program


IDENTITY = 'sig identity[T](T) -> T {}\nfunc identity(x: T) { return x }\n'
FIRST = 'sig first[T](T,T) -> T {}\nfunc first(a: T,b: T) { return a }\n'
OVERFLOW = failures(FailureId.IntegerOverflow)
FLOAT_OVERFLOW = failures(FailureId.FloatOverflow)


class CallInferenceTests(unittest.TestCase):
    def annotated(self, source):
        program, _ = checked(source)
        syms = build_symbols(program)
        program = normalize_types(program, syms.failuresets)
        diags = Diagnostics()
        bindings = typecheck_program(program, diags, syms=syms)
        return program, syms, bindings, diags

    def test_exact_inference_and_expression_types(self):
        cases = [('identity[Int](1)', 'Int', 1), ('identity[Float](1.0)', 'Float', 1.0),
                 ('identity[Int64](1i64)', 'Int64', 1), ('add[Int](1,2)', 'Int', 3),
                 ('add[Float](1.0,2.0)', 'Float', 3.0), ('neg[Int](1)', 'Int', -1),
                 ('neg[Float](1.0)', 'Float', -1.0), ('(-1)', 'Int', -1)]
        for expr, name, value in cases:
            with self.subTest(expr=expr):
                program, syms, _, _ = self.annotated(IDENTITY + f'var x: {name} = {expr}')
                call = program.items[-1].expr
                self.assertEqual(syms.expression_types[id(call)], TypeRef(name))
                self.assertEqual(syms.resolved_calls[id(call)].return_type, TypeRef(name))
                cell = eval_program(program).environment['x']
                self.assertEqual(cell.value, value)
                self.assertIs(type(cell.value), type(value))

    def test_print_without_expected_type(self):
        for expr, expected, effects in [('add[Int](1,2)', '3\n', OVERFLOW),
                                        ('add[Float](1.0,2.0)', '3.0\n', FLOAT_OVERFLOW),
                                        ('add[Float](1,2.0)', '3.0\n', FLOAT_OVERFLOW),
                                        ('add[Float](1.0,2)', '3.0\n', FLOAT_OVERFLOW),
                                        ('neg[Int](1)', '-1\n', EMPTY_FAILURES),
                                        ('neg[Float](1.0)', '-1.0\n', EMPTY_FAILURES),
                                        ('-1', '-1\n', EMPTY_FAILURES),
                                        ('(-1)', '-1\n', EMPTY_FAILURES)]:
            with self.subTest(expr=expr):
                typ = 'Float' if '.' in expected else 'Int'
                program, syms, bindings, _ = self.annotated(f'print[{typ}]({expr})')
                self.assertEqual(effect_expr(program.items[-1].expr, bindings, syms), effects)
                self.assertEqual(output(program), expected)

    def test_mixed_arguments_share_static_runtime_and_effect_resolution(self):
        for op in ['add', 'sub', 'mul']:
            for args in ['1,2.0', '1.0,2']:
                with self.subTest(op=op, args=args):
                    program, syms, bindings, diags = self.annotated(f'var x: Float = {op}[Float]({args})')
                    call = program.items[0].expr
                    resolution = syms.resolved_calls[id(call)]
                    self.assertEqual(resolution.type_bindings, {'T': TypeRef('Float')})
                    self.assertEqual(resolution.parameter_types, (TypeRef('Float'),) * 2)
                    self.assertEqual(resolution.implementation, f'core.float.{op}')
                    self.assertEqual(effect_expr(call, bindings, syms), FLOAT_OVERFLOW | (failures(FailureId.FloatUnderflow) if op == "mul" else EMPTY_FAILURES))
                    self.assertEqual([d.message for d in diags], ['unhandled failures: FloatOverflow' + (', FloatUnderflow' if op == 'mul' else '')])
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
        for target, expr, impl in [('Float', 'add[Int](1,2)', 'core.int.add'),
                                   ('Float', 'identity[Int](1)', None),
                                   ('Int64', 'identity[Int](1)', None)]:
            program, syms, bindings, _ = self.annotated(IDENTITY + f'var x: {target} = {expr}')
            call = program.items[-1].expr
            self.assertEqual(syms.expression_types[id(call)], TypeRef('Int'))
            self.assertEqual(syms.resolved_calls[id(call)].type_bindings, {'T': TypeRef('Int')})
            self.assertEqual(syms.resolved_calls[id(call)].implementation, impl)
            self.assertEqual(bindings['x'].ty, TypeRef(target))
            self.assertEqual(effect_expr(call, bindings, syms), OVERFLOW if impl else EMPTY_FAILURES)
            cell = eval_program(program).environment['x']
            self.assertEqual(cell.typ, TypeRef(target))
            self.assertIs(type(cell.value), float if target == 'Float' else int)
        with self.assertRaisesRegex(TypecheckError, "type mismatch: expected TypeRef\\(name='Int'.*got TypeRef\\(name='Float'"):
            checked('var x: Int = add[Float](1,2.0)')
        program, _ = checked(f'var x: Float = add[Int]({INT_MAX},1)')
        env, context, _ = recorded(program)
        self.assertIsInstance(env['x'], UninitializedBinding)
        self.assertEqual(context.failure_history[0].failure_id, FailureId.IntegerOverflow)

    def test_unresolved_variables_not_filled_from_any_outer_boundary(self):
        for declaration, call in [('sig make[T]() -> T {}', 'make()'),
                                  ('sig strange[T](Int) -> T {}', 'strange(1)'),
                                  ('type SomeType[T]\nsig make[T]() -> SomeType[T] {}', 'make()')]:
            for context in [f'var x: Int = {call}', f'print[Int64]({call})',
                            f'sig f() -> Int {{}}\nfunc f() {{ return {call} }}',
                            f'sig f() -> Int {{}}\nfunc f() {{ let x: Int = {call}\nreturn x }}',
                            f'sigtype DeferredInt() -> Int {{ failure {{ Never }} }}\nvar t: Thunk[DeferredInt] = thunk({call})']:
                with self.subTest(declaration=declaration, context=context), self.assertRaisesRegex(
                        TypecheckError, "explicit type arguments"):
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
            program, syms, _, _ = self.annotated(FIRST + f'var x: Int64 = first[Int64]({args})')
            self.assertEqual(syms.resolved_calls[id(program.items[-1].expr)].type_bindings['T'], TypeRef('Int64'))
            self.assertEqual(eval_program(program).environment['x'].value, 1)
            with self.assertRaisesRegex(TypecheckError, 'Int64.*does not guarantee Addable'):
                checked(f'print[Float](add[Int64]({args}))')
        for args in ['1i64,2.0', '1.0,2i64']:
            with self.assertRaisesRegex(TypecheckError, "type mismatch"):
                checked(FIRST + f'print[Float](first[Int64]({args}))')

    def test_recursive_typeref_and_thunk_runtime(self):
        source = ('sigtype Deferred() -> Int64 {}\n'
                  'sig unwrap(Thunk[Deferred]) -> Int64 {}\n'
                  'func unwrap(t: Thunk[Deferred]) { return force(t) }\n'
                  'print[Int64](unwrap(thunk(2i64)))')
        self.assertEqual(output(checked(source)[0]), '2\n')
        program, syms, _, _ = self.annotated(source)
        unwrap = program.items[-1].expr.args[0].expr
        self.assertEqual(syms.resolved_calls[id(unwrap)].parameter_types[0].sigtype_name, 'Deferred')
        generic = ('type SomeType[T]\nsig extract[T](SomeType[T]) -> T { builtin test.extract }\n'
                   'sig box() -> SomeType[Int] { builtin test.box }\n')
        with patch.dict(BUILTINS, {'test.extract': lambda box: box[0], 'test.box': lambda: (1,)}):
            program, syms, _, _ = self.annotated(generic + 'var x: Int = extract[Int](box())')
            self.assertEqual(syms.resolved_calls[id(program.items[-1].expr)].type_bindings['T'], TypeRef('Int'))
            with self.assertRaisesRegex(TypecheckError, 'type mismatch'):
                checked(generic + 'sigtype D() -> Int {} var t: Thunk[D] = thunk(1) var x: Int = extract[Int](t)')

    def test_recursive_matching_does_not_add_constructor_variance(self):
        prefix = ('sigtype D() -> Float {} sig pair[T](Thunk[D],T) -> T {}\n'
                  'func pair(a: Thunk[D],b: T) { return b }\n')
        with self.assertRaisesRegex(TypecheckError, 'type mismatch'):
            checked(prefix + 'var x: Float = pair[Int](thunk(1),2.0)')
        checked(prefix + 'var x: Float = pair[Int](thunk(1.0),2)')
        prefix = ('sigtype D() -> Int {} sig take(Thunk[D]) -> Int {}\n'
                  'func take(t: Thunk[D]) { return force(t) }\n')
        with self.assertRaisesRegex(TypecheckError, 'type mismatch'):
            checked(prefix + 'var x: Int = take(thunk(add[Int](1,2)))')
        checked('sigtype D() -> Int { failure { IntegerOverflow } } sig take(Thunk[D]) -> Int { failure { IntegerOverflow } }\n'
                'func take(t: Thunk[D]) { return force(t) }\n'
                'var x: Int = take(thunk(1))')

    def test_user_function_parameters_are_coerced_to_resolved_type(self):
        frames = []
        def inspect(block, env, syms, outer):
            frames.append(env)
            return eval_block(block, env, syms, outer)
        with patch('ginger.eval.eval_block', side_effect=inspect):
            program, _ = checked(FIRST + 'print[Float](first[Float](1,2.0))\nprint[Int64](first[Int64](1,2i64))')
            self.assertEqual(output(program), '1.0\n1\n')
        self.assertEqual(frames[0]['a'].typ, TypeRef('Float'))
        self.assertIs(type(frames[0]['a'].value), float)
        self.assertEqual(frames[1]['a'].typ, TypeRef('Int64'))
        self.assertIs(type(frames[1]['a'].value), int)

    def test_nested_calls_and_symbolic_user_function_frames(self):
        source = IDENTITY + ('sig relay[U](U) -> U {}\nfunc relay(x: U) { let y: U = identity[U](x)\nreturn y }\n'
                             'print[Int](add[Int](identity[Int](1),2))\nprint[Float](relay[Float](2.0))\nprint[Int64](relay[Int64](3i64))')
        self.assertEqual(output(checked(source)[0]), '3\n2.0\n3\n')
        source = ('sigtype D() -> Float {} sig wrap[T](T,Float) -> Thunk[D] {}\n'
                  'func wrap(tag: T,x: Float) { let y: Float = x\nreturn thunk(y) }\n'
                  'print[Float](force(wrap[Int](1,1.0)))\nprint[Float](force(wrap[Float](2.0,2.0)))')
        self.assertEqual(output(checked(source)[0]), '1.0\n2.0\n')

    def test_integer_overflow_effect_and_resolve_eligibility(self):
        program, diags = checked(f'var x: Int = {INT_MAX}\nvar y: Int = add[Int](x,1)\n'
                                 'resolve y { IntegerOverflow { y = 0\nprint[Int](999) } }')
        self.assertEqual(diags.items, [])
        self.assertEqual(output(program), '999\n')
        for args in ['1.0,2.0', '1,2.0', '1.0,2']:
            with self.assertRaisesRegex(TypecheckError, 'latest write.*no declared or inferred'):
                checked(f'var x: Float = add[Float]({args})\n'
                        'resolve x { IntegerOverflow { x = 0\nprint[Int](999) } }')
        program, syms, bindings, _ = self.annotated('print[Float](add[Float](add[Int](1,2),3.0))')
        outer = program.items[0].expr.args[0].expr
        self.assertEqual(syms.resolved_calls[id(outer)].implementation, 'core.float.add')
        self.assertEqual(effect_expr(outer, bindings, syms), OVERFLOW | FLOAT_OVERFLOW)

    def test_returns_and_function_upper_bounds(self):
        body = 'func f() { return add[Int](1,2) }\n'
        source = 'sig f() -> Float { failure { IntegerOverflow } }\n' + body
        self.assertEqual(output(checked(source + 'print[Float](f())')[0]), '3.0\n')
        with self.assertRaisesRegex(TypecheckError, 'undeclared failures: IntegerOverflow'):
            checked('sig f() -> Float {}\n' + body)
        source = 'sig f() -> Float { failure { FloatOverflow, FloatUnderflow } }\nfunc f() { return add[Float](1,2.0) }\nprint[Float](f())'
        self.assertEqual(output(checked(source)[0]), '3.0\n')

    def test_local_initializer_preserves_call_type_before_widening(self):
        source = ('sig f() -> Float { failure { IntegerOverflow, FloatOverflow, FloatUnderflow } }\n'
                  'func f() { var y: Float = add[Int](1,2)\ny = add[Float](1,2.0)\nreturn y }\nprint[Float](f())')
        program, syms, _, _ = self.annotated(source)
        body = syms.funcs['f'].body.stmts
        self.assertEqual(syms.expression_types[id(body[0].expr)], TypeRef('Int'))
        self.assertEqual(syms.expression_types[id(body[1].expr)], TypeRef('Float'))
        self.assertEqual(output(program), '3.0\n')

    def test_thunk_latent_effect_uses_selected_implementation(self):
        source = ('sigtype CheckedInt() -> Int { failure { IntegerOverflow } }\nsigtype CheckedFloat() -> Float { failure { FloatOverflow } }\nlet a: Thunk[CheckedInt] = thunk(add[Int](1,2))\n'
                  'let b: Thunk[CheckedFloat] = thunk(add[Float](1,2.0))\n')
        program, _, bindings, diags = self.annotated(source)
        self.assertEqual(bindings['a'].ty.latent_failures, OVERFLOW)
        self.assertEqual(bindings['b'].ty.latent_failures, FLOAT_OVERFLOW)
        self.assertEqual(diags.items, [])
        self.assertEqual(output(program), '')
        with self.assertRaisesRegex(TypecheckError, 'type mismatch'):
            checked('sigtype DeferredFloatIntegerOverflow() -> Float { failure { IntegerOverflow } }\nvar t: Thunk[DeferredFloatIntegerOverflow] = thunk(add[Int](1,2))')

    def test_structural_matching_and_body_are_not_inference_sources(self):
        for source in ['sig f[T](T) -> T {}\nfunc f(x: U) { return x }',
                       'sig f[T](T) -> T {}\nfunc f(x: T) { return 1 }']:
            with self.assertRaises(TypecheckError):
                checked(source)

    def test_independent_variables_and_typegroup_requirements(self):
        source = ('typegroup Floating { Float }\n'
                  'sig choose[T,U](T,U) -> U { require U in Floating }\n'
                  'func choose(a: T,b: U) { return b }\nprint[Float](choose[Int64,Float](1i64,2.0))')
        self.assertEqual(output(checked(source)[0]), '2.0\n')
        with self.assertRaisesRegex(TypecheckError, 'requirement not satisfied'):
            checked(source.replace('choose[Int64,Float](1i64,2.0)', 'choose[Float,Int64](1.0,2i64)'))

    def test_generic_body_uses_declared_guarantee(self):
        program, _ = checked('sig twice[T](T) -> T { require T guarantees Addable failure { IntegerOverflow, FloatOverflow, FloatUnderflow } }\n'
                             'func twice(x: T) { return add[T](x,x) }\nprint[Int](twice[Int](2))')
        self.assertEqual(output(program), '4\n')
