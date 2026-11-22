import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from ginger.ast import ImplDecl, ImplMethod, TypeRef
from ginger.core.catalog_loader import load_core_catalog_json
from ginger.parser import Parser, parse
from ginger.pipeline import compile, execute, run
from ginger.symbols_builder import build_symbols
from ginger.tokenizer import tokenize
from ginger.type_identity import type_identity_key


class SourceBuiltinBoundaryTests(unittest.TestCase):
    def test_source_bindings_fail_before_runtime(self):
        declarations = [
            'guarantee Opposite { flip(self: Self) -> Self }\n'
            'impl Int guarantees Opposite { flip = builtin core.int.neg }',
            'impl Int guarantees Addable {}',
            'sig alias(Int) -> Int { builtin core.int.neg }',
            'sig alias(Int) -> Int {\n failure { IntegerOverflow }\n builtin core.int.neg\n}',
            '@attr.io\nsig alias(Int) -> Unit { builtin core.int.print }',
        ]
        for source in declarations:
            for entry in (parse, compile, run):
                with self.subTest(source=source, entry=entry.__name__):
                    with patch('ginger.pipeline.execute') as runtime:
                        with self.assertRaisesRegex(SyntaxError, 'builtin is internal'):
                            entry(source)
                        runtime.assert_not_called()

    def test_direct_parser_declaration_entry_points_reject(self):
        for source, method in [
            ('impl Int guarantees Addable { add = builtin core.int.add }', 'parse_impl'),
            ('sig alias(Int) -> Int { builtin core.int.neg }', 'parse_sig'),
        ]:
            with self.subTest(method=method):
                with self.assertRaisesRegex(SyntaxError, 'builtin is internal'):
                    getattr(Parser(tokenize(source)), method)()

    def test_builtin_is_not_an_expression_or_callable_name(self):
        for source in ('builtin core.int.neg', 'core.int.neg(1)',
                       'var x: Int = builtin core.int.neg',
                       'sig f() -> Int {}\nfunc f() { return builtin core.int.neg }'):
            with self.subTest(source=source), self.assertRaises(SyntaxError):
                compile(source)

    def test_catalog_impl_and_direct_sig_remain_executable(self):
        catalog = {
            'impls': [{'type': {'ref': 'Int'}, 'guarantee': 'Opposite',
                       'methods': [{'name': 'flip', 'builtin': 'core.int.neg'}]}],
            'sigs': [{'name': 'alias', 'params': ['Int'], 'ret': 'Int',
                      'builtin': 'core.int.neg'}],
        }
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'catalog.json'
            path.write_text(json.dumps(catalog))
            declarations = load_core_catalog_json(path)
        program = parse('guarantee Opposite { flip(self: Self) -> Self }\n'
                        'sig flip(T) -> T { require T guarantees Opposite }\n'
                        'var x: Int = flip(2)\nvar y: Int = alias(3)')
        program.items[1:1] = declarations
        symbols = build_symbols(program)
        self.assertEqual(symbols.impls[(type_identity_key(TypeRef('Int')), 'Opposite', 'flip')],
                         'core.int.neg')
        self.assertEqual(symbols.sigs['alias'].builtin, 'core.int.neg')
        result = execute(program)
        self.assertEqual(result.environment['x'].value, -2)
        self.assertEqual(result.environment['y'].value, -3)

    def test_internal_ast_remains_supported(self):
        program = parse('guarantee Opposite { flip(self: Self) -> Self }')
        program.items.append(ImplDecl(TypeRef('Int'), 'Opposite',
                                      [ImplMethod('flip', 'core.int.neg')]))
        self.assertIn((type_identity_key(TypeRef('Int')), 'Opposite', 'flip'),
                      build_symbols(program).impls)

    def test_standard_catalog_and_generic_dispatch_through_public_source(self):
        result = run('sig twice(T) -> T { require T guarantees Addable\n'
                     'failure { IntegerOverflow when T in BoundedArithmetic }\n'
                     'failure { FloatOverflow when T in FiniteArithmetic } }\n'
                     'func twice(x: T) { return add(x,x) }\n'
                     'var a: Int = add(1,2)\nvar b: Float = add(1.0,2.0)\n'
                     'var c: Int = sub(7,2)\nvar d: Int = mul(3,4)\n'
                     'var e: Int = neg(5)\nvar f: Int = twice(6)')
        self.assertEqual([result.environment[k].value for k in 'abcdef'],
                         [3, 3.0, 5, 12, -5, 12])
