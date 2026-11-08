import unittest

from ginger.ast import TypeRef
from ginger.core.catalog_loader import _failure
from ginger.core.failure_contract import FailureContract, ConditionalFailure, substitute_contract
from ginger.core.failure_spec import FailureId
from ginger.errors import TypecheckError
from ginger.eval import eval_program
from ginger.runtime.failures import FailureStatus
from ginger.symbols_builder import build_symbols, instantiated_signature_failures
from test_failure_contract import checked


class TypeGroupContractTests(unittest.TestCase):
    def symbols(self, source):
        return build_symbols(checked(source)[0])

    def test_empty_block_and_equivalent_membership(self):
        syms = self.symbols('typegroup Empty {}\ntypegroup Block {\nInt\nFloat\n}\n'
                            'typegroup Union = Int | Float')
        self.assertEqual(syms.typegroups['Empty'], set())
        self.assertEqual(syms.typegroups['Block'], syms.typegroups['Union'])

    def test_extension_order_require_and_condition(self):
        declaration = 'typegroup Group { Int }\n'
        registration = 'register Group { Float }\n'
        function = ('sig keep(T) -> T { require T in Group failure { IOErr when T in Group } }\n'
                    'func keep(x: T) { return x }\nvar x: Float = keep(1.0)\n')
        for source in [declaration + registration + function,
                       registration + function + declaration,
                       function + declaration + registration]:
            with self.subTest(source=source):
                syms = self.symbols(source)
                self.assertEqual(syms.typegroups['Group'], {'Int', 'Float'})
                self.assertEqual(instantiated_signature_failures(syms, 'keep', {'T': TypeRef('Float')}),
                                 frozenset({FailureId.IOErr}))
                self.assertNotIn('Group', syms.type_guarantees['Float'])

    def test_duplicates_and_unknown_destination(self):
        for source in ['typegroup Group { Int, Int }', 'typegroup Group = Int | Int',
                       'typegroup Group {}\nregister Group { Int, Int }',
                       'register Group { Int }\ntypegroup Group { Int }',
                       'typegroup Group {}\nregister Group { Int }\nregister Group { Int }',
                       'typegroup Group { Thunk[Int]<Never>, Thunk[Int]<IOErr> }']:
            with self.subTest(source=source), self.assertRaisesRegex(TypecheckError, 'duplicate'):
                self.symbols(source)
        with self.assertRaisesRegex(TypecheckError, 'unknown register target'):
            self.symbols('register Missing { Int }')

    def test_shared_namespace_including_catalog(self):
        for source in ['guarantee Example {}\ntypegroup Example {}',
                       'typegroup Example {}\nguarantee Example {}',
                       'guarantee BoundedArithmetic {}', 'typegroup Addable {}',
                       'typegroup Example {}\ntypegroup Example {}']:
            with self.subTest(source=source), self.assertRaises(TypecheckError):
                self.symbols(source)

    def test_guarantee_is_not_a_failure_group(self):
        with self.assertRaisesRegex(TypecheckError, 'unknown typegroup'):
            self.symbols('guarantee Marker {}\nsig keep(T) -> T { failure { IOErr when T in Marker } }')
        with self.assertRaisesRegex(SyntaxError, 'when T in Group'):
            self.symbols('sig keep(T) -> T { failure { IOErr when T guarantees BoundedArithmetic } }')

    def test_standard_extension_changes_contract_only_and_is_isolated(self):
        call = 'var x: Float = add(1.0,2.0)\n'
        registration = 'register BoundedArithmetic { Float }\n'
        for source in [registration + call, call + registration]:
            program, _ = checked(source)
            syms = build_symbols(program)
            self.assertEqual(instantiated_signature_failures(syms, 'add', {'T': TypeRef('Float')}),
                             frozenset({FailureId.IntegerOverflow, FailureId.FloatOverflow}))
            result = eval_program(program)
            self.assertEqual(result.environment['x'].value, 3.0)
            self.assertFalse(result.failure_history)
        self.assertEqual(self.symbols('').typegroups['BoundedArithmetic'], {'Int'})
        with self.assertRaisesRegex(TypecheckError, 'does not guarantee Addable'):
            checked('register BoundedArithmetic { Int64 }\nvar x: Int64 = add(1i64,2i64)')

    def test_extension_does_not_change_actual_float_failure(self):
        program, _ = checked('register BoundedArithmetic { Float }\n'
                             'var x: Float = mul(1.0e308,2.0)')
        result = eval_program(program)
        self.assertEqual([event.failure_id for event in result.failure_history],
                         [FailureId.FloatOverflow])

    def test_catalog_rejects_old_condition_schema(self):
        with self.assertRaises(ValueError):
            _failure({'failure': 'IntegerOverflow',
                      'when': {'typevar': 'T', 'guarantees': 'BoundedArithmetic'}})

    def test_symbolic_substitution_uses_typegroup(self):
        contract = FailureContract(conditional=frozenset({ConditionalFailure(FailureId.IOErr, 'T', 'Group')}))
        symbolic = substitute_contract(contract, {'T': TypeRef('U')}, {'Group': {'Int'}})
        self.assertEqual(next(iter(symbolic.conditional)).type_var, 'U')
        for typ, expected in [('Int', {FailureId.IOErr}), ('Float', set())]:
            self.assertEqual(substitute_contract(symbolic, {'U': TypeRef(typ)}, {'Group': {'Int'}}).concrete_failures(), expected)

    def test_resolved_event_remains_in_static_contract(self):
        source = '''sig calculate() -> Float { failure { DivideByZero, FloatOverflow, FloatUnderflow } }
func calculate() {
    return resolve div(1.0,0.0) { DivideByZero { return 0.0 } }
}
var x: Float = calculate()
'''
        program, _ = checked(source)
        syms = build_symbols(program)
        self.assertIn(FailureId.DivideByZero, syms.sig_failures['calculate'])
        result = eval_program(program)
        self.assertEqual(result.environment['x'].value, 0.0)
        self.assertEqual(len(result.failure_history), 1)
        self.assertEqual(result.failure_history[0].status, FailureStatus.RESOLVED)
        with self.assertRaisesRegex(TypecheckError, 'DivideByZero'):
            checked(source.replace('DivideByZero, FloatOverflow', 'FloatOverflow'))
