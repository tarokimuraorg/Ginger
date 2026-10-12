import unittest

from ginger.diagnostics import Diagnostics
from ginger.errors import TypecheckError
from ginger.lower import lower_program
from ginger.parser import parse
from ginger.pipeline import compile, run
from ginger.symbols_builder import build_symbols
from ginger.typecheck import typecheck_program


class RequirementReferenceTests(unittest.TestCase):
    def assert_rejected(self, source, diagnostic):
        program = lower_program(parse(source))
        for stage, check in [
            ('build_symbols', lambda: build_symbols(program)),
            ('typecheck', lambda: typecheck_program(program, Diagnostics())),
            ('compile', lambda: compile(source)),
        ]:
            with self.subTest(stage=stage), self.assertRaisesRegex(TypecheckError, diagnostic):
                check()

    def test_unused_unknown_guarantee(self):
        self.assert_rejected(
            'sig op(T) -> T { require T guarantees MissingGuarantee }',
            "unknown guarantee 'MissingGuarantee'.*'op'")

    def test_used_unknown_guarantee(self):
        self.assert_rejected(
            'sig op(T) -> T { require T guarantees MissingGuarantee }\n'
            'var x: Int = op(1)', 'unknown guarantee')

    def test_unused_valid_guarantee_without_implementation(self):
        compile('guarantee G { op(self: Self) -> Self }\n'
                'sig unused(T) -> T { require T guarantees G }')

    def test_forward_guarantee(self):
        compile('sig op(T) -> T { require T guarantees G }\n'
                'guarantee G { op(self: Self) -> Self }')

    def test_unknown_typegroup_used_and_unused(self):
        for suffix in ['', '\nvar x: Int = op(1)']:
            with self.subTest(suffix=suffix):
                self.assert_rejected(
                    'sig op(T) -> T { require T in MissingGroup }' + suffix,
                    "unknown typegroup 'MissingGroup'.*'op'")

    def test_valid_typegroup_in_either_source_order(self):
        group = 'typegroup ExistingGroup = Float\n'
        sig = 'sig unused(T) -> T { require T in ExistingGroup }\n'
        for source in [group + sig, sig + group]:
            with self.subTest(source=source):
                compile(source)

    def test_namespaces_are_distinct(self):
        self.assert_rejected(
            'typegroup G = Int\nsig op(T) -> T { require T guarantees G }',
            "unknown guarantee 'G'")
        self.assert_rejected(
            'guarantee G {}\nsig op(T) -> T { require T in G }',
            "unknown typegroup 'G'")

    def test_all_requirements_are_checked(self):
        for requirement, diagnostic in [
            ('require T guarantees Missing', 'unknown guarantee'),
            ('require T in Missing', 'unknown typegroup'),
        ]:
            with self.subTest(requirement=requirement):
                self.assert_rejected(
                    'guarantee A {}\ntypegroup Existing = Int\n'
                    'sig op(T) -> T { require T guarantees A '
                    'require T in Existing ' + requirement + ' }', diagnostic)

    def test_unknown_requirement_precedes_signature_validation(self):
        self.assert_rejected(
            'guarantee G { bad(self: Self) -> Float }\n'
            'sig bad(T) -> T { require T guarantees G }\n'
            'sig op(T) -> T { require T guarantees Missing }', 'unknown guarantee')

    def test_user_function_does_not_hide_unknown_requirement(self):
        for requirement, diagnostic in [
            ('guarantees Missing', 'unknown guarantee'), ('in Missing', 'unknown typegroup'),
        ]:
            with self.subTest(requirement=requirement):
                self.assert_rejected(
                    f'sig op(T) -> T {{ require T {requirement} }}\n'
                    'func op(x: T) { return x }', diagnostic)

    def test_user_function_with_valid_requirement(self):
        source = ('guarantee G {}\nregister Int guarantees G\n'
                  'sig op(T) -> T { require T guarantees G }\n'
                  'func op(x: T) { return x }\n')
        compile(source)
        self.assertEqual(run(source + 'var x: Int = op(1)').environment['x'].value, 1)

    def test_satisfaction_remains_a_call_site_check(self):
        source = ('guarantee G {}\n'
                  'sig op(T) -> T { require T guarantees G }\n'
                  'func op(x: T) { return x }\nvar x: Int = op(1)')
        build_symbols(lower_program(parse(source)))
        with self.assertRaisesRegex(TypecheckError, 'does not guarantee G'):
            compile(source)

    def test_preloaded_guarantee(self):
        compile('sig unused(T) -> T { require T guarantees Addable }')


if __name__ == '__main__':
    unittest.main()
