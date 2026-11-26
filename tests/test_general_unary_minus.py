import math
import unittest
from unittest.mock import Mock, patch

from test_failure_contract import checked, output, recorded
from ginger.builtin import BUILTINS
from ginger.errors import TypecheckError
from ginger.lower import lower_program
from internal_declarations import parse
from ginger.tokenizer import tokenize
from ginger.eval import eval_program


class GeneralUnaryMinusTests(unittest.TestCase):
    def test_literals_boundaries_and_motivation(self):
        for expr, expected in [('-1', '-1'), ('-9007199254740991', '-9007199254740991'),
                               ('-1i64', '-1'), ('-9223372036854775807i64', '-9223372036854775807'),
                               ('-1.0', '-1.0'), ('-1e3', '-1000.0'), ('-1e+3', '-1000.0'),
                               ('-1e-3', '-0.001'), ('-0.0', '-0.0'),
                               ('toFloat(-9007199254740991)', '-9007199254740991.0')]:
            with self.subTest(expr=expr):
                typ = 'Float' if '.' in expected else 'Int64' if 'i64' in expr else 'Int'
                program, _ = checked(f'print[{typ}]({expr})')
                self.assertEqual(output(program), expected + '\n')

    def test_expression_contexts_and_precedence(self):
        prefix = ('var x: Int = 2\nsig f(Int) -> Int {}\nfunc f(x: Int) { return -x }\n')
        for expr, expected in [('-x', '-2'), ('-f(x)', '2'), ('-(x + 3)', '-5'),
                               ('-(x * 3)', '-6'), ('-toFloat(x)', '-2.0'), ('f(-x)', '2'),
                               ('-2 * 3', '-6'), ('-x + 3', '1'), ('-(-x)', '2'),
                               ('(-((-x)))', '2'), ('8.0 / (-2.0)', '-4.0')]:
            with self.subTest(expr=expr):
                typ = 'Float' if '.' in expected else 'Int'
                program, _ = checked(prefix + f'print[{typ}]({expr})')
                self.assertEqual(output(program), expected + '\n')
        program, _ = checked('sig f() -> Int {}\nfunc f() { -1\nreturn -2 }\nprint[Int](f())')
        self.assertEqual(output(program), '-2\n')
        # Syntax accepts top-level unary statements; existing Unit-only typing remains.
        self.assertEqual(lower_program(parse('-1')).items[0].expr.args, lower_program(parse('neg[Int](1)')).items[0].expr.args)
        program, _ = checked('var x: Int = -1\nx = -x\nprint[Int](x)')
        self.assertEqual(output(program), '1\n')

    def test_binary_rhs_common_rule_and_spacing(self):
        for op in '+-*/':
            for left, right in [('1', '1'), ('a', 'b')]:
                for space in ['', ' ', '\t']:
                    expr = f'{left}{space}{op}{space}-{space}{right}'
                    with self.subTest(expr=expr), self.assertRaisesRegex(SyntaxError, 'after binary operator'):
                        parse(f'print[Int]({expr})')
            for operand in ['1', 'b']:
                program, _ = checked(f'var b: Int = 1\nprint[{"Float" if op == "/" else "Int"}](1 {op} (-{operand}))')
                self.assertEqual(output(program), {'+': '0\n', '-': '2\n', '*': '-1\n', '/': '-1.0\n'}[op])
        with self.assertRaises(SyntaxError):
            parse('print[Float](8.0 / -2.0)')

    def test_repeated_minus_token_boundary(self):
        for expr in ['--x', '- -x', '-\t-x']:
            with self.subTest(expr=expr), self.assertRaisesRegex(SyntaxError, 'repeated bare'):
                parse(f'print[Int]({expr})')

    def test_exponent_tokens_and_incomplete_boundary(self):
        for expr in ['1e3', '1e+3', '1e-3']:
            self.assertEqual([(t.kind, t.text) for t in tokenize(expr)[:-1]], [('FLOAT', expr)])
            self.assertEqual([(t.kind, t.text) for t in tokenize('-' + expr)[:-1]],
                             [('SYM', '-'), ('FLOAT', expr)])
        for expr in ['1e', '1E', '1e+', '1e-', '1E+', '1E-',
                     '1e + (-3)', '1e - 3', '1e -3', '1e + 3']:
            with self.subTest(expr=expr), self.assertRaisesRegex(SyntaxError, '^Float exponent requires digits at 0$'):
                tokenize(expr)

    def test_one_e_alone_is_never_an_identifier(self):
        with self.assertRaisesRegex(SyntaxError, '^Float exponent requires digits at 0$'):
            parse('1e')

    def test_lowering_equivalence(self):
        for expr in ['1', '1i64', '1.0', '1e-3', 'x', 'f(x)', '(x + y)', 'toFloat(x)']:
            self.assertEqual(lower_program(parse(f'print[Int](-{expr})')).items[0].expr.args[0].expr.args, lower_program(parse(f'print[Int](neg[Bool]({expr}))')).items[0].expr.args[0].expr.args)

    def test_invalid_values_match_neg(self):
        for expr in ['9007199254740992', '9223372036854775808i64', '1e400', 'eq[Int](1,1)', 'true']:
            messages = []
            typ = 'Bool' if expr.startswith('eq') else 'Float' if 'e' in expr else 'Int64' if 'i64' in expr else 'Int'
            for form in [f'-{expr}', f'neg[{typ}]({expr})']:
                with self.assertRaises(TypecheckError) as caught:
                    checked(f'print[Int]({form})')
                messages.append(str(caught.exception))
            self.assertEqual(*messages)

    def test_operand_failure_skips_neg_and_preserves_cause(self):
        histories = []
        for expr in ['-div(1.0,0.0)', 'neg[Float](div(1.0,0.0))']:
            program, _ = checked(f'var x: Float = {expr}')
            neg = Mock(side_effect=AssertionError('neg must not execute'))
            with patch.dict(BUILTINS, {'core.float.neg': neg}):
                _, context, stdout = recorded(program)
            neg.assert_not_called()
            self.assertEqual(stdout, '')
            self.assertEqual(len(context.failure_history), 1)
            histories.append(context.failure_history)
        self.assertEqual(*histories)

    def test_dispatch_return_validation_and_signed_zero(self):
        for expr in ['-1', 'neg[Int](1)']:
            program, _ = checked(f'print[Int]({expr})')
            for invalid in [True, 9007199254740992]:
                with patch.dict(BUILTINS, {'core.int.neg': lambda x: invalid}):
                    result = eval_program(program)
                    self.assertIsNotNone(result.value_contract_violation)
            custom = Mock(return_value=17)
            with patch.dict(BUILTINS, {'core.int.neg': custom}):
                self.assertEqual(output(program), '17\n')
            custom.assert_called_once_with(1)
        program, _ = checked('print[Float](-0.0)')
        self.assertEqual(math.copysign(1, float(output(program))), -1)
