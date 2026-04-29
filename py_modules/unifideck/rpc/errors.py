"""RPC error types.

OP-24a | py_modules/unifideck/rpc/errors.py
"""
from __future__ import annotations


class RpcError(Exception):
    """Typed RPC failure that ``rpc_wrapper`` converts to an error envelope.

    Parameters
    ----------
    code:
        Machine-readable identifier (e.g. ``"store_not_found"``).
    message:
        Optional human-readable detail.
    **context:
        Arbitrary key/value pairs forwarded as ``data`` in the envelope.
    """

    def __init__(self, code: str, message: str = "", **context: object) -> None:
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code
        self.message = message
        self.context: dict[str, object] = context
