from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import sys

from filelock import Timeout as LockTimeout
from rich.console import Console
from rich.text import Text

from . import __version__
from .api import APIError, BattlecodeAPI
from .config import KEY_PATTERN, AccountStore, Credential, config_dir, importable_credential, redact
from .models import active_bot, record_label, rows, team_data, winrate
from .replays import load_replay


def parser() -> argparse.ArgumentParser:
    cli = argparse.ArgumentParser(
        prog="battlecode-cli", description="UNSW Battlecode dashboard and local benchmark arena"
    )
    cli.add_argument("--version", action="version", version=f"battlecode-cli {__version__}")
    cli.add_argument(
        "--demo", action="store_true", help="Offline dashboard with synthetic account data"
    )
    cli.add_argument(
        "--refresh",
        type=int,
        default=45,
        metavar="SECONDS",
        help="Refresh interval, minimum 15 (default: 45)",
    )
    sub = cli.add_subparsers(dest="command")
    sub.add_parser("status", help="Read-only account summary")
    auth = sub.add_parser("auth", help="Manage saved keys; only one account is connected")
    auth.add_argument(
        "action",
        choices=["set", "add", "import", "list", "use", "status", "delete", "disconnect", "clear"],
        default="status",
        nargs="?",
    )
    auth.add_argument(
        "identifier", nargs="?", help="Saved account ID (or unique prefix/label), never an API key"
    )
    auth.add_argument("--name", default="", help="A friendly label for a new key")
    auth.add_argument(
        "--save-only", action="store_true", help="Save a verified key without connecting it"
    )
    auth.add_argument(
        "--stdin", action="store_true", help="Read a new key from stdin instead of a hidden prompt"
    )
    auth.add_argument("--yes", action="store_true", help="Confirm local deletion noninteractively")
    replay = sub.add_parser("replay", help="Watch a local replay without an API key")
    replay.add_argument("file", help=".replay, .replay.gz or supported replay JSON")
    replay.add_argument(
        "--info",
        action="store_true",
        help="Print a read-only replay summary as JSON, without launching the UI",
    )
    web = sub.add_parser(
        "web", help="Open the local browser dashboard, keeping the terminal UI available"
    )
    web.add_argument(
        "--no-browser", action="store_true", help="Print the local URL without opening a browser"
    )
    web.add_argument(
        "--port", type=int, default=0, help="Loopback port (default: choose a free port)"
    )
    web.add_argument(
        "--demo",
        dest="web_demo",
        action="store_true",
        help="Synthetic account data, no server calls",
    )
    return cli


async def account_status(console: Console) -> int:
    api = None
    try:
        credential = AccountStore().active_credential()
        if not credential:
            console.print(
                "No connected account. Open battlecode-cli or run battlecode-cli auth add."
            )
            return 1
        api = BattlecodeAPI(credential)
        team_response, submissions = await asyncio.gather(api.get("/team"), api.get("/submissions"))
        team = team_data(team_response)
        active = active_bot(rows(submissions, "submissions"))
        console.print(Text(redact(str(team.get("name", "Your team"))), style="bold"))
        console.print(
            Text(
                f"Rating {team.get('elo', 'n/a')}  ·  Rank #{team_response.get('rank', 'n/a')}  ·  {record_label(team)}"
            )
        )
        console.print(
            Text(
                redact(
                    f"Active: {(active or {}).get('name', 'none')}  ·  {winrate(active or {})} wins"
                )
            )
        )
        return 0
    except (APIError, ValueError, OSError, LockTimeout) as error:
        console.print(Text(redact(str(error))))
        return 1
    finally:
        if api:
            await api.close()


