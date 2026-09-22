import unittest
from unittest.mock import patch
from test_failure_contract import checked, output
from ginger.ast import IntLit, Int64Lit, TypeRef
from ginger.diagnostics import Diagnostics
from ginger.errors import TypecheckError
from ginger.eval import eval_program, eval_block
from ginger.numeric import INT_MIN, INT_MAX, INT64_MIN, INT64_MAX, widen_value
from ginger.parser import parse
from ginger.symbols_builder import build_symbols
from ginger.tokenizer import tokenize
from ginger.typecheck import compatible, same_type, typecheck_program


class BoundedIntegerTests(unittest.TestCase):
    def values(self, source):
        return eval_program(checked(source)[0])

    def test_literals(self):
        for typ, suffix, maximum, node in [('Int', '', INT_MAX, IntLit), ('Int64', 'i64', INT64_MAX, Int64Lit)]:
            for value in [0, 1, maximum] + ([2**53] if suffix else []):
                source = f'var x: {typ} = {value}{suffix}'
                self.assertIsInstance(parse(source).items[0].expr, node)
                env = self.values(source)
                self.assertEqual(env['x'].value, value)
                self.assertEqual(env['x'].typ, TypeRef(typ))
            self.assertEqual(self.values(f'var x: {typ} = (-{maximum}{suffix})')['x'].value, -maximum)
            for literal in [f'{maximum+1}{suffix}', f'(-{maximum+1}{suffix})']:
                with self.subTest(literal=literal), self.assertRaisesRegex(TypecheckError, 'out of range'):
                    checked(f'var x: {typ} = {literal}')

    def test_syntax(self):
        self.assertEqual([t.kind for t in tokenize('123 123i64')], ['INT', 'INT64', 'EOF'])
        for source in ['1.0i64', '1i64foo', '1i64_', '1 i64', '-1', '-1i64']:
            with self.subTest(source=source), self.assertRaises(SyntaxError):
                checked(f'var x: Int64 = {source}')
        self.assertEqual(self.values('var x: Int64 = 001i64')['x'].value, 1)

    def test_matrix(self):
        for actual in ['Int', 'Int64', 'Float']:
            for expected in ['Int', 'Int64', 'Float']:
                a, e = TypeRef(actual), TypeRef(expected)
                self.assertEqual(same_type(a, e), actual == expected)
                self.assertEqual(compatible(a, e), actual == expected or actual == 'Int')
        self.assertIs(type(widen_value(1, TypeRef('Int64'), TypeRef('Float'))), int)
        self.assertIs(type(widen_value(1, TypeRef('Int'), TypeRef('Float'))), float)
        for name in ['Thunk', 'SomeType']:
            self.assertFalse(compatible(TypeRef(name, (TypeRef('Int'),), latent_failures=frozenset()), TypeRef(name, (TypeRef('Int64'),), latent_failures=frozenset())))

    def test_fixed_types(self):
        program, _ = checked('var x: Int = 1\nvar y: Int64 = x\nvar z: Float = x\ny = 2\nz = x')
        bindings = typecheck_program(program, Diagnostics())
        env = eval_program(program)
        for name, typ in [('x', 'Int'), ('y', 'Int64'), ('z', 'Float')]:
            self.assertEqual(bindings[name].ty, TypeRef(typ))
            self.assertEqual(env[name].typ, TypeRef(typ))
        self.assertEqual(self.values('var x: Int64 = 1')['x'].typ, TypeRef('Int64'))

    def test_rejected_boundaries(self):
        sources = ['var x: Int = 1i64', 'var x: Float = 1i64', 'var x: Int = 1\nx = 2i64',
                   'var x: Int64 = 1.0', 'var x: Int64 = 1i64\nvar y: Float = x',
                   'var x: Int64 = 9007199254740992i64\nvar y: Float = x',
                   'var x: Float = toFloat(1i64)', 'var x: Thunk[Int64, Never] = thunk(1)',
                   'var x: Int64 = add(1, 2i64)', 'sig f(Int64) -> Unit {}\nfunc f(x: Int) {}',
                   'sig f(Int) -> Int {}\nfunc f(x: Int) { return x }\nvar x: Int = f(1i64)',
                   'sig f() -> Int {}\nfunc f() { return 1i64 }']
        for source in sources:
            with self.subTest(source=source), self.assertRaises(TypecheckError):
                checked(source)

    def test_functions(self):
        source = ('sig f(Int64) -> Int64 {}\nfunc f(x: Int64) { print(x)\nreturn x }\n'
                  'sig g() -> Int64 {}\nfunc g() { return 1 }\n'
                  'var x: Int64 = f(1)\nvar y: Int64 = g()')
        def inspect(block, env, syms, outer):
            if 'x' in env:
                self.assertEqual(env['x'].typ, TypeRef('Int64'))
            return eval_block(block, env, syms, outer)
        with patch('ginger.eval.eval_block', side_effect=inspect):
            self.assertEqual(output(checked(source)[0]), '1\n')
        self.assertEqual(self.values('var t: Thunk[Int64, Never] = thunk(1i64)\nvar x: Int64 = force(t)')['x'].typ, TypeRef('Int64'))

    def test_capabilities(self):
        program, _ = checked('var lo: Int64 = (-9223372036854775807i64)\nvar hi: Int64 = neg(lo)\n'
                             'var n: Int64 = (-1i64)\nvar e: Bool = eq(lo,hi)\nvar l: Bool = lt(lo,hi)\nvar o: Ordering = cmp(lo,hi)')
        env = eval_program(program)
        self.assertEqual(env['lo'].value, INT64_MIN)
        self.assertEqual(env['hi'].value, INT64_MAX)
        self.assertEqual(INT64_MIN, -INT64_MAX)
        self.assertEqual(INT_MIN, -INT_MAX)
        self.assertFalse(env['e'].value)
        self.assertTrue(env['l'].value)
        self.assertEqual(build_symbols(program).type_guarantees['Int64'], {'Negatable', 'Printable', 'Ord'})
        for expr in ['add(1i64,2i64)', 'sub(1i64,2i64)', 'mul(1i64,2i64)', 'div(1i64,2i64)']:
            with self.subTest(expr=expr), self.assertRaises(TypecheckError):
                checked(f'var x: Int64 = {expr}')

    def test_generic_parameter_keeps_concrete_type(self):
        source = ('sig identity(T) -> T {}\n'
                  'func identity(x: T) { return x }\n'
                  'var x: Int64 = identity(1i64)')
        self.assertEqual(self.values(source)['x'].value, 1)

    def test_legacy_dispatch_requires_integer_type(self):
        from ginger.runtime.dispatch import type_of
        from ginger.errors import EvalError
        with self.assertRaises(EvalError):
            type_of(1)
        self.assertEqual(type_of(1, TypeRef('Int64')), 'Int64')
