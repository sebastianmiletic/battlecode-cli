import asyncio
import json
import stat
import sys
import zipfile
from importlib.resources import files

import pytest

from battlecode_cli.arena import (
    ArenaError,
    ArenaPlan,
    ArenaRunner,
    ArenaStore,
    MapLibrary,
    aggregates,
    bot_version,
    opponent_versions,
    report,
    run_process,
    runner_environment,
    snapshot_bot,
)
from battlecode_cli.replays import decode_replay


def map_text(name="Fixture"):
    return f"MAP 6 4\nMAP_NAME {name}\nDRAGON 0 2 1 1 0 1\nDRAGON 1 2 4 2 5 2\n".encode()


def project(root, name):
    path = root / name
    path.mkdir()
    (path / "bot.toml").write_text(
        '[project]\nlanguage="python"\ninclude=["*.py"]\noutput="../../escape"\n'
    )
    (path / "main.py").write_text("raise RuntimeError('never execute on host')\n")
    return bot_version(str(path))


def test_import_hundreds_of_custom_maps_dedup_and_preserve_originals(tmp_path):
    source = tmp_path / "350-maps.zip"
    with zipfile.ZipFile(source, "w") as archive:
        for index in range(350):
            archive.writestr(f"nested/map-{index}.txt", map_text(f"Custom {index}"))
    library = MapLibrary(tmp_path / "library")
    result = library.import_path(str(source))
    assert result == {"added": 350, "duplicates": 0, "rejected": []}
    assert len(library.entries()) == 350
    assert all(entry["origins"] == ["custom"] for entry in library.entries())
    assert source.is_file()
    assert library.import_path(str(source))["duplicates"] == 350
    first = library.entries()[0]
    library.path(first["id"]).unlink()
    library.import_path(str(source))
    assert len(library.entries()) == 350
    assert library.path(first["id"]).is_file()


@pytest.mark.parametrize("name", ["../outside.map", "/absolute.map", "dir\\map.map", "C:map.map"])
def test_map_zip_rejects_unsafe_paths(tmp_path, name):
    source = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr(name.replace("\\", "/"), map_text())
    # Windows normalizes names while writing ZIPs; preserve the malicious on-disk spelling.
    if "\\" in name:
        source.write_bytes(
            source.read_bytes().replace(name.replace("\\", "/").encode(), name.encode())
        )
    with pytest.raises(ArenaError, match="unsafe"):
        MapLibrary(tmp_path / "library").import_path(str(source))
    assert not (tmp_path / "library").exists()


def test_map_zip_rejects_symlinks_and_bounded_sizes(tmp_path, monkeypatch):
    import battlecode_cli.arena as arena

    source = tmp_path / "linked.zip"
    info = zipfile.ZipInfo("link.map")
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr(info, "outside.map")
    library = MapLibrary(tmp_path / "library")
    with pytest.raises(ArenaError, match="linked"):
        library.import_path(str(source))
    monkeypatch.setattr(arena, "MAX_MAP_BYTES", 10)
    with pytest.raises(ArenaError, match="1 MB"):
        library.import_blobs([("large.map", map_text())])


def test_map_import_rejects_bad_geometry_and_missing_team_without_losing_valid_maps(tmp_path):
    library = MapLibrary(tmp_path)
    result = library.import_blobs(
        [
            ("valid.map", map_text()),
            ("bad.map", b"MAP 99999 9"),
            ("one-team.map", b"MAP 6 4\nDRAGON 0 2 1 1 0 1"),
        ]
    )
    assert result["added"] == 1
    assert len(result["rejected"]) == 2
    library.import_blobs([("official.map", map_text())], origin="official")
    assert library.entries()[0]["origins"] == ["custom", "official"]
    with pytest.raises(ArenaError):
        library.path("../../outside")


def test_batch_matrix_uses_repeats_both_seats_and_shared_opponents(tmp_path):
    bots = tuple(project(tmp_path, name) for name in ("A", "B", "Opponent"))
    plan = ArenaPlan(bots, ({"id": "1"}, {"id": "2"}), repeats=3)
    plan.validate()
    assert plan.pairs == [(0, 1), (0, 2), (1, 2)]
    assert plan.count == 36
    with pytest.raises(ArenaError, match="10,000"):
        ArenaPlan(bots, ({"id": "x"},) * 1000, repeats=10).validate()
    with pytest.raises(ArenaError, match="at least one map"):
        ArenaPlan(bots, ()).validate()
    with pytest.raises(ArenaError, match="64-bit"):
        ArenaPlan(bots, ({"id": "x"},), seed=-1).validate()


def test_opponents_folder_validation(tmp_path):
    folder = tmp_path / "opponents"
    folder.mkdir()
    with pytest.raises(ArenaError, match="No opponent"):
        opponent_versions(str(folder))
    project(folder, "shared")
    assert len(opponent_versions(str(folder))) == 1
    assert opponent_versions("") == []


def test_runner_env_excludes_account_ai_and_github_secrets(monkeypatch):
    for name in (
        "BATTLECODE_API_KEY",
        "UNSWBC_KEY",
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "GH_TOKEN",
        "GITHUB_TOKEN",
        "UNSWBC_CLANG_WEBC",
        "PYTHONPATH",
    ):
        monkeypatch.setenv(name, "sensitive-fixture")
    env = runner_environment()
    assert "sensitive-fixture" not in env.values()
    assert env["UNSWBC_NO_UPDATE"] == "1"


