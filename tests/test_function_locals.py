from ginger.eval import UninitializedBinding
import unittest
from unittest.mock import patch

from test_failure_contract import checked, output
from ginger.ast import VarDecl, AssignStmt, CallExpr, TypeRef
from ginger.diagnostics import Diagnostics
from ginger.errors import EvalError, TypecheckError
from ginger.eval import eval_program, eval_block
from ginger.numeric import INT_MAX
from ginger.parser import parse
from ginger.symbols_builder import build_symbols, normalize_types
from ginger.typecheck import typecheck_program


def function(body, ret='Int', contract='', params='', sig_params=''):
    return f'sig f({sig_params}) -> {ret} {{ {contract} }}\nfunc f({params}) {{ {body} }}\n'


class FunctionLocalTests(unittest.TestCase):
    def test_existing_ast_and_lowering(self):
        source = function('let a: Int = 1\nvar b: Int = a + 1\nb = b * 2\nreturn b',
                          contract='failure IntegerOverflow')
        body = parse(source).items[1].body.stmts
        self.assertIsInstance(body[0], VarDecl)
        self.assertFalse(body[0].mutable)
        self.assertIsInstance(body[1], VarDecl)
        self.assertTrue(body[1].mutable)
        self.assertIsInstance(body[2], AssignStmt)
        program, _ = checked(source + 'print(f())')
        self.assertIsInstance(program.items[1].body.stmts[1].expr, CallExpr)
        self.assertIsInstance(program.items[1].body.stmts[2].expr, CallExpr)
        self.assertEqual(output(program), '4\n')

    def test_parameters_locals_and_return(self):
        source = function('let a: Int = x\nvar b: Int = a\nb = 3\nreturn b',
                          params='x: Int', sig_params='Int')
        self.assertEqual(output(checked(source + 'print(f(1))')[0]), '3\n')
        self.assertEqual(output(checked(function('var x: Int = 1\nx = 2', ret='Unit') + 'f()')[0]), '')

    def test_immutable_let_and_parameter_assignment(self):
        for source in [function('let x: Int = 1\nx = 2', ret='Unit'),
                       function('x = 2\nreturn x', params='x: Int', sig_params='Int')]:
            with self.subTest(source=source), self.assertRaisesRegex(TypecheckError, 'immutable binding'):
                checked(source)

    def test_duplicate_local_and_parameter_names(self):
        for first in ['let', 'var']:
            for second in ['let', 'var']:
                with self.subTest(first=first, second=second), self.assertRaisesRegex(TypecheckError, 'already defined'):
                    checked(function(f'{first} x: Int = 1\n{second} x: Int = 2', ret='Unit'))
            with self.assertRaisesRegex(TypecheckError, 'already defined'):
                checked(function(f'{first} x: Int = 1', ret='Unit', params='x: Int', sig_params='Int'))

    def test_declaration_order_and_self_reference(self):
        for body in ['var y: Int = x\nvar x: Int = 1', 'var x: Int = x',
                     'x = 1\nvar x: Int = 2']:
            for prefix in ['', 'var x: Int = 10\n']:
                with self.subTest(body=body, prefix=prefix), self.assertRaisesRegex(TypecheckError, 'unknown identifier'):
                    checked(prefix + function(body, ret='Unit'))

    def test_shadowing_is_local_and_globals_remain_inaccessible(self):
        source = 'var x: Int = 10\n' + function('var x: Int = 20\nx = 21\nreturn x')
        program, _ = checked(source + 'var answer: Int = f()')
        env = eval_program(program).environment
        self.assertEqual(env['x'].value, 10)
        self.assertEqual(env['answer'].value, 21)
        for body in ['return x', 'x = 20\nreturn 0']:
            with self.assertRaisesRegex(TypecheckError, 'unknown identifier'):
                checked('var x: Int = 10\n' + function(body))
        with self.assertRaisesRegex(TypecheckError, 'unknown identifier'):
            checked(function('let y: Int = 20\nreturn y') + 'var answer: Int = f()\nprint(y)')
        program, _ = checked(function('let y: Int = 20\nreturn y') + 'var answer: Int = f()')
        self.assertNotIn('y', eval_program(program).environment)

    def test_fresh_environment_per_call(self):
        source = function('var x: Int = 1\nlet before: Int = x\nx = 2\nreturn before')
        environments = []
        def observe(block, env, syms, outer):
            environments.append(env)
            return eval_block(block, env, syms, outer)
        with patch('ginger.eval.eval_block', side_effect=observe):
            self.assertEqual(output(checked(source + 'print(f())\nprint(f())')[0]), '1\n1\n')
        self.assertEqual(len(environments), 2)
        self.assertIsNot(environments[0], environments[1])
        self.assertIsNot(environments[0]['x'], environments[1]['x'])

    def test_required_annotations_and_unsupported_local_constructs(self):
        for body in ['let x = 1', 'var x = 2.0',
                     'var x: Int = add(1,2)\nresolve x { IntegerOverflow { x = 0 } }',
                     '{ let x: Int = 1 }',
                     'func nested() {}']:
            with self.subTest(body=body), self.assertRaises(SyntaxError):
                parse(function(body, ret='Unit'))

    def test_widening_and_fixed_static_runtime_types(self):
        source = function('var i: Int = 1\nvar wide: Int64 = i\nvar real: Float = i\n'
                          'wide = 2\nreal = 3\nlet result: Int64 = wide\nreturn result', ret='Int64')
        program, _ = checked(source + 'var answer: Int64 = f()')
        syms = build_symbols(program)
        normalized = normalize_types(program, syms.failuresets)
        typecheck_program(normalized, Diagnostics(), syms=syms)
        body = syms.funcs['f'].body.stmts
        self.assertEqual(syms.expression_types[id(body[1].expr)], TypeRef('Int'))
        self.assertEqual(syms.expression_types[id(body[5].expr)], TypeRef('Int64'))
        self.assertEqual(syms.expression_types[id(body[6].expr)], TypeRef('Int64'))
        environments = []
        def observe(block, env, syms, outer):
            environments.append(env)
            return eval_block(block, env, syms, outer)
        with patch('ginger.eval.eval_block', side_effect=observe):
            env = eval_program(program).environment
        local = environments[0]
        for name, typ, value in [('i', 'Int', 1), ('wide', 'Int64', 2), ('real', 'Float', 3.0)]:
            self.assertEqual(local[name].typ, TypeRef(typ))
            self.assertTrue(local[name].mutable)
            self.assertEqual(local[name].value, value)
        self.assertIs(type(local['real'].value), float)
        self.assertFalse(local['result'].mutable)
        self.assertEqual(env['answer'].value, 2)

    def test_same_rejected_conversions_for_initializer_and_assignment(self):
        for typ, initializer, bad in [('Int', '0', '1i64'), ('Float', '0.0', '1i64'),
                                      ('Int', '0', '1.0'), ('Int64', '0i64', '1.0')]:
            for body in [f'var x: {typ} = {bad}', f'var x: {typ} = {initializer}\nx = {bad}']:
                with self.subTest(body=body), self.assertRaisesRegex(TypecheckError, 'type mismatch'):
                    checked(function(body, ret='Unit'))

    def test_integer_overflow_acceptance_and_upper_bounds(self):
        body = 'return add(x,1)'
        source = function(body, params='x: Int', sig_params='Int', contract='failure IntegerOverflow')
        program, diags = checked(source + f'var x: Int = f({INT_MAX})\n'
            'resolve x { IntegerOverflow { x = 999 } }\nprint(x)')
        self.assertEqual(diags.items, [])
        self.assertEqual(output(program), '999\n')
        self.assertEqual(output(checked(source + 'print(f(1))')[0]), '2\n')
        for body in ['let y: Int = add(x,1)\nreturn y',
                     'var y: Int = 0\ny = mul(x,2)\nreturn y']:
            with self.subTest(body=body), self.assertRaisesRegex(TypecheckError, 'undeclared failures: IntegerOverflow'):
                checked(function(body, params='x: Int', sig_params='Int', contract='failure Never'))

    def test_failed_initializer_and_assignment_preserve_environment(self):
        for body, exists in [('var y: Int = add(x,1)\nreturn y', False),
                             ('var y: Int = 5\ny = add(x,1)\nreturn y', True)]:
            source = function(body, params='x: Int', sig_params='Int', contract='failure IntegerOverflow')
            program, _ = checked(source + f'var result: Int = f({INT_MAX})')
            environments = []
            def observe(block, env, syms, outer):
                environments.append(env)
                return eval_block(block, env, syms, outer)
            with patch('ginger.eval.eval_block', side_effect=observe):
                if exists:
                    self.assertEqual(eval_program(program).environment['result'].value, 5)
                else:
                    with self.assertRaisesRegex(EvalError, "uninitialized binding 'y'"):
                        eval_program(program)
            self.assertEqual(isinstance(environments[0]['y'], UninitializedBinding), not exists)
            if exists:
                self.assertEqual(environments[0]['y'].value, 5)

    def test_return_context_is_not_reintroduced(self):
        with self.assertRaisesRegex(TypecheckError, 'cannot determine type variable'):
            checked('sig make() -> T {}\n' + function('return make()'))
        program, _ = checked(function('let x: Int = add(1,2)\nreturn x', contract='failure IntegerOverflow') + 'print(f())')
        self.assertEqual(output(program), '3\n')

    def test_local_thunk_storage_snapshot_and_force(self):
        for keyword in ['let', 'var']:
            source = function(f'var x: Int = 1\n{keyword} t: Thunk[Int, Never] = thunk(x)\nx = 2\nreturn force(t)')
            self.assertEqual(output(checked(source + 'print(f())')[0]), '1\n')
        source = function('var t: Thunk[Int, Never] = thunk(1)\nt = thunk(2)\nreturn force(t)')
        self.assertEqual(output(checked(source + 'print(f())')[0]), '2\n')

    def test_local_thunk_latent_failure_and_function_contract(self):
        body = f'let t: Thunk[Int, IntegerOverflow] = thunk(add({INT_MAX},1))\n'
        program, diags = checked(function(body, ret='Unit') + 'f()')
        self.assertEqual(diags.items, [])
        self.assertEqual(output(program), '')
        with self.assertRaisesRegex(TypecheckError, 'undeclared failures: IntegerOverflow'):
            checked(function(body + 'return force(t)'))
        source = function(body + 'return force(t)', contract='failure IntegerOverflow')
        self.assertEqual(output(checked(source + 'var x: Int = f()\n'
            'resolve x { IntegerOverflow { x = 7 } }\nprint(x)')[0]), '7\n')
        with self.assertRaisesRegex(TypecheckError, 'type mismatch'):
            checked(function(body.replace('Thunk[Int, IntegerOverflow]', 'Thunk[Int, Never]'), ret='Unit'))
        with self.assertRaisesRegex(TypecheckError, 'type mismatch'):
            checked(function('let t: Thunk[Int64, Never] = thunk(1)', ret='Unit'))

    def test_unreachable_statements_still_checked_but_not_effectful(self):
        checked(function('return 1\nlet later: Int = add(1,2)'))
        with self.assertRaisesRegex(TypecheckError, 'type mismatch'):
            checked(function('return 1\nlet later: Int = 1i64'))
        with self.assertRaisesRegex(TypecheckError, 'unknown identifier'):
            checked(function('return 1\nlet later: Int = missing'))

    def test_initializer_dependency_requires_failure_contract(self):
        prefix = 'sig h() -> Unit { failure IOErr }\nfunc h() {}\n'
        with self.assertRaisesRegex(TypecheckError, 'undeclared failures: IOErr'):
            checked(prefix + function('let x: Unit = h()', ret='Unit'))
        _, diags = checked(prefix + function('let x: Unit = h()', ret='Unit', contract='failure IOErr'))
        self.assertEqual(diags.items, [])
