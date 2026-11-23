"""Empty contracts are rejected at the shared declaration boundary."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ginger.ast import GuaranteeDecl, ImplDecl, Program, TypeRef
from ginger.core.catalog_loader import load_core_catalog_json
from ginger.diagnostics import Diagnostics
from ginger.errors import TypecheckError
from ginger.parser import parse
from ginger.pipeline import compile, run
from ginger.symbols_builder import build_symbols
from ginger.typecheck import typecheck_program


class EmptyGuaranteeTests(unittest.TestCase):
    diagnostic = 'guarantee Empty must declare at least one method or capability requirement'

    def assert_empty_rejected(self, check):
        with self.assertRaises(TypecheckError) as caught:
            check()
        # Fail fast at the definition, without unknown-reference cascades.
        self.assertEqual(str(caught.exception), self.diagnostic)

    def test_source_empty_and_comments_at_all_entrypoints(self):
        for source in ['guarantee Empty {}',
                       'guarantee Empty {\n // No capabilities\n }']:
            for check in [lambda: build_symbols(parse(source)),
                          lambda: typecheck_program(parse(source), Diagnostics()),
                          lambda: compile(source), lambda: run(source)]:
                with self.subTest(source=source, check=check):
                    self.assert_empty_rejected(check)

    def test_require_reference_in_either_order(self):
        definition = 'guarantee Empty {}\n'
        reference = 'sig keep(T) -> T { require T guarantees Empty }\n'
        for source in [definition + reference, reference + definition]:
            with self.subTest(source=source):
                self.assert_empty_rejected(lambda: compile(source))

    def test_internal_ast_impl_in_either_order(self):
        definition = GuaranteeDecl('Empty', [])
        reference = ImplDecl(TypeRef('Int'), 'Empty', [])
        for items in [[definition], [definition, reference], [reference, definition]]:
            with self.subTest(items=items):
                self.assert_empty_rejected(lambda: build_symbols(Program(items)))

    def test_multiple_definitions(self):
        valid = 'guarantee Valid { witness(self: Self) -> Self }\n'
        for source in [valid + 'guarantee Empty {}', 'guarantee Empty {}\n' + valid]:
            with self.subTest(source=source):
                self.assert_empty_rejected(lambda: compile(source))

    def test_catalog_explicit_or_omitted_methods(self):
        for declaration in [{'name': 'Empty', 'methods': []}, {'name': 'Empty'}]:
            with self.subTest(declaration=declaration), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'catalog.json'
                path.write_text(json.dumps({'guarantees': [declaration]}))
                items = load_core_catalog_json(path)
                self.assert_empty_rejected(lambda: build_symbols(Program(items)))
                with patch('ginger.symbols_builder.prelude_items', return_value=items):
                    self.assert_empty_rejected(lambda: compile(''))

    def test_method_contract_and_forward_generic_requirement(self):
        compile('sig keep(T) -> T { require T guarantees Valid }\n'
                'func keep(x: T) { return x }\n'
                'guarantee Valid { witness(self: Self) -> Self }')

    def test_capability_requirement_syntax_is_not_introduced(self):
        # Neither the parser nor GuaranteeDecl supports guarantee inheritance.
        for body in ['require Self guarantees Addable',
                     'witness(self: Self) -> Self\nrequire Self guarantees Addable']:
            with self.subTest(body=body), self.assertRaises(SyntaxError):
                parse('guarantee Empty { ' + body + ' }')

    def test_empty_impl_still_reports_missing_method(self):
        declarations = parse('guarantee Valid { witness(self: Self) -> Self }').items
        with self.assertRaisesRegex(TypecheckError, "missing impl for method 'witness'"):
            build_symbols(Program(declarations + [ImplDecl(TypeRef('Int'), 'Valid', [])]))

    def test_standard_capabilities_still_have_methods_and_implementations(self):
        symbols = build_symbols(Program([]))
        for name, method in [('Addable', 'add'), ('Subtractable', 'sub'),
                             ('Multipliable', 'mul'), ('Negatable', 'neg'),
                             ('Ord', 'cmp'), ('Printable', 'print')]:
            with self.subTest(name=name):
                self.assertIn(method, [m.name for m in symbols.guarantees[name].methods])
                self.assertTrue(any(g == name and m == method for _, g, m in symbols.impls))

    def test_generic_failureset_thunk_contract(self):
        result = run('''failureset AddFailures(T) {
    IntegerOverflow when T in BoundedArithmetic,
    FloatOverflow when T in FiniteArithmetic,
    FloatUnderflow when T in FiniteArithmetic,
}
sig delayed(T,T) -> Thunk[T]<AddFailures(T)> {
    require T guarantees Addable
}
func delayed(a: T,b: T) { return thunk(a + b) }
var integer: Int = force(delayed(1,2))
var floating: Float = force(delayed(1.0,2.0))
''')
        self.assertEqual(result.environment['integer'].value, 3)
        self.assertEqual(result.environment['floating'].value, 3.0)


if __name__ == '__main__':
    unittest.main()
