"""Opaque values supported by the Python extension boundary."""


class ThunkHandle:
    """Execution-bound token. Only runtime-issued tokens can be returned as Thunks.

    No evaluation, construction, type inspection, or underlying Thunk identity
    API is provided. Python equality and hashing use object identity.
    """

    __slots__ = ()

    def __repr__(self):
        return '<ThunkHandle>'
