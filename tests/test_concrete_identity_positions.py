import unittest
from unittest.mock import patch

from ginger.ast import TypeRef
from ginger.builtin import BUILTINS
from ginger.errors import TypecheckError
from ginger.lower import lower_program
from ginger.parser import parse
from ginger.pipeline import compile
from ginger.symbols_builder import build_symbols, normalize_types
from ginger.type_identity import type_identity_key
from test_parameterized_type_identity import annotated


def declaration(kind, typ):
    if kind == 'impl':
        return (f'guarantee G {{ op(self: Self) -> Self }}\n'
                f'impl {typ} guarantees G {{ op = builtin test.op }}')
    if kind == 'register':
        return f'guarantee Marker {{}}\nregister {typ} guarantees Marker'
    return f'typegroup Group = {typ}'


class ConcreteIdentityPositionTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.dict(BUILTINS, {'test.op': lambda x: x, 'test.make': lambda: object()})
        patcher.start()
        self.addCleanup(patcher.stop)

    def assert_rejected(self, kind, typ, name, contextual=False):
        source = declaration(kind, typ)
        program = lower_program(parse(source))
        position = 'typegroup member' if kind == 'typegroup' else f'{kind} target'
        category = 'contextual placeholder' if contextual else 'symbolic type variable'
        message = f"{category} '{name}' is not allowed in {position}"
        for action in [lambda: build_symbols(program), lambda: compile(source)]:
            with self.assertRaisesRegex(TypecheckError, message):
                action()

    def test_symbolic_targets_rejected_at_declaration_time(self):
        for kind in ['impl', 'register', 'typegroup']:
            for typ, name in [('T', 'T'), ('Box[T]', 'T'), ('Box[Box[T]]', 'T'),
                              ('Pair[Int,U]', 'U'), ('T[U]', 'T'), ('Thunk[T]<Never>', 'T')]:
                with self.subTest(kind=kind, typ=typ):
                    self.assert_rejected(kind, typ, name)

    def test_self_placeholder_rejected_only_in_identity_positions(self):
        for kind in ['impl', 'register', 'typegroup']:
            for typ in ['Self', 'Box[Self]', 'Self[Int]']:
                with self.subTest(kind=kind, typ=typ):
                    self.assert_rejected(kind, typ, 'Self', contextual=True)
        compile('sig unchanged(Self) -> Self {}')

    def test_mixed_typegroup_rejects_any_symbolic_member(self):
        for members in ['Box[Int] | Box[T]', 'Box[T] | Box[Int]']:
            self.assert_rejected('typegroup', members, 'T')

    def test_concrete_and_open_world_targets_remain_valid(self):
        for kind in ['impl', 'register', 'typegroup']:
            for typ in ['Int', 'Box[Int]', 'Box[Float]', 'MissingType', 'Box[MissingType]',
                        'Box[Box[Int]]', 'Pair[Int,Float]', 'Thunk[Int]<Never>', 'Never']:
                with self.subTest(kind=kind, typ=typ):
                    source = declaration(kind, typ)
                    syms = build_symbols(lower_program(parse(source)))
                    compile(source)
                    target = parse(f'register {typ} guarantees Marker').items[0].typ
                    key = type_identity_key(normalize_types(target, {}))
                    if kind == 'impl':
                        self.assertEqual(syms.impls[(key, 'G', 'op')], 'test.op')
                    elif kind == 'register':
                        self.assertIn('Marker', syms.type_guarantees[key])
                    else:
                        self.assertEqual(syms.typegroups['Group'], {key})

    def test_validation_precedes_registration_conflicts(self):
        for source, position in [
            ('register Box[T] guarantees MissingGuarantee', 'register target'),
            ('impl Box[T] guarantees MissingGuarantee {}', 'impl target'),
            ('typegroup Group = Int\ntypegroup Group = Box[T]', 'typegroup member'),
        ]:
            with self.subTest(position=position), self.assertRaisesRegex(TypecheckError, position):
                compile(source)

    def test_generic_sig_inference_and_return_substitution_remain_valid(self):
        for parameter, argument, bindings in [
            ('T', 'Int', {'T': TypeRef('Int')}),
            ('Box[T]', 'Box[Int]', {'T': TypeRef('Int')}),
            ('Box[Box[T]]', 'Box[Box[Int]]', {'T': TypeRef('Int')}),
            ('Pair[T,U]', 'Pair[Int,Float]', {'T': TypeRef('Int'), 'U': TypeRef('Float')}),
            ('Thunk[T]<Never>', 'Thunk[Int]<Never>', {'T': TypeRef('Int')}),
        ]:
            with self.subTest(parameter=parameter):
                source = (f'sig extract({parameter}) -> T {{ builtin test.op }}\n'
                          f'sig make() -> {argument} {{ builtin test.make }}\n'
                          'var result: Int = extract(make())')
                program, syms, _ = annotated(source)
                call = syms.resolved_calls[id(program.items[-1].expr)]
                self.assertEqual(call.type_bindings, bindings)
                self.assertEqual(call.return_type, TypeRef('Int'))
                compile(source)

    def test_identity_encoder_still_accepts_symbolic_types(self):
        self.assertEqual(type_identity_key(TypeRef('Box', (TypeRef('T'),))), ('Box', ('T',)))


if __name__ == '__main__':
    unittest.main()
