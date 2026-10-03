import asyncio
import hashlib
import json
import struct
import sys
import time
import urllib.error
import urllib.request
from importlib.resources import files
from pathlib import Path

import httpx
import pytest

from battlecode_cli import cli, web
from battlecode_cli.api import APIError, BattlecodeAPI
from battlecode_cli.arena import ArenaRunner
from battlecode_cli.config import AccountStore
from battlecode_cli.web import Dashboard, LocalServer


def project(root, name):
    folder = root / name
    folder.mkdir()
    (folder / "bot.toml").write_text('[project]\nlanguage="python"\ninclude=["*.py"]\n')
    (folder / "main.py").write_text("raise RuntimeError('never execute source on host')\n")
    return str(folder)


@pytest.fixture
async def dashboard():
    backend = Dashboard(demo=True)
    yield backend
    await backend.close()


async def test_offline_browser_ships_all_official_maps_and_unchanged_image(dashboard):
    state = await dashboard.state()
    assert state["demo"]
    assert state["team"]["name"] == "Night shift"
    assert len(state["maps"]) == 15
    assets = files("battlecode_cli").joinpath("assets/maps")
    expected = {
        hashlib.sha256(entry.read_bytes()).hexdigest()[:24]
        for entry in assets.iterdir()
        if entry.name.endswith(".map")
    }
    assert {entry["id"] for entry in state["maps"]} == expected
    assert all(entry["origins"] == ["official"] for entry in state["maps"])
    logo = web.ASSETS.joinpath("brand.png").read_bytes()
    assert (
        hashlib.sha256(logo).hexdigest()
        == "3429b25388ab80e940226d6447260a368e0212ba630d87a079e56a0fb4b38940"
    )
    assert logo[:8] == b"\x89PNG\r\n\x1a\n"
    assert struct.unpack(">II", logo[16:24]) == (292, 88)
    assert b"See more of my projects" in web.ASSETS.joinpath("index.html").read_bytes()


async def test_builtin_map_import_restores_missing_files_without_duplicates(dashboard):
    entry = dashboard.maps.entries()[0]
    dashboard.maps.path(entry["id"]).unlink()
    result = dashboard.maps.ensure_official()
    assert len(dashboard.maps.entries()) == 15
    assert result["added"] == 1
    assert result["duplicates"] == 14


async def test_simulation_is_frozen_reviewed_and_saved_with_playback(
    dashboard, tmp_path, monkeypatch
):
    monkeypatch.setattr(web, "runner_path", lambda: "fixture")
    sample = files("battlecode_cli").joinpath("assets/sample.replay.json")
    fake = tmp_path / "runner.py"
    fixture = tmp_path / "sample.json"
    fixture.write_bytes(sample.read_bytes())
    fake.write_text(
        "import pathlib, sys\n"
        + f"pathlib.Path(sys.argv[sys.argv.index('-o')+1]).write_bytes(pathlib.Path({str(fixture)!r}).read_bytes())\n"
    )
    monkeypatch.setattr(
        web,
        "ArenaRunner",
        lambda store, maps, **kwargs: ArenaRunner(
            store, maps, [sys.executable, str(fake)], **kwargs
        ),
    )
    sources = [project(tmp_path, name) for name in ("Alpha", "Beta")]
    body = {
        "kind": "simulation",
        "a": sources[0],
        "b": sources[1],
        "maps": [dashboard.maps.entries()[0]["id"]],
        "seed": "18446744073709551615",
        "repeats": 2,
    }
    proposal = await dashboard.review(body)
    assert "4 games" in proposal["text"]
    assert dashboard.run_task is None
    # User edits the original after reviewing; only the reviewed source is executed.
    Path(sources[0], "main.py").write_text("changed after review\n")
    assert await dashboard.approve(proposal["approval"]) == {"started": True}
    await asyncio.wait_for(dashboard.run_task, 10)
    job = dashboard.current
    assert job["status"] == "complete"
    assert job["mode"] == "Simulation"
    assert len(job["games"]) == 4
    assert all(game["mode"] == "Simulation" and game["library_id"] for game in job["games"])
    assert dashboard.library.entries()[0]["mode"] == "Simulation"
    assert "never execute" in (dashboard.runs.path(job["id"]) / "bot-0" / "main.py").read_text()
    assert job["games"][0]["seed"] == 2**64 - 1
    assert job["games"][2]["seed"] == 0
    replay = await dashboard.replay(f"run:{job['id']}:0", 2, 2)
    assert replay["offset"] == 2 and len(replay["frames"]) == 2
    assert replay["map"]["width"] == 12
    assert replay["summary"]["frames"] == 7
    assert "dragons" in replay["frames"][0]
    page = await dashboard.dispatch("GET", "/api/simulations?offset=1&limit=2")
    assert page["total"] == 4 and len(page["games"]) == 2
    assert all(game["mode"] == "Simulation" for game in page["games"])
    with pytest.raises(ValueError, match="already used"):
        await dashboard.approve(proposal["approval"])


