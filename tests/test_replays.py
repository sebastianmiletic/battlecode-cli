import gzip
import json
import struct
from importlib.resources import files

import pytest

from battlecode_cli.capnp import DecodeError, Message, root_record, unpack
from battlecode_cli.replays import ReplayError, ReplayLibrary, decode_replay, load_replay, parse_map

MAP = "MAP 6 4\nMAP_NAME Fixture\nDRAGON 0 2 1 1 0 1\nDRAGON 1 2 4 2 5 2\nEDGE 0 1\n"


def json_replay(events=None):
    return json.dumps(
        {
            "format": "battlecode-cli-replay",
            "version": 1,
            "map": MAP,
            "bots": {"A": "Alpha", "B": "Beta"},
            "events": events or [],
            "winner": "A",
            "end_reason": "Fixture",
        }
    ).encode()


class Encoder:
    """Minimal synthetic, schema-shaped fixture encoder; not part of the shipped app."""

    def __init__(self):
        self.words = [0]
        self.root = self.allocate(7)
        self.struct(0, self.root, 2, 5)

    def allocate(self, count):
        start = len(self.words)
        self.words.extend([0] * count)
        return start

    def struct(self, pointer, target, data_words, pointers):
        self.words[pointer] = (
            ((target - pointer - 1) & 0x3FFFFFFF) << 2 | data_words << 32 | pointers << 48
        )

    def text(self, pointer, text):
        raw = text.encode() + b"\0"
        target = self.allocate((len(raw) + 7) // 8)
        padded = raw + b"\0" * (-len(raw) % 8)
        self.words[target : target + len(padded) // 8] = struct.unpack(
            f"<{len(padded) // 8}Q", padded
        )
        self.words[pointer] = (
            1 | ((target - pointer - 1) & 0x3FFFFFFF) << 2 | 2 << 32 | len(raw) << 35
        )

    def point(self, pointer, x, y):
        target = self.allocate(1)
        self.words[target] = x | y << 32
        self.struct(pointer, target, 1, 0)

    def message(self):
        return struct.pack("<II", 0, len(self.words)) + struct.pack(
            f"<{len(self.words)}Q", *self.words
        )


def binary_replay():
    output = Encoder()
    output.text(output.root + 2, MAP)
    output.text(output.root + 3, "Alpha")
    output.text(output.root + 4, "Beta")
    target = output.allocate(1 + 4 * 2)
    output.words[output.root + 5] = 1 | (target - (output.root + 5) - 1) << 2 | 7 << 32 | 8 << 35
    output.words[target] = 4 << 2 | 1 << 32 | 1 << 48
    start = target + 1
    # roundStart(0), dragonUpdate, tileChange, dragonDeath
    kinds = (0, 9, 3, 11)
    for index, kind in enumerate(kinds):
        event = start + index * 2
        output.words[event] = kind
        payload = output.allocate(1 + (2 if kind == 9 else 1 if kind == 3 else 0))
        output.struct(event + 1, payload, 1, 2 if kind == 9 else 1 if kind == 3 else 0)
        if kind == 9:
            output.point(payload + 1, 2, 1)
            output.point(payload + 2, 1, 1)
        elif kind == 3:
            output.words[payload] = 1
            output.point(payload + 1, 3, 1)
        elif kind == 11:
            output.words[payload] = 1 | 2 << 32
    result = output.allocate(1)
    output.words[result] = 1 | 1 << 32  # has result, eliminated, winner A
    output.struct(output.root + 6, result, 1, 0)
    return output.message()


def pack(raw):
    output = bytearray()
    for at in range(0, len(raw), 8):
        word = raw[at : at + 8]
        tag = sum((1 << bit) for bit, byte in enumerate(word) if byte)
        output.append(tag)
        output.extend(byte for byte in word if byte)
        if tag in (0, 255):
            output.append(0)
    return bytes(output)


@pytest.mark.parametrize(
    "transform", [lambda data: data, pack, gzip.compress, lambda data: gzip.compress(pack(data))]
)
def test_official_binary_round_states_and_result(transform):
    replay = decode_replay(transform(binary_replay()))
    assert replay.map.width == 6
    assert replay.bots == ("Alpha", "Beta")
    assert replay.winner == "A"
    assert len(replay.frames) == 2
    assert replay.frames[0].round == -1
    assert replay.frames[1].round == 0
    assert replay.frames[-1].dragons[0].body == ((2, 1), (1, 1))
    assert replay.frames[-1].stats[1].deaths == 1
    assert replay.frames[-1].stats[1].queen == 0
    assert (3, 1) in replay.frames[-1].pearls
    assert "died" in replay.frames[-1].events[0]


def test_json_split_and_death_stats():
    events = [
        {"type": "roundStart", "round": 0},
        {"type": "dragonUpdate", "id": 0, "head": [2, 1], "tail": [0, 1]},
        {
            "type": "dragonSplit",
            "parentId": 0,
            "childId": 2,
            "team": 0,
            "parentBody": [[2, 1], [1, 1]],
            "childBody": [[0, 1]],
        },
        {"type": "roundStart", "round": 1},
        {"type": "dragonDeath", "id": 1, "reason": "hit wall"},
    ]
    replay = decode_replay(json_replay(events))
    assert len(replay.frames) == 3
    assert replay.frames[1].stats[0].living == 2
    assert replay.frames[-1].stats[0].splits == 1
    assert replay.frames[-1].stats[1].deaths == 1
    assert replay.frames[-1].stats[0].queen == 2


def test_sample_and_quoted_file_paths(tmp_path):
    path = tmp_path / "sample with spaces.replay"
    path.write_bytes(files("battlecode_cli").joinpath("assets/sample.replay.json").read_bytes())
    replay = load_replay(f'"{path}"')
    assert len(replay.frames) > 2
    assert replay.map.width == 12
    assert replay.map.height == 6


def test_library_copies_deduplicates_repairs_and_preserves_original(tmp_path):
    source = tmp_path / "source.replay"
    source.write_bytes(binary_replay())
    library = ReplayLibrary(tmp_path / "library")
    entry, replay = library.import_file(source)
    assert replay.winner == "A"
    copied = library.path(entry["id"])
    assert copied.read_bytes() == source.read_bytes()
    assert len(library.entries()) == 1
    copied.unlink()
    duplicate, _ = library.import_file(source)
    assert entry == duplicate
    assert copied.exists()
    assert len(library.entries()) == 1
    library.remove(entry["id"])
    assert not copied.exists()
    assert source.exists()
    assert library.entries() == []
    with pytest.raises(ReplayError):
        library.path("../outside")


def test_library_rejects_corrupt_index(tmp_path):
    library = ReplayLibrary(tmp_path)
    library.index.write_text('{"version":1,"replays":[{"id":"../../outside"}]}')
    with pytest.raises(ReplayError, match="index is unreadable"):
        library.entries()


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"random data",
        b"\x1f\x8btruncated",
        b'{"format":"other"}',
        b"{" + b"[" * 2000,
        b"\x00\x01\x02",
    ],
)
def test_invalid_files_are_not_executed(raw):
    with pytest.raises(ValueError):
        decode_replay(raw)


