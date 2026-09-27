import unittest
from unittest.mock import patch

from test_failure_contract import checked
from ginger.ast import TypeRef
from ginger.builtin import BUILTINS, call_builtin
from ginger.diagnostics import Diagnostics
from ginger.errors import TypecheckError
from ginger.eval import eval_program, eval_block
from ginger.numeric import can_widen, widen_value
from ginger.typecheck import compatible, same_type, typecheck_program


class NumericWideningTests(unittest.TestCase):
    def values(self, source):
        program, _ = checked(source)
        return eval_program(program).environment

    def test_initialization_and_assignment(self):
        for source in ['var x: Float = 1',
                       'var x: Float = 0.0\nx = 1',
                       'var i: Int = 1\nvar x: Float = i',
                       'var i: Int = 1\nvar x: Float = 0.0\nx = i']:
            with self.subTest(source=source):
                program, _ = checked(source)
                self.assertEqual(typecheck_program(program, Diagnostics())['x'].ty, TypeRef('Float'))
                value = eval_program(program).environment['x'].value
                self.assertIs(type(value), float)
                self.assertEqual(value, 1.0)

    def test_reverse_and_bool_rejected(self):
        for source in ['var x: Int = 1.5', 'var x: Int = 0\nx = 1.5',
                       'sig f(Int) -> Int {}\nfunc f(x: Int) { return x }\nvar x: Int = f(1.5)',
                       'sig f() -> Int {}\nfunc f() { return 1.5 }',
                       'var x: Float = eq(1,1)',
                       'var x: Float = 0.0\nx = eq(1,1)',
                       'var x: Float = div(eq(1,1),2)',
                       'var x: Float = toFloat(1.5)',
                       'sig f(Float) -> Float {}\nfunc f(x: Float) { return x }\nvar x: Float = f(eq(1,1))',
                       'sig f() -> Float {}\nfunc f() { return eq(1,1) }']:
            with self.subTest(source=source), self.assertRaises(TypecheckError):
                checked(source)
        self.assertIs(widen_value(True, TypeRef('Bool'), TypeRef('Float')), True)

    def test_user_parameter_is_float_inside_body(self):
        source = ('sig inspect(Float) -> Unit { builtin core.float.print }\n'
                  'sig f(Float) -> Float {}\n'
                  'func f(x: Float) { inspect(x)\nreturn x }\n'
                  'var x: Float = f(1)')
        seen = []
        def inspect_block(block, env, syms, outer):
            self.assertIs(type(env['x'].value), float)
            return eval_block(block, env, syms, outer)
        with patch.dict(BUILTINS, {'core.float.print': lambda x: seen.append(x)}), \
                patch('ginger.eval.eval_block', side_effect=inspect_block):
            env = self.values(source)
        self.assertIs(type(seen[0]), float)
        self.assertIs(type(env['x'].value), float)

    def test_catalog_builtin_receives_floats(self):
        seen = []
        def divide(a, b):
            seen.extend([a, b])
            return a / b
        with patch.dict(BUILTINS, {'core.float.div': divide}):
            env = self.values('var x: Float = div(1,2)')
        self.assertEqual([type(v) for v in seen], [float, float])
        self.assertEqual(env['x'].value, 0.5)

    def test_return_converts_before_caller_uses_value(self):
        for body in ['return 1', 'return 1\nreturn 2.0', 'return 2.0\nreturn 1']:
            source = f'sig f() -> Float {{}}\nfunc f() {{ {body} }}\nprint(f())'
            seen = []
            with patch.dict(BUILTINS, {'core.float.print': lambda x: seen.append(x)}):
                self.values(source)
            self.assertIs(type(seen[0]), float)
        env = self.values('sig g() -> Int {}\nfunc g() { return 1 }\n'
                          'sig f() -> Float {}\nfunc f() { return g() }\nvar x: Float = f()')
        self.assertIs(type(env['x'].value), float)

    def test_equality_and_constructor_invariance(self):
        integer, floating = TypeRef('Int'), TypeRef('Float')
        self.assertFalse(same_type(integer, floating))
        self.assertTrue(can_widen(integer, floating))
        self.assertTrue(compatible(integer, floating))
        self.assertFalse(compatible(floating, integer))
        self.assertFalse(compatible(TypeRef('Bool'), floating))
        for name in ['Thunk', 'SomeType']:
            a = TypeRef(name, (integer,), latent_failures=frozenset())
            b = TypeRef(name, (floating,), latent_failures=frozenset())
            self.assertFalse(compatible(a, b))

    def test_declarations_thunks_and_generics_remain_strict(self):
        for source in ['sig f(Float) -> Unit {}\nfunc f(x: Int) {}',
                       'sig f(Int) -> Unit {}\nfunc f(x: Float) {}',
                       'var t: Thunk[Float, Never] = thunk(1)',
                       'var t: Thunk[Int, Never] = thunk(1)\nvar u: Thunk[Float, Never] = t',
                       'var x: Int = add(1,2.0)']:
            with self.subTest(source=source), self.assertRaises(TypecheckError):
                checked(source)
        self.assertEqual(self.values('var x: Float = add(1,2.0)')['x'].value, 3.0)
        env = self.values('var t: Thunk[Int, Never] = thunk(1)\nvar x: Float = force(t)')
        self.assertIs(type(env['x'].value), float)

    def test_bounded_int_converts_exactly(self):
        for value in [0, 1, 2**53 - 1, -(2**53 - 1)]:
            result = widen_value(value, TypeRef('Int'), TypeRef('Float'))
            self.assertEqual(int(result), value)
            self.assertEqual(result, call_builtin('core.int.toFloat', value))
        for value in [2**53 + 1, 10**400]:
            for source in [f'var x: Float = {value}',
                           f'var x: Float = toFloat({value})']:
                with self.subTest(source=source), self.assertRaises(TypecheckError):
                    self.values(source)
