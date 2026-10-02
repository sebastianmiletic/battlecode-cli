# battlecode-cli

A quiet, monochrome control room for [UNSW Battlecode](https://game.battlecode.au). Run **`battlecode`** to see your team, rating, rank, active bot, submission records and recent games without leaving the terminal.

![Battlecode dashboard with synthetic demo data](docs/dashboard.svg)

## Install

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```sh
git clone https://github.com/sebastianmiletic/battlecode-cli.git
cd battlecode-cli
uv tool install .
battlecode
```

For an editable installation, use `uv tool install --editable .`. Run `uv tool update-shell` if your shell cannot find `battlecode`.

Already using `unswbc`? The app reads its existing key from `~/.unswbc/keys.json`. No copy or setup is needed if the key is valid.

```sh
battlecode --demo       # synthetic data, offline, no account required
battlecode auth set     # hidden prompt; checks the key before saving
battlecode auth status  # check account access without displaying the key
battlecode status       # read-only account summary
```

Make an API key on [your team page](https://game.battlecode.au/team). Generating a new key invalidates your old one. You can also connect in the dashboard's Settings page.

## In the dashboard

| Key | View / action |
| --- | --- |
| `1` | Overview: rating, rank, record, active bot, latest battles |
| `2` | Bots: all submissions, win rates, build logs and per-map records |
| `3` | Games: recent battles, game details and replay downloads |
| `4` | Ladder: searchable opponents and a shortcut to challenge them |
| `5` | Upload: choose a ZIP or bot project folder |
| `6` | Challenge: practice or ranked, optional practice maps |
| `7` | Settings: connect an account and see download locations |
| `r` | Refresh |
| `Tab` / `Shift+Tab` | Move between controls |
| Arrow keys, `Enter` | Select a table row and inspect it |
| `Ctrl+p` | Command palette |
| `?` | Help |
| `Ctrl+q` | Quit |

The dashboard refreshes every 45 seconds by default (`battlecode --refresh 60` to change it). Data stays visible if a refresh fails, and the status line tells you when it is stale. Authentication failures pause polling until you reconnect. Network work runs asynchronously so navigation stays responsive.

### Bots and win rates

Win rate is **wins / (wins + draws + losses)**, using the server's submission records. Draws are included in the denominator, not counted as half a win. A bot with no completed games shows `n/a`. Build details include per-map records when the server supplies them.

Activation is supported through the official API. Select a built inactive bot and choose **Activate**, then confirm the replacement. Uploading a new version may automatically make it active after a successful server build. Uploads do not compile or execute source code locally. ZIPs must have `bot.toml` at the root and be at most 4 MB; folder uploads follow `project.include` and exclude the build output. Archives are checked for traversal, symlinks, duplicate entries, corruption and sensitive filenames.

### Games and replays

Practice is the default. Ranked challenges are five-game series on server-selected maps and affect your rating. The app shows an explicit confirmation before every challenge, upload or activation. It never retries a write automatically. If a write times out, refresh before retrying because the server may already have accepted it.

Select a battle, then a game, to download its `.replay`. **Open replay** downloads it and launches VS Code when `code` is available (install the UNSW Battlecode replay viewer using `unswbc vscode`). Otherwise it opens the local file with your OS. You can also open a battle on the website. Submission ZIPs can be downloaded from the Bots page.

Downloads are saved in your platform's user-data directory under `battlecode-cli/downloads`, shown in Settings. A fresh file is written atomically; incomplete downloads are removed. Bearer credentials are **never** forwarded to signed download URLs.

## Credentials and safety

Credential priority:

1. `BATTLECODE_API_KEY`
2. `UNSWBC_KEY`
3. This app's platform-specific config directory, `battlecode-cli/credentials.json`
4. `~/.unswbc/keys.json`

Keys entered into this app are verified with `GET /me`, then saved outside the repository with owner-only permissions. Settings can forget this app's key without modifying the toolkit's credentials. Environment variables still take precedence. Keys never appear in the UI, screenshots or logs.

Demo mode is read-only and never connects to the API. Account settings, team membership and tournaments are managed on the website. The dashboard cannot bypass server permissions, build states, quotas or rate limits.

Official [API documentation](https://game.battlecode.au/docs/api): 120 requests/minute per key, 30/minute for leaderboard/ratings, 12 uploads/hour and 60 challenge games/hour (with a separate dev-team quota).

## Development

```sh
uv sync --group dev
uv run pytest
uv run ruff check .
uv run battlecode --demo
```

Tests use mocked HTTP and Textual's headless pilot. They do not upload bots, switch your active submission or request real battles.

MIT licensed.
