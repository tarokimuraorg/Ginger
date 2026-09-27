import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import Mock, patch

import ginger.eval as evaluator
from ginger.builtin import BUILTINS, builtin_failure_contract
from ginger.core.failure_spec import EMPTY_FAILURES, FailureId, failures
from ginger.lower import lower_program
from ginger.numeric import INT_MAX
from ginger.parser import parse
from ginger.runtime.builtin_bridge import invoke_builtin
from ginger.runtime.context import RuntimeContext
from ginger.runtime.failures import FailureStatus, RaisedFailure
from ginger.runtime.results import NoValue, Value


def program(source):
    return lower_program(parse(source))


class BuiltinBridgeTests(unittest.TestCase):
    def setUp(self):
        self.context = RuntimeContext()

    def run_source(self, source):
        return evaluator._eval_program_with_context(program(source), self.context)

    def test_success_goes_through_value_result(self):
        results = []
        def observe(*args):
            result = invoke_builtin(*args)
            results.append(result)
            return result
        stream = io.StringIO()
        with patch('ginger.eval.invoke_builtin', side_effect=observe), redirect_stdout(stream):
            env = self.run_source('var x: Int = add(1,2)\nprint(x)')
        self.assertEqual(env['x'].value, 3)
        self.assertEqual(stream.getvalue(), '3\n')
        self.assertEqual([r.value_result for r in results], [Value(3), Value(None)])
        self.assertTrue(all(r.related_event_ids == () for r in results))
        self.assertEqual(self.context.failure_history, ())

    def test_declared_failure_records_once_and_continues(self):
        results = []
        def observe(*args):
            result = invoke_builtin(*args)
            results.append(result)
            return result
        stream = io.StringIO()
        with patch('ginger.eval.invoke_builtin', side_effect=observe), redirect_stdout(stream):
            self.run_source('print(div(1.0,0.0))\nprint(3)')
        self.assertEqual(stream.getvalue(), '3\n')
        self.assertEqual(len(self.context.failure_history), 1)
        event = self.context.failure_history[0]
        self.assertEqual((event.failure_id, event.status, event.origin),
                         (FailureId.DivideByZero, FailureStatus.UNRESOLVED, 'core.float.div'))
        self.assertEqual(self.context.get_call(event.call_id).function_name, '<program>')
        self.assertEqual(results[0].value_result, NoValue())
        self.assertEqual(results[0].related_event_ids, (event.event_id,))
        with self.assertRaises(LookupError):
            evaluator._active_runtime.get()

    def test_two_independent_invocations_keep_two_events(self):
        frame = self.context.create_call('<program>')
        results = [invoke_builtin('core.float.div', [1.0, 0.0], failures(FailureId.DivideByZero),
                                  self.context, frame.call_id) for _ in range(2)]
        self.assertEqual([r.related_event_ids for r in results], [(1,), (2,)])
        for result in results:
            self.assertIsInstance(result.value_result, NoValue)
        self.assertEqual(len(self.context.failure_history), 2)

    def test_catch_resolves_events_without_changing_output(self):
        stream = io.StringIO()
        with redirect_stdout(stream):
            self.run_source('try print(div(1.0,0.0))\ncatch DivideByZero print(2)\n'
                            'try print(div(1.0,0.0))\ncatch DivideByZero print(4)\nprint(3)')
        self.assertEqual(stream.getvalue(), '2\n4\n3\n')
        self.assertEqual([e.event_id for e in self.context.failure_history], [1, 2])
        # Catch resolves events without deleting bridge history.
        self.assertTrue(all(e.status is FailureStatus.RESOLVED
                            for e in self.context.failure_history))

    def test_no_value_not_published_or_passed_to_parent(self):
        for statement in [f'var x: Int = add({INT_MAX},1)',
                          f'var x: Int = 7\nx = add({INT_MAX},1)']:
            with self.subTest(statement=statement):
                envs = []
                original = evaluator.eval_expr
                def observe(expr, env, syms, outer=None):
                    envs.append(env)
                    return original(expr, env, syms, outer)
                with patch('ginger.eval.eval_expr', side_effect=observe):
                    self.run_source(statement)
                if statement.startswith('var x: Int = 7'):
                    self.assertEqual(envs[0]['x'].value, 7)
                else:
                    self.assertIsInstance(envs[0]['x'], evaluator.UninitializedBinding)
        outer = Mock(return_value=0)
        with patch.dict(BUILTINS, {'core.int.mul': outer}):
            self.run_source(f'var y: Int = mul(add({INT_MAX},1),2)')
        outer.assert_not_called()

    def test_argument_failure_has_inner_origin_only(self):
        outer = Mock(return_value=0.0)
        with patch.dict(BUILTINS, {'core.float.add': outer}):
            self.run_source('var x: Float = add(div(1.0,0.0),1.0)')
        outer.assert_not_called()
        self.assertEqual([e.origin for e in self.context.failure_history], ['core.float.div'])

    def test_undeclared_failure_not_legitimized_by_caller(self):
        source = (f'sig alias(Int,Int) -> Int {{ builtin core.int.add }}\n'
                  'sig f() -> Int { failure IntegerOverflow }\n'
                  f'func f() {{ return alias({INT_MAX},1) }}\nvar x: Int = f()')
        with self.assertRaises(RaisedFailure) as raised:
            self.run_source(source)
        self.assertEqual(raised.exception.fid, FailureId.IntegerOverflow)
        self.assertEqual(self.context.failure_history, ())

    def test_declared_custom_alias_records_failure(self):
        source = ('sig alias(Int,Int) -> Int { failure IntegerOverflow builtin core.int.add }\n'
                  f'var x: Int = alias({INT_MAX},1)')
        self.run_source(source)
        self.assertEqual([e.origin for e in self.context.failure_history], ['core.int.add'])

    def test_managed_arithmetic_shares_contract_but_alias_does_not_inherit_it(self):
        for name, args in [('add', f'{INT_MAX},1'), ('sub', f'(-{INT_MAX}),1'),
                           ('mul', f'{INT_MAX},2')]:
            with self.subTest(name=name):
                self.context = RuntimeContext()
                self.assertEqual(builtin_failure_contract(EMPTY_FAILURES, f'core.int.{name}',
                                                          direct_builtin=False),
                                 failures(FailureId.IntegerOverflow))
                self.assertEqual(builtin_failure_contract(EMPTY_FAILURES, f'core.int.{name}',
                                                          direct_builtin=True), EMPTY_FAILURES)
                self.run_source(f'var x: Int = {name}({args})')
                self.assertEqual([e.failure_id for e in self.context.failure_history],
                                 [FailureId.IntegerOverflow])

    def test_python_implementation_errors_remain_python_errors(self):
        for error in (TypeError('bug'), KeyError('bug'), AssertionError('bug')):
            with self.subTest(error=error):
                fn = Mock(side_effect=error)
                with patch.dict(BUILTINS, {'core.int.add': fn}), self.assertRaises(type(error)) as raised:
                    self.run_source('var x: Int = add(1,2)')
                self.assertIs(raised.exception, error)
                self.assertEqual(self.context.failure_history, ())

    def test_custom_raised_failure_matches_only_its_sig(self):
        error = RaisedFailure(FailureId.IOErr)
        fn = Mock(side_effect=error)
        with patch.dict(BUILTINS, {'core.int.print': fn}):
            with self.assertRaises(RaisedFailure) as raised:
                self.run_source('print(1)')
            self.assertIs(raised.exception, error)
            self.assertEqual(self.context.failure_history, ())
            self.run_source('sig custom(Int) -> Unit { failure IOErr builtin core.int.print }\ncustom(1)')
        self.assertEqual([e.failure_id for e in self.context.failure_history], [FailureId.IOErr])

    def test_user_functions_have_child_frame_without_duplicate_registration(self):
        self.run_source('sig f() -> Float { failure DivideByZero }\n'
                        'func f() { return div(1.0,0.0) }\nprint(f())')
        self.assertEqual(len(self.context.call_frames), 2)
        self.assertEqual(len(self.context.failure_history), 1)
        self.assertIsNone(self.context.get_call(1).parent_call_id)


if __name__ == '__main__':
    unittest.main()
