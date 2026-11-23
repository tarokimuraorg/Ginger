"""Impl method sets must exactly match their guarantee, through either input path."""
import json
import tempfile
import unittest
from pathlib import Path

from ginger.ast import ImplDecl, Program
from ginger.core.catalog_loader import load_core_catalog_json
from ginger.core.prelude import prelude_items
from ginger.errors import TypecheckError
from ginger.symbols_builder import build_symbols
from ginger.type_identity import type_identity_key
from internal_declarations import parse
from test_impl_uniqueness import PROBE, implementation


class ImplMethodTests(unittest.TestCase):
    def test_extra_methods_rejected_in_internal_ast(self):
        for methods in [('first', 'extra'), ('first', 'second', 'extra'), ('extra',)]:
            with self.subTest(methods=methods), self.assertRaisesRegex(
                    TypecheckError, "impl for type 'Int', guarantee 'Probe' defines unknown method 'extra'"):
                build_symbols(parse(PROBE + implementation(methods=methods)))

    def test_extra_methods_rejected_through_catalog_loader(self):
        for methods in [('first', 'extra'), ('first', 'second', 'extra'), ('extra',)]:
            with self.subTest(methods=methods), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'catalog.json'
                path.write_text(json.dumps({'impls': [
                    {'type': 'Int', 'guarantee': 'Probe', 'methods': [
                        {'name': name, 'builtin': 'core.int.neg'} for name in methods]}]}))
                program = parse(PROBE)
                program.items.extend(load_core_catalog_json(path))
                with self.assertRaisesRegex(TypecheckError, "defines unknown method 'extra'"):
                    build_symbols(program)

    def test_unknown_guarantee_has_priority(self):
        with self.assertRaisesRegex(TypecheckError, "^unknown guarantee 'Missing' for type 'Int'$"):
            build_symbols(parse(implementation(guarantee='Missing', methods=('extra',))))

    def test_complete_methods_and_forward_guarantee_are_accepted(self):
        for guarantee, methods in [('guarantee Probe { first(self: Self) -> Self }', ('first',)),
                                   (PROBE, ('first', 'second'))]:
            for source in [guarantee + '\n' + implementation(methods=methods),
                           implementation(methods=methods) + guarantee]:
                with self.subTest(source=source):
                    symbols = build_symbols(parse(source))
                    self.assertEqual({m for t, g, m in symbols.impls if g == 'Probe'}, set(methods))

    def test_standard_catalog_ast_and_symbols_have_exact_method_sets(self):
        declarations = [item for item in prelude_items() if isinstance(item, ImplDecl)]
        symbols = build_symbols(Program([]))
        pairs = [(type_identity_key(item.typ), item.guarantee) for item in declarations]
        self.assertEqual(len(declarations), 18)
        self.assertEqual(len(set(pairs)), 18)
        self.assertEqual(len(symbols.impls), 18)
        self.assertEqual(sum(len(item.methods) for item in declarations), 18)
        for item, (typ, guarantee) in zip(declarations, pairs):
            with self.subTest(typ=typ, guarantee=guarantee):
                names = [method.name for method in item.methods]
                required = {method.name for method in symbols.guarantees[guarantee].methods}
                self.assertEqual(len(names), len(set(names)))
                self.assertEqual(set(names), required)
                self.assertEqual({m for t, g, m in symbols.impls if (t, g) == (typ, guarantee)}, required)
                for method in item.methods:
                    self.assertEqual(symbols.impls[(typ, guarantee, method.name)], method.builtin)
