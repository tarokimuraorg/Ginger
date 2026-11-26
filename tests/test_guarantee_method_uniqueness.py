"""Guarantee method names are unique at the shared declaration boundary."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ginger.ast import FuncSig, GuaranteeDecl, ImplDecl, ImplMethod, Param, Program, TypeRef
from ginger.core.catalog_loader import load_core_catalog_json
from ginger.core.prelude import prelude_items
from ginger.diagnostics import Diagnostics
from ginger.errors import TypecheckError
from ginger.parser import parse
from ginger.pipeline import compile, run
from ginger.symbols_builder import build_symbols
from ginger.typecheck import typecheck_program


FIRST = 'first(self: Self) -> Self'


class GuaranteeMethodUniquenessTests(unittest.TestCase):
    def assert_duplicate(self, check):
        with self.assertRaises(TypecheckError) as caught:
            check()
        self.assertEqual(str(caught.exception),
                         "duplicate method 'first' in guarantee 'Example'")

    def assert_source_duplicate(self, methods):
        source = 'guarantee Example { ' + '\n'.join(methods) + ' }'
        for name, check in [
            ('symbols', lambda: build_symbols(parse(source))),
            ('typecheck', lambda: typecheck_program(parse(source), Diagnostics())),
            ('compile', lambda: compile(source)),
            ('run', lambda: run(source)),
        ]:
            with self.subTest(entrypoint=name):
                self.assert_duplicate(check)

    def test_identical_methods(self):
        self.assert_source_duplicate([FIRST, FIRST])

    def test_different_arity(self):
        self.assert_source_duplicate([FIRST, 'first(self: Self, other: Self) -> Self'])

    def test_different_parameter_type(self):
        self.assert_source_duplicate(['first(self: Self, other: Int) -> Self',
                                      'first(self: Self, other: Float) -> Self'])

    def test_different_return_type(self):
        self.assert_source_duplicate([FIRST, 'first(self: Self) -> Float'])

    def test_three_occurrences(self):
        self.assert_source_duplicate([FIRST, FIRST, FIRST])

    def test_nonleading_nonadjacent_duplicate(self):
        self.assert_source_duplicate(['before(self: Self) -> Self', FIRST,
                                      'between(self: Self) -> Self', FIRST])

    def test_internal_ast_before_impl_errors_in_either_order(self):
        def method():
            return FuncSig('first', [Param('self', TypeRef('Self'))], TypeRef('Self'))
        definition = GuaranteeDecl('Example', [method(), method()])
        impl = ImplDecl(TypeRef('Int'), 'Example', [])
        for items in [[definition], [definition, impl], [impl, definition]]:
            with self.subTest(items=items):
                self.assert_duplicate(lambda: build_symbols(Program(items)))

    def test_duplicate_before_sig_contract_errors(self):
        definition = f'guarantee Example {{ {FIRST} {FIRST} }}\n'
        signature = 'sig first[T](T,T) -> Float { require T guarantees Example }\n'
        for source in [definition + signature, signature + definition]:
            with self.subTest(source=source):
                self.assert_duplicate(lambda: compile(source))

    def test_catalog_duplicates(self):
        method = {'name': 'first', 'params': [{'name': 'self', 'type': 'Self'}],
                  'ret': 'Self'}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'catalog.json'
            path.write_text(json.dumps({'guarantees': [
                {'name': 'Example', 'methods': [method, method]}]}), encoding='utf-8')
            items = load_core_catalog_json(path)
        self.assert_duplicate(lambda: build_symbols(Program(items)))
        with patch('ginger.symbols_builder.prelude_items', side_effect=lambda: list(items)):
            self.assert_duplicate(lambda: compile(''))

    def test_distinct_method_names(self):
        compile(f'guarantee Example {{ {FIRST} second(self: Self) -> Self }}')

    def test_same_name_in_different_guarantees(self):
        compile(f'guarantee A {{ {FIRST} }} guarantee B {{ {FIRST} }}')

    def test_single_method(self):
        compile(f'guarantee Example {{ {FIRST} }}')

    def test_complete_single_impl(self):
        items = parse(f'guarantee Example {{ {FIRST} second(self: Self) -> Self }}').items
        items.append(ImplDecl(TypeRef('Int'), 'Example', [
            ImplMethod(name, 'core.int.neg') for name in ['first', 'second']]))
        symbols = build_symbols(Program(items))
        for name in ['first', 'second']:
            self.assertEqual(symbols.impls[('Int', 'Example', name)], 'core.int.neg')

    def test_standard_catalog_guarantees(self):
        declarations = [item for item in prelude_items() if isinstance(item, GuaranteeDecl)]
        self.assertEqual(len(declarations), 6)
        symbols = build_symbols(Program([]))
        self.assertEqual(set(symbols.guarantees), {item.name for item in declarations})
        for declaration in declarations:
            with self.subTest(guarantee=declaration.name):
                names = [method.name for method in declaration.methods]
                self.assertEqual(len(names), len(set(names)))
                self.assertEqual(symbols.guarantees[declaration.name].methods, declaration.methods)


if __name__ == '__main__':
    unittest.main()
