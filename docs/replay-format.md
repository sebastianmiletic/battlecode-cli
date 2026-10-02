# Replay formats

The built-in player is a read-only, round-state viewer, not a Battlecode engine. Imported files are copied to a local, content-hashed library. No replay is uploaded to Battlecode or an AI provider.

## Official 2026 replays

Supported: the current Battlecode Cap'n Proto binary replay, packed or unpacked, optionally gzipped. Format detection uses file contents, not the filename. A bounded pure-Python reader handles segment tables and single/double far pointers; no native Cap'n Proto installation or schema download is needed.

The decoder applies round starts, tile pearl changes, dragon movement, splits and deaths. The map includes initial bodies, starting queens, fountains, kelp and portal edges. The terminal player shows round-end frames, not every individual action. Sonar overlays, complete action logs and portal-link annotations are outside this viewer's scope. If the server schema changes or a replay is too large, use the official VS Code viewer (`unswbc vscode`).

## Portable JSON for tests and exports

This is **battlecode-cli's own format**, not a claimed server JSON export format. UTF-8 JSON, optionally gzipped:

```json
{
  "format": "battlecode-cli-replay",
  "version": 1,
  "map": "MAP 4 2\nMAP_NAME Example\nDRAGON 0 2 1 0 0 0\nDRAGON 1 1 3 1\n",
  "bots": {"A": "Example A", "B": "Example B"},
  "events": [
    {"type": "tileChange", "tile": [2, 0], "hasPearl": true},
    {"type": "roundStart", "round": 0},
    {"type": "dragonUpdate", "id": 0, "head": [2, 0], "tail": [0, 0]},
    {"type": "tileChange", "tile": [2, 0], "hasPearl": false},
    {"type": "dragonDeath", "id": 1, "reason": "hit wall"}
  ],
  "winner": "A",
  "end_reason": "Example export"
}
```

The map is the engine's text representation. Starting IDs follow the order of `DRAGON`/`SNAKE` lines, beginning at zero. Teams are `0` (A) and `1` (B); the first starting dragon for each team is its starting queen. Coordinates are `[x, y]`, bounded by the map. Body lists are head-first.

Events:

- `roundStart`: `round`, a nonnegative integer; strictly increasing.
- `tileChange`: `tile`, a point; `hasPearl`, a boolean.
- `dragonUpdate`: existing `id`, `head` and `tail` points. The new head is prepended and the body trimmed to the recorded tail.
- `dragonSplit`: `parentId`, unique living `childId`, `team`, `parentBody`, `childBody` (nonempty point lists).
- `dragonDeath`: `id`, optional string/numeric `reason`. Numeric reasons are 0 wall, 1 self, 2 body, 3 head-to-head, 4 invalid action.

`winner` is `"A"`, `"B"` or `null` (draw/unfinished); `end_reason` supplies context. Unknown JSON event types are rejected, not executed. There is no script, pickle or executable object deserialization.

The initial frame precedes the first round. Subsequent frames contain states after each recorded round; frame numbers and game round numbers are shown separately.

## Safety limits

- Input: 64 MiB, including a second size check during reading.
- Gzip output: 64 MiB; unpacked Cap'n Proto: 128 MiB.
- JSON: 8 MiB; map text: 2 MiB.
- Geometry: each dimension up to 512, at most 65,536 tiles.
- Frames: 2,001; body length: 8,192; living dragons: 20,000.
- Retained body/pearl state units: 2,000,000 across frames.
- Bounded list/pointer traversal and a decoding deadline for event application.
- Local library: 1,000 entries; files are atomically copied and duplicates reuse the same entry.

These are intentionally conservative. A rejected file may be a valid replay that exceeds this lightweight viewer's budget, not necessarily a corrupt game.