async def test_runner_fixed_seeds_snapshots_results_and_no_host_bot_execution(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("BATTLECODE_API_KEY", "bc_runner_fixture")
    library = MapLibrary(tmp_path / "maps")
    library.import_blobs([("one.map", map_text("One")), ("two.map", map_text("Two"))])
    bots = tuple(project(tmp_path, name) for name in ("A", "B"))
    sample = json.loads(files("battlecode_cli").joinpath("assets/sample.replay.json").read_bytes())
    sample["winner"] = "A"
    fixture = tmp_path / "sample.json"
    fixture.write_text(json.dumps(sample))
    fake = tmp_path / "fake_runner.py"
    fake.write_text(
        "import os, pathlib, sys\n"
        "assert '--sandbox' in sys.argv\n"
        "assert 'BATTLECODE_API_KEY' not in os.environ\n"
        "target=pathlib.Path(sys.argv[sys.argv.index('-o')+1])\n"
        f"target.write_bytes(pathlib.Path({str(fixture)!r}).read_bytes())\n"
        "print('wrote replay')\n"
    )
    store = ArenaStore(tmp_path / "runs")
    runner = ArenaRunner(store, library, [sys.executable, str(fake)])
    plan = ArenaPlan(bots, tuple(library.entries()), repeats=2, seed=41)
    updates = []
    job = await runner.run(plan, lambda update: updates.append(len(update["games"])))
    assert job["status"] == "complete"
    assert len(job["games"]) == 8
    assert all(not game["error"] for game in job["games"])
    assert [(g["a"], g["b"], g["seed"]) for g in job["games"][:4]] == [
        (0, 1, 41),
        (1, 0, 41),
        (0, 1, 42),
        (1, 0, 42),
    ]
    assert store.load(job["id"])["games"] == job["games"]
    assert store.entries()[0]["completed"] == 8
    assert aggregates(job)[0]["wins"] == 4
    assert aggregates(job)[0]["losses"] == 4
    assert "PER-MAP RECORDS" in report(job)
    assert "invalid-action" in report(job) or "recorded movement" in report(job)
    snapshot = store.path(job["id"]) / "bot-0"
    assert 'output=".arena-build"' in (snapshot / "bot.toml").read_text()
    assert not (tmp_path / "escape").exists()
    assert "bc_runner_fixture" not in (store.path(job["id"]) / "results.json").read_text()


async def test_runner_cancellation_preserves_finished_results(tmp_path):
    library = MapLibrary(tmp_path / "maps")
    library.import_blobs([("one.map", map_text())])
    bots = tuple(project(tmp_path, name) for name in ("A", "B"))
    fake = tmp_path / "sleep.py"
    fake.write_text("import time\ntime.sleep(30)\n")
    store = ArenaStore(tmp_path / "runs")
    runner = ArenaRunner(store, library, [sys.executable, str(fake)])
    task = asyncio.create_task(runner.run(ArenaPlan(bots, tuple(library.entries()))))
    await asyncio.sleep(0.2)
    runner.cancel.set()
    job = await asyncio.wait_for(task, 3)
    assert job["status"] == "cancelled"
    assert store.load(job["id"])["status"] == "cancelled"
    assert len(job["games"]) == 0


async def test_process_timeout_and_cancel_kill_process_tree(tmp_path):
    fake = tmp_path / "sleep.py"
    fake.write_text("import time\ntime.sleep(30)\n")
    with pytest.raises(ArenaError, match="timed out"):
        await run_process(
            [sys.executable, str(fake)], cwd=tmp_path, timeout=0.1, cancel=asyncio.Event()
        )
    event = asyncio.Event()
    event.set()
    with pytest.raises(asyncio.CancelledError):
        await run_process([sys.executable, str(fake)], cwd=tmp_path, timeout=30, cancel=event)


def test_diagnostics_count_intra_round_moves_not_just_sampled_frames():
    events = [
        {"type": "roundStart", "round": 0},
        {"type": "dragonUpdate", "id": 0, "head": [2, 1], "tail": [0, 1]},
        {"type": "dragonUpdate", "id": 0, "head": [3, 1], "tail": [1, 1]},
        {"type": "dragonDeath", "id": 1, "reason": 0},
    ]
    replay = decode_replay(
        json.dumps(
            {
                "format": "battlecode-cli-replay",
                "version": 1,
                "map": map_text().decode(),
                "events": events,
                "winner": "A",
            }
        ).encode()
    )
    assert len(replay.frames) == 2
    assert replay.diagnostics["A"]["moves"] == 2
    assert replay.diagnostics["A"]["growth"] == 1
    assert replay.diagnostics["A"]["directions"] == {"east": 2}
    assert replay.diagnostics["B"]["deaths"] == {"hit wall": 1}
    assert replay.diagnostics["B"]["queen_death_round"] == 0
    assert not replay.diagnostics["command_events"]


def test_cpp_snapshots_cannot_select_native_toolkit_build_route(tmp_path):
    source = tmp_path / "mixed"
    source.mkdir()
    (source / "bot.toml").write_text('[project]\nlanguage="cpp"\ninclude=["*.cpp","*.py"]\n')
    (source / "main.cpp").write_text("int main() {}")
    (source / "main.py").write_text("raise RuntimeError('never execute')")
    with pytest.raises(ArenaError, match="remove root main.py"):
        snapshot_bot(bot_version(str(source)), tmp_path / "snapshot")


async def test_modified_map_is_rejected_before_runner_start(tmp_path):
    library = MapLibrary(tmp_path / "maps")
    library.import_blobs([("fixture.map", map_text())])
    entries = tuple(library.entries())
    library.path(entries[0]["id"]).write_bytes(map_text("Changed after review"))
    bots = tuple(project(tmp_path, name) for name in ("A", "B"))
    runner = ArenaRunner(ArenaStore(tmp_path / "runs"), library, ["must-not-execute"])
    with pytest.raises(ArenaError, match="selected map changed"):
        await runner.run(ArenaPlan(bots, entries))
