"""Lossless Unicode document windows with immutable revision and source offsets."""
from __future__ import annotations

from bisect import bisect_right
import hashlib

CHUNK_CHARACTERS = 4000


def line_starts(content: str) -> list[int]:
    starts, offset = [], 0
    for line in content.splitlines(keepends=True):
        starts.append(offset)
        offset += len(line)
    return starts


def chunks(content: str) -> list[dict]:
    starts = line_starts(content)
    result, cursor = [], 0
    while cursor < len(content):
        end = min(cursor + CHUNK_CHARACTERS, len(content))
        if end < len(content):
            # Prefer complete physical lines, while still bounding a huge single line.
            boundary = bisect_right(starts, end) - 1
            if boundary >= 0 and starts[boundary] >= cursor + CHUNK_CHARACTERS // 2:
                end = starts[boundary]
        text = content[cursor:end]
        result.append({"index": len(result), "char_start": cursor, "char_end": end,
                       "line_start": bisect_right(starts, cursor),
                       "line_end": bisect_right(starts, end - 1), "content": text,
                       "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()})
        cursor = end
    return result


def metadata(content: str) -> dict:
    return {"character_length": len(content), "line_count": len(content.splitlines()),
            "chunk_count": len(chunks(content))}


def matching_excerpt(line: str, keyword: str, width: int = 512) -> dict | None:
    """Locate casefold matches without treating expanded Unicode as source offsets."""
    folded = line.casefold()
    match = folded.find(keyword.casefold())
    if match < 0:
        return None
    positions, offset = [], 0
    for character in line:
        positions.append(offset)
        offset += len(character.casefold())
    begin = bisect_right(positions, match) - 1
    finish = bisect_right(positions, match + len(keyword.casefold()) - 1)
    excerpt_start = max(0, begin - 128)
    excerpt_end = min(len(line), max(excerpt_start + width, finish))
    return {"excerpt": line[excerpt_start:excerpt_end],
            "excerpt_char_start": excerpt_start, "excerpt_char_end": excerpt_end,
            "match_char_start": begin, "match_char_end": finish}
