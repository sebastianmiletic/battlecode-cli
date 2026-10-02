from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import sys

from rich.console import Console
from rich.text import Text

from . import __version__
from .api import APIError, BattlecodeAPI
from .config import Credential, clear_credential, config_dir, load_credential, save_credential
from .models import active_bot, record_label, rows, team_data, winrate


def parser() -> argparse.ArgumentParser:
    cli = argparse.ArgumentParser(
        prog="battlecode", description="UNSW Battlecode terminal control room"
    )
    cli.add_argument("--version", action="version", version=f"battlecode-cli {__version__}")
    cli.add_argument(
        "--demo", action="store_true", help="Offline, read-only dashboard with synthetic data"
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
    auth = sub.add_parser("auth", help="Connect or inspect credentials")
    auth.add_argument("action", choices=["set", "status", "clear"], default="status", nargs="?")
    auth.add_argument(
        "--stdin", action="store_true", help="Read key from stdin instead of a hidden prompt"
    )
    return cli


async def account_status(console: Console) -> int:
    api = BattlecodeAPI(load_credential())
    try:
        team_response, submissions = await asyncio.gather(api.get("/team"), api.get("/submissions"))
        team = team_data(team_response)
        active = active_bot(rows(submissions, "submissions"))
        console.print(Text(str(team.get("name", "Your team")), style="bold"))
        console.print(
            Text(
                f"Rating {team.get('elo', 'n/a')}  ·  Rank #{team_response.get('rank', 'n/a')}  ·  {record_label(team)}"
            )
        )
        console.print(
            Text(f"Active: {(active or {}).get('name', 'none')}  ·  {winrate(active or {})} wins")
        )
        return 0
    except APIError as error:
        console.print(Text(str(error)))
        return 1
    finally:
        await api.close()


async def auth_command(args: argparse.Namespace, console: Console) -> int:
    if args.action == "clear":
        clear_credential()
        console.print("App key removed. Environment and unswbc keys are unchanged.")
        return 0
    credential = load_credential()
    if args.action == "set":
        override = next(
            (k for k in ("BATTLECODE_API_KEY", "UNSWBC_KEY") if os.environ.get(k)), None
        )
        if override:
            console.print(
                f"Unset {override} before saving a different key. It overrides saved keys."
            )
            return 1
        token = (
            sys.stdin.readline().strip()
            if args.stdin
            else getpass.getpass("Battlecode API key (hidden): ").strip()
        )
        credential = Credential(token, "new key")
    elif not credential:
        console.print("No key found. Run battlecode auth set or connect in Settings.")
        return 1
    api = BattlecodeAPI(credential)
    try:
        who = await api.get("/me")
        if args.action == "set":
            save_credential(credential.token)
            console.print(
                Text(f"Key verified and saved in {config_dir()} with owner-only permissions.")
            )
        else:
            console.print(Text(f"Key source: {credential.source}"))
        console.print(Text(f"Connected to {(who.get('team') or {}).get('name', 'your team')}."))
        return 0
    except (APIError, ValueError, OSError) as error:
        from .config import redact

        console.print(Text(redact(str(error), credential.token)))
        return 1
    finally:
        await api.close()


def main() -> None:
    args = parser().parse_args()
    console = Console(highlight=False)
    try:
        if args.command == "auth":
            raise SystemExit(asyncio.run(auth_command(args, console)))
        if args.command == "status":
            raise SystemExit(asyncio.run(account_status(console)))
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            console.print(
                "The dashboard needs an interactive terminal. Use battlecode status for text output."
            )
            raise SystemExit(1)
        from .app import BattlecodeApp

        BattlecodeApp(demo=args.demo, refresh=args.refresh).run()
    except KeyboardInterrupt:
        raise SystemExit(130) from None
