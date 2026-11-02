import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from ginger.ast import Program, RegisterDecl
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
                source = 'guarantee Mark {}\nregister Mark {' + block + '}'
                program = parse(source)
                declarations = program.items[1:]
                self.assertTrue(all(isinstance(d, RegisterDecl) for d in declarations))
                self.assertEqual([d.typ.name for d in declarations], names)
                symbols = build_symbols(program)
                for name in names:
                    self.assertIn('Mark', symbols.type_guarantees[name])
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
                compile('guarantee Mark {}\n' + blocks)

    def test_unknown_guarantee(self):
        with self.assertRaisesRegex(TypecheckError, "unknown guarantee 'Missing'"):
            compile('register Missing { Int }')

    def test_forward_reference(self):
        compile('register Mark { Int, Float }\nguarantee Mark {}')

    def test_method_guarantee_rejected_in_either_order(self):
        declaration = 'guarantee CustomNegatable { neg(self: Self) -> Self }\n'
        registration = 'register CustomNegatable { Int }\n'
        for source in [declaration + registration, registration + declaration]:
            with self.subTest(source=source), self.assertRaisesRegex(TypecheckError, 'requires implementations'):
                compile(source)

    def test_typegroup_rejected_in_either_order(self):
        group = 'typegroup Numbers = Int | Float\n'
        registration = 'guarantee Mark {}\nregister Mark { Numbers }\n'
        for source in [group + registration, registration + group]:
            with self.subTest(source=source), self.assertRaisesRegex(TypecheckError, 'not a concrete register target'):
                compile(source)

    def test_existing_catalog_format_and_duplicate_with_source(self):
        data = {'guarantees': [{'name': 'Mark', 'methods': []}],
                'registers': [{'type': {'ref': 'Int'}, 'guarantee': 'Mark'}]}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'catalog.json'
            path.write_text(json.dumps(data))
            declarations = load_core_catalog_json(path)
        symbols = build_symbols(Program(declarations))
        self.assertIn('Mark', symbols.type_guarantees['Int'])
        with self.assertRaisesRegex(TypecheckError, 'duplicate register'):
            build_symbols(Program(declarations + parse('register Mark { Int }').items))
        # The shipped JSON catalog also participates in duplicate detection.
        with self.assertRaisesRegex(TypecheckError, 'duplicate register'):
            compile('register FiniteArithmetic { Float }')

    def test_generic_require_and_typegroup(self):
        source = '''sig keep(T) -> T {
    require T guarantees Mark
    require T in Numbers
}
func keep(x: T) { return x }
guarantee Mark {}
register Mark { Int, Float }
typegroup Numbers = Int | Float
print(keep(1))
print(keep(2.0))
'''
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            run(source)
        self.assertEqual(stdout.getvalue(), '1\n2.0\n')


if __name__ == '__main__':
    unittest.main()
