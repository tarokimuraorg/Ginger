"""Name resolution contracts through the public source parser and runtime."""
import io
from contextlib import redirect_stdout
from itertools import permutations
import unittest
from unittest.mock import patch

import ginger.eval as evaluator
from ginger.ast import TypeRef
from ginger.core.failure_spec import FailureId
from ginger.diagnostics import Diagnostics
from ginger.errors import TypecheckError
from ginger.lower import lower_program
from ginger.parser import parse
from ginger.symbols_builder import build_symbols, normalize_types, instantiated_signature_failures
from ginger.typecheck import typecheck_program


IOERR = frozenset({FailureId.IOErr})


def checked(source):
    program = lower_program(parse(source))
    symbols = build_symbols(program)
    program = normalize_types(program, symbols.failuresets)
    diagnostics = Diagnostics()
    typecheck_program(program, diagnostics, syms=symbols)
    return program, symbols, diagnostics


def execute(program):
    stream = io.StringIO()
    with redirect_stdout(stream):
        result = evaluator.eval_program(program)
    return result, stream.getvalue()


class NameResolutionContractsTests(unittest.TestCase):
    def test_shared_declarations_all_valid_orders_and_forward_calls(self):
        declarations = (
            'guarantee Shared { witness(self: Self) -> Self }',
            'failureset Shared { IOErr }',
            'sig Shared() -> Unit { failure { Shared } }',
            'func Shared() {}',
        )
        for order in permutations(range(4)):
            if order.index(2) > order.index(3):
                continue
            for forward in (False, True):
                with self.subTest(order=order, forward=forward):
                    # Unrelated declarations must not affect collection either.
                    separator = '\ntypegroup Unrelated { Float }\n' if forward else '\n'
                    source = separator.join([declarations[order[0]],
                                             '\n'.join(declarations[i] for i in order[1:])])
                    source = 'Shared()\n' + source if forward else source + '\nShared()'
                    program, symbols, diagnostics = checked(source)
                    self.assertIn('Shared', symbols.guarantees)
                    self.assertIn('Shared', symbols.sigs)
                    self.assertIn('Shared', symbols.funcs)
                    self.assertEqual(symbols.failuresets['Shared'], IOERR)
                    self.assertEqual(symbols.sig_failures['Shared'], IOERR)
                    call = program.items[0 if forward else -1].expr
                    resolved = symbols.resolved_calls[id(call)]
                    self.assertIsNone(resolved.implementation)
                    self.assertIsNone(resolved.deferred_guarantee)
                    self.assertEqual(resolved.return_type, TypeRef('Unit'))
                    with patch('ginger.eval.eval_user_func', wraps=evaluator.eval_user_func) as user_call:
                        result, stdout = execute(program)
                    self.assertEqual(user_call.call_count, 1)
                    self.assertEqual(user_call.call_args.args[0], 'Shared')
                    frame, = [f for f in result.call_frames.values() if f.function_name == 'Shared']
                    self.assertEqual(frame.declared_failure_contract, IOERR)
                    self.assertEqual(frame.pending_event_ids, ())
                    self.assertFalse(result.failure_history)
                    self.assertIsNone(result.contract_violation)
                    self.assertEqual(stdout, '')
                    self.assertEqual([(d.code, d.message) for d in diagnostics],
                                     [('UNHANDLED_FAILURES', 'unhandled failures: IOErr')])

    def test_function_and_variable(self):
        program, _, _ = checked('sig Shared() -> Int {}\nfunc Shared() { return 9 }\n'
                                'var Shared: Int = 4\nprint(Shared())\nprint(Shared)')
        result, stdout = execute(program)
        self.assertEqual(stdout, '9\n4\n')
        self.assertEqual(result.environment['Shared'].value, 4)

    def test_function_and_parameter(self):
        program, _, _ = checked('sig Shared() -> Int {}\nfunc Shared() { return 9 }\n'
                                'sig relay(Int) -> Int {}\n'
                                'func relay(Shared: Int) { print(Shared())\nreturn Shared }\n'
                                'print(relay(4))')
        self.assertEqual(execute(program)[1], '9\n4\n')

    def test_bare_function_name_is_not_a_value(self):
        with self.assertRaisesRegex(TypecheckError, "unknown identifier 'Shared'"):
            checked('sig Shared() -> Int {}\nfunc Shared() { return 9 }\nprint(Shared)')

    def test_function_and_concrete_type_name(self):
        program, symbols, _ = checked('sig Int(Int) -> Int {}\n'
                                      'func Int(x: Int) { return x }\nvar x: Int = Int(9)\nprint(x)')
        self.assertEqual(symbols.sigs['Int'].ret, TypeRef('Int'))
        result, stdout = execute(program)
        self.assertEqual((result.environment['x'].value, stdout), (9, '9\n'))

    def test_typegroup_failureset_and_function_use_their_own_contexts(self):
        group = 'typegroup Shared { Int }\n'
        failureset = 'failureset Shared { IOErr }\n'
        functions = ('sig Shared(T) -> T { require T in Shared failure { Shared } }\n'
                     'func Shared(x: T) { return x }\n'
                     'sig conditional(T) -> T { failure { IOErr when T in Shared } }\n'
                     'func conditional(x: T) { return x }\n')
        calls = 'var a: Int = Shared(4)\nvar b: Int = conditional(9)\nvar c: Float = conditional(2.0)'
        for declarations in (group + failureset + functions, failureset + functions + group,
                             functions + group + failureset):
            with self.subTest(declarations=declarations):
                program, symbols, _ = checked(calls + '\n' + declarations)
                self.assertEqual(symbols.typegroups['Shared'], {'Int'})
                self.assertEqual(symbols.failuresets['Shared'], IOERR)
                self.assertNotIn('Shared', symbols.type_guarantees['Int'])
                self.assertEqual(symbols.sig_failures['Shared'], IOERR)
                for typ, expected in [('Int', IOERR), ('Float', frozenset())]:
                    self.assertEqual(instantiated_signature_failures(
                        symbols, 'conditional', {'T': TypeRef(typ)}), expected)
                result, _ = execute(program)
                self.assertEqual([result.environment[n].value for n in 'abc'], [4, 9, 2.0])
                frames = [f for f in result.call_frames.values() if f.function_name != '<program>']
                self.assertEqual([f.declared_failure_contract for f in frames],
                                 [IOERR, IOERR, frozenset()])
                self.assertFalse(result.failure_history)
                with self.assertRaisesRegex(TypecheckError, 'requirement not satisfied'):
                    checked(declarations + 'Shared(2.0)')

    def test_same_names_do_not_change_catalog_builtin_or_deferred_dispatch(self):
        body = ('sig relay(T,T) -> T { require T guarantees Addable '
                'failure { IntegerOverflow, FloatOverflow } }\n'
                'func relay(a: T,b: T) { return add(a,b) }\n'
                'var direct: Float = toFloat(4)\nvar concrete: Int = add(2,3)\n'
                'var deferred: Int = relay(6,7)')
        for extra in ('', 'guarantee add { witness(self: Self) -> Self }\n'
                      'failureset add { IOErr }\ntypegroup toFloat { Int }\n'
                      'failureset toFloat { IOErr }\n'):
            with self.subTest(extra=extra):
                program, symbols, _ = checked(extra + body)
                direct, concrete, _ = program.items[-3:]
                self.assertEqual(symbols.resolved_calls[id(direct.expr)].implementation, 'core.int.toFloat')
                self.assertEqual(symbols.resolved_calls[id(concrete.expr)].implementation, 'core.int.add')
                call = symbols.funcs['relay'].body.stmts[0].expr
                deferred = symbols.resolved_calls[id(call)]
                self.assertEqual((deferred.deferred_guarantee, deferred.deferred_method), ('Addable', 'add'))
                self.assertIsNone(deferred.implementation)
                with patch('ginger.eval.invoke_builtin', wraps=evaluator.invoke_builtin) as builtin:
                    result, _ = execute(program)
                self.assertEqual([c.args[0] for c in builtin.call_args_list],
                                 ['core.int.toFloat', 'core.int.add', 'core.int.add'])
                self.assertEqual([result.environment[n].value for n in ('direct', 'concrete', 'deferred')],
                                 [4.0, 5, 13])
                self.assertFalse(result.failure_history)

    def test_user_function_precedes_catalog_direct_builtin_and_guarantee_method(self):
        # Existing catalog sigs supply the required preceding declarations.
        for source, name, expected in [
            ('func toFloat(x: Int) { return 9.0 }\nvar x: Float = toFloat(4)', 'toFloat', 9.0),
            ('func add(a: T,b: T) { return a }\nvar x: Int = add(2,3)', 'add', 2),
        ]:
            with self.subTest(name=name):
                program, symbols, _ = checked(source)
                resolved = symbols.resolved_calls[id(program.items[-1].expr)]
                self.assertIsNone(resolved.implementation)
                self.assertIsNone(resolved.deferred_guarantee)
                with patch('ginger.eval.invoke_builtin', wraps=evaluator.invoke_builtin) as builtin:
                    result, _ = execute(program)
                builtin.assert_not_called()
                self.assertEqual(result.environment['x'].value, expected)
                self.assertIn(name, [f.function_name for f in result.call_frames.values()])

    def test_duplicate_declarations_and_sig_order(self):
        for source, message in [
            ('sig Shared() -> Unit {}\nsig Shared(Int) -> Int {}', "duplicate sig 'Shared'"),
            ('sig Shared() -> Unit {}\nfunc Shared() {}\nfunc Shared() {}', "duplicate func 'Shared'"),
            ('guarantee Shared { a(self: Self) -> Self }\n'
             'guarantee Shared { b(self: Self) -> Self }', "duplicate guarantee 'Shared'"),
            ('func Shared() {}\nsig Shared() -> Unit {}', "has no corresponding sig 'Shared'"),
            ('sig toFloat(Int) -> Float {}', "duplicate sig 'toFloat'"),
        ]:
            with self.subTest(source=source), self.assertRaisesRegex(TypecheckError, message):
                checked(source)


if __name__ == '__main__':
    unittest.main()
