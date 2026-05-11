"""parser_binary.py — Low-level binary primitives for UPC's varint encoding.

# OP-55f | py_modules/unifideck/stores/ubisoft/parser_binary.py | Depends: (none)

UPC stores its configuration and ownership cache in a custom binary
format with variable-length integers ("Konrad's varint"). This module
contains pure decode helpers — no I/O, no logging — that the higher-
level parser uses to walk records.
"""
from __future__ import annotations

import math


def _convert_data(data: int) -> int:
    """Reverse Konrad's compact varint encoding."""
    if data > 256 * 256:
        data -= 128 * 256 * math.ceil(data / (256 * 256))
        data -= 128 * math.ceil(data / 256)
    elif data > 256:
        data -= 128 * math.ceil(data / 256)
    return data


def parse_record_size(
    header: bytes, offset: int, second_eight: bool,
) -> tuple[int, int, int]:
    """Read a record-size varint at ``offset``.

    Returns (decoded_size, bytes_consumed, raw_size). ``second_eight``
    selects the alternate 0x10-rooted branch used by ownership records,
    which apply Konrad's transform a second time.
    """
    raw = 0
    consumed = 0
    shift = 0
    while offset + consumed < len(header):
        byte = header[offset + consumed]
        raw |= (byte & 0x7F) << shift
        consumed += 1
        shift += 7
        if not (byte & 0x80):
            break
    decoded = _convert_data(raw)
    if second_eight:
        decoded = _convert_data(decoded)
    return decoded, consumed, raw


def parse_install_id(header: bytes, offset: int) -> tuple[int, int]:
    """Decode the install_id following an 0x08 marker.

    Returns (install_id, bytes_consumed). When there is no 0x08 at the
    offset, returns (0, 0) so the caller can detect a missing marker.
    """
    if offset >= len(header) or header[offset] != 0x08:
        return 0, 0
    raw = 0
    consumed = 1
    shift = 0
    while offset + consumed < len(header):
        byte = header[offset + consumed]
        raw |= (byte & 0x7F) << shift
        consumed += 1
        shift += 7
        if not (byte & 0x80):
            break
    return _convert_data(raw), consumed


def parse_launch_id(header: bytes, offset: int) -> tuple[int, int]:
    """Decode the launch_id following an 0x10 marker.

    Returns (launch_id, bytes_consumed). Returns (0, 0) when there
    is no marker at the offset.
    """
    if offset >= len(header) or header[offset] != 0x10:
        return 0, 0
    raw = 0
    consumed = 1
    shift = 0
    while offset + consumed < len(header):
        byte = header[offset + consumed]
        raw |= (byte & 0x7F) << shift
        consumed += 1
        shift += 7
        if not (byte & 0x80):
            break
    return _convert_data(raw), consumed


def parse_ownership_record(chunk: bytes) -> tuple | None:
    """Decode a single ownership record.

    Returns (launch_id, launch_id_2) or ``None`` if the record can't
    be decoded. ``launch_id_2`` is 0 when the record has no secondary
    0x10 field.
    """
    if not chunk:
        return None
    offset = 0
    launch_id, consumed = parse_install_id(chunk, offset)
    if consumed == 0:
        return None
    offset += consumed
    launch_id_2 = 0
    if offset < len(chunk) and chunk[offset] == 0x10:
        decoded2, consumed2 = parse_launch_id(chunk, offset)
        launch_id_2 = decoded2
        offset += consumed2
    return launch_id, launch_id_2