async def auth_command(args: argparse.Namespace, console: Console) -> int:
    store = AccountStore()
    api = None
    token = ""
    try:
        if args.action in ("disconnect", "clear"):
            store.disconnect()
            console.print(
                "Disconnected. Saved keys remain; no environment or toolkit key will connect automatically."
            )
            return 0
        if args.action == "list":
            active = store.active_id
            for account in store.accounts():
                console.print(
                    Text(
                        f"{account.id[:8]}  {account.label}  /  {account.team_name}  /  {'ACTIVE' if account.id == active else 'saved'}  /  {account.storage}"
                    )
                )
            if not store.accounts():
                console.print("No saved keys. Run battlecode-cli auth add.")
            return 0
        if args.action in ("use", "delete"):
            if not args.identifier:
                raise ValueError(
                    "Supply a saved account ID or label from battlecode-cli auth list."
                )
            account = store.resolve(args.identifier)
            if args.action == "delete":
                if not args.yes:
                    if not sys.stdin.isatty():
                        raise ValueError("Use --yes to confirm local deletion noninteractively.")
                    answer = input(
                        f"Delete local key for {account.label}? Type delete to confirm: "
                    ).strip()
                    if answer != "delete":
                        console.print("Cancelled. No key was deleted.")
                        return 0
                store.delete(account.id)
                console.print(
                    "Local key deleted. No other key was connected. Revoke it on the team page to disable it everywhere."
                )
                return 0
            credential = store.credential(account.id)
        elif args.action in ("set", "add", "import"):
            if args.action == "import":
                credential = importable_credential()
                if not credential:
                    raise ValueError(
                        "No importable environment/toolkit key was found. Use auth add."
                    )
                token = credential.token
            else:
                token = (
                    sys.stdin.readline().strip()
                    if args.stdin
                    else getpass.getpass("Battlecode API key (hidden): ").strip()
                )
                if not KEY_PATTERN.fullmatch(token):
                    raise ValueError("Use an API key from your team page, starting with bc_.")
                credential = Credential(token, "new key")
        else:
            credential = store.active_credential()
            if not credential:
                raise ValueError(
                    "No connected account. Open battlecode-cli or run battlecode-cli auth add."
                )
        api = BattlecodeAPI(credential)
        who = await api.get("/me")
        if args.action in ("set", "add", "import"):
            account = store.add(token, args.name, who, activate=not args.save_only)
            if args.action == "import" and credential.source == str(
                config_dir() / "credentials.json"
            ):
                (config_dir() / "credentials.json").unlink(missing_ok=True)
            console.print(
                Text(f"Saved {account.label} ({account.id[:8]}), using {account.storage}.")
            )
            console.print(
                "Saved only, not connected."
                if args.save_only
                else "Connected. Every other saved key is disconnected."
            )
        elif args.action == "use":
            store.activate(account.id)
            console.print(Text(f"Connected to {account.label}. This is the only active key."))
        else:
            console.print(Text(f"Connected to {(who.get('team') or {}).get('name', 'your team')}."))
        return 0
    except (APIError, ValueError, OSError, LockTimeout) as error:
        console.print(Text(redact(str(error), token)))
        return 1
    finally:
        if api:
            await api.close()


def main() -> None:
    args = parser().parse_args()
    console = Console(highlight=False)
    try:
        if args.command == "web":
            if not 0 <= args.port <= 65535:
                console.print("Use a port between 0 and 65535. Port 0 chooses a free port.")
                raise SystemExit(1)
            from .web import serve

            try:
                serve(
                    demo=args.demo or args.web_demo,
                    port=args.port,
                    open_browser=not args.no_browser,
                    refresh=args.refresh,
                )
            except (ValueError, OSError, LockTimeout) as error:
                console.print(Text(redact(str(error))))
                raise SystemExit(1) from None
            return
        if args.command == "auth":
            raise SystemExit(asyncio.run(auth_command(args, console)))
        if args.command == "status":
            raise SystemExit(asyncio.run(account_status(console)))
        if args.command == "replay" and args.info:
            try:
                summary = load_replay(args.file).summary()
                console.print(Text(json.dumps(summary, indent=2, ensure_ascii=True)))
            except (ValueError, OSError) as error:
                console.print(Text(redact(str(error))))
                raise SystemExit(1) from None
            return
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            console.print(
                "The dashboard needs an interactive terminal. Use battlecode-cli status or replay FILE --info for text output."
            )
            raise SystemExit(1)
        from .app import BattlecodeApp

        BattlecodeApp(
            demo=args.demo,
            refresh=args.refresh,
            replay_path=args.file if args.command == "replay" else None,
        ).run(mouse=True)
    except KeyboardInterrupt:
        raise SystemExit(130) from None
