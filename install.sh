#!/bin/sh
# Installs into your user account. No sudo, aliases, or preinstalled Python required.
set -eu

ROOT=$(CDPATH='' cd "$(dirname "$0")" && pwd)
PACKAGE='https://github.com/sebastianmiletic/battlecode-cli/archive/refs/tags/v0.4.0.tar.gz'
if [ -f "$0" ] && [ -f "$ROOT/src/battlecode_cli/__init__.py" ]; then
    PACKAGE=$ROOT
fi

if command -v uv >/dev/null 2>&1; then
    UV=$(command -v uv)
else
    printf '%s\n' 'Installing uv from https://astral.sh/uv ...'
    BOOTSTRAP=$(mktemp)
    trap 'rm -f "$BOOTSTRAP"' 0
    if command -v curl >/dev/null 2>&1; then
        curl --proto '=https' --tlsv1.2 -fsSL https://astral.sh/uv/install.sh -o "$BOOTSTRAP"
    elif command -v wget >/dev/null 2>&1; then
        wget -q https://astral.sh/uv/install.sh -O "$BOOTSTRAP"
    else
        printf '%s\n' 'Install curl or wget, then run this installer again.' >&2
        exit 1
    fi
    sh "$BOOTSTRAP"
    UV="${UV_INSTALL_DIR:-$HOME/.local/bin}/uv"
    if [ ! -x "$UV" ]; then
        printf '%s\n' 'uv was not found. Open a new terminal, then rerun install.sh.' >&2
        exit 1
    fi
fi

"$UV" tool install --force --python 3.13 --from "$PACKAGE" battlecode-cli
BIN=$("$UV" tool dir --bin)
"$UV" tool update-shell
"$BIN/battlecode-cli" --version
printf '\n%s\n' 'Installed: battlecode-cli (battlecode is also available).'
case ":$PATH:" in
    *":$BIN:"*) printf '%s\n' 'Run: battlecode-cli' ;;
    *) printf '%s\n' 'Open a new terminal, then run: battlecode-cli'
       printf 'To launch immediately: "%s/battlecode-cli"\n' "$BIN" ;;
esac
