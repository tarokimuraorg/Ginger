import unittest
from unittest.mock import Mock, patch

from ginger.ast import TypeRef
from ginger.builtin import BUILTINS
from ginger.diagnostics import Diagnostics
from ginger.errors import TypecheckError
from ginger.lower import lower_program
from ginger.parser import parse
from ginger.pipeline import compile, run
from ginger.symbols_builder import build_symbols
from ginger.typecheck import typecheck_program


class GuaranteeMethodArityTests(unittest.TestCase):
    def test_mismatch_rejected_before_execution(self):
        for params, sig_params, args, implementation in [
            ('self: Self, other: Self', 'T', '1', lambda a, b: a + b),
            ('self: Self', 'T,T', '1,2', lambda a: a),
        ]:
            declaration = (
                f'guarantee G {{ op({params}) -> Self }}\n'
                'impl Int guarantees G { op = builtin test.op }\n'
                f'sig op({sig_params}) -> T {{ require T guarantees G }}\n')
            for call in ['', f'var x: Int = op({args})']:
                source = declaration + call
                builtin = Mock(side_effect=implementation)
                with self.subTest(source=source), patch.dict(BUILTINS, {'test.op': builtin}):
                    program = lower_program(parse(source))
                    for check in [
                        lambda: build_symbols(program),
                        lambda: typecheck_program(program, Diagnostics()),
                        lambda: compile(source),
                        lambda: run(source),
                    ]:
                        with self.assertRaisesRegex(TypecheckError, "arity mismatch.*'op'"):
                            check()
                    builtin.assert_not_called()

    def test_matching_concrete_and_deferred_dispatch(self):
        source = (
            'guarantee G { op(self: Self, other: Self) -> Self }\n'
            'impl Int guarantees G { op = builtin test.op }\n'
            'sig op(T,T) -> T { require T guarantees G }\n'
            'sig relay(U,U) -> U { require U guarantees G }\n'
            'func relay(a: U,b: U) { return op(a,b) }\n'
            'var concrete: Int = op(1,2)\n'
            'var deferred: Int = relay(3,4)')
        builtin = Mock(side_effect=lambda a, b: a + b)
        with patch.dict(BUILTINS, {'test.op': builtin}):
            program = compile(source)
            syms = build_symbols(program)
            typecheck_program(program, Diagnostics(), syms=syms)
            call = syms.funcs['relay'].body.stmts[0].expr
            resolved = syms.resolved_calls[id(call)]
            self.assertEqual(resolved.type_bindings, {'T': TypeRef('U')})
            self.assertEqual(resolved.deferred_guarantee, 'G')
            self.assertEqual(resolved.deferred_method, 'op')
            self.assertIsNone(resolved.implementation)
            result = run(source)
        self.assertEqual(result.environment['concrete'].value, 3)
        self.assertEqual(result.environment['deferred'].value, 7)
        self.assertEqual([call.args for call in builtin.call_args_list], [(1, 2), (3, 4)])

    def test_user_function_is_not_guarantee_dispatch(self):
        source = (
            'guarantee G { op(self: Self, other: Self) -> Self }\n'
            'impl Int guarantees G { op = builtin test.op }\n'
            'sig op(T) -> T { require T guarantees G }\n'
            'func op(x: T) { return x }\n'
            'var x: Int = op(1)')
        builtin = Mock(side_effect=lambda a, b: a + b)
        with patch.dict(BUILTINS, {'test.op': builtin}):
            self.assertEqual(run(source).environment['x'].value, 1)
        builtin.assert_not_called()

    def test_multiple_guarantee_requirements_are_not_arity_validated(self):
        source = (
            'guarantee G { op(self: Self, other: Self) -> Self }\n'
            'guarantee H {}\n'
            'impl Int guarantees G { op = builtin test.op }\n'
            'register Int guarantees H\n'
            'sig op(T) -> T { require T guarantees G require T guarantees H }\n')
        with patch.dict(BUILTINS, {'test.op': lambda a, b: a + b}):
            compile(source)
            with self.assertRaisesRegex(TypecheckError, "no implementation for function 'op'"):
                compile(source + 'var x: Int = op(1)')


if __name__ == '__main__':
    unittest.main()
