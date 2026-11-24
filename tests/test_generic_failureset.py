import unittest

from ginger.ast import FailureSetRef, TypeRef
from ginger.core.failure_contract import ConditionalFailure, FailureContract
from ginger.core.failure_spec import FailureId as F, EMPTY_FAILURES
from ginger.diagnostics import Diagnostics
from ginger.errors import TypecheckError
from ginger.eval import eval_program
from ginger.lower import lower_program
from ginger.numeric import INT_MAX
from ginger.parser import parse
from ginger.symbols_builder import build_symbols, normalize_types, signature_contract
from ginger.typecheck import (Binding, effect_expr, latent_contract, resolve_typeref,
                              symbolic_effect_expr, typecheck_program)
from test_failure_contract import checked, recorded


DEFINITION = '''failureset AddFailures(T) {
    IntegerOverflow when T in BoundedArithmetic,
    FloatOverflow when T in FiniteArithmetic,
    FloatUnderflow when T in FiniteArithmetic,
}
'''
CALCULATE = '''sig calculate(T,T) -> T {
    require T guarantees Addable
    failure { AddFailures(T) }
}
func calculate(a: T,b: T) { return a + b }
'''
INNER = '''sig inner(U,U) -> U {
    require U guarantees Addable
    failure { AddFailures(U) }
}
func inner(a: U,b: U) { return calculate(a,b) }
'''
INTEGER = frozenset({F.IntegerOverflow})
FLOAT = frozenset({F.FloatOverflow, F.FloatUnderflow})


def symbolic(variable):
    return FailureContract(EMPTY_FAILURES, frozenset({
        ConditionalFailure(F.IntegerOverflow, variable, 'BoundedArithmetic'),
        ConditionalFailure(F.FloatOverflow, variable, 'FiniteArithmetic'),
        ConditionalFailure(F.FloatUnderflow, variable, 'FiniteArithmetic'),
    }))


def annotated(source):
    program = lower_program(parse(source))
    syms = build_symbols(program)
    program = normalize_types(program, syms.failuresets)
    diagnostics = Diagnostics()
    env = typecheck_program(program, diagnostics, syms=syms)
    return program, syms, env, diagnostics


