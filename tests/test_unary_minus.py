import unittest
from pathlib import Path

from test_failure_contract import checked, output
from ginger.ast import UnaryMinusExpr, TypeRef
from ginger.core.failure_spec import EMPTY_FAILURES, FailureId
from ginger.diagnostics import Diagnostics
from ginger.errors import TypecheckError
from ginger.lower import lower_program
from ginger.parser import parse
from ginger.runtime.failures import RaisedFailure
from ginger.symbols_builder import build_symbols
from ginger.tokenizer import tokenize
from ginger.typecheck import effect_expr, typecheck_program


FLOAT_IDENTITY = 'sig identity(Float) -> Float {}\nfunc identity(x: Float) { return x }\n'


class UnaryMinusTests(unittest.TestCase):
    def test_valid_expressions_and_values(self):
        prefix = ('var x: Int = 3\nvar a: Int = 2\nvar b: Int = 4\n'
                  'sig f(Int) -> Int {}\nfunc f(n: Int) { return n }\n')
        cases = {
            '(-1)': '-1', '(-2.5)': '-2.5', '(-x)': '-3',
            '(-(a + b))': '-6', '(neg(x))': '-3',
            '1 - (-2)': '3', '1 + (-2)': '-1', 'x * (-2)': '-6',
            'f((-2))': '-2', 'neg(x)': '-3', 'neg(neg(x))': '3',
            '(x)': '3', '(-x) + 1': '-2', '(-2) * 3': '-6',
            '( - x )': '-3', '1-(-2)': '3', 'f(( - 2 ))': '-2',
            '(((-x)))': '-3', '(-neg(x))': '3',
        }
        for expr, expected in cases.items():
            with self.subTest(expr=expr):
                typ = 'Float' if '.' in expected else 'Int'
                program, diags = checked(prefix + f'var result: {typ} = {expr}\nprint(result)')
                arithmetic = {'(-(a + b))', '1 - (-2)', '1 + (-2)', 'x * (-2)',
                              '(-x) + 1', '(-2) * 3', '1-(-2)'}
                self.assertEqual([d.message for d in diags],
                                 ['unhandled failures: IntegerOverflow'] if expr in arithmetic else [])
                self.assertEqual(output(program), expected + '\n')

    def test_invalid_syntax_in_expression_contexts(self):
        expressions = ['-1', '-x', '-a + b', '1--2', '1 - -2',
                       'x * -2', 'f(-2)', '-(x)', '-2 * 3',
                       '(-(-x))', '(-((-x)))', '(-((((-x)))))',
                       '( - ( - x ) )', '(1--2)', '(1 - -2)',
                       '(- -x)', '(-)', '+x', '++x']
        for expr in expressions:
            for template in ['var result: Int = {}', 'print({})',
                             'sig f() -> Int {{}}\nfunc f() {{ return {} }}']:
                with self.subTest(expr=expr, template=template):
                    with self.assertRaises(SyntaxError):
                        parse(template.format(expr))

    def test_bare_minus_diagnostic_position_and_tokenization(self):
        for expr in ['1--2', '1 - -2', 'x * -2', 'f(-2)', '-(x)']:
            source = 'var result: Int = ' + expr
            with self.subTest(expr=expr), self.assertRaisesRegex(
                    SyntaxError, rf"bare unary '-'.* at {source.rindex('-')}$"):
                parse(source)
        self.assertEqual([(t.kind, t.text) for t in tokenize('1--2')],
                         [(t.kind, t.text) for t in tokenize('1 - -2')])

    def test_ast_and_lowering_equivalence(self):
        source = 'var x: Int = (-(1 + 2))'
        self.assertIsInstance(parse(source).items[0].expr, UnaryMinusExpr)
        self.assertEqual(lower_program(parse(source)),
                         lower_program(parse('var x: Int = neg(1 + 2)')))
        self.assertEqual(lower_program(parse('print((neg(1)))')),
                         lower_program(parse('print(neg(1))')))

    def test_types_and_neg_results_match(self):
        for typ, literal in [('Int', '2'), ('Float', '2.5')]:
            with self.subTest(typ=typ):
                program, diags = checked(
                    f'var x: {typ} = {literal}\nvar a: {typ} = (-x)\n'
                    f'var b: {typ} = neg(x)\nprint(a)\nprint(b)')
                env = typecheck_program(program, Diagnostics())
                self.assertEqual(env['a'].ty, TypeRef(typ))
                self.assertEqual(env['a'].ty, env['b'].ty)
                lines = output(program).splitlines()
                self.assertEqual(lines[0], lines[1])
                self.assertEqual(diags.items, [])

    def test_invalid_types_match_neg_diagnostics(self):
        # Bool literals do not exist yet; eq supplies a real Bool value.
        for typ, expr in [('Bool', 'eq(1, 1)'), ('Ordering', 'cmp(1, 2)'),
                          ('Unit', 'print(1)'), ('Thunk[Int, Never]', 'thunk(1)'),
                          ('Int', 'true')]:
            messages = []
            for form in [f'(-{expr})', f'neg({expr})']:
                with self.subTest(form=form):
                    with self.assertRaises(TypecheckError) as raised:
                        checked(f'var result: {typ} = {form}')
                    messages.append(str(raised.exception))
            self.assertEqual(*messages)

    def test_argument_inference_without_expected_type(self):
        for form in ['(-1)', 'neg(1)']:
            for source in [f'print({form})',
                           f'sig f() -> Int {{}}\nfunc f() {{ return {form} }}\nprint(f())']:
                with self.subTest(source=source):
                    program, diags = checked(source)
                    self.assertEqual(diags.items, [])
                    self.assertEqual(output(program), '-1\n')

    def test_standard_neg_has_no_failure(self):
        syms = build_symbols(parse(''))
        self.assertEqual(syms.sig_failures['neg'], EMPTY_FAILURES)
        for expr in ['(-1)', '(-2.5)']:
            typ = 'Float' if '.' in expr else 'Int'
            program, _ = checked(f'var result: {typ} = {expr}')
            self.assertEqual(effect_expr(program.items[0].expr, {}, syms),
                             EMPTY_FAILURES)

    def test_inner_failure_warning_catch_and_runtime(self):
        for expr in ['(-(div(1.0, 0.0)))', 'neg(div(1.0, 0.0))',
                     '(-(1.0 / 0.0))']:
            with self.subTest(expr=expr):
                program, diags = checked(FLOAT_IDENTITY + f'print(identity({expr}))')
                self.assertEqual([d.message for d in diags],
                                 ['unhandled failures: DivideByZero'])
                with self.assertRaises(RaisedFailure) as raised:
                    output(program)
                self.assertEqual(raised.exception.fid, FailureId.DivideByZero)
                program, diags = checked(FLOAT_IDENTITY + f'try print(identity({expr}))\ncatch DivideByZero print(7)')
                self.assertEqual(diags.items, [])
                self.assertEqual(output(program), '7\n')

    def test_function_failure_contract(self):
        source = FLOAT_IDENTITY + ('sig risky() -> Float { failure DivideByZero }\n'
                  'func risky() { return div(1.0, 0.0) }\n'
                  'sig f() -> Float { %s }\nfunc f() { return identity((-(risky()))) }\n')
        with self.assertRaisesRegex(TypecheckError, 'undeclared failures: DivideByZero'):
            checked(source % '')
        program, diags = checked((source % 'failure DivideByZero') +
                                 'try print(f())\ncatch DivideByZero print(8)')
        self.assertEqual(diags.items, [])
        self.assertEqual(output(program), '8\n')

    def test_thunk_preserves_latent_and_force_failures(self):
        prefix = FLOAT_IDENTITY + 'var t: Thunk[Float, DivideByZero] = thunk((-(div(1.0, 0.0))))\n'
        program, diags = checked(prefix)
        self.assertEqual(diags.items, [])
        self.assertEqual(output(program), '')
        program, diags = checked(prefix +
                                 'try print(identity((-force(t))))\ncatch DivideByZero print(9)')
        self.assertEqual(diags.items, [])
        self.assertEqual(output(program), '9\n')
        with self.assertRaisesRegex(TypecheckError, 'type mismatch'):
            checked(prefix.replace('Float, DivideByZero', 'Float, Never'))

    def test_arithmetic_precedence_and_associativity(self):
        for expr, expected in [('(1 + 2 * 3)', '7'), ('((1 + 2) * 3)', '9'),
                               ('(8 - 3 - 2)', '3'), ('(8.0 / 2.0 / 2.0)', '2.0'),
                               ('(-2) * 3 + 10', '4'), ('(-(2 + 3 * 4))', '-14'),
                               ('(1 + (-2) * 3)', '-5')]:
            with self.subTest(expr=expr):
                typ = 'Float' if '.' in expected else 'Int'
                program, _ = checked(f'var result: {typ} = {expr}\nprint(result)')
                self.assertEqual(output(program), expected + '\n')
        program, _ = checked('print((1 / 2))')
        self.assertEqual(output(program), '0.5\n')

    def test_all_sample_baselines(self):
        expected = {
            'Scene_1': ('9\n4.5\n-9\n', ['unhandled failures: IntegerOverflow'] * 2
                         + ['unhandled failures: DivideByZero']),
            'Scene_2': ('2.0\n', []), 'Scene_3': ('0\n', []),
            'Scene_4': ('1\n2\n', []),
            'Scene_5': ('0\n', []),
            'Scene_6': ('0\n5.0\n', []),
            'Scene_7': ('-2\n-2\n3\n', ['unhandled failures: IntegerOverflow']),
        }
        root = Path(__file__).resolve().parents[1] / 'ginger' / 'scripts'
        for name, (stdout, warnings) in expected.items():
            with self.subTest(name=name):
                program, diags = checked((root / f'{name}.ginger').read_text())
                self.assertEqual(output(program), stdout)
                self.assertEqual([d.message for d in diags], warnings)


if __name__ == '__main__':
    unittest.main()
