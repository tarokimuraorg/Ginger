import unittest
from unittest.mock import patch

from ginger.ast import TypeRef
from ginger.builtin import BUILTINS
from ginger.core.failure_contract import ConditionalFailure
from ginger.core.failure_spec import EMPTY_FAILURES, FailureId, failures
from ginger.diagnostics import Diagnostics
from ginger.errors import EvalError, TypecheckError
from ginger.eval import eval_program
from ginger.lower import lower_program
from ginger.parser import parse
from ginger.symbols_builder import build_symbols, normalize_types, _same_type
from ginger.type_identity import type_identity_key
from ginger.typecheck import collect_type_evidence, effect_expr, typecheck_program


CAPABILITY = '''guarantee Tagged { op(self: Self) -> Self }
sig op(T) -> T { require T guarantees Tagged }
sig relay(U) -> U { require U guarantees Tagged }
func relay(x: U) { return op(x) }
'''


def implementation(typ, builtin='test.box_int_op'):
    return f'impl {typ} guarantees Tagged {{ op = builtin {builtin} }}\n'


def use(typ, call='op'):
    return (f'sig make() -> {typ} {{ builtin test.make }}\n'
            f'var result: {typ} = {call}(make())')


def annotated(source):
    program = lower_program(parse(source))
    symbols = build_symbols(program)
    program = normalize_types(program, symbols.failuresets)
    bindings = typecheck_program(program, Diagnostics(), syms=symbols)
    return program, symbols, bindings


def normalized_type(spelling):
    typ = parse(f'register {spelling} guarantees Marker').items[0].typ
    return normalize_types(typ, {})


