from __future__ import annotations

import re

from fastapi import HTTPException, Response


def _safe_download_filename(value: str | None) -> str:
    filename = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "").strip()).strip(".-")
    return filename or "generated-media.bin"


def media_content_response(
    content: bytes,
    *,
    media_type: str,
    filename: str,
    download: bool = False,
    cache_control: str = "private, max-age=300",
    range_header: str | None = None,
) -> Response:
    """Build a media response with one optional HTTP byte range.

    Callers authenticate and authorize access before loading the content bytes.
    The full content is already in memory, so this is intended for short previews.
    """
    headers = {
        "Accept-Ranges": "bytes",
        "Cache-Control": cache_control,
        "Content-Disposition": (
            f'{"attachment" if download else "inline"}; '
            f'filename="{_safe_download_filename(filename)}"'
        ),
    }
    if range_header is None:
        return Response(content=content, media_type=media_type, headers=headers)

    match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header.strip(), flags=re.IGNORECASE)
    size = len(content)
    if (
        match is None
        or size == 0
        or not any(match.groups())
        or any(len(part) > 20 for part in match.groups())
    ):
        raise HTTPException(
            status_code=416,
            detail="Requested range is not satisfiable",
            headers={"Content-Range": f"bytes */{size}", "Accept-Ranges": "bytes"},
        )

    first, last = match.groups()
    if first:
        start = int(first)
        end = min(int(last), size - 1) if last else size - 1
    else:
        suffix_length = int(last)
        start = max(size - suffix_length, 0)
        end = size - 1
    if start >= size or end < start or (not first and suffix_length == 0):
        raise HTTPException(
            status_code=416,
            detail="Requested range is not satisfiable",
            headers={"Content-Range": f"bytes */{size}", "Accept-Ranges": "bytes"},
        )

    headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    return Response(
        content=content[start : end + 1],
        media_type=media_type,
        status_code=206,
        headers=headers,
    )


__all__ = ["media_content_response"]
