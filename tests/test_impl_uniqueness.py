"""Impl declarations are unique independently of their method contents."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ginger.ast import ImplDecl, Program
from ginger.core.catalog_loader import load_core_catalog_json
from ginger.core.prelude import prelude_items
from ginger.errors import TypecheckError
from ginger.symbols_builder import build_symbols
from ginger.type_identity import type_identity_key
from internal_declarations import parse


PROBE = 'guarantee Probe { first(self: Self) -> Self second(self: Self) -> Self }\n'


def implementation(typ='Int', guarantee='Probe', methods=('first', 'second')):
    body = ' '.join(f'{name} = builtin core.int.neg' for name in methods)
    return f'impl {typ} guarantees {guarantee} {{ {body} }}\n'


class ImplUniquenessTests(unittest.TestCase):
    def assert_duplicate(self, program, typ='Int', guarantee='Probe', method=None):
        with self.assertRaises(TypecheckError) as caught:
            build_symbols(program)
        expected = f"duplicate impl for type '{typ}', guarantee '{guarantee}'"
        if method is not None:
            expected += f", method '{method}'"
        self.assertEqual(str(caught.exception), expected)

    def test_duplicate_declarations_regardless_of_methods(self):
        for first, second in [
            (('first', 'second'), ('first', 'second')),
            (('first',), ('second',)),
            (('first', 'second'), ()),
            ((), ('first', 'second')),
            ((), ()),
        ]:
            with self.subTest(first=first, second=second):
                self.assert_duplicate(parse(PROBE + implementation(methods=first)
                                            + implementation(methods=second)))

    def test_duplicate_methods_within_one_declaration(self):
        self.assert_duplicate(parse(PROBE + implementation(methods=('first', 'first', 'second'))),
                              method='first')

    def test_single_empty_or_incomplete_impl_keeps_missing_method_check(self):
        for methods in [(), ('first',)]:
            with self.subTest(methods=methods), self.assertRaisesRegex(TypecheckError, 'missing impl'):
                build_symbols(parse(PROBE + implementation(methods=methods)))

    def test_standard_catalog_conflicts_with_internal_ast(self):
        for methods in [('add',), (), ('extra',)]:
            with self.subTest(methods=methods):
                self.assert_duplicate(parse(implementation(guarantee='Addable', methods=methods)),
                                      guarantee='Addable')

    def test_catalog_duplicates_through_loader(self):
        for first, second in [(('first', 'second'), ('first', 'second')),
                              (('first',), ('second',)),
                              (('first', 'second'), ()), ((), ('first', 'second')), ((), ())]:
            with self.subTest(first=first, second=second), tempfile.TemporaryDirectory() as directory:
                data = {'impls': [
                    {'type': 'Int', 'guarantee': 'Probe',
                     'methods': [{'name': name, 'builtin': 'core.int.neg'} for name in methods]}
                    for methods in [first, second]
                ]}
                path = Path(directory) / 'catalog.json'
                path.write_text(json.dumps(data), encoding='utf-8')
                items = prelude_items() + load_core_catalog_json(path)
                with patch('ginger.symbols_builder.prelude_items', return_value=items):
                    self.assert_duplicate(parse(PROBE))

    def test_independent_pairs_and_complete_multimethod_impls(self):
        source = PROBE + PROBE.replace('Probe', 'Other')
        source += implementation() + implementation(guarantee='Other')
        source += implementation('Float')
        symbols = build_symbols(parse(source))
        for typ, guarantee in [('Int', 'Probe'), ('Int', 'Other'), ('Float', 'Probe')]:
            self.assertIn(guarantee, symbols.type_guarantees[typ])
            for method in ['first', 'second']:
                self.assertEqual(symbols.impls[(typ, guarantee, method)], 'core.int.neg')

    def test_type_arguments_and_order_remain_distinct(self):
        types = ['Box[Int]', 'Box[Float]', 'Pair[Int,Float]', 'Pair[Float,Int]']
        program = parse('type Box\ntype Pair\n' + PROBE + ''.join(implementation(typ) for typ in types))
        symbols = build_symbols(program)
        keys = [type_identity_key(item.typ) for item in program.items if isinstance(item, ImplDecl)]
        self.assertEqual(len(set(keys)), 4)
        for key in keys:
            self.assertEqual(symbols.impls[(key, 'Probe', 'first')], 'core.int.neg')

    def test_same_identity_spelling_and_thunk_contracts_cannot_bypass_check(self):
        for first, second in [('Box[Int]', 'Box[ Int ]'),
                              ('Thunk[Pure]', 'Thunk[ Pure ]'),
                              ('Thunk[Checked]', 'Thunk[Checked]')]:
            with self.subTest(first=first, second=second):
                program = parse('sigtype Checked() -> Int { failure { IntegerOverflow } }\nsigtype Pure() -> Int {}\ntype Box\nfailureset Overflow { IntegerOverflow }\n' + PROBE
                                + implementation(first) + implementation(second))
                self.assert_duplicate(program, typ=type_identity_key(program.items[-1].typ))

    def test_invalid_targets_are_checked_before_duplicate_declarations(self):
        for typ in ['T', 'Self', 'Box[T]', 'Box[Self]']:
            with self.subTest(typ=typ), self.assertRaisesRegex(TypecheckError, 'not allowed in impl target'):
                build_symbols(parse('type Box\n' + PROBE + implementation(typ) + implementation(typ)))

    def test_standard_catalog_pairs_and_every_method_are_preserved(self):
        declarations = [item for item in prelude_items() if isinstance(item, ImplDecl)]
        pairs = [(type_identity_key(item.typ), item.guarantee) for item in declarations]
        self.assertEqual(len(pairs), 18)
        self.assertEqual(len(set(pairs)), len(pairs))
        symbols = build_symbols(Program([]))
        self.assertTrue({'Addable', 'Subtractable', 'Multipliable', 'Negatable', 'Ord', 'Printable'}
                        <= {g for _, g in pairs})
        for item, (typ, guarantee) in zip(declarations, pairs):
            self.assertIn(guarantee, symbols.type_guarantees[typ])
            for method in item.methods:
                self.assertEqual(symbols.impls[(typ, guarantee, method.name)], method.builtin)


if __name__ == '__main__':
    unittest.main()