class ParameterizedTypeIdentityTests(unittest.TestCase):
    def setUp(self):
        # The payload is deliberately opaque; Box has no container semantics.
        self.payload = object()
        self.seen = []

        def selected(value):
            self.seen.append('Int')
            return value

        def other(value):
            self.seen.append('other')
            return value

        patcher = patch.dict(BUILTINS, {
            'test.make': lambda: self.payload,
            'test.box_int_op': selected,
            'test.other_op': other,
        })
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_parser_and_lowering_preserve_targets(self):
        source = (implementation('Box[Int]') +
                  'register Box[Float] guarantees Marker\n'
                  'typegroup Boxes = Box[Int] | Box[Float]')
        parsed = parse(source)
        lowered = lower_program(parsed)
        for program in [parsed, lowered]:
            self.assertEqual(program.items[0].typ, TypeRef('Box', (TypeRef('Int'),)))
            self.assertEqual(program.items[1].typ, TypeRef('Box', (TypeRef('Float'),)))
            self.assertEqual(program.items[2].members,
                             [program.items[0].typ, program.items[1].typ])

    def test_structural_equality_and_generic_inference(self):
        integer = normalized_type('Box[Int]')
        floating = normalized_type('Box[Float]')
        self.assertNotEqual(integer, floating)
        self.assertFalse(_same_type(integer, floating))
        self.assertEqual({integer: 'int', floating: 'float'}[integer], 'int')
        evidence = {}
        collect_type_evidence(normalized_type('Box[T]'), integer, evidence)
        self.assertEqual(evidence, {'T': [TypeRef('Int')]})
        with self.assertRaisesRegex(TypecheckError, 'type mismatch'):
            collect_type_evidence(integer, floating, {})
        with self.assertRaisesRegex(TypecheckError, 'type mismatch'):
            annotated('sig take(Box[Int]) -> Box[Int] { builtin test.box_int_op }\n'
                      + use('Box[Float]', 'take'))

    def test_specialized_direct_success_and_self_projection(self):
        program, symbols, _ = annotated(CAPABILITY + implementation('Box[Int]') + use('Box[Int]'))
        resolved = symbols.resolved_calls[id(program.items[-1].expr)]
        typ = normalized_type('Box[Int]')
        self.assertEqual(resolved.type_bindings, {'T': typ})
        self.assertEqual(resolved.parameter_types, (typ,))
        self.assertEqual(resolved.return_type, typ)
        self.assertEqual(resolved.implementation, 'test.box_int_op')
        result = eval_program(program)
        self.assertIs(result.environment['result'].value, self.payload)
        self.assertEqual(self.seen, ['Int'])

    def test_specialized_impl_rejects_other_types_direct_and_deferred(self):
        for typ in ['Box[Float]', 'Box[MissingType]', 'Box']:
            for call in ['op', 'relay']:
                with self.subTest(typ=typ, call=call), self.assertRaisesRegex(
                        TypecheckError, 'does not guarantee Tagged'):
                    annotated(CAPABILITY + implementation('Box[Int]') + use(typ, call))
        self.assertEqual(self.seen, [])

    def test_deferred_success_preserves_concrete_frame_type(self):
        program, symbols, _ = annotated(CAPABILITY + implementation('Box[Int]') + use('Box[Int]', 'relay'))
        body_call = symbols.funcs['relay'].body.stmts[0].expr
        resolved = symbols.resolved_calls[id(body_call)]
        self.assertIsNone(resolved.implementation)
        self.assertEqual(resolved.deferred_guarantee, 'Tagged')
        result = eval_program(program)
        frame, = [f for f in result.call_frames.values() if f.function_name == 'relay']
        self.assertEqual(dict(frame.type_bindings), {'U': normalized_type('Box[Int]')})
        self.assertEqual(result.environment['result'].typ, normalized_type('Box[Int]'))
        self.assertIs(result.environment['result'].value, self.payload)
        self.assertEqual(self.seen, ['Int'])

    def test_two_specializations_select_distinct_impls_direct_and_deferred(self):
        for call in ['op', 'relay']:
            for typ, expected in [('Box[Int]', 'Int'), ('Box[Float]', 'other')]:
                with self.subTest(call=call, typ=typ):
                    source = (CAPABILITY + implementation('Box[Int]') +
                              implementation('Box[Float]', 'test.other_op') + use(typ, call))
                    program, _, _ = annotated(source)
                    self.seen.clear()
                    eval_program(program)
                    self.assertEqual(self.seen, [expected])

    def test_deferred_missing_runtime_impl_never_falls_back_to_outer_name(self):
        source = (CAPABILITY + implementation('Box[Int]') +
                  implementation('Box[Float]', 'test.other_op') + use('Box[Float]', 'relay'))
        program, _, _ = annotated(source)

        def remove_after_check(prog, diags, *, syms):
            result = typecheck_program(prog, diags, syms=syms)
            # Isolate runtime lookup from the normal call-site rejection above.
            del syms.impls[(type_identity_key(normalized_type('Box[Float]')), 'Tagged', 'op')]
            return result

        with patch('ginger.eval.typecheck_program', side_effect=remove_after_check):
            with self.assertRaisesRegex(EvalError, 'no resolved runtime implementation'):
                eval_program(program)
        self.assertEqual(self.seen, [])

    def test_register_isolation(self):
        prefix = ('guarantee Marker {}\nregister Box[Int] guarantees Marker\n'
                  'sig take(T) -> T { require T guarantees Marker }\n'
                  'func take(x: T) { return x }\n')
        program, _, _ = annotated(prefix + use('Box[Int]', 'take'))
        self.assertIs(eval_program(program).environment['result'].value, self.payload)
        for typ in ['Box[Float]', 'Box[MissingType]', 'Box']:
            with self.subTest(typ=typ), self.assertRaisesRegex(TypecheckError, 'does not guarantee Marker'):
                annotated(prefix + use(typ, 'take'))

    def test_distinct_registers_allowed_but_exact_duplicate_rejected(self):
        prefix = 'guarantee Marker {}\nregister Box[Int] guarantees Marker\n'
        _, symbols, _ = annotated(prefix + 'register Box[Float] guarantees Marker')
        for typ in ['Box[Int]', 'Box[Float]']:
            self.assertEqual(symbols.type_guarantees[type_identity_key(normalized_type(typ))], {'Marker'})
        with self.assertRaisesRegex(TypecheckError, 'duplicate register'):
            annotated(prefix + 'register Box[Int] guarantees Marker')

    def test_typegroup_isolation(self):
        prefix = ('typegroup Boxes = Box[Int]\n'
                  'sig take(T) -> T { require T in Boxes }\n'
                  'func take(x: T) { return x }\n')
        program, _, _ = annotated(prefix + use('Box[Int]', 'take'))
        self.assertIs(eval_program(program).environment['result'].value, self.payload)
        for typ in ['Box[Float]', 'Box[MissingType]', 'Box']:
            with self.subTest(typ=typ), self.assertRaisesRegex(TypecheckError, 'requirement not satisfied'):
                annotated(prefix + use(typ, 'take'))
        program, symbols, _ = annotated(prefix.replace('= Box[Int]', '= Box[Int] | Box[Float]') +
                                        use('Box[Float]', 'take'))
        self.assertEqual(len(symbols.typegroups['Boxes']), 2)
        eval_program(program)

    def test_nested_and_multi_argument_isolation_across_all_registrations(self):
        for target, other in [('Box[Box[Int]]', 'Box[Box[Float]]'),
                              ('Pair[Int, Float]', 'Pair[Int, Int]'),
                              ('Pair[Int, Float]', 'Pair[Float, Int]')]:
            for kind in ['impl', 'register', 'typegroup']:
                with self.subTest(target=target, other=other, kind=kind):
                    if kind == 'impl':
                        prefix, calls = CAPABILITY + implementation(target), ['op', 'relay']
                    else:
                        declaration, requirement = (
                            (f'guarantee Marker {{}}\nregister {target} guarantees Marker', 'T guarantees Marker')
                            if kind == 'register' else (f'typegroup Boxes = {target}', 'T in Boxes'))
                        prefix = (declaration + f'\nsig take(T) -> T {{ require {requirement} }}\n'
                                  'func take(x: T) { return x }\n')
                        calls = ['take']
                    for call in calls:
                        program, _, _ = annotated(prefix + use(target, call))
                        eval_program(program)
                        with self.assertRaisesRegex(TypecheckError, 'requirement not satisfied'):
                            annotated(prefix + use(other, call))

    def test_open_world_exact_registration_and_unregistered_plain_values(self):
        for typ in ['MissingType', 'Box[MissingType]']:
            for call in ['op', 'relay']:
                program, _, _ = annotated(CAPABILITY + implementation(typ) + use(typ, call))
                self.assertIs(eval_program(program).environment['result'].value, self.payload)
            program, _, _ = annotated(use(typ).replace('op(make())', 'make()'))
            self.assertIs(eval_program(program).environment['result'].value, self.payload)

    def test_thunk_result_identity_and_existing_effect_independence(self):
        for declaration, requirement, call in [
            (CAPABILITY + implementation('Thunk[Int, Never]'), '', 'op'),
            (CAPABILITY + implementation('Thunk[Int, Never]'), '', 'relay'),
            ('guarantee Marker {}\nregister Thunk[Int, Never] guarantees Marker\n',
             'T guarantees Marker', 'take'),
            ('typegroup Boxes = Thunk[Int, Never]\n', 'T in Boxes', 'take'),
        ]:
            prefix = declaration
            if requirement:
                prefix += (f'sig take(T) -> T {{ require {requirement} }}\n'
                           'func take(x: T) { return x }\n')
            for spelling, expression in [('Thunk[Int, Never]', 'thunk(1)'),
                                         ('Thunk[Int, IntegerOverflow]', 'thunk(add(1,2))')]:
                with self.subTest(call=call, spelling=spelling):
                    program, _, _ = annotated(prefix + f'var result: {spelling} = {call}({expression})')
                    self.assertIsNone(eval_program(program).contract_violation)
            with self.assertRaisesRegex(TypecheckError, 'requirement not satisfied'):
                annotated(prefix + f'var result: Thunk[Float, Never] = {call}(thunk(1.0))')

    def test_metadata_remains_outside_capability_identity(self):
        pure = normalized_type('Thunk[Int, Never]')
        effectful = normalized_type('Thunk[Int, IntegerOverflow]')
        conditional = TypeRef('Thunk', (TypeRef('Int'),), latent_failures=EMPTY_FAILURES,
                              latent_conditions=frozenset({ConditionalFailure(
                                  FailureId.IntegerOverflow, 'T', 'Marker')}))
        raw = TypeRef('Thunk', (TypeRef('Int'),), failure_specs=('Never',))
        for other in [effectful, conditional, raw]:
            self.assertNotEqual(pure, other)
            self.assertEqual(type_identity_key(pure), type_identity_key(other))
        self.assertFalse(_same_type(pure, effectful))
        self.assertFalse(_same_type(pure, conditional))

    def test_conditional_failure_uses_exact_registration(self):
        prefix = ('guarantee Marker {}\nregister Box[Int] guarantees Marker\n'
                  'sig keep(T) -> T { failure IntegerOverflow when T guarantees Marker }\n'
                  'func keep(x: T) { return x }\n')
        for typ, expected in [('Box[Int]', failures(FailureId.IntegerOverflow)),
                              ('Box[Float]', EMPTY_FAILURES), ('Box[MissingType]', EMPTY_FAILURES)]:
            with self.subTest(typ=typ):
                program, symbols, bindings = annotated(prefix + use(typ, 'keep'))
                self.assertEqual(effect_expr(program.items[-1].expr, bindings, symbols), expected)
                result = eval_program(program)
                frame, = [f for f in result.call_frames.values() if f.function_name == 'keep']
                self.assertEqual(frame.declared_failure_contract, expected)

    def test_duplicate_and_missing_impl_validation_use_full_identity(self):
        prefix = CAPABILITY + implementation('Box[Int]')
        with self.assertRaisesRegex(TypecheckError, 'duplicate impl'):
            annotated(prefix + implementation('Box[Int]'))
        with self.assertRaisesRegex(TypecheckError, 'missing impl'):
            annotated(prefix + 'impl Box[Float] guarantees Tagged {}')

    def test_argument_free_keys_preserve_primitive_registrations(self):
        for name in ['Int', 'Float', 'Int64', 'Bool', 'Ordering', 'Unit']:
            self.assertEqual(type_identity_key(TypeRef(name)), name)
        _, symbols, _ = annotated('')
        self.assertEqual(symbols.impls[('Int', 'Addable', 'add')], 'core.int.add')
        self.assertEqual(symbols.impls[('Float', 'Addable', 'add')], 'core.float.add')
        self.assertIn('Negatable', symbols.type_guarantees['Int64'])


if __name__ == '__main__':
    unittest.main()
