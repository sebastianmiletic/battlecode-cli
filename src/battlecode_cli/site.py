"""Website navigation, bounded public HTML reads, and documented JSON adapters.

HTML is parsed as inert text: scripts, forms, trackers, styles and credentials are not run.
The saved reference is a design input, never a source of fake live account records.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from html.parser import HTMLParser
from importlib.resources import files
from urllib.parse import urljoin, urlsplit

import httpx

from . import __version__
from .config import SERVER, redact
from .models import date_label, rows, team_data
from .replays import clean_label

NAVIGATION = [
    ("", [("overview", "Overview", "⊞"), ("updates", "Updates", "▤")]),
    (
        "Compete",
        [
            ("leaderboard", "Leaderboard", "▥"),
            ("ratings", "Ratings", "↗"),
            ("battles", "Battles", "⚔"),
            ("games", "Games", "⊞"),
            ("tournaments", "Tournaments", "♜"),
        ],
    ),
    (
        "Build",
        [
            ("bots", "Submissions", "◇"),
            ("arena", "Arena", "⚔"),
            ("documentation", "Documentation", "▤"),
            ("visualiser", "Visualiser", "▷"),
            ("map-editor", "Map editor", "▧"),
        ],
    ),
    (
        "Manage",
        [
            ("team", "Team", "♙"),
            ("my-battles", "Your battles", "↶"),
            ("my-games", "Your games", "⊞"),
            ("teams", "Find a team", "⌕"),
        ],
    ),
]
PAGES = [(ident, label) for _, items in NAVIGATION for ident, label, _ in items] + [
    ("settings", "Account / API keys")
]
PAGE_URLS = {
    "overview": "/",
    "updates": "/updates",
    "leaderboard": "/leaderboard",
    "ratings": "/ratings",
    "battles": "/battles",
    "games": "/games",
    "tournaments": "/tournaments",
    "bots": "/submissions",
    "documentation": "/docs",
    "visualiser": "/visualiser",
    "map-editor": "/map-editor",
    "team": "/team",
    "my-battles": "/battles?mine=1",
    "my-games": "/games?mine=1",
    "teams": "/teams",
    "settings": "/profile",
}
DOCS = [
    (
        "Intro",
        [
            ("overview", "Overview"),
            ("quickstart", "Quickstart"),
            ("submitting", "Submitting via Website"),
        ],
    ),
    (
        "Game Rules",
        [
            ("structure", "Structure"),
            ("map-info", "Game Map"),
            ("pearls", "Pearls"),
            ("kelp-and-portals", "Kelp and Portals"),
            ("vision", "Vision"),
            ("movement", "Movement"),
            ("splitting", "Splitting"),
            ("sonar", "Sonar"),
            ("death", "Death"),
        ],
    ),
    ("Competing", [("game-format", "Game Format"), ("elo", "ELO System")]),
    (
        "Advanced",
        [
            ("cli", "CLI"),
            ("execution-order", "Execution Order"),
            ("timeouts", "Timeouts"),
            ("libraries", "Standard Library"),
            ("helper", "Helper Reference"),
            ("protocol", "IO Protocol"),
            ("protocol-upgrade", "Protocol Upgrade"),
            ("map-files", "Map Files"),
            ("api", "API"),
        ],
    ),
]
DOC_SLUGS = {slug for _, items in DOCS for slug, _ in items}
MAX_HTML = 2 * 1024 * 1024


def safe_url(value: str) -> str | None:
    url = urljoin(SERVER + "/", value)
    parsed = urlsplit(url)
    return (
        url
        if parsed.scheme == "https"
        and parsed.netloc == urlsplit(SERVER).netloc
        and not parsed.username
        and redact(url) == url
        and not any(ord(character) < 32 for character in url)
        else None
    )


@dataclass
class WebRow:
    cells: list[str] = field(default_factory=list)
    links: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class WebPage:
    title: str
    text: str
    rows: list[WebRow]
    links: list[tuple[str, str]]
    loaded_at: float = 0


class PageParser(HTMLParser):
    """Extract the main content only, preserving headings, lists, code and table rows."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.main = self.skip = self.pre = 0
        self.parts, self.rows, self.links = [], [], []
        self.row = None
        self.cell = None
        self.anchor = None
        self.heading = None
        self.title = ""
        self.time_value = None
        self.time_parts = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "main":
            self.main += 1
        if tag in ("script", "style", "noscript", "template"):
            self.skip += 1
        if not self.main or self.skip:
            return
        if tag in ("h1", "h2", "h3", "h4"):
            self.parts.append("\n\n" + "#" * int(tag[1]) + " ")
            if tag == "h1":
                self.heading = []
        elif tag in ("p", "section", "article", "blockquote"):
            self.parts.append("\n\n")
        elif tag == "li":
            self.parts.append("\n• ")
        elif tag in ("br", "tr"):
            self.parts.append("\n")
        elif tag == "pre":
            self.pre += 1
            self.parts.append("\n\n```\n")
        if tag == "tr":
            self.row = WebRow()
        if tag in ("td", "th") and self.row is not None:
            self.cell = []
        if tag == "time":
            self.time_value = attributes.get("datetime")
            self.time_parts = []
        if tag == "a" and safe_url(attributes.get("href", "")):
            self.anchor = (safe_url(attributes.get("href", "")), [])

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "template"):
            self.skip = max(0, self.skip - 1)
        if not self.skip and self.main:
            if tag == "a" and self.anchor:
                url, parts = self.anchor
                label = clean_label(" ".join(parts), 200)
                if label:
                    self.links.append((label, url))
                    if self.row is not None:
                        self.row.links.append((label, url))
                    self.parts.append(f"[↗]({url}) ")
                self.anchor = None
            if tag == "time" and self.time_value:
                if not any(part.strip() for part in self.time_parts):
                    label = date_label(self.time_value)
                    self.parts.append(label + " ")
                    if self.cell is not None:
                        self.cell.append(label)
                self.time_value = None
            if tag in ("td", "th") and self.cell is not None:
                self.row.cells.append(clean_label(" ".join(self.cell), 300))
                self.cell = None
                self.parts.append(" | ")
            if tag == "tr" and self.row is not None:
                if self.row.cells:
                    self.rows.append(self.row)
                self.row = None
            if tag == "h1" and self.heading is not None:
                self.title = clean_label(" ".join(self.heading), 120)
                self.heading = None
            if tag in ("h1", "h2", "h3", "h4"):
                self.parts.append("\n")
            if tag == "pre":
                self.pre = max(0, self.pre - 1)
                self.parts.append("\n```\n")
        if tag == "main":
            self.main = max(0, self.main - 1)

    def handle_data(self, text):
        if not self.main or self.skip:
            return
        safe = redact(re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text))
        if self.time_value:
            self.time_parts.append(safe)
        if not self.pre:
            safe = " ".join(safe.split())
            if safe:
                safe += " "
        self.parts.append(safe)
        if self.cell is not None:
            self.cell.append(safe)
        if self.anchor:
            self.anchor[1].append(safe)
        if self.heading is not None:
            self.heading.append(safe)

    def result(self) -> WebPage:
        text = re.sub(r"\n[ \t]+", "\n", "".join(self.parts))
        text = re.sub(r"\n{4,}", "\n\n\n", text).strip()
        return WebPage(
            self.title, text, self.rows[:200], list(dict.fromkeys(self.links))[:300], time.time()
        )


