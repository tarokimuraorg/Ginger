"""Shallow runtime immutability, distinct from checked value validation."""
import io
import unittest
from contextlib import redirect_stdout
from dataclasses import FrozenInstanceError
from unittest.mock import patch

from ginger.ast import IdentExpr, TypeRef
from ginger.builtin import BUILTINS
from ginger.core.failure_spec import EMPTY_FAILURES, FailureId
from ginger.eval import Cell, FunctionEnv
from ginger.pipeline import run
from ginger.runtime.thunk import ThunkValue


class RuntimeImmutabilityTests(unittest.TestCase):
    def test_cell_fields_are_frozen(self):
        cell = Cell(1, True, TypeRef('Int'))
        for field, value in [('value', 2), ('mutable', False), ('typ', TypeRef('Float'))]:
            with self.subTest(field=field), self.assertRaises(FrozenInstanceError):
                setattr(cell, field, value)

    def test_thunk_fields_and_captured_cells_are_frozen(self):
        env = {'x': Cell(1, True)}
        outer = {}
        thunk = ThunkValue(IdentExpr('x'), env, outer, EMPTY_FAILURES)
        for field, value in [('expr', IdentExpr('y')), ('env', {}), ('outer', None),
                             ('potential_failure_contract', {FailureId.FloatOverflow})]:
            with self.subTest(field=field), self.assertRaises(FrozenInstanceError):
                setattr(thunk, field, value)
        with self.assertRaises(FrozenInstanceError):
            thunk.env['x'].value = 2
        thunk.env['x'] = Cell(2, True)
        outer['y'] = Cell(3, False)
        self.assertEqual(thunk.env['x'].value, 2)
        self.assertIs(thunk.outer['y'], outer['y'])

    def test_contract_normalization_copies_mutable_input(self):
        saved = {FailureId.FloatOverflow}
        thunk = ThunkValue(IdentExpr('x'), {}, None, saved)
        saved.clear()
        self.assertIs(type(thunk.potential_failure_contract), frozenset)
        self.assertEqual(thunk.potential_failure_contract, frozenset({FailureId.FloatOverflow}))

    def test_contract_rejects_unsupported_collections_and_invalid_elements(self):
        for saved in [None, [], (), iter([FailureId.FloatOverflow]), 'FloatOverflow',
                      {'FloatOverflow'}, frozenset({1})]:
            with self.subTest(saved=type(saved)), self.assertRaises(TypeError):
                ThunkValue(IdentExpr('x'), {}, None, saved)

    def test_reassignment_replaces_cell_and_preserves_capture(self):
        captured = []
        from ginger.runtime.context import RuntimeContext
        issue = RuntimeContext._issue_thunk_handle
        def inspect(context, t, typ):
            captured.append(t.env['x'])
            return issue(context, t, typ)
        out = io.StringIO()
        with patch.object(RuntimeContext, '_issue_thunk_handle', inspect), patch.dict(
                BUILTINS, {'test.inspect': lambda handle: None}), redirect_stdout(out):
            result = run('sig inspect(Thunk[Int]<Never>) -> Unit { builtin test.inspect }\n'
                         'var x: Int = 1\nvar t: Thunk[Int]<Never> = thunk(x)\n'
                         'inspect(t)\nx = 2\nprint(force(t))\nprint(x)')
        self.assertEqual(out.getvalue(), '1\n2\n')
        self.assertIs(result.environment['t'].value.env['x'], captured[0])
        self.assertIsNot(result.environment['x'], captured[0])
        self.assertEqual(captured[0].value, 1)

    def test_function_env_copy_preserves_shared_cells_and_metadata(self):
        env = FunctionEnv({'T': TypeRef('Int')}, {'x': Cell(1, False)})
        env.call_frame_id = 7
        copied = env.copy()
        self.assertIsNot(copied, env)
        self.assertIs(copied['x'], env['x'])
        self.assertIs(copied.type_bindings, env.type_bindings)
        self.assertEqual(copied.call_frame_id, 7)
        copied['x'] = Cell(2, False)
        self.assertEqual(env['x'].value, 1)

    def test_generic_nested_retained_frames_and_custom_identity(self):
        seen = []
        def identity(t):
            seen.append(t)
            return t
        for typ, literal, expected in [('Int', '1', 1), ('Float', '1.5', 1.5)]:
            with self.subTest(typ=typ), patch.dict(BUILTINS, {'test.identity': identity}):
                result = run('sig capture(T) -> Thunk[Thunk[T]<Never>]<Never> {}\n'
                             'func capture(a: T) { return thunk(thunk(a)) }\n'
                             'sig identity(Thunk[T]<Never>) -> Thunk[T]<Never> { builtin test.identity }\n'
                             f'var t: Thunk[Thunk[{typ}]<Never>]<Never> = capture({literal})\n'
                             f'var u: Thunk[Thunk[{typ}]<Never>]<Never> = identity(t)\n'
                             f'var inner: Thunk[{typ}]<Never> = force(u)\n'
                             f'var x: {typ} = force(inner)')
            self.assertIsNone(result.value_contract_violation)
            self.assertEqual(result.environment['x'].value, expected)
            self.assertIs(result.environment['u'].value, result.environment['t'].value)
            from ginger.extension import ThunkHandle
            self.assertIsInstance(seen[-1], ThunkHandle)
            env = result.environment['t'].value.env
            self.assertEqual(result.call_frames[env.call_frame_id].type_bindings['T'], TypeRef(typ))
