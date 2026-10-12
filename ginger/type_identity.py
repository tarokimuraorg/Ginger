"""Structural keys for normalized capability targets and typegroup members."""

from typing import Union

from .ast import TypeRef


# Preserve the existing key for argument-free types, including primitives.
TypeIdentityKey = Union[str, tuple[str, tuple['TypeIdentityKey', ...]]]


def type_identity_key(typ: TypeRef) -> TypeIdentityKey:
    """Keep recursive name/args identity, without interpreting patterns or effects.

    Callers supply normalized TypeRefs. Failure specifications and latent
    contracts remain effect information and do not select capabilities.
    """
    if not typ.args:
        return typ.name
    return (typ.name, tuple(type_identity_key(arg) for arg in typ.args))
