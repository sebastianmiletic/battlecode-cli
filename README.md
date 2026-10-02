# battlecode-cli

A monochrome terminal control room for [UNSW Battlecode](https://game.battlecode.au). Team status, bots, challenges and a built-in replay player, with mouse and keyboard controls.

![Dashboard, synthetic demo data](docs/dashboard.svg)

## Install from GitHub

### macOS / Linux

```sh
curl -fsSL https://raw.githubusercontent.com/sebastianmiletic/battlecode-cli/v0.2.0/install.sh | sh
```

### Windows

Run in PowerShell, preferably inside Windows Terminal:

```powershell
irm https://raw.githubusercontent.com/sebastianmiletic/battlecode-cli/v0.2.0/install.ps1 | iex
```

These scripts install [uv](https://docs.astral.sh/uv/) if needed, obtain Python 3.13, install the versioned GitHub source archive into your user account, and configure PATH. No administrator access or shell alias is needed. Review the [shell](install.sh) or [PowerShell](install.ps1) script before executing it if you prefer.

**Open a new terminal**, then type:

```sh
battlecode-cli
```

`battlecode` remains a supported alias. The installer also prints the executable's full path for launching immediately in a terminal whose PATH has not refreshed.

### Install from a checkout

```sh
git clone https://github.com/sebastianmiletic/battlecode-cli.git
cd battlecode-cli
sh install.sh
```

On Windows, use `powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1` instead of `sh install.sh`.

Already have uv and Git? Use `uv tool install --python 3.13 --from "git+https://github.com/sebastianmiletic/battlecode-cli.git@v0.2.0" battlecode-cli`, then `uv tool update-shell`. Python 3.11+ is supported; the installers choose 3.13.

Update by rerunning the installer from a current checkout. Uninstall with `uv tool uninstall battlecode-cli`; saved accounts and replay files are preserved. Delete saved API keys in the dashboard before uninstalling if you also want their local secrets removed.

## First launch

With no connected account, the dashboard opens **API keys** and focuses the hidden key field.

1. Click **Open team page**, then create a key at [game.battlecode.au/team](https://game.battlecode.au/team).
2. Paste it into the hidden input. Optionally give it a friendly account label.
3. Press `Enter` or click **Connect & save**. The app verifies `GET /me` before saving.

Creating a new key on the website invalidates the old one. Never paste your key into a chat or a shell command argument.

**Continue offline** opens the local replay library. **Try sample** demonstrates playback without an account. `battlecode-cli --demo` shows synthetic dashboard data and disables server mutations and API-key management.

## API-key management

Open **API keys (7)** to add, save, use, disconnect or delete named keys.

- **Exactly one saved profile is active.** Others remain saved but disconnected.
- **Save only** verifies and stores a key without changing the connected account.
- Switching verifies the selected key, confirms replacement of an existing connection and clears account-specific dashboard data.
- **Disconnect** keeps saved keys. It never falls back to another key.
- **Delete key** confirms removal of the local secret. It does not delete your team or revoke the server key. Revoke on the team page to disable a key everywhere.
- Existing environment/toolkit keys are offered for **explicit import**, not automatic login. Priority for this import is `BATTLECODE_API_KEY`, `UNSWBC_KEY`, the v0.1 app file, then `~/.unswbc/keys.json`.
- An account change from another terminal invalidates the dashboard's old connection and any pending write approval. Reconnect explicitly in API keys.

Secrets use the OS keyring when available. Linux/macOS without a keyring fall back to **unencrypted, owner-only files** (directory `0700`, key files `0600`). Windows requires Credential Manager and refuses plaintext fallback. Metadata and secrets stay outside the checkout; keys are not displayed or deliberately logged. Moving an OS-keyring-backed profile to another computer requires adding the key again.

```sh
battlecode-cli auth add                 # hidden prompt; verify, save and connect
battlecode-cli auth add --save-only     # save without connecting
battlecode-cli auth import              # explicitly import an existing key
battlecode-cli auth list                # labels/IDs, never the secrets
battlecode-cli auth use ACCOUNT_ID      # verify and switch
battlecode-cli auth status              # read-only access check
battlecode-cli auth disconnect
battlecode-cli auth delete ACCOUNT_ID   # confirmation; --yes for automation
battlecode-cli status                   # read-only team summary
```

`auth set` is an alias for adding a key. `auth clear` disconnects without deleting saved keys. `auth add --stdin` accepts a key from a secure pipeline, never as an argument.

## Mouse and keyboard

Click the sidebar, table rows, buttons, inputs and selectors. Use the wheel to scroll; forms and action bars scroll when the terminal is small. All workflows also support keyboard navigation. Recommended minimum: **80 × 24**, with larger terminals showing more data.

| Key | View / action |
| --- | --- |
| `1` | Overview: team, rating, rank, active bot, recent results |
| `2` | Bots: submissions, win rates, logs, per-map records, activation |
| `3` | Games: battles, individual games, replay download/view |
| `4` | Ladder: search teams and prepare a challenge |
| `5` | Upload: browse for a bot ZIP or project folder |
| `6` | Challenge: practice by default; ranked requires confirmation |
| `7` | API keys: account management and local file locations |
| `8` | Replays: local imports and offline viewing |
| `r` | Refresh |
| `Tab` / `Shift+Tab` | Move between controls |
| Arrows / `Enter` | Select and inspect table rows |
| `Ctrl+p` | Command palette |
| `?` | Help |
| `Ctrl+q` | Quit |

Input fields keep typing precedence over navigation shortcuts. A supporting terminal is required for mouse reporting; keyboard controls work without a mouse. Hold your terminal's selection modifier (commonly Shift) if you want to select/copy terminal text instead.

The dashboard refreshes every 45 seconds (`--refresh 60` changes this). Failed requests preserve and label the last successful snapshot as stale. Rejected keys pause polling; writes are never retried automatically.

### Bots and challenges

Win rate is **wins / (wins + draws + losses)**. Draws count in the denominator, not as half a win. No completed games displays `n/a`.

Select a built inactive bot, click **Activate**, then confirm. ZIP downloads and build/per-map details are available on Bots. Uploads require `bot.toml` at the ZIP root and a maximum ZIP size of 4 MB. Folder packaging follows `project.include`, excludes build output, checks for sensitive files, and never compiles or executes your source locally. Successful server builds may automatically become active.

Practice is the default. Ranked challenges are five-game series on server-selected maps and affect rating. Every upload, activation and challenge has a confirmation gate that defaults to Cancel. If a write times out, refresh before trying again because the server may have accepted it.

## Games and local replays

Select a battle on Games, then an individual game. **View replay** downloads its official `.replay`, imports a local copy and opens the built-in player. **Download replay** only saves the file. Signed download URLs never receive your bearer key.

On **Replays (8)**, browse for `.replay`, `.replay.gz`, or [supported replay JSON](docs/replay-format.md), then click **Import replay**. Click a library row or **Watch selected** to play. Imports are content-deduplicated local copies; removal confirms deletion of that copy and leaves your original file untouched.

**Replay imports are not server uploads.** Battlecode has no replay-upload API. No API key is needed to import, decode or watch a local file.

![Built-in replay player, synthetic sample](docs/replay.svg)

Player controls:

- **Play / pause**, playback speed, First/Last and single-frame stepping.
- `Space` toggles playback; `Left`/`Right` step; `Home`/`End` seek when a viewer control is focused.
- Click or drag the timeline, or enter a frame number and click **Jump**.
- Click a dragon/tile to inspect it. Scroll the map to pan.
- Detailed mode draws kelp and portal edges; compact mode hides edges and fits smaller terminals.
- Team statistics and **Events** expose lengths, living units, splits and deaths.
- `Esc` or **Back** returns to the dashboard.

```sh
battlecode-cli replay "path/to/game.replay"        # open the player
battlecode-cli replay "path/to/game.replay" --info # JSON summary, no UI or API request
```

Playback shows **round-end states**, not an action-by-action simulator. Sonar overlays, complete action logs and portal-link annotations are not implemented. The bounded decoder supports the current 2026 binary Cap'n Proto schema, packed/unpacked and gzip formats; it needs no native replay dependency. Safety limits reject very large or unsupported files. For the full official visualization, install the VS Code replay extension with `unswbc vscode` and open your downloaded file there.

Downloads and replay copies live in your platform's user-data directory. Paths are shown in API keys. Interrupted downloads do not replace existing files.

## Claude / Codex

AI chat is **not implemented in v0.2**. [The integration proposal](docs/ai-integration.md) explains two routes: expose safe Battlecode tools to Claude Code/Codex using MCP, or add an in-app chat view using an official provider SDK/CLI. Both should start read-only, share only explicitly selected context, and retain human approval for every server mutation.

## Development

```sh
uv sync --group dev
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run battlecode-cli --demo
uv tool install --editable .
```

CI tests Python 3.11/3.13 on macOS, Linux and Windows, builds the package and verifies the installed commands. Tests use mocked HTTP, an in-memory keyring and temporary files; real network requests are prohibited. No real bots are uploaded, activated or challenged during tests.

Official [API documentation](https://game.battlecode.au/docs/api). Server permissions, build states, quotas and rate limits still apply. This is an unofficial client. MIT licensed.
