"""Formal extension boundary and private registry regressions."""
import unittest
from dataclasses import FrozenInstanceError, replace
from unittest.mock import Mock, patch

import ginger.eval as evaluator
from ginger.ast import IdentExpr, TypeRef
from ginger.builtin import BUILTINS, call_builtin
from ginger.core.failure_spec import EMPTY_FAILURES, FailureId
from ginger.extension import ThunkHandle
from ginger.lower import lower_program
from ginger.parser import parse
from ginger.runtime.context import RuntimeContext
from ginger.runtime.thunk import ThunkValue
from ginger.runtime.failures import RaisedFailure


class ExtensionThunkHandleTests(unittest.TestCase):
    def evaluate(self, source, implementations):
        with patch.dict(BUILTINS, implementations):
            return evaluator.eval_program(lower_program(parse(source)))

    def assert_rejected(self, result, reason):
        self.assertEqual(result.value_contract_violation.reason, reason)
        self.assertEqual(result.value_contract_violation.boundary_kind, 'extension_return')
        self.assertEqual(result.value_contract_violation.expected_type.name, 'Thunk')
        self.assertEqual(result.failure_history, ())
        self.assertNotIn('t', result.environment)
        self.assertNotIn('x', result.environment)
        self.assertNotIn('later', result.environment)

    def test_public_shape_and_identity(self):
        seen = []
        def accept(handle):
            seen.append(handle)
            self.assertIs(type(handle), ThunkHandle)
            self.assertNotIsInstance(handle, ThunkValue)
            for name in ['expr', 'env', 'outer', 'potential_failure_contract',
                         '_thunk', '__dict__', 'force', 'public_type', 'context']:
                self.assertFalse(hasattr(handle, name), name)
            self.assertEqual(repr(handle), '<ThunkHandle>')
        result = self.evaluate(
            'sig accept(Thunk[Int]<Never>) -> Unit { builtin test.accept }\n'
            'accept(thunk(1))', {'test.accept': accept})
        self.assertIsNone(result.value_contract_violation)
        self.assertEqual(seen[0], seen[0])
        self.assertNotEqual(seen[0], ThunkHandle())
        self.assertEqual({seen[0]: 1}[seen[0]], 1)

    def test_generic_identity_concrete_registry_and_internal_identity(self):
        entries = []
        issue = RuntimeContext._issue_thunk_handle
        def observe(context, thunk, typ):
            handle = issue(context, thunk, typ)
            entries.append(context._resolve_thunk_handle(handle))
            return handle
        for typ, literal, expected in [('Int', '1', 1), ('Float', '1.5', 1.5)]:
            with patch.object(RuntimeContext, '_issue_thunk_handle', observe):
                result = self.evaluate(
                    'sig identity(Thunk[T]<Never>) -> Thunk[T]<Never> { builtin test.id }\n'
                    'sig relay(T) -> Thunk[T]<Never> {}\n'
                    'func relay(a: T) { return identity(thunk(a)) }\n'
                    f'var t: Thunk[{typ}]<Never> = relay({literal})\n'
                    f'var x: {typ} = force(t)', {'test.id': lambda t: t})
            self.assertIsNone(result.value_contract_violation)
            self.assertEqual(entries[-1].public_type.args, (TypeRef(typ),))
            self.assertIs(entries[-1].thunk, result.environment['t'].value)
            self.assertEqual(result.environment['x'].value, expected)
            with self.assertRaises(FrozenInstanceError):
                entries[-1].public_type = TypeRef('Int')

    def test_store_load_and_foreign_execution(self):
        saved = []
        implementations = {'test.store': lambda t: saved.append(t), 'test.load': lambda: saved[-1]}
        declarations = ('sig store(Thunk[Int]<Never>) -> Unit { builtin test.store }\n'
                        'sig load() -> Thunk[Int]<Never> { builtin test.load }\n')
        tail = 'var t: Thunk[Int]<Never> = load()\nvar x: Int = force(t)\nvar later: Int = 1'
        result = self.evaluate(declarations + 'store(thunk(7))\n' + tail, implementations)
        self.assertEqual(result.environment['x'].value, 7)
        self.assert_rejected(self.evaluate(declarations + tail, implementations), 'invalid_extension_handle')
        self.assertFalse(hasattr(result, '_thunk_handles'))

    def test_fabricated_and_wrong_python_types(self):
        raw = ThunkValue(IdentExpr('x'), {}, None, EMPTY_FAILURES)
        for value in [ThunkHandle(), raw, 1, None, object()]:
            called = Mock(return_value=value)
            result = self.evaluate(
                'sig load() -> Thunk[Int]<Never> { builtin test.load }\n'
                'var t: Thunk[Int]<Never> = load()\nvar x: Int = force(t)\nvar later: Int = 1',
                {'test.load': called})
            self.assert_rejected(result, 'invalid_extension_handle' if isinstance(value, ThunkHandle)
                                 else 'wrong_python_type')
            called.assert_called_once_with()
        with patch.dict(BUILTINS, {'test.raw': lambda: raw}):
            self.assertIs(call_builtin('test.raw'), raw)

    def test_issued_result_type_and_latent_compatibility(self):
        for source_type, target_type, valid in [
                ('Int]<Never>', 'Float]<Never>', False),
                ('Int]<Never>', 'Int]<IntegerOverflow>', True),
                ('Int]<IntegerOverflow>', 'Int]<Never>', False),
                ('Box[Int]]<Never>', 'Box[Float]]<Never>', False)]:
            saved = []
            source_type, target_type = 'Thunk[' + source_type, 'Thunk[' + target_type
            # Box payload is deliberately opaque; only issued TypeRef identity matters.
            source_expr = 'make()' if 'Box' in source_type else '1'
            result = self.evaluate(
                'sig make() -> Box[Int] { builtin test.make }\n'
                f'sig store({source_type}) -> Unit {{ builtin test.store }}\n'
                f'sig load() -> {target_type} {{ builtin test.load }}\n'
                f'store(thunk({source_expr}))\nvar t: {target_type} = load()\nvar later: Int = 1',
                {'test.make': lambda: {}, 'test.store': lambda t: saved.append(t),
                 'test.load': lambda: saved[-1]})
            if valid:
                self.assertIsNone(result.value_contract_violation)
            else:
                self.assert_rejected(result, 'contract_mismatch')

    def test_saved_contract_still_checked_after_registry_type(self):
        issue = RuntimeContext._issue_thunk_handle
        def corrupt(context, thunk, typ):
            # Internal fault fixture, never an extension API.
            return issue(context, replace(thunk, potential_failure_contract={FailureId.FloatOverflow}), typ)
        with patch.object(RuntimeContext, '_issue_thunk_handle', corrupt):
            result = self.evaluate(
                'sig identity(Thunk[Int]<Never>) -> Thunk[Int]<Never> { builtin test.id }\n'
                'var t: Thunk[Int]<Never> = identity(thunk(1))', {'test.id': lambda t: t})
        self.assertEqual(result.value_contract_violation.reason, 'contract_mismatch')
        self.assertEqual(result.value_contract_violation.boundary_kind, 'builtin')
        self.assertEqual(result.failure_history, ())

    def test_nested_and_nonmemoized(self):
        effect = Mock(return_value=3)
        result = self.evaluate(
            'sig effect() -> Int { builtin test.effect }\n'
            'sig identity(Thunk[T]<Never>) -> Thunk[T]<Never> { builtin test.id }\n'
            'var outer: Thunk[Thunk[Int]<Never>]<Never> = identity(thunk(thunk(effect())))\n'
            'var inner: Thunk[Int]<Never> = force(outer)\n'
            'var x: Int = force(inner)\nvar y: Int = force(inner)',
            {'test.effect': effect, 'test.id': lambda t: t})
        self.assertEqual(result.environment['x'].value, 3)
        self.assertEqual(effect.call_count, 2)
        self.assertIsInstance(result.environment['inner'].value, ThunkValue)

    def test_open_world_payload_alias_and_handle_roundtrip(self):
        raw = ThunkValue(IdentExpr('x'), {}, None, EMPTY_FAILURES)
        payload = [raw, ThunkHandle()]
        def box(handle):
            payload.append(handle)
            return payload
        result = self.evaluate(
            'sig box(Thunk[Int]<Never>) -> Box[Thunk[Int]<Never>] { builtin test.box }\n'
            'sig alias(Box[Thunk[Int]<Never>]) -> Box[Thunk[Int]<Never>] { builtin test.alias }\n'
            'sig extract(Box[Thunk[Int]<Never>]) -> Thunk[Int]<Never> { builtin test.extract }\n'
            'var a: Box[Thunk[Int]<Never>] = box(thunk(5))\n'
            'var b: Box[Thunk[Int]<Never>] = alias(a)\nvar x: Int = force(extract(b))',
            {'test.box': box, 'test.alias': lambda v: v, 'test.extract': lambda v: v[-1]})
        self.assertEqual(result.environment['x'].value, 5)
        self.assertIs(result.environment['a'].value, payload)
        self.assertIs(result.environment['b'].value, payload)
        self.assertIs(payload[0], raw)

    def test_raised_failure_skips_handle_return_adapter(self):
        def fail():
            raise RaisedFailure(FailureId.IOErr)
        result = self.evaluate(
            'sig load() -> Thunk[Int]<Never> { failure IOErr builtin test.load }\n'
            'var t: Thunk[Int]<Never> = load()\nvar later: Int = 1', {'test.load': fail})
        self.assertIsNone(result.value_contract_violation)
        self.assertEqual(len(result.failure_history), 1)
        self.assertEqual(result.environment['later'].value, 1)
