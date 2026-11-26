"""Structural keys for normalized capability targets and typegroup members."""

from dataclasses import replace
from typing import Union

from .ast import TypeRef


# Preserve the existing key for argument-free types, including primitives.
TypeIdentityKey = Union[str, tuple[str, tuple['TypeIdentityKey', ...]]]


def type_identity_key(typ: TypeRef) -> TypeIdentityKey:
    """Keep recursive name/args identity, without interpreting patterns or effects.

    A Thunk refers to its contract by name, including before normalization
    when typegroup membership is collected for conditional failures.
    """
    if typ.sigtype_name is not None:
        contract = TypeRef(typ.sigtype_name, typ.sigtype_args)
        return (typ.name, (type_identity_key(contract),))
    if not typ.args:
        return typ.name
    return (typ.name, tuple(type_identity_key(arg) for arg in typ.args))


def bind_type(typ, bindings):
    if typ.name in bindings:
        return bindings[typ.name]
    return replace(typ, args=tuple(bind_type(a, bindings) for a in typ.args),
                   sigtype_args=tuple(bind_type(a, bindings) for a in typ.sigtype_args))
