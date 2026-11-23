import io
import json
from contextlib import redirect_stdout
from dataclasses import fields
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from ginger.ast import FuncDecl, SigDecl
from ginger.core.catalog_loader import load_core_catalog_json
from ginger.lower import lower_program
from ginger.parser import parse
from ginger.pipeline import run
from ginger.symbols_builder import build_symbols


class RemovedAttributesTests(unittest.TestCase):
    def test_removed_syntax_rejected(self):
        sources = [
            f'@attr.{name}\n{decl}'
            for name in ('io', 'handled', 'custom')
            for decl in ('sig notify() -> Unit {}', 'func notify() {}')
        ] + [
            '@attr.io\n@attr.io\nsig notify() -> Unit {}',
            '@attr.io sig notify() -> Unit {}',
            'sig notify() -> Unit {}\n@attr.io\nfunc notify() {}',
            'func notify() {\n@attr.io\nprint(1)\n}',
            '@attr.io',
        ]
        for decl in ('var x: Int = 1', 'guarantee G {}',
                     'impl Int guarantees G {}', 'typegroup G {}',
                     'register G { Int }', 'failureset F { IOErr }',
                     'resolve x { IOErr { print(1) } }'):
            sources.extend(f'@attr.{name}\n{decl}' for name in ('io', 'handled'))
        for source in sources:
            for entry in (parse, run):
                with self.subTest(source=source, entry=entry.__name__):
                    with self.assertRaisesRegex(SyntaxError, "Unexpected character '@'"):
                        entry(source)

    def test_plain_function_prints(self):
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            run('sig notify() -> Unit {}\nfunc notify() { print(1) }\nnotify()')
        self.assertEqual(stdout.getvalue(), '1\n')

    def test_ast_reconstruction_without_attributes(self):
        for cls in (SigDecl, FuncDecl):
            self.assertNotIn('attrs', {field.name for field in fields(cls)})
        program = parse('sig identity(Int) -> Int { failure { IOErr } }\n'
                        'func identity(x: Int) { return x }')
        lowered = lower_program(program)
        symbols = build_symbols(lowered)
        for item in [*program.items, *lowered.items,
                     symbols.sigs['identity'], symbols.funcs['identity']]:
            self.assertFalse(hasattr(item, 'attrs'))
        self.assertEqual(lowered.items, program.items)
        self.assertEqual(symbols.sigs['identity'].failures, ['IOErr'])
        self.assertEqual(symbols.funcs['identity'].params, program.items[1].params)

    def test_standard_catalog_has_no_attributes(self):
        root = Path(__file__).resolve().parents[1] / 'ginger' / 'catalog'
        def check(value):
            if isinstance(value, dict):
                self.assertNotIn('attrs', value)
                for child in value.values():
                    check(child)
            elif isinstance(value, list):
                for child in value:
                    check(child)
        paths = list(root.glob('*.json'))
        self.assertTrue(paths)
        for path in paths:
            with self.subTest(path=path):
                check(json.loads(path.read_text()))
                load_core_catalog_json(path)
        self.assertIn('print', build_symbols(parse('')).sigs)

    def test_catalog_rejects_removed_field(self):
        for attrs in ([], ['io'], ['handled'], None):
            with self.subTest(attrs=attrs), TemporaryDirectory() as directory:
                path = Path(directory) / 'catalog.json'
                path.write_text(json.dumps({'sigs': [
                    {'name': 'f', 'params': [], 'ret': 'Unit', 'attrs': attrs}]}))
                with self.assertRaisesRegex(ValueError, "'attrs' has been removed"):
                    load_core_catalog_json(path)

    def test_other_unknown_catalog_keys_remain_ignored(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'catalog.json'
            path.write_text(json.dumps({'sigs': [
                {'name': 'f', 'params': [], 'ret': 'Unit', 'extra': []}]}))
            self.assertEqual(load_core_catalog_json(path)[0].name, 'f')
