"""Schema-free protobuf wire-format reader for Battle.net's ``product.db``.

py_modules/unifideck/stores/battlenet/product_db/wire.py

``product.db`` is a protobuf blob written by the Battle.net Agent. We do
**not** vendor a protobuf runtime to read it, for two reasons: the file is
under 2 KB, and anything that leaks into ``launcher/`` has to survive the
multi-ABI native-extension vendoring for system Python 3.10-3.14. A ~150
line pure-stdlib wire walker avoids both problems.

Schema-free is the point. This module knows the protobuf *wire format*
and nothing about Battle.net's message definitions — field numbers live
in ``schema.py``. Agent updates can renumber or add fields and this code
keeps parsing; the consequence is confined to a field lookup returning
``None``, which ``reader.py`` degrades over.

Verified on-device 2026-08-09 against a real ``product.db`` containing an
installed game, plus the Agent and client records.
"""

from __future__ import annotations

from collections.abc import Iterator

# Wire types we understand. Groups (3, 4) were deprecated in proto2 and
# have never appeared in a product.db; they raise rather than silently
# desynchronising the byte stream.
_WIRE_VARINT = 0
_WIRE_FIXED64 = 1
_WIRE_LEN = 2
_WIRE_FIXED32 = 5

# A varint above this many shifts cannot fit in the 64-bit values protobuf
# defines, so the stream is corrupt rather than merely unfamiliar.
_MAX_VARINT_SHIFT = 63

# Bounds recursion when walking nested submessages. Real records nest four
# deep (install -> cached_state -> base_state -> progress); anything much
# beyond that means we are misreading bytes as a submessage.
MAX_DEPTH = 8

Field = tuple[int, int, int | bytes]


class WireError(ValueError):
    """Raised when the byte stream is not decodable protobuf.

    Callers are expected to treat this as "unreadable file", never as a
    crash: a torn write mid-download produces exactly this.
    """


def read_varint(buf: bytes, pos: int) -> tuple[int, int]:
    """Decode one base-128 varint. Returns ``(value, next_pos)``."""
    value = 0
    shift = 0
    while pos < len(buf):
        byte = buf[pos]
        pos += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, pos
        shift += 7
        if shift > _MAX_VARINT_SHIFT:
            raise WireError("varint exceeds 64 bits")
    raise WireError("truncated varint")


def _read_len_delimited(buf: bytes, pos: int) -> tuple[bytes, int]:
    length, pos = read_varint(buf, pos)
    if pos + length > len(buf):
        raise WireError("length-delimited field overruns buffer")
    return buf[pos : pos + length], pos + length


def _read_fixed(buf: bytes, pos: int, width: int) -> tuple[int, int]:
    if pos + width > len(buf):
        raise WireError(f"truncated fixed{width * 8}")
    return int.from_bytes(buf[pos : pos + width], "little"), pos + width


def _read_value(buf: bytes, pos: int, wire: int) -> tuple[int | bytes, int]:
    """Decode one field value according to its wire type."""
    if wire == _WIRE_VARINT:
        return read_varint(buf, pos)
    if wire == _WIRE_LEN:
        return _read_len_delimited(buf, pos)
    if wire == _WIRE_FIXED64:
        return _read_fixed(buf, pos, 8)
    if wire == _WIRE_FIXED32:
        return _read_fixed(buf, pos, 4)
    raise WireError(f"unsupported wire type {wire}")


def iter_fields(buf: bytes) -> Iterator[Field]:
    """Yield ``(field_number, wire_type, value)`` for one message.

    Length-delimited values are yielded as raw ``bytes`` — the caller
    decides whether they are a UTF-8 string or a nested submessage, since
    the wire format does not distinguish them.
    """
    pos = 0
    end = len(buf)
    while pos < end:
        key, pos = read_varint(buf, pos)
        field_no, wire = key >> 3, key & 0x07
        if field_no == 0:
            raise WireError("field number 0 is not valid")
        value, pos = _read_value(buf, pos, wire)
        yield field_no, wire, value


def submessages(buf: bytes, field_no: int) -> Iterator[bytes]:
    """Yield the raw bytes of every length-delimited field ``field_no``.

    Repeated fields are the norm here: ``product.db`` stores one entry per
    installed product under the same field number.
    """
    for fno, wire, value in iter_fields(buf):
        if fno == field_no and wire == _WIRE_LEN and isinstance(value, bytes):
            yield value


def get_scalar(buf: bytes, field_no: int) -> int | None:
    """Return the first varint/fixed value for ``field_no``, else ``None``."""
    for fno, wire, value in iter_fields(buf):
        if fno == field_no and wire != _WIRE_LEN and isinstance(value, int):
            return value
    return None


def get_bytes(buf: bytes, field_no: int) -> bytes | None:
    """Return the first length-delimited value for ``field_no``, else ``None``."""
    for fno, wire, value in iter_fields(buf):
        if fno == field_no and wire == _WIRE_LEN and isinstance(value, bytes):
            return value
    return None


def get_str(buf: bytes, field_no: int) -> str | None:
    """Return ``field_no`` decoded as UTF-8, or ``None`` if absent/empty.

    Empty is treated as absent on purpose: a product that is mid-download
    carries zero-length placeholders where the version string will go, and
    callers want "not known yet", not ``""``.
    """
    raw = get_bytes(buf, field_no)
    if not raw:
        return None
    return raw.decode("utf-8", "replace") or None