@pytest.mark.parametrize(
    "event",
    [
        {"type": "roundStart", "round": -1},
        {"type": "dragonUpdate", "id": 99, "head": [1, 1], "tail": [1, 1]},
        {"type": "tileChange", "tile": [99, 0], "hasPearl": True},
        {"type": "tileChange", "tile": [1, 1], "hasPearl": "false"},
        {
            "type": "dragonSplit",
            "parentId": 0,
            "childId": 1,
            "team": 0,
            "parentBody": [[1, 1]],
            "childBody": [[1, 1]],
        },
        {"type": "execute", "code": "raise RuntimeError()"},
    ],
)
def test_invalid_json_events(event):
    with pytest.raises(ReplayError):
        decode_replay(json_replay([event]))


@pytest.mark.parametrize(
    "text", ["MAP 0 1", "MAP 10000 10000", "MAP 2 2\nDRAGON 0 1 9 9", "MAP 2 2\nDRAGON 0 2 1 1"]
)
def test_bad_map_geometry(text):
    with pytest.raises(ValueError):
        parse_map(text)


def test_limits_and_pointer_cycles():
    with pytest.raises(DecodeError):
        unpack(b"\0\xff", limit=8)
    with pytest.raises(DecodeError):
        unpack(b"\xffabc")
    message = Message(struct.pack("<IIQ", 0, 1, 2))  # far-pointer cycle
    with pytest.raises(DecodeError, match="traversal"):
        message.pointer(0, 0)
    with pytest.raises(ValueError):
        root_record(struct.pack("<IIQ", 0, 1, 3))  # capabilities are not permitted


def test_single_and_double_far_pointers():
    single = (
        struct.pack("<IIII", 1, 1, 2, 0)
        + struct.pack("<Q", 2 | 1 << 32)
        + struct.pack("<QQ", 1 << 32, 42)
    )
    assert Message(single).pointer(0, 0).i32(0) == 42
    double = (
        struct.pack("<IIII", 2, 1, 2, 1)
        + struct.pack("<Q", 6 | 1 << 32)
        + struct.pack("<QQ", 2 | 2 << 32, 1 << 32)
        + struct.pack("<Q", 43)
    )
    assert Message(double).pointer(0, 0).i32(0) == 43
