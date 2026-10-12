import unittest
from unittest.mock import patch

from ginger.ast import TypeRef
from ginger.builtin import BUILTINS
from ginger.diagnostics import Diagnostics
from ginger.errors import TypecheckError
from ginger.lower import lower_program
from ginger.parser import parse
from ginger.pipeline import compile, run
from ginger.symbols_builder import build_symbols
from ginger.typecheck import typecheck_program


class RequirementTypeVariableTests(unittest.TestCase):
    def assert_rejected(self, source, variable='T', function='op'):
        program = lower_program(parse(source))
        for stage, check in [
            ('build_symbols', lambda: build_symbols(program)),
            ('typecheck', lambda: typecheck_program(program, Diagnostics())),
            ('compile', lambda: compile(source)),
        ]:
            with self.subTest(stage=stage), self.assertRaisesRegex(
                    TypecheckError,
                    f"requirement type variable '{variable}'.*not inferable.*'{function}'"):
                check()

    def test_requirement_only_used_and_unused(self):
        for suffix in ['', '\nvar x: Int = op(1)']:
            with self.subTest(suffix=suffix):
                self.assert_rejected('guarantee G {}\n'
                                     'sig op(Int) -> Int { require T guarantees G }' + suffix)

    def test_return_only_used_and_unused(self):
        for suffix in ['', '\nvar x: Int = make()']:
            with self.subTest(suffix=suffix):
                self.assert_rejected('guarantee G {}\n'
                                     'sig make() -> T { require T guarantees G }' + suffix,
                                     function='make')

    def test_normal_parameter_variable(self):
        compile('guarantee G {}\nsig op(T) -> T { require T guarantees G }')

    def test_nested_parameter_variable(self):
        compile('guarantee G {}\n'
                'sig extract(Box[Box[T]]) -> T { require T guarantees G }')

    def test_nested_parameter_binding(self):
        source = ('guarantee G {}\nregister Int guarantees G\n'
                  'sig box() -> Box[Box[Int]] { builtin test.box }\n'
                  'sig keep(Box[Box[T]]) -> Box[Box[T]] { require T guarantees G }\n'
                  'func keep(x: Box[Box[T]]) { return x }\n'
                  'var x: Box[Box[Int]] = keep(box())')
        with patch.dict(BUILTINS, {'test.box': lambda: ((1,),)}):
            program = lower_program(parse(source))
            syms = build_symbols(program)
            typecheck_program(program, Diagnostics(), syms=syms)
            call = program.items[-1].expr
            self.assertEqual(syms.resolved_calls[id(call)].type_bindings,
                             {'T': TypeRef('Int')})
            self.assertEqual(run(source).environment['x'].value, ((1,),))

    def test_thunk_parameter_binding(self):
        source = ('guarantee G {}\nregister Int guarantees G\n'
                  'sig use(Thunk[T]<Never>) -> T { require T guarantees G }\n'
                  'func use(x: Thunk[T]<Never>) { return force(x) }\n'
                  'var x: Int = use(thunk(1))')
        self.assertEqual(run(source).environment['x'].value, 1)

    def test_latent_failure_name_is_not_a_type_variable(self):
        self.assert_rejected('failureset U { IntegerOverflow }\nguarantee G {}\n'
                             'sig op(Thunk[Int]<U>) -> Int { require U guarantees G }',
                             variable='U')

    def test_variable_constructor_stops_inference_traversal(self):
        # Matching T[U] binds T as a whole; it does not generate evidence for U.
        self.assert_rejected('guarantee G {}\n'
                             'sig op(T[U]) -> T { require U guarantees G }', variable='U')

    def test_second_parameter_need_not_appear_in_return(self):
        source = ('guarantee G {}\nregister Float guarantees G\n'
                  'sig combine(T,U) -> T { require U guarantees G }\n'
                  'func combine(a: T,b: U) { return a }\n'
                  'var x: Int = combine(1,2.0)')
        self.assertEqual(run(source).environment['x'].value, 1)

    def test_typegroup_invalid_targets(self):
        for params, ret in [('Int', 'Int'), ('', 'T')]:
            with self.subTest(params=params):
                self.assert_rejected('typegroup Existing = Int\n'
                                     f'sig op({params}) -> {ret} {{ require T in Existing }}')

    def test_typegroup_parameter_and_forward_reference(self):
        compile('sig op(T) -> T { require T in Existing }\ntypegroup Existing = Int')

    def test_all_requirement_targets_are_checked(self):
        for requirement in ['require U guarantees B', 'require U in Existing']:
            with self.subTest(requirement=requirement):
                self.assert_rejected('guarantee A {}\nguarantee B {}\n'
                                     'typegroup Existing = Int\n'
                                     'sig op(T) -> T { require T guarantees A '
                                     + requirement + ' }', variable='U')

    def test_multiple_and_duplicate_valid_requirements(self):
        compile('guarantee A {}\nguarantee B {}\ntypegroup Existing = Int\n'
                'sig op(T) -> T { require T guarantees A require T guarantees B '
                'require T guarantees A require T in Existing }')

    def test_user_function_invalid_target(self):
        for suffix in ['', '\nvar x: Int = op(1)']:
            with self.subTest(suffix=suffix):
                self.assert_rejected('guarantee G {}\n'
                                     'sig op(Int) -> Int { require T guarantees G }\n'
                                     'func op(x: Int) { return x }' + suffix)

    def test_user_function_valid_target(self):
        source = ('guarantee G {}\nregister Int guarantees G\n'
                  'sig op(T) -> T { require T guarantees G }\n'
                  'func op(x: T) { return x }\nvar x: Int = op(1)')
        self.assertEqual(run(source).environment['x'].value, 1)

    def test_concrete_target(self):
        for requirement in ['guarantees G', 'in Existing']:
            with self.subTest(requirement=requirement):
                self.assert_rejected('guarantee G {}\ntypegroup Existing = Int\n'
                                     f'sig op(Int) -> Int {{ require Int {requirement} }}',
                                     variable='Int')

    def test_target_precedes_unknown_reference(self):
        for requirement in ['guarantees Missing', 'in Missing']:
            with self.subTest(requirement=requirement):
                self.assert_rejected(f'sig op(T) -> T {{ require U {requirement} }}',
                                     variable='U')

    def test_target_precedes_signature_projection(self):
        self.assert_rejected('guarantee G { op(self: Self, other: Self) -> Float }\n'
                             'sig op(Int) -> Int { require T guarantees G }')

    def test_direct_builtin_diagnostic_is_preserved(self):
        with self.assertRaisesRegex(TypecheckError, 'cannot have both requires and builtin'):
            compile('sig op(Int) -> Int { require T guarantees Missing builtin core.int.neg }')

    def test_return_only_without_requirement_is_unchanged(self):
        compile('sig make() -> T {}')
        with self.assertRaisesRegex(TypecheckError, "cannot determine type variable 'T'"):
            compile('sig make() -> T {}\nvar x: Int = make()')

    def test_implementation_count_is_not_an_inference_source(self):
        for extra in ['', '\nimpl Float guarantees G { make = builtin core.float.neg }']:
            with self.subTest(extra=extra):
                self.assert_rejected('guarantee G { make() -> Self }\n'
                                     'impl Int guarantees G { make = builtin core.int.neg }'
                                     + extra + '\nsig make() -> T { require T guarantees G }',
                                     function='make')

    def test_body_return_is_not_an_inference_source(self):
        self.assert_rejected('guarantee G {}\n'
                             'sig make() -> T { require T guarantees G }\n'
                             'func make() { return 1 }', function='make')


if __name__ == '__main__':
    unittest.main()
