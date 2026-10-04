import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from test_failure_contract import checked, output, recorded
from ginger.ast import CallExpr, TypeRef
from ginger.builtin import BUILTINS, INT_ARITHMETIC_FAILURES
from ginger.core.failure_spec import FailureId, EMPTY_FAILURES, failures
from ginger.diagnostics import Diagnostics
from ginger.errors import TypecheckError
from ginger.eval import eval_block, eval_program
from ginger.pipeline import run
from ginger.symbols_builder import build_symbols, normalize_types
from ginger.typecheck import Binding, effect_expr, typecheck_program

OVERFLOW = failures(FailureId.IntegerOverflow)


def addition(body, *, requires='require T guarantees Addable', contract='failure IntegerOverflow'):
    return (f'sig addTwo(T,T) -> T {{ {requires} {contract} }}\n'
            f'func addTwo(a: T,b: T) {{ return {body} }}\n')


class GenericGuaranteeTests(unittest.TestCase):

    def annotated(self, source):
        program, _ = checked(source)
        syms = build_symbols(program)
        program = normalize_types(program, syms.failuresets)
        diags = Diagnostics()
        bindings = typecheck_program(program, diags, syms=syms)
        return program, syms, bindings, diags

    def test_pipeline_run_uses_body_proofs_and_instantiates_contracts(self):
            for body in ['add(a,b)', '(a + b)']:
                for conditional in [False, True]:
                    contract = 'failure IntegerOverflow'
                    if conditional:
                        contract += ' when T guarantees BoundedArithmetic'
                    for typ, args, expected in [('Int', '1,2', '3\n'),
                                                ('Float', '1.0,2.0', '3.0\n'),
                                                ('Int64', '1i64,2i64', None)]:
                        source = addition(body, contract=contract) + f'print(addTwo({args}))'
                        stdout, stderr = io.StringIO(), io.StringIO()
                        with self.subTest(body=body, conditional=conditional, typ=typ):
                            with redirect_stdout(stdout), redirect_stderr(stderr):
                                if typ == 'Int64':
                                    with self.assertRaisesRegex(TypecheckError, 'does not guarantee Addable'):
                                        run(source)
                                    continue
                                result = run(source)
                            self.assertEqual(stdout.getvalue(), expected)
                            self.assertIsNone(result.contract_violation)
                            effect = EMPTY_FAILURES if conditional and typ == 'Float' else OVERFLOW
                            frame, = [f for f in result.call_frames.values()
                                      if f.function_name == 'addTwo']
                            self.assertEqual(frame.declared_failure_contract, effect)
                            warning = ('warning[UNHANDLED_FAILURES]: unhandled failures: IntegerOverflow\n'
                                       if effect else '')
                            self.assertEqual(stderr.getvalue(), warning)
                    with self.subTest(body=body, conditional=conditional, requires=False):
                        with self.assertRaisesRegex(TypecheckError, 'does not guarantee Addable'):
                            run(addition(body, requires='', contract=contract) + 'print(addTwo(1,2))')

    def test_explicit_and_infix_add_int_and_float(self):
            for body in ['add(a,b)', '(a + b)']:
                for typ, args, expected in [('Int', '1,2', '3\n'),
                                            ('Float', '1.0,2.0', '3.0\n')]:
                    with self.subTest(body=body, typ=typ):
                        program, syms, bindings, _ = self.annotated(
                            addition(body) + f'var x: {typ} = addTwo({args})\nprint(x)')
                        call = program.items[-2].expr
                        self.assertEqual(syms.resolved_calls[id(call)].type_bindings,
                                         {'T': TypeRef(typ)})
                        self.assertEqual(syms.resolved_calls[id(call)].return_type, TypeRef(typ))
                        self.assertEqual(bindings['x'].ty, TypeRef(typ))
                        self.assertEqual(output(program), expected)
    
    def test_int64_call_site_rejected(self):
        for body in ['add(a,b)', '(a + b)']:
            with self.subTest(body=body), self.assertRaisesRegex(
                    TypecheckError, 'Int64.*does not guarantee Addable'):
                checked(addition(body) + 'print(addTwo(1i64,2i64))')

    def test_missing_addable_rejected_without_calls(self):
        for body in ['add(a,b)', '(a + b)']:
            with self.subTest(body=body), self.assertRaisesRegex(
                    TypecheckError, 'does not guarantee Addable'):
                checked(addition(body, requires=''))

    def test_typegroup_is_not_a_capability_proof(self):
        for body in ['add(a,b)', '(a + b)']:
            with self.subTest(body=body), self.assertRaisesRegex(
                    TypecheckError, 'does not guarantee Addable'):
                checked('typegroup Numeric = Int | Float\n' +
                        addition(body, requires='require T in Numeric'))

    def test_symbolic_add_failure_upper_bound(self):
        for body in ['add(a,b)', '(a + b)']:
            with self.subTest(body=body), self.assertRaisesRegex(
                    TypecheckError, 'undeclared failures: IntegerOverflow'):
                checked(addition(body, contract='failure Never'))

    def test_explicit_and_lowered_infix_share_symbolic_resolution(self):
        resolutions = []
        for body in ['add(a,b)', '(a + b)']:
            program, syms, _, _ = self.annotated(addition(body))
            call = syms.funcs['addTwo'].body.stmts[0].expr
            self.assertIsInstance(call, CallExpr)
            self.assertEqual(call.callee, 'add')
            resolved = syms.resolved_calls[id(call)]
            self.assertEqual(resolved.type_bindings, {'T': TypeRef('T')})
            self.assertEqual(resolved.parameter_types, (TypeRef('T'), TypeRef('T')))
            self.assertEqual(resolved.return_type, TypeRef('T'))
            self.assertEqual(resolved.deferred_guarantee, 'Addable')
            self.assertEqual(resolved.deferred_method, 'add')
            self.assertIsNone(resolved.implementation)
            env = {'a': Binding(TypeRef('T'), False), 'b': Binding(TypeRef('T'), False)}
            self.assertEqual(effect_expr(call, env, syms), OVERFLOW)
            resolutions.append(resolved)
        self.assertEqual(resolutions[0], resolutions[1])

    def test_conservative_union_ignores_unrelated_builtin_contracts(self):
        unrelated = {'core.int.neg': failures(FailureId.IOErr),
                        'core.int.sub': failures(FailureId.PrintErr)}
        with patch.dict(INT_ARITHMETIC_FAILURES, unrelated):
            program, syms, _, _ = self.annotated(addition('add(a,b)'))
        call = syms.funcs['addTwo'].body.stmts[0].expr
        env = {'a': Binding(TypeRef('T'), False), 'b': Binding(TypeRef('T'), False)}
        self.assertEqual(effect_expr(call, env, syms), OVERFLOW)

    def test_extra_unconditional_failure_remains_public_for_float(self):
        for body in ['add(a,b)', '(a + b)']:
            program, syms, bindings, diags = self.annotated(
                addition(body) + 'var x: Float = addTwo(1.0,2.0)')
            self.assertEqual(effect_expr(program.items[-1].expr, bindings, syms), OVERFLOW)
            self.assertEqual(bindings['x'].initializer_failures, OVERFLOW)
            self.assertEqual([d.message for d in diags], ['unhandled failures: IntegerOverflow'])

    def test_negatable_capability_and_unary_sugar(self):
        for body in ['neg(x)', '(-x)']:
            source = ('sig opposite(T) -> T { require T guarantees Negatable }\n'
                        f'func opposite(x: T) {{ return {body} }}\n')
            for typ, arg, expected in [('Int', '1', '-1\n'),
                                        ('Float', '1.0', '-1.0\n'),
                                        ('Int64', '1i64', '-1\n')]:
                with self.subTest(body=body, typ=typ):
                    program, syms, bindings, diags = self.annotated(source + f'print(opposite({arg}))')
                    symbolic = syms.funcs['opposite'].body.stmts[0].expr
                    resolved = syms.resolved_calls[id(symbolic)]
                    self.assertEqual(resolved.deferred_guarantee, 'Negatable')
                    self.assertEqual(resolved.deferred_method, 'neg')
                    self.assertIsNone(resolved.implementation)
                    self.assertEqual(effect_expr(program.items[-1].expr, bindings, syms), EMPTY_FAILURES)
                    self.assertEqual(diags.items, [])
                    self.assertEqual(output(program), expected)

    def test_missing_negatable_rejects_explicit_and_lowered_unary(self):
        for body in ['neg(x)', '(-x)']:
            with self.subTest(body=body), self.assertRaisesRegex(
                    TypecheckError, 'does not guarantee Negatable'):
                checked('sig opposite(T) -> T {}\n'
                        f'func opposite(x: T) {{ return {body} }}')

    def test_printable_capability_in_generic_unit_body(self):
        source = ('sig show(T) -> Unit { require T guarantees Printable }\n'
                    'func show(x: T) { print(x) }\n'
                    'show(1)\nshow(2.0)\nshow(3i64)\nshow(eq(1,1))')
        program, syms, _, diags = self.annotated(source)
        call = syms.funcs['show'].body.stmts[0].expr
        self.assertEqual(syms.resolved_calls[id(call)].deferred_guarantee, 'Printable')
        self.assertEqual(syms.resolved_calls[id(call)].return_type, TypeRef('Unit'))
        self.assertEqual(diags.items, [])
        self.assertEqual(output(program), '1\n2.0\n3\ntrue\n')
        with self.assertRaisesRegex(TypecheckError, 'does not guarantee Printable'):
            checked(source.replace('require T guarantees Printable', ''))

    def test_nested_generic_substitution_and_frame_bindings(self):
        source = (addition('(a + b)') +
                    'sig relay(U,U) -> U { require U guarantees Addable failure IntegerOverflow }\n'
                    'func relay(a: U,b: U) { return addTwo(a,b) }\n'
                    'print(relay(1,2))\nprint(relay(1.0,2.0))')
        program, syms, _, _ = self.annotated(source)
        nested = syms.funcs['relay'].body.stmts[0].expr
        self.assertEqual(syms.resolved_calls[id(nested)].type_bindings, {'T': TypeRef('U')})
        environments = []
        def observe(block, env, syms, outer):
            environments.append((dict(env.type_bindings), env['a'].typ))
            return eval_block(block, env, syms, outer)
        with patch('ginger.eval.eval_block', side_effect=observe):
            _, context, stdout = recorded(program)
        self.assertEqual(stdout, '3\n3.0\n')
        self.assertEqual(environments,
                            [({'U': TypeRef('Int')}, TypeRef('Int')),
                            ({'T': TypeRef('Int')}, TypeRef('Int')),
                            ({'U': TypeRef('Float')}, TypeRef('Float')),
                            ({'T': TypeRef('Float')}, TypeRef('Float'))])
        frames = list(context.call_frames.values())[1:]
        self.assertEqual([frame.function_name for frame in frames],
                            ['relay', 'addTwo', 'relay', 'addTwo'])
        self.assertEqual([dict(frame.type_bindings) for frame in frames],
                            [entry[0] for entry in environments])

    def test_nested_caller_needs_its_own_capability(self):
        with self.assertRaisesRegex(TypecheckError, 'does not guarantee Addable'):
            checked(addition('add(a,b)') +
                    'sig relay(U,U) -> U { failure IntegerOverflow }\n'
                    'func relay(a: U,b: U) { return addTwo(a,b) }')

    def test_thunk_captures_generic_dispatch_binding(self):
        source = ('sig delayed(T,T) -> Thunk[T, IntegerOverflow] { require T guarantees Addable }\n'
                    'func delayed(a: T,b: T) { return thunk((a + b)) }\n'
                    'print(force(delayed(1,2)))\nprint(force(delayed(1.0,2.0)))')
        self.assertEqual(output(checked(source)[0]), '3\n3.0\n')

    def test_runtime_uses_ginger_binding_for_equal_python_value_types(self):
        source = ('guarantee Tagged { tag(self: Self) -> Self }\n'
                    'impl Int guarantees Tagged { tag = builtin test.int.tag }\n'
                    'impl Int64 guarantees Tagged { tag = builtin test.int64.tag }\n'
                    'sig tag(T) -> T { require T guarantees Tagged }\n'
                    'sig label(U) -> U { require U guarantees Tagged }\n'
                    'func label(x: U) { return tag(x) }\n'
                    'var a: Int = label(1)\nvar b: Int64 = label(1i64)')
        seen = []
        def integer(value):
            seen.append(('Int', type(value)))
            return value + 10
        def int64(value):
            seen.append(('Int64', type(value)))
            return value + 20
        with patch.dict(BUILTINS, {'test.int.tag': integer, 'test.int64.tag': int64}):
            program, _ = checked(source)
            result = eval_program(program)
        self.assertEqual(seen, [('Int', int), ('Int64', int)])
        self.assertEqual((result.environment['a'].value, result.environment['b'].value), (11, 21))
        self.assertEqual([dict(f.type_bindings) for f in result.call_frames.values()][1:],
                            [{'U': TypeRef('Int')}, {'U': TypeRef('Int64')}])

    def test_generic_body_is_not_rechecked_for_each_invocation(self):
        program, _ = checked(addition('add(a,b)') +
                                'print(addTwo(1,2))\nprint(addTwo(2,3))\nprint(addTwo(1.0,2.0))')
        with patch('ginger.eval.typecheck_program', wraps=typecheck_program) as checker:
            self.assertEqual(output(program), '3\n5\n3.0\n')
        self.assertEqual(checker.call_count, 1)
    

if __name__ == '__main__':
    unittest.main()
