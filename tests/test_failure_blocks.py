import io
import unittest
from contextlib import redirect_stderr, redirect_stdout

from ginger.ast import ConditionalFailureDecl
from ginger.errors import TypecheckError
from ginger.parser import parse
from ginger.pipeline import compile, run


class FailureBlockTests(unittest.TestCase):
    def signature(self, declarations):
        return 'sig f(T) -> T { ' + declarations + ' }'

    def test_forms_reuse_flat_ast(self):
        for contents, expected in [
            ('IntegerOverflow', ['IntegerOverflow']),
            ('IntegerOverflow, FloatOverflow', ['IntegerOverflow', 'FloatOverflow']),
            ('\nIntegerOverflow,\nFloatOverflow\n', ['IntegerOverflow', 'FloatOverflow']),
            ('IntegerOverflow,', ['IntegerOverflow']),
            ('\nIntegerOverflow,\nFloatOverflow,\n', ['IntegerOverflow', 'FloatOverflow']),
            ('Never', ['Never']),
            ('Never,', ['Never']),
            ('IOErr, FloatOverflow when T guarantees FiniteArithmetic,',
             ['IOErr', ConditionalFailureDecl('FloatOverflow', 'T', 'FiniteArithmetic')]),
        ]:
            with self.subTest(contents=contents):
                source = self.signature('failure {' + contents + '}')
                self.assertEqual(parse(source).items[0].failures, expected)
                compile(source)
        source = self.signature('failure { IOErr }\nfailure { FloatOverflow }')
        self.assertEqual(parse(source).items[0].failures, ['IOErr', 'FloatOverflow'])
        compile(source)
        self.assertEqual(parse(self.signature('')).items[0].failures, [])

    def test_invalid_syntax_and_removed_legacy_forms(self):
        for declaration in [
            'failure {}', 'failure {\n}',
            'failure IntegerOverflow',
            'failure IntegerOverflow when T guarantees BoundedArithmetic',
            'failure { IntegerOverflow FloatOverflow }',
            'failure { IntegerOverflow\nFloatOverflow }',
            'failure { ,IntegerOverflow }',
            'failure { IntegerOverflow,, FloatOverflow }',
            'failure { IntegerOverflow } when T guarantees BoundedArithmetic',
            'failure { IntegerOverflow',
            'failure { Never when T guarantees BoundedArithmetic }',
            'failure { IOErr[Int] }', 'failure { IntegerOverflow<Never> }',
        ]:
            with self.subTest(declaration=declaration), self.assertRaises(SyntaxError):
                parse(self.signature(declaration))

    def test_conflicts_within_and_across_blocks(self):
        conditional = 'FloatOverflow when T guarantees FiniteArithmetic'
        for entries, message in [
            (['Never', 'IntegerOverflow'], "cannot combine 'Never'"),
            (['FloatOverflow', 'Never'], "cannot combine 'Never'"),
            (['Never', conditional], "cannot combine 'Never'"),
            (['Never', 'Never'], 'duplicate failure'),
            (['IntegerOverflow', 'IntegerOverflow'], 'duplicate failure'),
            ([conditional, conditional], 'duplicate failure'),
            (['FloatOverflow', conditional], 'redundant conditional failure'),
            ([conditional, 'FloatOverflow'], 'redundant conditional failure'),
        ]:
            for declaration in ['failure { ' + ', '.join(entries) + ' }',
                                '\n'.join('failure { ' + e + ' }' for e in entries)]:
                with self.subTest(declaration=declaration), self.assertRaisesRegex(SyntaxError, message):
                    parse(self.signature(declaration))

    def test_semantic_validation(self):
        for entry, message in [
            ('MissingFailure', 'unknown failure'),
            ('MissingFailure when T guarantees FiniteArithmetic', 'unknown failure'),
            ('IOErr when T guarantees MissingGuarantee', 'unknown guarantee'),
            ('IOErr when U guarantees FiniteArithmetic', 'unknown type variable'),
        ]:
            with self.subTest(entry=entry), self.assertRaisesRegex(TypecheckError, message):
                compile(self.signature('failure { ' + entry + ' }'))
        with self.assertRaisesRegex(TypecheckError, 'not inferable'):
            compile('sig f() -> T { require T guarantees Addable '
                    'failure { IOErr when T guarantees BoundedArithmetic } }')

    def test_representative_addition_value_and_specialized_warnings(self):
        source = '''sig addBoth(T, T) -> T {
    require T guarantees Addable
    failure {
        IntegerOverflow when T guarantees BoundedArithmetic,
        FloatOverflow when T guarantees FiniteArithmetic,
        FloatUnderflow when T guarantees FiniteArithmetic
    }
}
func addBoth(a: T, b: T) { return a + b }
'''
        for args, value, warning in [
            ('1, 2', '3\n', 'IntegerOverflow'),
            ('1.0, 2.0', '3.0\n', 'FloatOverflow, FloatUnderflow'),
        ]:
            stdout, stderr = io.StringIO(), io.StringIO()
            with self.subTest(args=args), redirect_stdout(stdout), redirect_stderr(stderr):
                result = run(source + 'print(addBoth(' + args + '))')
            self.assertEqual(stdout.getvalue(), value)
            self.assertEqual(stderr.getvalue(),
                             'warning[UNHANDLED_FAILURES]: unhandled failures: ' + warning + '\n')
            self.assertIsNone(result.contract_violation)
            self.assertEqual(len(result.failure_history), 0)


if __name__ == '__main__':
    unittest.main()