class GenericFailureSetTests(unittest.TestCase):
    def test_parser_and_lower_preserve_definition_and_reference(self):
        program = parse(DEFINITION + CALCULATE)
        declaration = program.items[0]
        self.assertEqual(declaration.type_param, 'T')
        self.assertEqual(len(declaration.members), 3)
        self.assertIs(lower_program(program).items[0], declaration)
        self.assertEqual(program.items[1].failures,
                         [FailureSetRef('AddFailures', TypeRef('T'))])

    def test_concrete_expansion_and_empty_contract(self):
        for typ, expected in [('Int', INTEGER), ('Float', FLOAT), ('String', EMPTY_FAILURES)]:
            with self.subTest(typ=typ):
                syms = build_symbols(parse('type String\n' + DEFINITION +
                    f'sigtype D() -> {typ} {{ failure {{ AddFailures({typ}) }} }} sig f() -> Thunk[D] '
                    f'{{ failure {{ AddFailures({typ}) }} }}'))
                self.assertEqual(signature_contract(syms, 'f'), FailureContract(expected))
                self.assertEqual(latent_contract(syms.sigs['f'].ret), FailureContract(expected))

    def test_symbolic_contract_and_substitution(self):
        _, syms, _, _ = annotated(DEFINITION + CALCULATE + INNER)
        self.assertEqual(signature_contract(syms, 'inner'), symbolic('U'))
        from ginger.core.failure_contract import substitute_contract
        effect = signature_contract(syms, 'calculate')
        for name, expected in [('Int', INTEGER), ('Float', FLOAT), ('String', EMPTY_FAILURES)]:
            concrete = substitute_contract(effect, {'T': TypeRef(name)}, syms.typegroups)
            self.assertEqual(concrete, FailureContract(expected))

    def test_acceptance_output_events_and_concrete_call_effects(self):
        source = DEFINITION + CALCULATE + INNER + 'print(inner(1,2))\nprint(inner(1.0,2.0))'
        program, syms, env, diagnostics = annotated(source)
        self.assertEqual([effect_expr(item.expr.args[0].expr, env, syms)
                          for item in program.items[-2:]], [INTEGER, FLOAT])
        self.assertEqual([d.message for d in diagnostics], [
            'unhandled failures: IntegerOverflow',
            'unhandled failures: FloatOverflow, FloatUnderflow'])
        _, context, stdout = recorded(program)
        self.assertEqual(stdout, '3\n3.0\n')
        self.assertFalse(context.failure_history)
        frames = [f for f in context.call_frames.values() if f.function_name == 'inner']
        self.assertEqual([f.declared_failure_contract for f in frames], [INTEGER, FLOAT])

    def test_delayed_inner_outer_acceptance(self):
        source = DEFINITION + CALCULATE + INNER + """
sig outer(T,T) -> T {
    require T guarantees Addable
    failure { AddFailures(T) }
}
func outer(a: T,b: T) { return inner(a,b) }
print(outer(6,4))
print(outer(2.5,7.4))
"""
        program, syms, _, _ = annotated(source)
        self.assertEqual(signature_contract(syms, 'outer'), symbolic('T'))
        _, context, stdout = recorded(program)
        self.assertEqual(stdout, '10\n9.9\n')
        self.assertFalse(context.failure_history)
        self.assertEqual([f.declared_failure_contract for f in context.call_frames.values()
                          if f.function_name == 'outer'], [INTEGER, FLOAT])

    def test_delayed_force_keeps_conditions_and_creation_has_no_effect(self):
        program, syms, env, _ = annotated(DEFINITION + CALCULATE + INNER +
            'sigtype DeferredIntSum() -> Int { failure { AddFailures(Int) } }\nsigtype DeferredFloatSum() -> Float { failure { AddFailures(Float) } }\nvar a: Thunk[DeferredIntSum] = thunk(calculate(1,2))\n'
            'var b: Thunk[DeferredFloatSum] = thunk(calculate(1.0,2.0))')
        self.assertEqual(env['a'].ty.latent_failures, INTEGER)
        self.assertEqual(env['b'].ty.latent_failures, FLOAT)
        for item in program.items[-2:]:
            self.assertEqual(effect_expr(item.expr, env, syms), EMPTY_FAILURES)
        result = eval_program(program)
        self.assertEqual(result.environment['a'].value.potential_failure_contract, INTEGER)
        # The public upper bound includes underflow; addition's own contract does not.
        self.assertEqual(result.environment['b'].typ.latent_failures, FLOAT)
        self.assertEqual(result.environment['b'].value.potential_failure_contract, FLOAT)
        self.assertFalse(result.failure_history)

    def test_registration_is_order_independent(self):
        registration = 'register BoundedArithmetic { Float, }\n'
        use = 'sigtype DeferredFloatSum() -> Float { failure { AddFailures(Float) } }\nsig f() -> Thunk[DeferredFloatSum] { failure { AddFailures(Float) } }\n'
        for source in [registration + DEFINITION + use, use + DEFINITION + registration]:
            syms = build_symbols(parse(source))
            self.assertEqual(syms.sig_failures['f'], INTEGER | FLOAT)
            self.assertEqual(syms.sigs['f'].ret.latent_failures, INTEGER | FLOAT)
        program, syms, _, _ = annotated(DEFINITION + CALCULATE + INNER + registration +
                                      'print(inner(1.0,2.0))')
        _, context, stdout = recorded(program)
        self.assertEqual(stdout, '3.0\n')
        self.assertFalse(context.failure_history)
        self.assertEqual([f.declared_failure_contract for f in context.call_frames.values()
                          if f.function_name == 'inner'], [INTEGER | FLOAT])

    def test_forward_group_and_concrete_type_identity(self):
        syms = build_symbols(parse('''
type Box
failureset Tagged(T) { IOErr when T in TaggedGroup }
sig yes() -> Unit { failure { Tagged(Box[Int]) } }
sig no() -> Unit { failure { Tagged(Box[Float]) } }
register TaggedGroup { Box[Int] }
typegroup TaggedGroup {}
'''))
        self.assertEqual(syms.sig_failures['yes'], frozenset({F.IOErr}))
        self.assertEqual(syms.sig_failures['no'], EMPTY_FAILURES)

    def test_legacy_mixed_overlap_and_named_equivalence(self):
        source = DEFINITION + '''
failureset Basic { DivideByZero }
failureset Other(U) {
 IntegerOverflow when U in BoundedArithmetic,
 FloatOverflow when U in FiniteArithmetic,
 FloatUnderflow when U in FiniteArithmetic,
}
sig f(T) -> T { failure { Basic, DivideByZero, AddFailures(T), Other(T) } }
func f(x: T) { return x }
'''
        _, syms, _, _ = annotated(source)
        self.assertEqual(signature_contract(syms, 'f'),
                         FailureContract(frozenset({F.DivideByZero}), symbolic('T').conditional))
        checked(DEFINITION + CALCULATE.replace('AddFailures(T)', 'Other(T)') + source[len(DEFINITION):])

    def test_thunk_parameter_contract_compared_after_type_binding(self):
        checked(DEFINITION + CALCULATE + '''
sigtype Deferred() -> Int { failure { AddFailures(Int) } }
sig run(U,Thunk[Deferred]) -> Int { failure { AddFailures(Int) } }
func run(x: U,t: Thunk[Deferred]) { return force(t) }
print(run(1.0,thunk(calculate(1,2))))
''')

    def test_explicit_conditions_and_local_thunk_annotation(self):
        explicit = ("IntegerOverflow when U in BoundedArithmetic, "
                    "FloatOverflow when U in FiniteArithmetic, "
                    "FloatUnderflow when U in FiniteArithmetic")
        source = DEFINITION + CALCULATE + INNER.replace('AddFailures(U)', explicit)
        program, syms, _, _ = annotated(source + 'print(inner(1,2))')
        self.assertEqual(signature_contract(syms, 'inner'), symbolic('U'))
        self.assertEqual(recorded(program)[2], '3\n')

    def test_unconditional_members_in_generic_definition(self):
        source = DEFINITION.replace('IntegerOverflow when', 'IOErr, IntegerOverflow when')
        syms = build_symbols(parse(source + 'sig f(T) -> T { failure { AddFailures(T) } }'))
        self.assertEqual(signature_contract(syms, 'f'),
                         FailureContract(frozenset({F.IOErr}), symbolic('T').conditional))

    def test_force_is_delayed_and_nonmemoized(self):
        source = DEFINITION + CALCULATE + f"""sigtype DeferredIntSum() -> Int {{ failure {{ AddFailures(Int) }} }}\n
var t: Thunk[DeferredIntSum] = thunk(calculate({INT_MAX},1))
var first: Int = force(t)
resolve first {{ IntegerOverflow {{ first = 0 }} }}
var second: Int = force(t)
resolve second {{ IntegerOverflow {{ second = 1 }} }}
"""
        program, _ = checked(source)
        result = eval_program(program)
        self.assertIsNone(result.contract_violation)
        self.assertEqual([e.failure_id for e in result.failure_history],
                         [F.IntegerOverflow, F.IntegerOverflow])
        self.assertEqual(len({e.event_id for e in result.failure_history}), 2)
        self.assertEqual((result.environment['first'].value, result.environment['second'].value), (0, 1))
        self.assertTrue(all(e.status.value == 'resolved' for e in result.failure_history))

    def test_group_membership_does_not_supply_capabilities(self):
        source = DEFINITION + CALCULATE + 'register BoundedArithmetic { Int64 }\n'
        with self.assertRaisesRegex(TypecheckError, 'does not guarantee Addable'):
            checked(source + 'calculate(1i64,2i64)')

    def test_invalid_declarations(self):
        cases = [
            'failureset Bad(T) { IOErr when T in UnknownGroup }',
            'failureset Bad(T) { IOErr when U in BoundedArithmetic }',
            'failureset Bad { IOErr when T in BoundedArithmetic }',
            'failureset Bad(T) { UnknownFailure when T in BoundedArithmetic }',
            'failureset Bad(T) { Never when T in BoundedArithmetic }',
            'failureset Bad(T) { Bad(T) }',
            'failureset Bad(T) { Other(T) } failureset Other(U) { Bad(U) }',
            'failureset Bad(T,U) { IOErr }',
            'failureset Bad(T) {}',
            'failureset Bad(T) { IOErr, IOErr }',
            'failureset Bad(T) { IOErr when T in BoundedArithmetic, IOErr when T in BoundedArithmetic }',
            'failureset Bad(T) { IOErr, IOErr when T in BoundedArithmetic }',
            'failureset Bad { IOErr } failureset Bad(T) { IOErr }',
        ]
        for source in cases:
            with self.subTest(source=source), self.assertRaises((TypecheckError, SyntaxError)):
                checked(source)

    def test_invalid_references_in_sig_and_thunk(self):
        for ref in ['UnknownFailures(Int)', 'AddFailures', 'AddFailures(Int,Float)',
                    'AddFailures()', 'Basic(Int)', 'AddFailures(U)', 'AddFailures(Box[U])',
                    'AddFailures(T), Never', 'AddFailures(T), AddFailures(T)']:
            for use in [f'sig f(T) -> T {{ failure {{ {ref} }} }}',
                        f'sigtype D() -> T {{ failure {{ {ref} }} }} sig f(T) -> Thunk[D] {{}}']:
                with self.subTest(use=use), self.assertRaises((TypecheckError, SyntaxError)):
                    checked(DEFINITION + 'type Box\nfailureset Basic { IOErr }\n' + use)
        with self.assertRaisesRegex(TypecheckError, 'unknown type variable'):
            checked(DEFINITION + 'sigtype DeferredIntAddFailuresT() -> Int { failure { AddFailures(T) } }\nvar t: Thunk[DeferredIntAddFailuresT] = thunk(1)')

    def test_existing_comma_and_never_rules(self):
        for source in ['failureset Bad(T) { IOErr PrintErr }',
                       'sig f(T) -> T { failure { AddFailures(T) IOErr } }',
                       'sig f() -> Int { failure {} }',
                       'sig f() -> Int { failure { Never, } }']:
            with self.subTest(source=source), self.assertRaises(SyntaxError):
                checked(DEFINITION + source)
        checked(DEFINITION + 'sig f() -> Int { failure { Never } } func f() { return 1 }')

    def test_incompatible_conditions_and_arguments_are_rejected(self):
        cases = [
            CALCULATE.replace('AddFailures(T)', 'Never'),
            CALCULATE + INNER.replace('AddFailures(U)', 'AddFailures(Int)'),
            CALCULATE + INNER.replace('AddFailures(U)', 'Never'),
            CALCULATE + 'sigtype DeferredIntAddFailuresFloat() -> Int { failure { AddFailures(Float) } }\nvar t: Thunk[DeferredIntAddFailuresFloat] = thunk(calculate(1,2))',
            CALCULATE + 'sigtype DeferredFloatAddFailuresInt() -> Float { failure { AddFailures(Int) } }\nvar t: Thunk[DeferredFloatAddFailuresInt] = thunk(calculate(1.0,2.0))',
            CALCULATE + '''sig wrong(T,U) -> T {
                require T guarantees Addable failure { AddFailures(U) }
            } func wrong(a: T,b: U) { return calculate(a,a) }''',
            '''failureset Wrong(T) { IntegerOverflow when T in FiniteArithmetic }
            ''' + CALCULATE.replace('AddFailures(T)', 'Wrong(T)'),
        ]
        for source in cases:
            with self.subTest(source=source), self.assertRaises(TypecheckError):
                checked(DEFINITION + source)

    def test_resolved_failure_remains_static_and_runtime_ids_are_preserved(self):
        source = DEFINITION + CALCULATE + '''
sig recovered(U,U) -> U { require U guarantees Addable failure { AddFailures(U) } }
func recovered(a: U,b: U) {
 return resolve calculate(a,b) { IntegerOverflow { return a } }
}
''' + f'var x: Int = recovered({INT_MAX},1)'
        program, syms, env, _ = annotated(source)
        self.assertEqual(effect_expr(program.items[-1].expr, env, syms), INTEGER)
        result = eval_program(program)
        self.assertIsNone(result.contract_violation)
        self.assertEqual(result.environment['x'].value, INT_MAX)
        self.assertEqual(len(result.failure_history), 1)
        self.assertEqual(result.failure_history[0].failure_id, F.IntegerOverflow)
        with self.assertRaisesRegex(TypecheckError, 'undeclared failures'):
            checked(source.replace('failure { AddFailures(U) }', 'failure { Never }'))

    def test_empty_contract_does_not_disable_runtime_validation(self):
        # Explicit builtin aliases are an existing internal trust boundary.
        source = '''failureset Empty(T) { IntegerOverflow when T in EmptyGroup }
        typegroup EmptyGroup {}
        sig alias(Int,Int) -> Int { failure { Empty(Int) } builtin core.int.add }
        ''' + f'var x: Int = alias({INT_MAX},1)'
        program, _ = checked(source)
        result = eval_program(program)
        self.assertEqual(result.contract_violation.failure_id, F.IntegerOverflow)


if __name__ == '__main__':
    unittest.main()
