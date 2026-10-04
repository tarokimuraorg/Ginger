import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ginger.ast import FailureSetDecl, Program
from ginger.core.catalog_loader import load_core_catalog_json
from ginger.core.failure_spec import FailureId
from ginger.errors import TypecheckError
from ginger.lower import lower_program
from ginger.parser import parse
from ginger.symbols_builder import build_symbols
from test_failure_contract import checked, output


SET = 'failureset CalculationFailure { DivideByZero IOErr }\n'
EXPECTED = frozenset({FailureId.DivideByZero, FailureId.IOErr})


class FailureSetTests(unittest.TestCase):
    def test_parser_and_lowering(self):
        program = parse(SET)
        self.assertEqual(program.items, [FailureSetDecl('CalculationFailure',
                                                       ['DivideByZero', 'IOErr'])])
        self.assertIs(lower_program(program).items[0], program.items[0])
        self.assertEqual(build_symbols(program).failuresets['CalculationFailure'], EXPECTED)

    def test_expansion_and_mixed_declarations(self):
        for clause in ('failure CalculationFailure',
                       'failure CalculationFailure failure PrintErr'):
            with self.subTest(clause=clause):
                syms = build_symbols(parse(SET + f'sig f() -> Unit {{ {clause} }}'))
                expected = EXPECTED | ({FailureId.PrintErr} if 'PrintErr' in clause else set())
                self.assertEqual(syms.sig_failures['f'], expected)

    def test_upper_bound_and_call_propagation(self):
        source = SET + '''
sig f() -> Float { failure CalculationFailure }
func f() { return div(1.0, 0.0) }
sig g() -> Float { failure DivideByZero failure IOErr }
func g() { return f() }
'''
        _, diags = checked(source)
        self.assertEqual(diags.items, [])
        with self.assertRaisesRegex(TypecheckError, "func 'g'.*undeclared failures: IOErr"):
            checked(source.replace('failure DivideByZero failure IOErr', 'failure DivideByZero'))
        with self.assertRaisesRegex(TypecheckError, 'undeclared failures: DivideByZero'):
            checked(source.replace('{ DivideByZero IOErr }', '{ IOErr }'))

    def test_symbol_validation(self):
        cases = [
            ('failureset A { UnknownFailure }', 'unknown failure in failureset'),
            ('failureset A { DivideByZero DivideByZero }', 'duplicate failure in failureset'),
            ('failureset A { Never }', 'Never is not allowed in failureset'),
            ('failureset A { IOErr } failureset A { DivideByZero }', 'duplicate failureset definition'),
            ('failureset A { IOErr } failureset B { A }', 'nested failureset is not supported'),
            ('failureset B { A } failureset A { IOErr }', 'nested failureset is not supported'),
            ('failureset A { A }', 'nested failureset is not supported'),
            ('failureset A {}', 'empty failureset is not supported'),
        ]
        for source, message in cases:
            with self.subTest(source=source):
                program = lower_program(parse(source))
                with self.assertRaisesRegex(TypecheckError, message):
                    build_symbols(program)

    def test_namespace_collisions(self):
        for name in ('IOErr', 'Never'):
            with self.subTest(name=name), self.assertRaisesRegex(TypecheckError, 'conflicts'):
                build_symbols(parse(f'failureset {name} {{ DivideByZero }}'))
        syms = build_symbols(parse('''
guarantee Shared {}
typegroup Shared = Int
failureset Shared { IOErr }
sig Shared() -> Unit { failure Shared }
func Shared() {}
'''))
        self.assertEqual(syms.sig_failures['Shared'], frozenset({FailureId.IOErr}))

    def test_forward_reference_and_overlap(self):
        syms = build_symbols(parse('''
sig f() -> Unit { failure A failure B failure IOErr }
failureset A { IOErr }
failureset B { IOErr DivideByZero }
'''))
        self.assertEqual(syms.sig_failures['f'], EXPECTED)

    def test_sig_never_and_duplicate_rules(self):
        for clause in ('failure CalculationFailure failure Never',
                       'failure Never failure CalculationFailure',
                       'failure CalculationFailure failure CalculationFailure'):
            with self.subTest(clause=clause), self.assertRaises(SyntaxError):
                parse(SET + f'sig f() -> Unit {{ {clause} }}')

    def test_resolve_only_individual_ids(self):
        prefix = SET + '''
sig f() -> Float { failure CalculationFailure }
func f() { return div(1.0, 0.0) }
var x: Float = f()
'''
        with self.assertRaisesRegex(TypecheckError, "unknown failure 'CalculationFailure'"):
            checked(prefix + 'resolve x { CalculationFailure { x = 0 } }')
        program, diags = checked(prefix + 'resolve x { DivideByZero { x = 0\nprint(0) }\n'
                                 'IOErr { x = 1\nprint(1) } }')
        self.assertEqual(diags.items, [])
        self.assertEqual(output(program), '0\n')
        with self.assertRaisesRegex(TypecheckError, "cannot resolve 'PrintErr'"):
            checked(prefix + 'resolve x { PrintErr { x = 0 } }')

    def test_catalog_reference(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'catalog.json'
            path.write_text(json.dumps({'sigs': [{'name': 'f', 'params': [], 'ret': 'Unit',
                                                'failures': ['CalculationFailure']}]}))
            catalog = load_core_catalog_json(path)
        with patch('ginger.symbols_builder.prelude_items', side_effect=lambda: list(catalog)):
            self.assertEqual(build_symbols(parse(SET)).sig_failures['f'], EXPECTED)
        with self.assertRaisesRegex(TypecheckError, 'unknown failure'):
            build_symbols(Program(catalog))

    def test_function_and_thunk_contract_equivalence(self):
        sources = [
            'sig f() -> Unit { %s }\n'
            'func f() { print(div(1.0,0.0)) }\n'
            'sig g() -> Unit { failure DivideByZero failure IOErr }\nfunc g() { f() }\nf()',
            'sig f(Thunk[Float, Never]) -> Float { %s }\n'
            'func f(t: Thunk[Float, Never]) { return force(t) }',
            'sig h(Int) -> Unit { %s builtin core.int.print }\n'
            'sig f() -> Unit { failure DivideByZero failure IOErr }\nfunc f() { h(1) }',
        ]
        for source in sources:
            with self.subTest(source=source):
                _, named = checked(SET + source % 'failure CalculationFailure')
                _, explicit = checked(source % 'failure DivideByZero failure IOErr')
                self.assertEqual(named.items, explicit.items)
                self.assertFalse(any(d.code == 'FAILURE_CONTRACT_DEFERRED' for d in named))
        _, diags = checked(SET + 'sig h(Int) -> Unit { failure CalculationFailure '
                           'builtin core.int.print }\nvar x: Unit = h(1)\n'
                           'resolve x { IOErr { x = print(0) } }')
        self.assertEqual([d.message for d in diags], ['unhandled failures: DivideByZero'])
