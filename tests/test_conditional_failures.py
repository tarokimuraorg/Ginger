import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_failure_contract import checked, output, recorded
from ginger.ast import Program, TypeRef
from ginger.builtin import INT_ARITHMETIC_FAILURES, builtin_failure_contract, call_builtin
from ginger.core.catalog_loader import load_core_catalog_json
from ginger.core.failure_contract import ConditionalFailure, FailureContract
from ginger.core.failure_spec import FailureId, EMPTY_FAILURES, failures
from ginger.diagnostics import Diagnostics
from ginger.errors import TypecheckError
from ginger.eval import UninitializedBinding, eval_call, eval_program
from ginger.numeric import INT_MAX
from ginger.runtime.failures import FailureStatus, RaisedFailure
from ginger.runtime.thunk import ThunkValue
from ginger.symbols_builder import build_symbols, normalize_types
from ginger.typecheck import Binding, effect_expr, symbolic_effect_expr, typecheck_program


OVERFLOW = failures(FailureId.IntegerOverflow)
BOUNDED_OVERFLOW = ConditionalFailure(FailureId.IntegerOverflow, 'T', 'BoundedArithmetic')
FLOAT_OVERFLOW = failures(FailureId.FloatOverflow)
FINITE_UNDERFLOW = ConditionalFailure(FailureId.FloatUnderflow, 'T', 'FiniteArithmetic')
FINITE_OVERFLOW = ConditionalFailure(FailureId.FloatOverflow, 'T', 'FiniteArithmetic')
CONDITIONAL_OVERFLOW = FailureContract(EMPTY_FAILURES, frozenset({BOUNDED_OVERFLOW, FINITE_OVERFLOW}))
CONDITION = ('failure { IntegerOverflow when T in BoundedArithmetic, '
             'FloatOverflow when T in FiniteArithmetic }')


def addition(body='add[T](a,b)', *, requires='require T guarantees Addable', contract=CONDITION):
    return (f'sig addTwo[T](T,T) -> T {{ {requires} {contract} }}\n'
            f'func addTwo(a: T,b: T) {{ return {body} }}\n')


