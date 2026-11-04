"""Decode base64 file uploads with a size limit."""

from __future__ import annotations

import base64
import binascii

from .errors import ApiError

MB = 1024 * 1024


def decode_upload(content_base64: str, *, max_bytes: int, what: str = "파일") -> bytes:
    if len(content_base64) > (max_bytes * 4) // 3 + 8:
        raise ApiError(413, "FILE_TOO_LARGE", f"{what}이(가) 너무 큽니다 (최대 {max_bytes // MB} MB).")
    try:
        data = base64.b64decode(content_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ApiError(422, "INVALID_UPLOAD", f"{what}을(를) 읽지 못했습니다 (base64 오류).") from exc
    if not data:
        raise ApiError(422, "EMPTY_UPLOAD", f"{what}이(가) 비어 있습니다.")
    if len(data) > max_bytes:
        raise ApiError(413, "FILE_TOO_LARGE", f"{what}이(가) 너무 큽니다 (최대 {max_bytes // MB} MB).")
    return data
