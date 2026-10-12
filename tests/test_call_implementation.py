import unittest
from unittest.mock import patch

from ginger.diagnostics import Diagnostics
from ginger.errors import TypecheckError
from ginger.lower import lower_program
from ginger.parser import parse
from ginger.pipeline import compile, run
from ginger.symbols_builder import build_symbols
from ginger.typecheck import typecheck_program


class CallImplementationTests(unittest.TestCase):
    def test_declaration_only_call_rejected_before_execution(self):
        for declaration, call in [
            ('sig missing(Int) -> Int {}', 'var x: Int = missing(1)'),
            ('sig missing(T) -> T {}', 'var x: Int = missing(1)'),
            ('sig missing(T) -> T {}',
             'sig relay(U) -> U {}\nfunc relay(x: U) { return missing(x) }'),
        ]:
            source = declaration + '\n' + call
            with self.subTest(source=source):
                program = lower_program(parse(source))
                syms = build_symbols(program)
                message = "^no implementation for function 'missing'$"
                with self.assertRaisesRegex(TypecheckError, message):
                    typecheck_program(program, Diagnostics(), syms=syms)
                with self.assertRaisesRegex(TypecheckError, message):
                    compile(source)
                with patch('ginger.pipeline.execute') as execute:
                    with self.assertRaisesRegex(TypecheckError, message):
                        run(source)
                    execute.assert_not_called()

    def test_unused_declaration_only_sig_is_accepted(self):
        for source in ['sig missing(Int) -> Int {}', 'sig missing(T) -> T {}']:
            with self.subTest(source=source):
                program = compile(source)
                syms = build_symbols(program)
                self.assertIn('missing', syms.sigs)
                self.assertNotIn('missing', syms.funcs)
                self.assertIsNone(syms.sigs['missing'].builtin)
                self.assertEqual(typecheck_program(program, Diagnostics(), syms=syms), {})
                self.assertEqual(syms.resolved_calls, {})


if __name__ == '__main__':
    unittest.main()
