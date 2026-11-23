import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from ginger.ast import GuaranteeDecl, ImplDecl, Program, RegisterDecl, TypeRef
from ginger.core.catalog_loader import load_core_catalog_json
from ginger.errors import TypecheckError
from ginger.parser import parse
from ginger.pipeline import compile, run
from ginger.symbols_builder import build_symbols


class RegisterBlockTests(unittest.TestCase):
    def test_block_forms_expand_to_individual_declarations(self):
        for block, names in [
            ('Int', ['Int']),
            ('Int, Float', ['Int', 'Float']),
            ('\nInt,\nFloat\n', ['Int', 'Float']),
            ('Int, Float,', ['Int', 'Float']),
            ('\nInt,\nFloat,\n', ['Int', 'Float']),
            ('Int64', ['Int64']),
        ]:
            with self.subTest(block=block):
                source = 'typegroup Mark {}\nregister Mark {' + block + '}'
                program = parse(source)
                declarations = program.items[1:]
                self.assertTrue(all(isinstance(d, RegisterDecl) for d in declarations))
                self.assertEqual([d.typ.name for d in declarations], names)
                symbols = build_symbols(program)
                self.assertEqual(symbols.typegroups['Mark'], set(names))
                for name in names:
                    self.assertNotIn('Mark', symbols.type_guarantees.get(name, set()))
                compile(source)

    def test_invalid_syntax(self):
        for source in ['register Mark {}', 'register Mark {\n}',
                       'register Int guarantees Mark',
                       'register Mark { Int Float }', 'register Mark { Int,, Float }']:
            with self.subTest(source=source), self.assertRaises(SyntaxError):
                parse(source)

    def test_duplicate_identity(self):
        for blocks in ['register Mark { Int, Int }',
                       'register Mark { Int, Float }\nregister Mark { Int }',
                       'register Mark { Box[Int], Box[Int] }',
                       'register Mark { Thunk[Int]<Never>, Thunk[Int]<IntegerOverflow> }']:
            with self.subTest(blocks=blocks), self.assertRaisesRegex(TypecheckError, 'duplicate register'):
                compile('typegroup Mark {}\n' + blocks)

    def test_unknown_destination(self):
        with self.assertRaisesRegex(TypecheckError, "unknown register target 'Missing'"):
            compile('register Missing { Int }')

    def test_forward_reference(self):
        compile('register Mark { Int, Float }\ntypegroup Mark {}')

    def test_method_guarantee_rejected_in_either_order(self):
        declaration = 'guarantee CustomNegatable { neg(self: Self) -> Self }\n'
        registration = 'register CustomNegatable { Int }\n'
        for source in [declaration + registration, registration + declaration]:
            with self.subTest(source=source), self.assertRaisesRegex(
                    TypecheckError, 'register target must be a typegroup: CustomNegatable is a guarantee'):
                compile(source)

    def test_empty_guarantee_rejected_in_either_order(self):
        declaration = 'guarantee Mark {}\n'
        registration = 'register Mark { Int, Float }\n'
        for source in [declaration + registration, registration + declaration]:
            with self.subTest(source=source), self.assertRaisesRegex(
                    TypecheckError, 'register target must be a typegroup: Mark is a guarantee'):
                compile(source)

    def test_catalog_guarantee_registration_is_rejected(self):
        for methods in [[], [{'name': 'op', 'params': [], 'ret': 'Int'}]]:
            data = {'guarantees': [{'name': 'Mark', 'methods': methods}],
                    'registers': [{'type': 'Int', 'guarantee': 'Mark'}]}
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'catalog.json'
                path.write_text(json.dumps(data))
                declarations = load_core_catalog_json(path)
            with self.assertRaisesRegex(TypecheckError, 'Mark is a guarantee'):
                build_symbols(Program(declarations))

    def test_empty_guarantee_impl_remains_supported(self):
        symbols = build_symbols(Program([
            GuaranteeDecl('Mark', []), ImplDecl(TypeRef('Int'), 'Mark', [])]))
        self.assertIn('Mark', symbols.type_guarantees['Int'])
        self.assertNotIn('Mark', symbols.typegroups)

    def test_typegroup_rejected_in_either_order(self):
        group = 'typegroup Numbers { Int, Float }\n'
        registration = 'typegroup Mark {}\nregister Mark { Numbers }\n'
        for source in [group + registration, registration + group]:
            with self.subTest(source=source), self.assertRaisesRegex(TypecheckError, 'not a concrete register target'):
                compile(source)

    def test_existing_catalog_format_and_duplicate_with_source(self):
        data = {'typegroups': [{'name': 'Mark', 'members': []}],
                'registers': [{'type': {'ref': 'Int'}, 'guarantee': 'Mark'}]}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'catalog.json'
            path.write_text(json.dumps(data))
            declarations = load_core_catalog_json(path)
        symbols = build_symbols(Program(declarations))
        self.assertEqual(symbols.typegroups['Mark'], {'Int'})
        with self.assertRaisesRegex(TypecheckError, 'duplicate register'):
            build_symbols(Program(declarations + parse('register Mark { Int }').items))
        # The shipped JSON catalog also participates in duplicate detection.
        with self.assertRaisesRegex(TypecheckError, 'duplicate register'):
            compile('register FiniteArithmetic { Float }')

    def test_generic_require_and_typegroup(self):
        source = '''sig keep(T) -> T {
    require T in Mark
    require T in Numbers
}
func keep(x: T) { return x }
typegroup Mark {}
register Mark { Int, Float }
typegroup Numbers { Int, Float }
print(keep(1))
print(keep(2.0))
'''
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            run(source)
        self.assertEqual(stdout.getvalue(), '1\n2.0\n')
        with self.assertRaisesRegex(TypecheckError, 'T in Mark required'):
            compile(source + 'keep(1i64)')


if __name__ == '__main__':
    unittest.main()
