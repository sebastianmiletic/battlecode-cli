"""Original offline technical notes, not a redistributed copy of the official articles."""

NOTES = {
    "overview": """## Explore the competition

Battlecode ranks teams. Your active submission plays online battles; local Arena simulations never change ELO.

- **Submissions**: inspect your versions, build logs and per-map records; review uploads or activation.
- **Arena**: compare two local versions and optional shared opponents in the official judge sandbox.
- **Your battles**: inspect an account's series and their individual games.
- **Visualiser**: read local or downloaded replays without running bot code.
- **Map editor**: create, validate and save local official-format maps.

Missing account records display n/a. Demo records are synthetic. Refresh a topic to read its full current official article in this terminal.""",
    "quickstart": """## A safe first session

1. Connect a key created on your official Team page in **Account / API keys**. The app verifies it before saving.
2. Choose a bot ZIP or project folder in **Submissions → Upload**. Inspect the prepared version and confirm before sending it.
3. Inspect its build log and active state. Do not assume an uploaded version compiled successfully.
4. Use **Arena → Simulation** for a local comparison, or **Online** for an explicitly reviewed server challenge.
5. Open a saved game in **Visualiser** to step through round-end states.

Local replay viewing, bundled maps and map editing work offline. Local execution additionally needs the optional official unswbc runner, installed only after confirmation.""",
    "submitting": """## Prepare and review

The server accepts Python, C and C++ source packages. The upload ZIP limit is 4 MB. A team can upload 12 versions per hour.

Use **Submissions → Upload** to select a project folder or ZIP, give it a version name and optional description, and review the prepared package. Packaging validates source; it does not execute it.

A successful build may become active. Inspect **Versions → Details / log** for the returned state, build log and recorded map results. Downloads are your own uploaded sources, not opponents' private code.""",
    "structure": """## Inspect a game

Replays contain a map, starting dragons, recorded actions and round-end snapshots. Team A and team B have starting queens. The viewer names queens, heads, bodies and pearls separately.

Replay playback is read-only. A frame is not necessarily a single action: an official frame after the initial state represents the end of a round. Arena diagnostics count recorded commands and events independently of these display frames.

Use Refresh topic for the current complete game structure and victory rules before changing strategy.""",
    "map-info": """## Maps and seats

Arena includes the official toolkit's maps and accepts validated local custom maps. Choose Official, Custom or All stored maps, then select the subset to run.

Both seats are enabled by default. A seat swap does not guarantee identical team random streams. Reports preserve map names, content hashes, seed and bot labels.

The map editor and replay visualiser show board coordinates. Detailed replay rendering includes kelp edges and portal markers; compact rendering explicitly hides edges.""",
    "pearls": """## Spawn terrain and growth

Map TILE directives specify a pearl spawn interval. With spawning enabled, minGap must be at least 1 and no greater than maxGap. A maxGap of 0 disables spawning. SYMMETRY can synchronize mirrored spawn countdowns.

The editor's Pearl spawn brush writes the selected tile's interval. The visualiser distinguishes pearls from dragon segments, and Arena diagnostics report recorded feeding and growth events.

A count of growth events is evidence about that replay, not proof that a particular strategy change caused a win.""",
    "kelp-and-portals": """## Terrain edges

Official map EDGE directives encode open edges, kelp and paired portals. Kelp uses kind 1 and portal ID -1. Portals use kind 2: exactly two edges share a non-negative ID, with the same orientation.

In Map editor, choose an edge brush, a direction and, for portals, the pair's ID. Validate both ends before saving. The official engine is the final authority on game behaviour.

The terminal replay view marks portals with @ and kelp with lines. It does not infer unrecorded portal intentions or annotate portal links.""",
    "vision": """## Recorded world versus bot knowledge

The visualiser reconstructs the recorded world. This is not an assertion that a bot could see every displayed tile at that time.

Use tile inspection for recorded dragon IDs, teams, lengths and coordinates. This viewer does not implement a bot-vision or sonar overlay.

Refresh this topic for the current official observation and visibility rules. Do not build a strategy around information that is visible only in a full replay.""",
    "movement": """## Read the movement evidence

Arena reports recorded directions, moves, requested steps, commands and sprints when present in official replay logs. Movement counts and command counts describe different things.

The player steps through round-end snapshots. Use the recorded-event diagnostics for events within a round rather than estimating activity from frame differences.

Timeouts, crashes and unfinished games are counted separately and excluded from win rates. Refresh for the exact legal move and collision rules.""",
    "splitting": """## Splits in reports

Replay frames retain living dragons and per-team split totals; Arena diagnostics also inspect recorded split events. Starting queen IDs are retained separately from later dragon IDs.

A greater dragon count alone does not establish successful splitting: deaths and subsequent movement matter. Inspect the event log and team lengths alongside the result.

Refresh for the exact split command, length constraints and action-order rules.""",
    "sonar": """## Sonar and observations

The current visualiser is a recorded-world viewer, not a bot observation debugger. It does not render sonar overlays or invent unrecorded requests.

Use the current official topic for the precise sonar request and response contract. Compare that contract to your bot's actual command output and saved judge logs.

AI advice is not built into the client; rule-based inspection pointers in Arena reports name their limitations.""",
    "death": """## Diagnose losses

Official diagnostics classify recorded deaths and retain queen-death timing. Available categories include wall, self, body, head-to-head and invalid-action events.

Inspect queen-death rounds, lengths and movement totals alongside the actual winner. A collision count does not identify a causal strategy mistake by itself.

Execution failures and unfinished replays are errors, not opponent wins. The Results report keeps these separate from decided-game records.""",
    "game-format": """## Online and local modes

**Ranked** online challenges are five-game series on server-selected maps and can change rating. **Unranked** challenges do not change rating; optional mapIds apply only to this mode.

**Simulation** is local official-sandbox execution. It is neither a ranked request nor an unranked server game and never changes ELO. Completed replays are saved automatically.

Online uses active server bots. Local comparisons need local or legitimately shared source. Opponents' private server sources are unavailable.""",
    "elo": """## Ratings and history

The ladder ranks teams. Ratings and eligible-team history are read from documented server feeds. When historical rank is reconstructed from today's eligible teams, the chart says inferred: past eligibility and tie ordering are unknown.

If server history is absent, the client may display locally observed team ELO/rank points. It does not manufacture a historical curve from a current value.

Win rate is wins divided by wins + draws + losses. Missing or empty denominators display n/a. Refresh for the current rating formula and eligibility rules.""",
    "cli": """## Client commands

```sh
battlecode-cli
battlecode-cli --demo
battlecode-cli status
battlecode-cli auth add --name "My team"
battlecode-cli auth list
battlecode-cli auth disconnect
battlecode-cli replay game.replay
battlecode-cli replay game.replay --info
battlecode-cli web
```

Use the hidden interactive key prompt, not a key in a shell argument. Exactly one saved account is active; disconnecting never activates a fallback.

The optional official unswbc toolkit is a separate program. Its sandbox and source toolchain are used by Arena; this app does not replace the judge engine.""",
    "execution-order": """## Preserve execution evidence

Map starting dragons receive IDs in file order; the starting lines alternate teams. Official action/event logs and the official engine determine the actual execution order.

Round-end playback aggregates a round into a display frame. It should not be used to infer within-round causality. Arena diagnostics independently process all recorded command events.

Refresh for the exact current ordering rules when designing interactions between multiple dragons.""",
    "timeouts": """## Separate execution errors

Arena has a configurable per-game timeout, default 600 seconds and bounded from 30 to 3,600 seconds. Stop terminates the sandbox process tree and preserves completed results.

This outer execution limit is not the judge's per-action time allowance. Judge crashes/timeouts are inspected through replay diagnostics and available logs.

The client does not retry mutations or automatically resume interrupted batches. Read the current official topic for the engine's bot-time contract.""",
    "libraries": """## Official runtime libraries

Use the libraries and language versions supported by the official toolkit and judge sandbox. Native local compilation is not a substitute for testing under the official WASM/Python route.

Arena snapshots your prepared sources before executing them. C/C++ projects need visible compilation units; Python projects need a root main.py. Bot packages are validated without executing them.

Refresh this topic for the current supported standard-library contract. Do not assume every library installed on your host exists in the sandbox.""",
    "helper": """## Helper reference

Use the official language helper APIs for decoding observations and emitting commands. The authoritative signatures and examples are available with Refresh topic.

The client does not rewrite your bot's protocol or expose private opponent code. Its source inspector and build log can help identify compilation or packaging problems, while the official sandbox checks runtime behaviour.

Keep credentials out of bot source and archives. Secret-looking files and unsafe archive paths are rejected by packaging.""",
    "protocol": """## IO protocol

Bots communicate with the judge through the official input/output protocol. A replay viewer is not a substitute for implementing that protocol correctly.

Prepare valid source, inspect build and judge logs, and test through **Arena → Simulation**. Invalid-action and execution-failure evidence is kept separate from decided-game win rates.

Refresh for exact messages, command schemas and examples; this offline note intentionally does not invent protocol fields.""",
    "protocol-upgrade": """## Protocol compatibility

Use a helper/toolkit version compatible with the current judge. Re-test older bots in the official sandbox after a protocol update, rather than assuming a successful native run proves compatibility.

This client records source snapshots, seed, maps, seat order and replay outcomes for reproducible comparisons. It does not translate older bot command formats.

Refresh for the official migration instructions and current compatibility details.""",
    "map-files": """## Official text format

MAP width height is the first directive. Optional MAP_NAME sets a readable title; SYMMETRY is x, y or xy.

```text
MAP 8 8
MAP_NAME Local practice
SYMMETRY xy
TILE_COUNT 1
TILE 3 3 8 20
EDGE_COUNT 0
DRAGON_COUNT 2
DRAGON 0 2 1 1 0 1
DRAGON 1 2 6 6 7 6
```

TILE x y minGap maxGap defines a spawn. EDGE index kind portalId defines terrain. Edge rows alternate horizontal and vertical; each row has width + 1 entries.

DRAGON team segmentCount x y ... lists segments head first. Teams are 0/1, alternate in file order, and need adjacent, non-overlapping segments with length at least 2. Count directives must match their records.

The native editor supports these directives, direct source editing, count normalization, validation, undo/redo and confirmed local saves. Nothing is uploaded.""",
    "api": """## Documented JSON API

Base: https://game.battlecode.au/api/v1. Authenticate with Authorization: Bearer using a key created on the official Team page. Never place a real key in a URL, screenshot or public prompt.

### Account and submissions

- GET /me and /team: current user/team and team records.
- GET /submissions and /submissions/:id: own versions and build logs.
- POST /submissions: multipart name, language, description, zip; ZIP maximum 4 MB.
- POST /submissions/:id/activate: select the playing version.
- GET /submissions/:id/download: your uploaded ZIP.

### Battles

- GET /battles?limit=50: own newest series; limit up to 200.
- GET /battles/:id: games, status, queue position; judge log for own battles.
- POST /battles: teamId, ranked, and optional unranked mapIds.
- GET /battles/:id/replay: finished game replay; signed redirects must not receive your bearer key.

### Read-only feeds

GET /leaderboard, /ratings, /teams, /teams/:id, /tournaments, /tournaments/:id, /maps and /queue.

### Limits and safety

120 requests per minute per key; /leaderboard and /ratings have a separate 30/minute limit. Honor 429 Retry-After. Uploads: 12/hour/team. Challenges: 60 games/hour, plus a separate 60 against dev teams.

Team settings, membership and account changes are website-only. There is no documented custom-map or replay-upload endpoint. Every upload, activation and challenge in this client requires review and is never automatically retried.""",
}


def document(slug, title):
    return f"# {title}\n\n{NOTES[slug]}\n\n[Full official topic](https://game.battlecode.au/docs/{slug})\n"
