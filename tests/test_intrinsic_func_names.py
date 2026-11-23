"""Reserve intrinsic names only at the user func registration boundary."""
import io
from contextlib import redirect_stdout
import unittest

from ginger.diagnostics import Diagnostics
from ginger.errors import TypecheckError
from ginger.eval import eval_program
from ginger.lower import lower_program
from ginger.parser import parse
from ginger.symbols_builder import build_symbols
from ginger.typecheck import typecheck_program


class IntrinsicFuncNamesTests(unittest.TestCase):
    def assert_rejected(self, name, body, parameter_type='T'):
        program = lower_program(parse(
            f'func {name}(x: {parameter_type}) {{ {body} }}'))
        for check in (build_symbols, lambda p: typecheck_program(p, Diagnostics())):
            with self.subTest(name=name, check=check), self.assertRaises(TypecheckError) as error:
                check(program)
            self.assertEqual(str(error.exception),
                             f"cannot define func '{name}': reserved intrinsic name")

    def run_source(self, source):
        program = lower_program(parse(source))
        symbols = build_symbols(program)
        typecheck_program(program, Diagnostics(), syms=symbols)
        stream = io.StringIO()
        with redirect_stdout(stream):
            result = eval_program(program)
        self.assertIsNone(result.contract_violation)
        return symbols, stream.getvalue()

    def test_intrinsic_func_declarations_rejected(self):
        for name in ('thunk', 'force'):
            self.assert_rejected(name, 'return x')

    def test_name_collision_precedes_body_type_error(self):
        for name in ('thunk', 'force'):
            self.assert_rejected(name, 'var bad: Int = 1.5\nreturn x')

    def test_name_collision_precedes_body_failure_contract_violation(self):
        for name in ('thunk', 'force'):
            self.assert_rejected(name, 'div(1.0, 0.0)\nreturn x')

    def test_name_collision_precedes_parameter_mismatch(self):
        for name in ('thunk', 'force'):
            self.assert_rejected(name, 'return x', 'Int')

    def test_standard_sigs_and_intrinsic_calls_remain(self):
        symbols, stdout = self.run_source('print(force(thunk(4)))')
        self.assertEqual(stdout, '4\n')
        for name in ('thunk', 'force'):
            self.assertIn(name, symbols.sigs)
            self.assertNotIn(name, symbols.funcs)

    def test_source_sig_redeclarations_remain_duplicates(self):
        for name in ('thunk', 'force'):
            with self.subTest(name=name), self.assertRaisesRegex(
                    TypecheckError, f"duplicate sig '{name}'"):
                build_symbols(parse(f'sig {name}(T) -> T {{}}'))

    def test_variables_coexist_with_intrinsic_calls(self):
        _, stdout = self.run_source('var force: Int = 9\nvar thunk: Int = 4\n'
                                    'print(force)\nprint(thunk)\n'
                                    'print(force(thunk(force)))')
        self.assertEqual(stdout, '9\n4\n9\n')

    def test_parameters_coexist_with_intrinsic_calls(self):
        for name in ('thunk', 'force'):
            with self.subTest(name=name):
                _, stdout = self.run_source('sig example(Int) -> Int {}\n'
                    f'func example({name}: Int) {{ return force(thunk({name})) }}\n'
                    'print(example(8))')
                self.assertEqual(stdout, '8\n')

    def test_independent_declaration_names_remain_allowed(self):
        for name in ('thunk', 'force'):
            with self.subTest(name=name):
                symbols, stdout = self.run_source(
                    f'guarantee {name} {{ {name}(self: Self) -> Self }}\n'
                    f'failureset {name} {{ IOErr }}\nprint(force(thunk(4)))')
                self.assertEqual(symbols.guarantees[name].methods[0].name, name)
                self.assertIn(name, symbols.failuresets)
                self.assertEqual(stdout, '4\n')

    def test_ordinary_and_partial_match_function_names_remain_allowed(self):
        for name in ('identity', 'thunkValue', 'enforce'):
            with self.subTest(name=name):
                symbols, stdout = self.run_source(f'sig {name}(Int) -> Int {{}}\n'
                    f'func {name}(x: Int) {{ return x }}\nprint({name}(7))')
                self.assertIn(name, symbols.funcs)
                self.assertEqual(stdout, '7\n')


if __name__ == '__main__':
    unittest.main()