def parse_page(raw: str) -> WebPage:
    if len(raw.encode()) > MAX_HTML:
        raise ValueError("Website page exceeds the 2 MB display limit.")
    parser = PageParser()
    parser.feed(raw)
    result = parser.result()
    if not result.text:
        raise ValueError("This website page did not include readable server-rendered content.")
    return result


class WebsiteClient:
    """Public read-only client. Never receives credentials or runs saved-site JavaScript."""

    def __init__(self, transport=None):
        self.client = httpx.AsyncClient(
            timeout=20,
            follow_redirects=False,
            transport=transport,
            headers={"User-Agent": f"battlecode-cli/{__version__}"},
        )
        self.cache = {}

    async def close(self):
        await self.client.aclose()

    async def page(self, path: str, *, force=False) -> WebPage:
        if not path.startswith("/") or path.startswith("//") or safe_url(path) is None:
            raise ValueError("Choose a Battlecode website page.")
        cached = self.cache.get(path)
        if cached and not force and time.time() - cached.loaded_at < 45:
            return cached
        try:
            async with self.client.stream("GET", SERVER + path) as response:
                if response.status_code != 200:
                    raise ValueError(
                        f"Website returned HTTP {response.status_code}. Open it on the website if login is required."
                    )
                raw = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(raw) + len(chunk) > MAX_HTML:
                        raise ValueError("Website page exceeds the 2 MB display limit.")
                    raw.extend(chunk)
            result = parse_page(raw.decode("utf-8", "replace"))
            self.cache[path] = result
            while len(self.cache) > 64:
                self.cache.pop(next(iter(self.cache)))
            return result
        except httpx.HTTPError as error:
            raise ValueError(
                "The public website could not be reached. Cached/offline pages are still available."
            ) from error


def bundled_doc(slug: str) -> WebPage:
    if slug not in DOC_SLUGS:
        raise ValueError("Choose a documentation topic.")
    resource = files("battlecode_cli").joinpath(f"assets/guide/{slug}.json")
    if not resource.is_file():
        from .guide import document

        title = next(label for _, topics in DOCS for topic, label in topics if topic == slug)
        return WebPage(title, document(slug, title), [], [])
    data = json.loads(resource.read_bytes())
    return WebPage(
        data["title"],
        data["text"],
        [],
        [(label, url) for label, url in data.get("links", []) if safe_url(url)],
    )


def directory_rows(value) -> list[dict]:
    return [
        {**team_data(row), **{key: item for key, item in row.items() if key != "team"}}
        for row in rows(value, "teams", "leaderboard")
    ]


def named_record(value: dict) -> str:
    if not isinstance(value, dict):
        return "n/a"
    source = value.get("record", value)
    if not isinstance(source, dict) or not all(
        key in source for key in ("wins", "draws", "losses")
    ):
        return "n/a"
    return " / ".join(str(source[key]) for key in ("wins", "draws", "losses"))


def tournament_rows(value) -> list[dict]:
    return rows(value, "tournaments", "events")


def public_matches(page: WebPage, kind: str) -> list[dict]:
    result = []
    for row in page.rows:
        # The site's Games table also links Watch to /battles/:id, not /games/:id.
        routes = "(?:games|battles)" if kind == "games" and len(row.cells) >= 7 else kind
        match = next(
            (
                found
                for _, url in row.links
                if (found := re.search(rf"/{routes}/([0-9]+)(?:[/?#]|$)", url))
            ),
            None,
        )
        if match:
            result.append({"id": int(match[1]), "cells": row.cells, "links": row.links})
    return result
