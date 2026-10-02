# Arena

Arena is a local benchmark coordinator for the official `unswbc` runner, not a new engine or server endpoint. Benchmarks do not upload, activate or challenge bots, affect server ELO, or send custom maps online.

## Inputs

- Two version ZIPs or project folders containing `bot.toml`. Your own server submissions can be downloaded through Bots' **Arena A / Arena B** buttons.
- Optional opponents folder containing up to 20 shared source ZIPs/projects. Both candidates play each opponent, plus each other.
- Official map text from `GET /maps` when authenticated, or the installed runner's bundled maps offline. Live server maps take precedence when supplied.
- Custom `.map`/`.txt` files, folders searched recursively, or ZIPs. Geometry and both starting teams are checked before copying; the official engine may still correct map defects. Corrections are flagged in results.

Other teams' private submission source is not available through the API. Server challenges use active bots, not arbitrary inactive versions. Arena's **Online** tab uses the existing, confirmed challenge workflow, unranked by default. Ranked challenges use five server-selected maps; custom maps cannot be sent.

## Execution

Install the official runner explicitly with **Install runner**, or:

```sh
uv tool install --python 3.13 'unswbc>=1.2.2,<2'
```

The local runner is optional; the dashboard and offline replay viewer work without it. C/C++ builds use the judge's WASM toolchain; Python runs in its sandbox. Native execution is never a fallback. C/C++ snapshots must contain visible compilation units and cannot contain a root `main.py`, which would select the toolkit's wrong build route.

Review confirms the two versions, opponents, map count, total games, seat policy and seed. Validated source is copied into a private workspace, and bot output paths are normalized. Content-hashed maps are verified again before snapshotting. Changing original files after review does not change the prepared bot archives.

Every matchup runs each chosen map and repeat. A repeat uses `base seed + repeat index`, modulo 2^64, shared between candidates and opponents. Both seats run by default. Seat swaps reduce starting-position bias; they do not imply identical bot random streams between teams. Self-play is allowed when comparing identical source.

Games run sequentially, preserving navigation responsiveness and avoiding uncontrolled CPU fan-out. Sandbox/toolchain caches are reusable. The first C/C++ run can take longer while the official toolkit prepares its compiler. No commands use a shell string. Runner processes receive an allowlist of OS/toolchain environment variables, not Battlecode, AI-provider or GitHub credentials. Debug bot logs/drawings are disabled in benchmark replays; action/state events remain available.

**Stop**, a timeout, or application cancellation stops the runner process tree. Finished games are saved incrementally. Restarted, unfinished batches are marked interrupted; automatic resumption is not implemented.

## Results and diagnostics

Each completed game retains its official replay, a bounded/redacted runner log, seed, seat assignments, wall time, final state and diagnostics. Results show all games; any successful game's replay opens in the built-in player. Watching it also adds a content-deduplicated local library copy.

The report includes:

- Per-bot and per-map W/D/L, win rate and mean rounds.
- Every recorded dragon movement update, positive body growth, actual split and death event, not a sample of round-end frames.
- Command counts, requested movement steps and sprint counts when the official replay includes command events. Custom JSON does not claim to contain command logs.
- Queen death round, death causes and final lengths in per-game failure notes.
- Rule-based pointers for wall/self/body collisions, head-to-head contests and invalid actions. These are inspection priorities, not proof of strategic causation. Invalid-action deaths alone cannot distinguish bad output from CPU-budget problems.

Errors, timeouts and unfinished official replays are excluded from win-rate denominators. Draws remain in the denominator. A completed batch can contain errors, which are counted separately. Local win rates are not ELO predictions. Small batches and a fixed map pool can overfit.

JSON exports contain the complete structured batch; CSV exports contain one row per game. Exports ask for a destination and confirmation, including overwrite disclosure. Formula-leading CSV text is escaped. No export goes online. The full report summarizes the first 50 decided games and first 20 errors; JSON contains every game.

## Bounds and storage

- 1,000 unique stored maps; 1 MB per map; 128 MB per import.
- Folder traversal: 20,000 entries; ZIP: 5,000 entries. Unsafe paths, encrypted entries and symlinks are rejected. Duplicate content is reused; missing copies can be restored by reimporting. Invalid individual maps are reported without discarding valid maps in the batch.
- Up to 20 additional opponents, 1–10 repeats and 10,000 games per batch.
- Per-game timeout: 30–3,600 seconds, default 600. Logs retain at most 2 MB.
- Existing upload archive/source and replay decoding safety limits also apply.
- Execution stops before the next game when less than 256 MB disk space remains. Large batches can consume substantial space; this is not an upfront disk reservation.

Maps live in the platform user-data directory under `arena/maps`; batches under `arena/runs/<id>`. A batch contains source/map snapshots, `results.json`, lightweight `summary.json`, and game replay/log files. Nothing is placed in Git. Old batches remain on disk; automatic pruning and in-app batch deletion are not implemented. Delete a batch's directory manually when it is not running if you no longer need its source/results/replays.