class ConditionalFailureTests(unittest.TestCase):
    def annotated(self, source):
        program, _ = checked(source)
        syms = build_symbols(program)
        program = normalize_types(program, syms.failuresets)
        diagnostics = Diagnostics()
        bindings = typecheck_program(program, diagnostics, syms=syms)
        return program, syms, bindings, diagnostics

    def catalog_symbols(self, failures):
        catalog = {'sigs': [{'name': 'f', 'type_params': ['T'], 'params': ['T'], 'ret': 'T', 'failures': failures}]}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'catalog.json'
            path.write_text(json.dumps(catalog))
            return build_symbols(Program(load_core_catalog_json(path)))

    def test_explicit_and_infix_generic_add_specialize_int_and_float(self):
        for body in ['add[T](a,b)', '(a + b)']:
            for typ, args, expected, effect in [('Int', '1,2', '3\n', OVERFLOW),
                                                ('Float', '1.0,2.0', '3.0\n', FLOAT_OVERFLOW)]:
                with self.subTest(body=body, typ=typ):
                    program, syms, env, _ = self.annotated(
                        addition(body) + f'var x: {typ} = addTwo[{typ}]({args})\nprint[{typ}](x)')
                    call = program.items[-2].expr
                    self.assertEqual(syms.resolved_calls[id(call)].type_bindings, {'T': TypeRef(typ)})
                    self.assertEqual(effect_expr(call, env, syms), effect)
                    self.assertEqual(env['x'].latest_write_failures, effect)
                    self.assertEqual(output(program), expected)

    def test_int64_still_lacks_addable(self):
        for body in ['add[T](a,b)', '(a + b)']:
            with self.subTest(body=body), self.assertRaisesRegex(
                    TypecheckError, 'Int64.*does not guarantee Addable'):
                checked(addition(body) + 'print[Int](addTwo[Int64](1i64,2i64))')

    def test_conditional_clause_does_not_supply_addable(self):
        for body in ['add[T](a,b)', '(a + b)']:
            with self.subTest(body=body), self.assertRaisesRegex(
                    TypecheckError, 'does not guarantee Addable'):
                checked(addition(body, requires=''))

    def test_unhandled_warnings_use_concrete_contract(self):
        for body in ['add[T](a,b)', '(a + b)']:
            for args, expected in [('1,2', ['unhandled failures: IntegerOverflow']),
                                   ('1.0,2.0', ['unhandled failures: FloatOverflow'])]:
                with self.subTest(body=body, args=args):
                    typ = 'Float' if '.' in args else 'Int'
                    _, diagnostics = checked(addition(body) + f'print[{typ}](addTwo[{typ}]({args}))')
                    self.assertEqual([d.message for d in diagnostics], expected)

    def test_symbolic_effect_and_declared_clause_match(self):
        contracts = []
        for body in ['add[T](a,b)', '(a + b)']:
            _, syms, _, _ = self.annotated(addition(body))
            self.assertEqual(syms.sig_failures['addTwo'], EMPTY_FAILURES)
            self.assertEqual(syms.sig_conditional_failures['addTwo'], frozenset({BOUNDED_OVERFLOW, FINITE_OVERFLOW}))
            call = syms.funcs['addTwo'].body.stmts[0].expr
            env = {'a': Binding(TypeRef('T'), False), 'b': Binding(TypeRef('T'), False)}
            contract = symbolic_effect_expr(call, env, syms)
            self.assertEqual(contract, CONDITIONAL_OVERFLOW)
            self.assertIsNone(syms.resolved_calls[id(call)].implementation)
            contracts.append(contract)
        self.assertEqual(contracts[0], contracts[1])

    def test_never_cannot_cover_symbolic_overflow(self):
        for body in ['add[T](a,b)', '(a + b)']:
            with self.subTest(body=body), self.assertRaisesRegex(TypecheckError, 'undeclared failures'):
                checked(addition(body, contract='failure { Never }'))

    def test_runtime_int_overflow_preserves_event_and_continuation(self):
        for body in ['add[T](a,b)', '(a + b)']:
            program, _ = checked(addition(body) + f'var x: Int = addTwo[Int]({INT_MAX},1)\nprint[Int](7)')
            env, context, stdout = recorded(program)
            self.assertIsInstance(env['x'], UninitializedBinding)
            self.assertEqual(stdout, '7\n')
            event, = context.failure_history
            self.assertEqual(event.failure_id, FailureId.IntegerOverflow)
            self.assertEqual(event.origin, 'core.int.add')
            self.assertEqual(event.status, FailureStatus.UNRESOLVED)
            self.assertEqual(context.get_call(event.call_id).declared_failure_contract, OVERFLOW)

    def test_int_resolve_allowed_and_recovers_overflow(self):
        for body in ['add[T](a,b)', '(a + b)']:
            program, diagnostics = checked(
                addition(body) + f'var x: Int = addTwo[Int]({INT_MAX},1)\n'
                'resolve x { IntegerOverflow { x = 0\nprint[Int](9) } }\nprint[Int](x)')
            self.assertEqual(diagnostics.items, [])
            env, context, stdout = recorded(program)
            self.assertEqual((env['x'].value, stdout), (0, '9\n0\n'))
            self.assertEqual(context.failure_history[0].status, FailureStatus.RESOLVED)

    def test_float_resolve_rejected_for_absent_conditional_effect(self):
        for body in ['add[T](a,b)', '(a + b)']:
            with self.subTest(body=body), self.assertRaisesRegex(
                    TypecheckError, 'latest write.*no declared or inferred'):
                checked(addition(body) + 'var x: Float = addTwo[Float](1.0,2.0)\n'
                        'resolve x { IntegerOverflow { x = 0 } }')

    def test_user_call_frames_hold_concrete_contracts_and_bindings(self):
        program, _ = checked(addition('(a + b)') +
                             'var x: Int = addTwo[Int](1,2)\nvar y: Float = addTwo[Float](1.0,2.0)')
        result = eval_program(program)
        frames = list(result.call_frames.values())[1:]
        self.assertEqual([f.function_name for f in frames], ['addTwo', 'addTwo'])
        self.assertEqual([f.declared_failure_contract for f in frames], [OVERFLOW, FLOAT_OVERFLOW])
        self.assertEqual([dict(f.type_bindings) for f in frames],
                         [{'T': TypeRef('Int')}, {'T': TypeRef('Float')}])
        self.assertTrue(all(isinstance(f.declared_failure_contract, frozenset) for f in frames))

    def test_thunk_uses_instantiated_latent_failures_and_remains_nonmemoized(self):
        source = (addition('(a + b)') +
                  f'sigtype CheckedInt() -> Int {{ failure {{ IntegerOverflow }} }}\nsigtype CheckedFloat() -> Float {{ failure {{ FloatOverflow }} }}\nvar intThunk: Thunk[CheckedInt] = thunk(addTwo[Int]({INT_MAX},1))\n'
                  'var floatThunk: Thunk[CheckedFloat] = thunk(addTwo[Float](1.0,2.0))\n')
        program, syms, env, diagnostics = self.annotated(source)
        self.assertEqual(env['intThunk'].ty.latent_failures, OVERFLOW)
        self.assertEqual(env['floatThunk'].ty.latent_failures, FLOAT_OVERFLOW)
        self.assertEqual(effect_expr(program.items[-2].expr, env, syms), EMPTY_FAILURES)
        self.assertEqual(diagnostics.items, [])
        program, _ = checked(source + 'print[Float](force(floatThunk))\n'
                             'print[Int](force(intThunk))\nprint[Int](force(intThunk))')
        _, context, stdout = recorded(program)
        self.assertEqual(stdout, '3.0\n')
        self.assertEqual([e.failure_id for e in context.failure_history],
                         [FailureId.IntegerOverflow, FailureId.IntegerOverflow])
        with self.assertRaisesRegex(TypecheckError, 'type mismatch'):
            checked(addition() + 'sigtype DeferredInt() -> Int { failure { Never } }\nvar t: Thunk[DeferredInt] = thunk(addTwo[Int](1,2))')

    def test_generic_thunk_requires_a_concrete_contract(self):
        for body in ['add[T](a,b)', '(a + b)']:
            with self.subTest(body=body), self.assertRaisesRegex(TypecheckError, 'fully concrete'):
                checked('sigtype Deferred[T]() -> T {} ' + addition(body).replace(
                    'return ' + body, 'let saved: Thunk[Deferred[T]] = thunk(' + body + ') return force(saved)'))

    def test_nested_generic_condition_substitutes_u_to_t(self):
        source = ('sig inner[U](U,U) -> U { require U guarantees Addable '
                  'failure { IntegerOverflow when U in BoundedArithmetic, FloatOverflow when U in FiniteArithmetic } }\n'
                  'func inner(a: U,b: U) { return (a + b) }\n'
                  'sig outer[T](T,T) -> T { require T guarantees Addable ' + CONDITION + ' }\n'
                  'func outer(a: T,b: T) { return inner[T](a,b) }\n'
                  'var x: Int = outer[Int](1,2)\nvar y: Float = outer[Float](1.0,2.0)')
        program, syms, env, _ = self.annotated(source)
        call = syms.funcs['outer'].body.stmts[0].expr
        self.assertEqual(syms.resolved_calls[id(call)].type_bindings, {'U': TypeRef('T')})
        generic_env = {'a': Binding(TypeRef('T'), False), 'b': Binding(TypeRef('T'), False)}
        self.assertEqual(symbolic_effect_expr(call, generic_env, syms), CONDITIONAL_OVERFLOW)
        self.assertEqual(effect_expr(program.items[-2].expr, env, syms), OVERFLOW)
        self.assertEqual(effect_expr(program.items[-1].expr, env, syms), FLOAT_OVERFLOW)
        result = eval_program(program)
        self.assertEqual((result.environment['x'].value, result.environment['y'].value), (3, 3.0))
        self.assertEqual([f.declared_failure_contract for f in list(result.call_frames.values())[1:]],
                         [OVERFLOW, OVERFLOW, FLOAT_OVERFLOW, FLOAT_OVERFLOW])

    def test_different_group_or_typevar_does_not_cover_condition(self):
        with self.assertRaisesRegex(TypecheckError, 'undeclared failures'):
            checked(addition(contract='failure { IntegerOverflow when T in FiniteArithmetic }'))
        with self.assertRaisesRegex(TypecheckError, 'undeclared failures'):
            checked('sig f[T,U](T,U) -> T { require T guarantees Addable '
                    'failure { IntegerOverflow when U in BoundedArithmetic, FloatOverflow when U in FiniteArithmetic } }\n'
                    'func f(a: T,b: U) { return add[T](a,a) }')

    def test_unconditional_declared_failure_covers_conditional_effect(self):
        for body in ['add[T](a,b)', '(a + b)']:
            program, syms, env, _ = self.annotated(
                addition(body, contract='failure { IntegerOverflow, FloatOverflow }') +
                'var x: Int = addTwo[Int](1,2)\nvar y: Float = addTwo[Float](1.0,2.0)')
            self.assertEqual(effect_expr(program.items[-2].expr, env, syms), OVERFLOW | FLOAT_OVERFLOW)
            self.assertEqual(effect_expr(program.items[-1].expr, env, syms), OVERFLOW | FLOAT_OVERFLOW)
            self.assertEqual(syms.sig_conditional_failures['addTwo'], frozenset())

    def test_conditional_declaration_cannot_cover_unconditional_inferred_failure(self):
        with self.assertRaisesRegex(TypecheckError, 'undeclared failures.*DivideByZero'):
            checked('sig risky[T](T) -> Float { failure { DivideByZero when T in BoundedArithmetic, FloatOverflow, FloatUnderflow } }\n'
                    'func risky(x: T) { return div(1.0,0.0) }')

    def test_optional_property_does_not_become_a_call_requirement(self):
        source = ('sig identity[T](T) -> T { failure { IOErr when T in BoundedArithmetic } }\n'
                  'func identity(x: T) { return x }\n'
                  'var a: Int = identity[Int](1)\nvar b: Float = identity[Float](1.0)')
        program, syms, env, _ = self.annotated(source)
        self.assertEqual(effect_expr(program.items[-2].expr, env, syms), failures(FailureId.IOErr))
        self.assertEqual(effect_expr(program.items[-1].expr, env, syms), EMPTY_FAILURES)
        self.assertEqual(eval_program(program).environment['b'].value, 1.0)

    def test_custom_float_marker_conditions_are_independent_capabilities(self):
        marker = 'typegroup TaggedFailure {}\nregister TaggedFailure { Float }\n'
        source = (marker + 'sig marked[T](T) -> T { failure { IOErr when T in TaggedFailure } }\n'
                  'func marked(x: T) { return x }\n'
                  'var a: Int = marked[Int](1)\nvar b: Float = marked[Float](1.0)')
        program, syms, env, diagnostics = self.annotated(source)
        self.assertEqual(syms.sig_conditional_failures['marked'],
                         frozenset({ConditionalFailure(FailureId.IOErr, 'T', 'TaggedFailure')}))
        self.assertNotIn('Int', syms.typegroups['TaggedFailure'])
        self.assertIn('Float', syms.typegroups['TaggedFailure'])
        self.assertEqual(effect_expr(program.items[-2].expr, env, syms), EMPTY_FAILURES)
        self.assertEqual(effect_expr(program.items[-1].expr, env, syms), failures(FailureId.IOErr))
        self.assertEqual([d.message for d in diagnostics], ['unhandled failures: IOErr'])
        result = eval_program(program)
        self.assertEqual((result.environment['a'].value, result.environment['b'].value), (1, 1.0))
        self.assertEqual([f.declared_failure_contract for f in list(result.call_frames.values())[1:]],
                         [EMPTY_FAILURES, failures(FailureId.IOErr)])
        with self.assertRaisesRegex(TypecheckError, 'does not guarantee Addable'):
            checked(marker + 'sig bad[T](T,T) -> T { require T in TaggedFailure '
                    'failure { IntegerOverflow } }\nfunc bad(a: T,b: T) { return add[T](a,b) }')

    def test_unknown_or_out_of_scope_typevariable_rejected(self):
        for signature in ['sig f[T](T) -> T', 'sig f(Int) -> Int']:
            variable = 'U' if '(T)' in signature else 'T'
            with self.subTest(signature=signature), self.assertRaises(TypecheckError):
                checked(signature + f' {{ failure {{ IOErr when {variable} in BoundedArithmetic }} }}')

    def test_unknown_guarantee_rejected(self):
        with self.assertRaisesRegex(TypecheckError, 'unknown typegroup'):
            checked('sig f[T](T) -> T { failure { IOErr when T in MissingGroup } }')

    def test_conditional_never_rejected(self):
        with self.assertRaises((SyntaxError, TypecheckError)):
            checked('sig f[T](T) -> T { failure { Never when T in BoundedArithmetic } }')

    def test_conditional_failureset_and_failure_variable_rejected(self):
        for name in ['ArithmeticFailure', 'F']:
            with self.subTest(name=name), self.assertRaises((SyntaxError, TypecheckError)):
                checked('failureset ArithmeticFailure { IntegerOverflow }\n'
                        f'sig f[T](T) -> T {{ failure {{ {name} when T in BoundedArithmetic }} }}')

    def test_duplicate_conditional_clause_rejected(self):
        with self.assertRaises((SyntaxError, TypecheckError)):
            checked('sig f[T](T) -> T { ' + CONDITION + '\n' + CONDITION + ' }')

    def test_unconditional_and_conditional_redundancy_rejected_in_both_orders(self):
        for clauses in ['failure { IntegerOverflow }\n' + CONDITION,
                        CONDITION + '\nfailure { IntegerOverflow }']:
            with self.subTest(clauses=clauses), self.assertRaises((SyntaxError, TypecheckError)):
                checked('sig f[T](T) -> T { ' + clauses + ' }')

    def test_only_typegroup_condition_syntax_allowed(self):
        conditions = ['T guarantees Numeric', 'T == Int', 'true',
                      'T guarantees BoundedArithmetic and T guarantees Addable',
                      'T guarantees BoundedArithmetic or T guarantees Addable',
                      '(T guarantees BoundedArithmetic)', 'x > 0']
        for condition in conditions:
            with self.subTest(condition=condition), self.assertRaises((SyntaxError, TypecheckError)):
                checked('typegroup Numeric { Int, Float }\n'
                        f'sig f[T](T) -> T {{ failure {{ IntegerOverflow when {condition} }} }}')

    def test_catalog_accepts_mixed_string_and_structured_entries(self):
        entry = {'failure': 'IntegerOverflow',
                 'when': {'typevar': 'T', 'in': 'BoundedArithmetic'}}
        syms = self.catalog_symbols(['IOErr', entry])
        self.assertEqual(syms.sig_failures['f'], failures(FailureId.IOErr))
        self.assertEqual(syms.sig_conditional_failures['f'], frozenset({BOUNDED_OVERFLOW}))
        syms = self.catalog_symbols(['DivideByZero'])
        self.assertEqual(syms.sig_failures['f'], failures(FailureId.DivideByZero))

    def test_catalog_rejects_invalid_conditional_entries_and_redundancy(self):
        entry = {'failure': 'IntegerOverflow',
                 'when': {'typevar': 'T', 'in': 'BoundedArithmetic'}}
        invalid = [[entry, entry], ['IntegerOverflow', entry],
                   [{'failure': 'Never', 'when': entry['when']}],
                   [{'failure': 'IntegerOverflow', 'when': {'typevar': 'U', 'in': 'BoundedArithmetic'}}],
                   [{'failure': 'IntegerOverflow', 'when': {'typevar': 'T', 'in': 'Missing'}}],
                   [{'failure': 'IntegerOverflow', 'when': {'typevar': 'T', 'in': 'Numeric'}}],
                   [{'failure': 'IntegerOverflow', 'when': {'typevar': 'T', 'equals': 'Int'}}],
                   [{'failure': 'IntegerOverflow', 'when': {'typevar': 'T', 'in': 'BoundedArithmetic', 'or': 'Addable'}}]]
        for entries in invalid:
            with self.subTest(entries=entries), self.assertRaises((ValueError, TypecheckError)):
                self.catalog_symbols(entries)

    def test_standard_arithmetic_contracts_and_marker_registration(self):
        syms = build_symbols(checked('')[0])
        self.assertIn('Int', syms.typegroups['BoundedArithmetic'])
        self.assertNotIn('Float', syms.typegroups['BoundedArithmetic'])
        self.assertNotIn('Addable', syms.type_guarantees['Int64'])
        self.assertNotIn('BoundedArithmetic', syms.guarantees)
        for operation in ['add', 'sub', 'mul']:
            with self.subTest(operation=operation):
                self.assertEqual(syms.sig_failures[operation], EMPTY_FAILURES)
                self.assertEqual(syms.sig_conditional_failures[operation], frozenset({BOUNDED_OVERFLOW, FINITE_OVERFLOW} | ({FINITE_UNDERFLOW} if operation == "mul" else set())))

    def test_standard_explicit_and_infix_arithmetic_share_all_contracts(self):
        operations = [('add', '+', 'Addable', 3), ('sub', '-', 'Subtractable', -1),
                      ('mul', '*', 'Multipliable', 2)]
        for operation, operator, guarantee, expected in operations:
            condition = CONDITION + (" failure { FloatUnderflow when T in FiniteArithmetic }" if operation == "mul" else "")
            symbolic = FailureContract(EMPTY_FAILURES, CONDITIONAL_OVERFLOW.conditional | frozenset({FINITE_UNDERFLOW})) if operation == "mul" else CONDITIONAL_OVERFLOW
            float_effect = FLOAT_OVERFLOW | (failures(FailureId.FloatUnderflow) if operation == "mul" else EMPTY_FAILURES)
            for body in [f'{operation}[T](a,b)', f'(a {operator} b)']:
                source = (f'sig generic[T](T,T) -> T {{ require T guarantees {guarantee} {condition} }}\n'
                          f'func generic(a: T,b: T) {{ return {body} }}\n')
                for typ, args, effect in [('Int', '1,2', OVERFLOW),
                                         ('Float', '1.0,2.0', float_effect)]:
                    with self.subTest(operation=operation, body=body, typ=typ):
                        program, syms, env, _ = self.annotated(source + f'var x: {typ} = generic[{typ}]({args})')
                        call = syms.funcs['generic'].body.stmts[0].expr
                        self.assertEqual(syms.resolved_calls[id(call)].deferred_guarantee, guarantee)
                        self.assertEqual(syms.resolved_calls[id(call)].deferred_method, operation)
                        self.assertEqual(symbolic_effect_expr(call, {'a': Binding(TypeRef('T'), False),
                                                                   'b': Binding(TypeRef('T'), False)}, syms),
                                         symbolic)
                        self.assertEqual(effect_expr(program.items[-1].expr, env, syms), effect)
                        cell = eval_program(program).environment['x']
                        self.assertEqual(cell.value, expected if typ == 'Int' else float(expected))
                        self.assertEqual(cell.typ, TypeRef(typ))
                with self.assertRaisesRegex(TypecheckError, 'does not guarantee ' + guarantee):
                    checked(source.replace(f'require T guarantees {guarantee}', ''))
            for typ, left, right, effect in [('Int', '1', '2', OVERFLOW),
                                           ('Float', '1.0', '2.0', float_effect)]:
                resolutions = []
                for expression in [f'{operation}[{typ}]({left},{right})', f'({left} {operator} {right})']:
                    program, syms, env, _ = self.annotated(f'var x: {typ} = {expression}')
                    call = program.items[0].expr
                    self.assertEqual(effect_expr(call, env, syms), effect)
                    resolutions.append(syms.resolved_calls[id(call)])
                self.assertEqual(resolutions[0], resolutions[1])

    def test_runtime_builtin_contracts_and_direct_alias_trust_boundary(self):
        for operation, left, right in [('add', INT_MAX, 1), ('sub', -INT_MAX, 2),
                                       ('mul', INT_MAX, 2)]:
            implementation = f'core.int.{operation}'
            self.assertEqual(INT_ARITHMETIC_FAILURES[implementation], OVERFLOW)
            self.assertEqual(builtin_failure_contract(EMPTY_FAILURES, implementation, direct_builtin=False), OVERFLOW)
            self.assertEqual(builtin_failure_contract(EMPTY_FAILURES, implementation, direct_builtin=True), EMPTY_FAILURES)
            with self.assertRaises(RaisedFailure) as raised:
                call_builtin(implementation, left, right)
            self.assertEqual(raised.exception.fid, FailureId.IntegerOverflow)
        program, syms, env, diagnostics = self.annotated(
            'sig alias(Int,Int) -> Int { builtin core.int.add }\n' +
            f'var x: Int = alias({INT_MAX},1)')
        self.assertEqual(effect_expr(program.items[-1].expr, env, syms), EMPTY_FAILURES)
        self.assertEqual(syms.sig_conditional_failures['alias'], frozenset())
        self.assertEqual(diagnostics.items, [])
        self.assertEqual(eval_program(program).contract_violation.failure_id, FailureId.IntegerOverflow)
        program, _ = checked('sig alias(Int,Int) -> Int { failure { IntegerOverflow } builtin core.int.add }\n' +
                             f'var x: Int = alias({INT_MAX},1)')
        result = eval_program(program)
        self.assertIsNone(result.contract_violation)
        self.assertEqual(result.failure_history[0].failure_id, FailureId.IntegerOverflow)

    def test_expected_type_and_argument_effect_do_not_change_instantiation(self):
        program, syms, env, _ = self.annotated(addition() + 'var x: Float = addTwo[Int](1,2)')
        call = program.items[-1].expr
        self.assertEqual(syms.resolved_calls[id(call)].type_bindings, {'T': TypeRef('Int')})
        self.assertEqual(effect_expr(call, env, syms), OVERFLOW)
        source = (addition() + f'sigtype CheckedInt() -> Int {{ failure {{ IntegerOverflow }} }}\nvar t: Thunk[CheckedInt] = thunk(add[Int]({INT_MAX},1))\n'
                  'var x: Float = addTwo[Float](toFloat(force(t)),2.0)\n'
                  'resolve x { IntegerOverflow { x = 0 } }')
        program, syms, env, diagnostics = self.annotated(source)
        call = program.items[-2].expr
        self.assertEqual(syms.resolved_calls[id(call)].type_bindings, {'T': TypeRef('Float')})
        self.assertEqual(effect_expr(call, env, syms), OVERFLOW | FLOAT_OVERFLOW)
        self.assertEqual([d.message for d in diagnostics], ['unhandled failures: FloatOverflow'])
        result = eval_program(program)
        self.assertEqual(result.environment['x'].value, 0.0)
        self.assertEqual(len(result.failure_history), 1)
        self.assertFalse(result.unresolved_events)
        self.assertIsNone(result.contract_violation)


if __name__ == '__main__':
    unittest.main()
