"""Bounded, read-only Cap'n Proto primitives for the public Battlecode replay schema.

No native extensions, executable deserialization or schema downloads are required.
"""

from __future__ import annotations

import struct
from collections.abc import Iterator
from dataclasses import dataclass

MAX_UNPACKED = 128 * 1024 * 1024
MAX_ITEMS = 2_000_000


class DecodeError(ValueError):
    pass


def unpack(data: bytes, limit: int = MAX_UNPACKED) -> bytes:
    output = bytearray()
    at = 0
    while at < len(data):
        tag = data[at]
        at += 1
        if tag == 0:
            if at >= len(data):
                raise DecodeError("Truncated packed zero run.")
            count = 8 * (data[at] + 1)
            at += 1
            if len(output) + count > limit:
                raise DecodeError("Replay expands beyond the unpacked safety limit.")
            output.extend(b"\0" * count)
        elif tag == 255:
            if at + 9 > len(data):
                raise DecodeError("Truncated packed literal run.")
            word = data[at : at + 8]
            at += 8
            count = data[at] * 8
            at += 1
            if at + count > len(data) or len(output) + 8 + count > limit:
                raise DecodeError("Invalid or oversized packed literal run.")
            output.extend(word)
            output.extend(data[at : at + count])
            at += count
        else:
            needed = tag.bit_count()
            if at + needed > len(data) or len(output) + 8 > limit:
                raise DecodeError("Truncated or oversized packed word.")
            word = bytearray(8)
            for bit in range(8):
                if tag & (1 << bit):
                    word[bit] = data[at]
                    at += 1
            output.extend(word)
    return bytes(output)


class Message:
    def __init__(self, data: bytes):
        if len(data) < 8 or len(data) > MAX_UNPACKED:
            raise DecodeError("Invalid replay message size.")
        count = struct.unpack_from("<I", data)[0] + 1
        if count > 512 or 4 + count * 4 > len(data):
            raise DecodeError("Invalid replay segment table.")
        sizes = struct.unpack_from(f"<{count}I", data, 4)
        at = ((4 + count * 4 + 7) // 8) * 8
        if at + sum(sizes) * 8 != len(data):
            raise DecodeError("Replay segments are truncated or contain trailing data.")
        self.segments = []
        view = memoryview(data)
        for size in sizes:
            self.segments.append(view[at : at + size * 8])
            at += size * 8
        self.budget = 12_000_000

    def span(self, segment: int, at: int, length: int) -> memoryview:
        if (
            not 0 <= segment < len(self.segments)
            or at < 0
            or length < 0
            or at + length > len(self.segments[segment])
        ):
            raise DecodeError("Replay pointer is outside its segment.")
        return self.segments[segment][at : at + length]

    def word(self, segment: int, word: int) -> int:
        return struct.unpack("<Q", self.span(segment, word * 8, 8))[0]

    def pointer(self, segment: int, word: int, *, depth: int = 0):
        self.budget -= 1
        if depth > 16 or self.budget < 0:
            raise DecodeError("Replay pointer traversal exceeded the safety limit.")
        pointer = self.word(segment, word)
        if pointer == 0:
            return None
        kind = pointer & 3
        if kind == 2:
            target_segment = pointer >> 32
            target_word = (pointer >> 3) & 0x1FFFFFFF
            if not (pointer >> 2) & 1:
                return self.pointer(target_segment, target_word, depth=depth + 1)
            pad = self.word(target_segment, target_word)
            tag = self.word(target_segment, target_word + 1)
            if pad & 7 != 2 or tag & 3 not in (0, 1):
                raise DecodeError("Invalid double-far replay pointer.")
            return self._object(pad >> 32, (pad >> 3) & 0x1FFFFFFF, tag)
        offset = (pointer >> 2) & 0x3FFFFFFF
        if offset & 0x20000000:
            offset -= 0x40000000
        return self._object(segment, word + 1 + offset, pointer)

    def _object(self, segment: int, base: int, pointer: int):
        if pointer & 3 == 0:
            data_words = (pointer >> 32) & 0xFFFF
            pointers = (pointer >> 48) & 0xFFFF
            self.span(segment, base * 8, (data_words + pointers) * 8)
            return Record(self, segment, base, data_words, pointers)
        if pointer & 3 == 1:
            size = (pointer >> 32) & 7
            count = (pointer >> 35) & 0x1FFFFFFF
            if count > MAX_UNPACKED // 8:
                raise DecodeError("Replay list is too large.")
            if size == 7:
                tag = self.word(segment, base)
                if tag & 3:
                    raise DecodeError("Invalid composite replay list.")
                elements = (tag >> 2) & 0x3FFFFFFF
                data_words, pointers = (tag >> 32) & 0xFFFF, (tag >> 48) & 0xFFFF
                if elements > MAX_ITEMS or elements * (data_words + pointers) > count:
                    raise DecodeError("Composite replay list is too large or truncated.")
                self.span(segment, (base + 1) * 8, count * 8)
                return List(self, segment, base + 1, size, elements, data_words, pointers)
            bits = (0, 1, 8, 16, 32, 64, 64)[size]
            self.span(segment, base * 8, (count * bits + 7) // 8)
            return List(self, segment, base, size, count)
        raise DecodeError("Unsupported capability pointer in replay.")


@dataclass(frozen=True, slots=True)
class Record:
    message: Message
    segment: int
    base: int
    data_words: int
    pointers: int

    def number(self, offset: int, fmt: str) -> int:
        length = struct.calcsize(fmt)
        if offset + length > self.data_words * 8:
            return 0
        return struct.unpack(fmt, self.message.span(self.segment, self.base * 8 + offset, length))[
            0
        ]

    def u16(self, offset: int) -> int:
        return self.number(offset, "<H")

    def i32(self, offset: int) -> int:
        return self.number(offset, "<i")

    def pointer(self, index: int):
        return (
            self.message.pointer(self.segment, self.base + self.data_words + index)
            if index < self.pointers
            else None
        )

    def text(self, index: int, limit: int = 2 * 1024 * 1024) -> str:
        value = self.pointer(index)
        if value is None:
            return ""
        if not isinstance(value, List) or value.size != 2 or value.count > limit:
            raise DecodeError("Invalid or oversized replay text.")
        raw = bytes(value.message.span(value.segment, value.base * 8, value.count))
        if raw and raw[-1] != 0:
            raise DecodeError("Replay text is not null-terminated.")
        return raw[:-1].decode("utf-8", errors="replace")


@dataclass(frozen=True, slots=True)
class List:
    message: Message
    segment: int
    base: int
    size: int
    count: int
    data_words: int = 0
    pointers: int = 0

    def records(self, limit: int = MAX_ITEMS) -> Iterator[Record]:
        if self.size != 7 or self.count > limit:
            raise DecodeError("Invalid or oversized replay record list.")
        step = self.data_words + self.pointers
        for index in range(self.count):
            yield Record(
                self.message, self.segment, self.base + index * step, self.data_words, self.pointers
            )


def root_record(data: bytes) -> Record:
    # Unpacked messages have an exact segment table; packed ones almost never do.
    try:
        message = Message(data)
        root = message.pointer(0, 0)
        if isinstance(root, Record) and root.pointers >= 4:
            return root
    except DecodeError:
        pass
    message = Message(unpack(data))
    root = message.pointer(0, 0)
    if not isinstance(root, Record) or root.pointers < 4:
        raise DecodeError("This is not a Battlecode replay.")
    return root