async def test_maps_review_copies_only_reviewed_content_and_reports_invalid(dashboard, tmp_path):
    source = tmp_path / "custom"
    source.mkdir()
    raw = b"MAP 6 4\nMAP_NAME Custom fixture\nDRAGON 0 2 1 1 0 1\nDRAGON 1 2 4 2 5 2\n"
    (source / "good.map").write_bytes(raw)
    (source / "bad.map").write_text("not a valid map")
    proposal = await dashboard.review({"kind": "maps", "path": str(source)})
    assert "1 valid maps" in proposal["text"] and "1 rejected" in proposal["text"]
    assert len(dashboard.maps.entries()) == 15
    (source / "good.map").write_text("changed since review")
    result = await dashboard.approve(proposal["approval"])
    assert result["added"] == 1 and len(result["rejected"]) == 1
    ident = hashlib.sha256(raw).hexdigest()[:24]
    assert dashboard.maps.path(ident).read_bytes() == raw


async def test_approvals_expire_and_new_reviews_invalidate_previous(dashboard):
    first = await dashboard.review({"kind": "remove-replay", "id": "a" * 64})
    second = await dashboard.review({"kind": "remove-replay", "id": "b" * 64})
    with pytest.raises(ValueError, match="expired"):
        await dashboard.approve(first["approval"])
    dashboard.approvals[second["approval"]].expires = time.monotonic() - 1
    with pytest.raises(ValueError, match="expired"):
        await dashboard.approve(second["approval"])


async def test_demo_blocks_online_mutations_and_credentials(dashboard):
    for body in (
        {"kind": "activate", "id": 104},
        {"kind": "upload"},
        {"kind": "challenge", "team": 1},
        {"kind": "disconnect"},
    ):
        with pytest.raises(ValueError):
            await dashboard.review(body)
    with pytest.raises(ValueError):
        await dashboard.connect({"key": "bc_fixture"})


async def test_external_account_changes_clear_data_and_invalidate_writes(monkeypatch):
    store = AccountStore()
    one = store.add("bc_browser_one", "One", {"team": {"name": "One"}}, activate=True)
    two = store.add("bc_browser_two", "Two", {"team": {"name": "Two"}}, activate=False)
    called = []
    api = BattlecodeAPI(
        store.credential(one.id),
        httpx.MockTransport(
            lambda request: (called.append(request), httpx.Response(200, json={}))[1]
        ),
    )
    backend = Dashboard(api=api)
    backend.custom_api = False
    backend.submissions = [{"id": 1, "name": "Own"}]
    proposal = await backend.review({"kind": "activate", "id": 1})
    store.activate(two.id)
    with pytest.raises(APIError, match="Account changed"):
        await backend.approve(proposal["approval"])
    assert backend.submissions == []
    assert backend.api.credential is None
    assert called == []
    assert store.active_id == two.id  # no silent switch in this dashboard
    await backend.close()


async def test_connect_verifies_then_save_only_disconnect_and_delete(monkeypatch):
    requests = []

    def handler(request):
        requests.append(request)
        assert request.method == "GET" and request.url.path.endswith("/me")
        if "rejected" in request.headers["authorization"]:
            return httpx.Response(401, json={"error": "invalid"})
        return httpx.Response(
            200, json={"team": {"name": "Verified"}, "user": {"username": "fixture"}}
        )

    monkeypatch.setattr(
        web,
        "BattlecodeAPI",
        lambda credential: BattlecodeAPI(credential, httpx.MockTransport(handler)),
    )
    backend = Dashboard()
    with pytest.raises(APIError):
        await backend.connect({"key": "bc_rejected"})
    assert backend.accounts.accounts() == []
    await backend.connect({"key": "bc_verified", "activate": False})
    assert backend.accounts.active_id is None and backend.api.credential is None
    profile = backend.accounts.accounts()[0]
    review = await backend.review({"kind": "use-account", "id": profile.id})
    await backend.approve(review["approval"])
    assert backend.accounts.active_id == profile.id
    review = await backend.review({"kind": "disconnect"})
    await backend.approve(review["approval"])
    assert backend.accounts.active_id is None and backend.api.credential is None
    review = await backend.review({"kind": "delete-account", "id": profile.id})
    await backend.approve(review["approval"])
    assert backend.accounts.accounts() == []
    assert all(request.method == "GET" for request in requests)
    await backend.close()


