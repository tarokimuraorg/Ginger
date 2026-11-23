import unittest

from ginger.parser import parse
from ginger.pipeline import compile
from ginger.errors import TypecheckError


class EnumerationBlockTests(unittest.TestCase):
    def source(self, kind, contents):
        if kind == 'failure':
            return 'sig f(T) -> T { failure {' + contents + '} }'
        prefix = 'typegroup Mark {}\n' if kind == 'register' else ''
        return prefix + kind + ' Mark {' + contents + '}'

    def test_comma_forms(self):
        for kind in ['failureset', 'failure', 'typegroup', 'register']:
            a, b = ('IOErr', 'DivideByZero') if kind in ['failureset', 'failure'] else ('Int', 'Float')
            for contents in [a, a + ',', a + ', ' + b, a + ', ' + b + ',',
                             '\n' + a + ',\n' + b + '\n',
                             '\n' + a + ',\n' + b + ',\n']:
                with self.subTest(kind=kind, contents=contents):
                    compile(self.source(kind, contents))

    def test_invalid_separators_and_names(self):
        for kind in ['failureset', 'failure', 'typegroup', 'register']:
            a, b = ('IOErr', 'DivideByZero') if kind in ['failureset', 'failure'] else ('Int', 'Float')
            for contents in [a + ' ' + b, a + '\n' + b, a + ',,' + b,
                             ',' + a, a + ',,', '123', a + ', 123']:
                with self.subTest(kind=kind, contents=contents), self.assertRaises(SyntaxError):
                    parse(self.source(kind, contents))

    def test_legacy_typegroup_rejected(self):
        for source in ['typegroup Number = Int | Float', 'typegroup Number = Int']:
            for action in [parse, compile]:
                with self.subTest(source=source, action=action), self.assertRaises(SyntaxError):
                    action(source)

    def test_empty_blocks_keep_existing_rules(self):
        compile('typegroup Empty {}')
        with self.assertRaisesRegex(TypecheckError, 'empty failureset'):
            compile('failureset Empty {}')
        for kind in ['failure', 'register']:
            with self.subTest(kind=kind), self.assertRaises(SyntaxError):
                parse(self.source(kind, ''))

    def test_never_exception_is_local_to_failure(self):
        compile(self.source('failure', 'Never'))
        for contents in ['Never,', '\nNever,\n', 'Never, IOErr', 'IOErr, Never',
                         'Never when T in BoundedArithmetic']:
            with self.subTest(contents=contents), self.assertRaises(SyntaxError):
                parse(self.source('failure', contents))
        for kind in ['typegroup', 'register']:
            compile(self.source(kind, 'Never,'))
        with self.assertRaisesRegex(TypecheckError, 'Never is not allowed'):
            compile(self.source('failureset', 'Never,'))

    def test_conditional_items(self):
        program = parse(self.source('failure', '''
            IntegerOverflow when T in BoundedArithmetic,
            FloatOverflow when T in FiniteArithmetic,
            FloatUnderflow when T in FiniteArithmetic,
        '''))
        self.assertEqual([f.group_name for f in program.items[0].failures],
                         ['BoundedArithmetic', 'FiniteArithmetic', 'FiniteArithmetic'])
        compile(self.source('failure', 'IOErr when T in FiniteArithmetic,'))
        with self.assertRaises(SyntaxError):
            parse(self.source('failure', 'IOErr when T guarantees FiniteArithmetic'))

    def test_non_enumeration_blocks_need_no_commas(self):
        compile('''
            guarantee Operations {
                first(self: Self) -> Self
                second(self: Self) -> Self
            }
            sig example() -> Unit { failure { DivideByZero, FloatOverflow, FloatUnderflow, IOErr } }
            func example() {
                var x: Float = div(1.0, 0.0)
                resolve x {
                    DivideByZero { x = 0.0 }
                    FloatOverflow { x = 99.0 }
                }
                var y: Float = resolve div(1.0, 0.0) {
                    DivideByZero { return 0.0 }
                    FloatOverflow { return 99.0 }
                }
                x = 2.0
                print(x)
            }
        ''')
