import unittest

from test_failure_contract import checked
from ginger.ast import TypeRef
from ginger.core.failure_spec import EMPTY_FAILURES, FailureId
from ginger.errors import TypecheckError
from ginger.parser import parse
from ginger.symbols_builder import build_symbols, normalize_types
from ginger.typecheck import type_expr


def parsed_type(spelling):
    return parse(f'var t: {spelling} = thunk(1)').items[0].typ


class ThunkSyntaxTests(unittest.TestCase):
    def test_parser_separates_arguments_and_contract(self):
        for specs in [('Never',), ('IntegerOverflow',),
                      ('IntegerOverflow', 'DivideByZero')]:
            with self.subTest(specs=specs):
                typ = parsed_type(f'Thunk[Int]<{", ".join(specs)}>')
                self.assertEqual(typ.args, (TypeRef('Int'),))
                self.assertEqual(typ.failure_specs, specs)
                self.assertIsNone(typ.latent_failures)

    def test_nested_type_and_whitespace_boundaries(self):
        typ = parsed_type('Thunk[Box[Int]] <IntegerOverflow>')
        self.assertEqual(typ.args, (TypeRef('Box', (TypeRef('Int'),)),))
        nested = parsed_type('Thunk[Thunk[Int]<IntegerOverflow>]<Never>')
        self.assertEqual(nested.args[0].failure_specs, ('IntegerOverflow',))
        self.assertEqual(nested.failure_specs, ('Never',))

    def test_generic_signature_parameter_and_return(self):
        sig = parse('sig use(Thunk[T]<Never>) -> Thunk[T]<Never> {}').items[0]
        self.assertEqual(sig.params[0], sig.ret)
        self.assertEqual(sig.ret.args, (TypeRef('T'),))
        self.assertEqual(sig.ret.failure_specs, ('Never',))

    def test_old_syntax_and_wrong_arity_are_rejected(self):
        # Intentional legacy spellings exercise the breaking syntax migration.
        for spelling in ['Thunk[Int, Never]', 'Thunk[Int, IntegerOverflow]',
                         'Thunk[Int, Float]<Never>', 'Thunk<Never>']:
            for source in [f'var t: {spelling} = thunk(1)',
                           f'sig f({spelling}) -> Unit {{}}',
                           f'sig f() -> {spelling} {{}}']:
                with self.subTest(source=source), self.assertRaisesRegex(
                        TypecheckError, 'Thunk expects one type argument'):
                    checked(source)

    def test_empty_and_malformed_contracts(self):
        for spelling in ['Thunk[Int]<>', 'Thunk[Int<Never>', 'Thunk[Int]<Never',
                         'Thunk[Int]Never>', 'Thunk[Int]<Never,>',
                         'Thunk[Int]<IntegerOverflow when T guarantees G>',
                         'Thunk[Int]<Box[Int]>']:
            with self.subTest(spelling=spelling), self.assertRaises(SyntaxError):
                parsed_type(spelling)

    def test_non_thunk_postfix_is_rejected_semantically(self):
        for spelling in ['Int<IntegerOverflow>', 'Box[Int]<IntegerOverflow>',
                         'Thunk[Box[Int]<Never>]<Never>']:
            with self.subTest(spelling=spelling), self.assertRaisesRegex(
                    TypecheckError, 'allowed only on Thunk'):
                normalize_types(parsed_type(spelling), {})

    def test_failure_declaration_does_not_discard_postfix(self):
        with self.assertRaisesRegex(SyntaxError, 'failure names cannot have'):
            parse('sig f() -> Int { failure IntegerOverflow<Never> }')

    def test_never_mixing_duplicates_and_unknown_names(self):
        for spec, message in [('Never, IntegerOverflow', 'cannot combine'),
                              ('IntegerOverflow, Never', 'cannot combine'),
                              ('IntegerOverflow, IntegerOverflow', 'duplicate failure'),
                              ('MissingFailure', 'unknown failure')]:
            with self.subTest(spec=spec), self.assertRaisesRegex(TypecheckError, message):
                normalize_types(parsed_type(f'Thunk[Int]<{spec}>'), {})
        pure = normalize_types(parsed_type('Thunk[Int]<Never>'), {})
        self.assertEqual(pure.latent_failures, EMPTY_FAILURES)
        self.assertIsNone(pure.failure_specs)
        self.assertNotIn('Never', FailureId.__members__)

    def test_multiple_failures_are_order_independent(self):
        first = normalize_types(parsed_type('Thunk[Int]<IntegerOverflow, DivideByZero>'), {})
        second = normalize_types(parsed_type('Thunk[Int]<DivideByZero, IntegerOverflow>'), {})
        self.assertEqual(first, second)
        self.assertEqual(first.latent_failures,
                         frozenset({FailureId.IntegerOverflow, FailureId.DivideByZero}))

    def test_intrinsic_inferred_types_match_annotations(self):
        # Local declarations require annotations; expression_types records inference.
        for expression, contract in [('thunk(1)', 'Never'),
                                     ('thunk(add(1,2))', 'IntegerOverflow')]:
            program = parse(expression)
            symbols = build_symbols(program)
            actual = type_expr(program.items[0].expr, None, {}, symbols)
            expected = normalize_types(parsed_type(f'Thunk[Int]<{contract}>'), {})
            self.assertEqual(actual, expected)


if __name__ == '__main__':
    unittest.main()