async def test_file_staging_names_and_replay_ranges_are_bounded(dashboard):
    upload = await dashboard.stage_file("bot", "../../A B.zip", b"fixture")
    assert Path(upload["path"]).parent.parent == dashboard.staging
    assert Path(upload["path"]).name == "A B.zip"
    with pytest.raises(ValueError):
        await dashboard.stage_file("bot", "secret.txt", b"fixture")
    with pytest.raises(ValueError):
        await dashboard.stage_file("bot", "large.zip", b"x" * (4 * 1024 * 1024 + 1))
    for ident in (
        "run:../outside:0",
        "library:../secret",
        "/private/key",
        "run:" + "a" * 32 + ":../../x",
    ):
        with pytest.raises(ValueError):
            dashboard.replay_path(ident)
    with pytest.raises(ValueError):
        await dashboard.replay("sample", -1)
    with pytest.raises(ValueError):
        await dashboard.replay("sample", 0, 81)


async def test_exports_confirm_snapshot_and_escape_csv_formulas(dashboard):
    job = {
        "id": "a" * 32,
        "status": "complete",
        "created_at": "2026-10-02T00:00:00Z",
        "total": 1,
        "seed": 0,
        "bots": [{"label": "+Alpha"}, {"label": "Beta"}],
        "maps": [],
        "games": [{"index": 0, "map": "=FORMULA", "a": 0, "b": 1, "seed": 0, "error": "failed"}],
    }
    dashboard.runs.save(job)
    proposal = await dashboard.review({"kind": "export", "id": job["id"], "format": "csv"})
    assert "1 saved games" in proposal["text"]
    result = await dashboard.approve(proposal["approval"])
    assert "'=FORMULA" in result["content"] and "'+Alpha" in result["content"]
    assert ",error," in result["content"]
    assert result["filename"].endswith(".csv")


@pytest.fixture
def local_server():
    server = LocalServer(Dashboard(demo=True))
    server.start()
    yield server
    server.close()


def request(server, path, *, headers=None, data=None):
    req = urllib.request.Request(server.url + path, headers=headers or {}, data=data)
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return response.status, response.headers, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.headers, error.read()


def session(server):
    status, headers, _ = request(server, "/")
    assert status == 200
    cookie = headers["Set-Cookie"].split(";")[0]
    _, _, raw = request(server, "/api/state", headers={"Cookie": cookie})
    return {
        "Cookie": cookie,
        "Origin": server.url,
        "X-Battlecode-CSRF": json.loads(raw)["csrf"],
        "Content-Type": "application/json",
    }


def test_loopback_sessions_csrf_origins_hosts_and_assets(local_server):
    server = local_server
    assert server.http.server_address[0] == "127.0.0.1"
    status, headers, _ = request(server, "/")
    assert status == 200
    assert "HttpOnly" in headers["Set-Cookie"] and "SameSite=Strict" in headers["Set-Cookie"]
    assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert request(server, "/api/state")[0] == 403
    assert request(server, "/", headers={"Host": "attacker.example"})[0] == 403
    for path in (
        "/assets/../../config.py",
        "/assets/brand.png/../index.html",
        "/assets/web.py",
        "/favicon.ico",
    ):
        assert request(server, path)[0] == 404
    assert request(server, "/assets/brand.png")[2] == web.ASSETS.joinpath("brand.png").read_bytes()
    headers = session(server)
    assert request(server, "/api/state", headers=headers)[0] == 200
    data = json.dumps({"kind": "remove-replay", "id": "a" * 64}).encode()
    assert (
        request(
            server,
            "/api/review",
            headers={**headers, "Origin": "https://attacker.example"},
            data=data,
        )[0]
        == 403
    )
    assert (
        request(
            server, "/api/review", headers={**headers, "X-Battlecode-CSRF": "wrong"}, data=data
        )[0]
        == 403
    )
    assert (
        request(
            server,
            "/api/review",
            headers={key: value for key, value in headers.items() if key != "Origin"},
            data=data,
        )[0]
        == 403
    )
    status, _, body = request(server, "/api/review", headers=headers, data=data)
    assert status == 200 and "approval" in json.loads(body)


