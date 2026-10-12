import unittest
from unittest.mock import Mock, patch

from ginger.builtin import BUILTINS
from ginger.diagnostics import Diagnostics
from ginger.errors import TypecheckError
from ginger.lower import lower_program
from ginger.parser import parse
from ginger.pipeline import compile, run
from ginger.symbols_builder import build_symbols
from ginger.typecheck import typecheck_program


def declaration(params, ret, sig_params, sig_ret, variable='T'):
    return (
        f'guarantee G {{ op({params}) -> {ret} }}\n'
        'impl Int guarantees G { op = builtin test.op }\n'
        f'sig op({sig_params}) -> {sig_ret} {{ require {variable} guarantees G }}\n'
    )


class GuaranteeMethodSignatureTests(unittest.TestCase):
    def assert_rejected(self, source, diagnostic, call=''):
        builtin = Mock(side_effect=lambda *args: args[0])
        with patch.dict(BUILTINS, {'test.op': builtin}):
            for suffix in ['', call] if call else ['']:
                program = lower_program(parse(source + suffix))
                for check in [
                    lambda: build_symbols(program),
                    lambda: typecheck_program(program, Diagnostics()),
                    lambda: compile(source + suffix),
                    lambda: run(source + suffix),
                ]:
                    with self.subTest(suffix=suffix), self.assertRaisesRegex(TypecheckError, diagnostic):
                        check()
            builtin.assert_not_called()

    def test_parameter_mismatch(self):
        self.assert_rejected(
            declaration('self: Self, other: Float', 'Self', 'T,T', 'T'),
            "parameter type mismatch.*'op'.*argument 2", 'var x: Int = op(1,2)')

    def test_return_mismatch(self):
        self.assert_rejected(
            declaration('self: Self', 'Float', 'T', 'T'),
            "return type mismatch.*'op'", 'var x: Int = op(1)')

    def test_concrete_parameter_and_return_are_preserved(self):
        for ret, sig_ret, result_type, implementation, expected in [
            ('Float', 'Float', 'Float', lambda a, b: b, 2.5),
            ('Self', 'U', 'Int', lambda a, b: a, 1),
        ]:
            source = declaration('receiver: Self, other: Float', ret, 'U,Float', sig_ret, 'U')
            builtin = Mock(side_effect=implementation)
            with self.subTest(ret=ret), patch.dict(BUILTINS, {'test.op': builtin}):
                result = run(source + f'var x: {result_type} = op(1,2.5)')
            self.assertEqual(result.environment['x'].value, expected)
            builtin.assert_called_once_with(1, 2.5)

    def test_required_variable_is_not_inferred_from_position(self):
        source = declaration('other: Float, receiver: Self', 'Self', 'Float,U', 'U', 'U')
        with patch.dict(BUILTINS, {'test.op': lambda a, b: b}):
            self.assertEqual(run(source + 'var x: Int = op(2.5,1)').environment['x'].value, 1)

    def test_sig_only_variable_is_not_a_projection(self):
        self.assert_rejected(
            declaration('self: Self, other: Self', 'Self', 'T,U', 'U'),
            'parameter type mismatch.*argument 2')

    def test_widening_is_not_signature_identity(self):
        for method_type, sig_type in [('Float', 'Int'), ('Int64', 'Int')]:
            with self.subTest(method_type=method_type):
                self.assert_rejected(
                    declaration(f'self: Self, other: {method_type}', 'Self', f'T,{sig_type}', 'T'),
                    'parameter type mismatch')
                self.assert_rejected(
                    declaration('self: Self', method_type, 'T', sig_type),
                    'return type mismatch')

    def test_recursive_projection_and_normalization(self):
        for method_type, sig_type in [
            ('Thunk[Self, Never]', 'Thunk[T, Never]'),
            ('Thunk[Thunk[Self, IntegerOverflow], Never]',
             'Thunk[Thunk[T, Overflow], Never]'),
            ('SomeType[Self]', 'SomeType[T]'),
        ]:
            source = 'failureset Overflow { IntegerOverflow }\n' + declaration(
                f'self: Self, other: {method_type}', method_type, f'T,{sig_type}', sig_type)
            with self.subTest(method_type=method_type), patch.dict(BUILTINS, {'test.op': lambda a, b: b}):
                compile(source)

    def test_nested_structure_and_latent_failures_must_match(self):
        for method_type, sig_type in [
            ('Thunk[Self, Never]', 'Thunk[T, IntegerOverflow]'),
            ('Thunk[Self, IntegerOverflow]', 'Thunk[T, Never]'),
            ('Thunk[Thunk[Self, Never], Never]', 'Thunk[Thunk[Float, Never], Never]'),
            ('SomeType[Self]', 'OtherType[T]'),
            ('SomeType[Self]', 'SomeType[T,Int]'),
        ]:
            with self.subTest(method_type=method_type, sig_type=sig_type):
                self.assert_rejected(
                    declaration(f'self: Self, other: {method_type}', 'Self', f'T,{sig_type}', 'T'),
                    'parameter type mismatch')
                self.assert_rejected(
                    declaration('self: Self', method_type, 'T', sig_type),
                    'return type mismatch')

    def test_nested_projection_concrete_and_deferred_runtime(self):
        source = declaration('self: Self, other: Thunk[Self, Never]',
                             'Thunk[Self, Never]', 'T,Thunk[T, Never]', 'Thunk[T, Never]')
        source += (
            'sig relay(U,Thunk[U, Never]) -> Thunk[U, Never] { require U guarantees G }\n'
            'func relay(a: U,b: Thunk[U, Never]) { return op(a,b) }\n'
            'var concrete: Int = force(op(1,thunk(2)))\n'
            'var deferred: Int = force(relay(3,thunk(4)))')
        with patch.dict(BUILTINS, {'test.op': lambda a, b: b}):
            result = run(source)
        self.assertEqual(result.environment['concrete'].value, 2)
        self.assertEqual(result.environment['deferred'].value, 4)

    def test_arity_then_parameters_then_return(self):
        self.assert_rejected(
            declaration('self: Self, other: Float', 'Float', 'T', 'T'), 'arity mismatch')
        self.assert_rejected(
            declaration('self: Self, other: Float', 'Float', 'T,T', 'T'),
            'parameter type mismatch')

    def test_user_function_and_direct_builtin_are_exempt(self):
        source = declaration('self: Self', 'Float', 'T', 'T')
        with patch.dict(BUILTINS, {'test.op': lambda a: a}):
            for program in [source + 'func op(x: T) { return x }\n',
                            source.replace('require T guarantees G',
                                           'builtin test.op')]:
                with self.subTest(program=program):
                    self.assertEqual(run(program + 'var x: Int = op(1)').environment['x'].value, 1)

    def test_absent_method_unknown_guarantee_and_multiple_requirements_unchanged(self):
        for source in [
            'guarantee G {}\nsig op(T) -> T { require T guarantees G }',
            'sig op(T) -> T { require T guarantees UnknownGuarantee }',
            'guarantee G { op(self: Self) -> Float }\nguarantee H {}\n'
            'sig op(T) -> T { require T guarantees G require T guarantees H }',
        ]:
            with self.subTest(source=source):
                compile(source)

    def test_public_failure_contract_remains_on_sig(self):
        source = declaration('self: Self', 'Self', 'T', 'T').replace(
            'require T guarantees G', 'require T guarantees G failure IntegerOverflow')
        with patch.dict(BUILTINS, {'test.op': lambda a: a}):
            compile(source)


if __name__ == '__main__':
    unittest.main()