def test_http_binary_import_secret_redaction_and_json_rejection(local_server):
    headers = session(local_server)
    data = files("battlecode_cli").joinpath("assets/sample.replay.json").read_bytes()
    status, _, raw = request(
        local_server,
        "/api/file?kind=replay",
        headers={
            **headers,
            "Content-Type": "application/octet-stream",
            "X-File-Name": "sample.replay",
        },
        data=data,
    )
    assert status == 200
    path = json.loads(raw)["path"]
    status, _, imported = request(
        local_server,
        "/api/import-replay",
        headers=headers,
        data=json.dumps({"path": path}).encode(),
    )
    assert status == 200
    ident = json.loads(imported)["entry"]["id"]
    assert request(local_server, f"/api/replay?id=library:{ident}", headers=headers)[0] == 200
    local_server.backend.error = "bc_secret_should_not_leave_python"
    _, _, raw = request(local_server, "/api/state", headers=headers)
    assert b"bc_secret_should_not_leave_python" not in raw
    assert request(local_server, "/api/review", headers=headers, data=b"[]")[0] == 400
    assert (
        request(
            local_server,
            "/api/review",
            headers={**headers, "Content-Type": "text/plain"},
            data=b"{}",
        )[0]
        == 400
    )
    assert request(local_server, "/api/review", headers=headers, data=b"x" * 65537)[0] == 413


def test_web_command_works_without_an_interactive_terminal(monkeypatch):
    called = []
    monkeypatch.setattr(web, "serve", lambda **kwargs: called.append(kwargs))
    monkeypatch.setattr(
        cli.sys, "argv", ["battlecode-cli", "web", "--demo", "--no-browser", "--port", "8765"]
    )
    cli.main()
    assert called == [{"demo": True, "open_browser": False, "port": 8765, "refresh": 45}]
    monkeypatch.setattr(cli.sys, "argv", ["battlecode-cli", "web", "--port", "70000"])
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 1


async def test_only_own_sources_download_and_unranked_challenges_are_single_use(tmp_path):
    import io
    import zipfile

    raw = io.BytesIO()
    with zipfile.ZipFile(raw, "w") as archive:
        archive.writestr("bot.toml", '[project]\nlanguage="python"\ninclude=["*.py"]\n')
        archive.writestr("main.py", "raise RuntimeError('never execute on host')\n")
    store = AccountStore()
    profile = store.add("bc_own_source_fixture", "Own", {"team": {"name": "Own"}}, activate=True)
    requests = []

    def handler(request):
        requests.append(request)
        if request.method == "GET":
            assert request.url.path.endswith("/submissions/1/download")
            return httpx.Response(200, content=raw.getvalue())
        assert request.url.path.endswith("/battles")
        return httpx.Response(200, json={"id": 10})

    api = BattlecodeAPI(store.credential(profile.id), httpx.MockTransport(handler))
    backend = Dashboard(api=api)
    backend.submissions = [{"id": 1, "name": "My version"}]
    own = await backend.choose_bot({"submission": 1})
    assert own.label == "My version" and own.prepared.language == "python"
    with pytest.raises(ValueError, match="Only your own"):
        await backend.choose_bot({"submission": 999})
    proposal = await backend.review({"kind": "challenge", "team": 42})
    assert len(requests) == 1 and requests[0].method == "GET"
    await backend.approve(proposal["approval"])
    assert json.loads(requests[-1].content) == {"teamId": 42, "ranked": False}
    with pytest.raises(ValueError):
        await backend.approve(proposal["approval"])
    assert len(requests) == 2
    await backend.close()


async def test_corrupt_replay_metadata_does_not_block_the_rest_of_the_dashboard(dashboard):
    dashboard.library.root.mkdir(parents=True)
    dashboard.library.index.write_text("not JSON")
    state = await dashboard.state()
    assert "unreadable" in state["error"]
    assert len(state["maps"]) == 15
    assert state["replays"] == [] and state["team"]["name"] == "Night shift"
