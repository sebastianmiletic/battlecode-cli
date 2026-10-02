import json
from pathlib import Path

import httpx
import pytest

from battlecode_cli.api import APIError, BattlecodeAPI
from battlecode_cli.config import Credential

KEY = "bc_test_fixture_not_a_real_key"


async def test_read_sends_auth_only_to_official_api():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"team": {"name": "Fixture"}})

    api = BattlecodeAPI(Credential(KEY, "test"), httpx.MockTransport(handler))
    assert (await api.get("/team"))["team"]["name"] == "Fixture"
    assert str(seen[0].url) == "https://game.battlecode.au/api/v1/team"
    assert seen[0].headers["authorization"] == f"Bearer {KEY}"
    await api.close()


async def test_missing_key_does_not_make_request():
    def handler(request):
        pytest.fail("Network must not be used without a key")

    api = BattlecodeAPI(None, httpx.MockTransport(handler))
    with pytest.raises(APIError, match="No API key"):
        await api.get("/team")
    await api.close()


async def test_auth_error_is_actionable_and_redacted():
    api = BattlecodeAPI(
        Credential(KEY), httpx.MockTransport(lambda _: httpx.Response(401, json={"error": KEY}))
    )
    with pytest.raises(APIError) as caught:
        await api.get("/team")
    assert caught.value.status == 401
    assert "Settings" in str(caught.value)
    assert KEY not in str(caught.value)
    await api.close()


async def test_rate_limit_respects_retry_after():
    api = BattlecodeAPI(
        Credential(KEY),
        httpx.MockTransport(
            lambda _: httpx.Response(429, headers={"Retry-After": "42"}, json={"error": "limit"})
        ),
    )
    with pytest.raises(APIError) as caught:
        await api.get("/leaderboard")
    assert caught.value.retry_after == 42
    await api.close()


async def test_writes_are_never_retried_after_timeout():
    requests = []

    def handler(request):
        requests.append(request)
        raise httpx.ReadTimeout("late", request=request)

    api = BattlecodeAPI(Credential(KEY), httpx.MockTransport(handler))
    with pytest.raises(APIError, match="may have succeeded"):
        await api.activate(12)
    assert len(requests) == 1
    assert requests[0].method == "POST"
    await api.close()


async def test_ranked_request_omits_maps():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"ids": [1]})

    api = BattlecodeAPI(Credential(KEY), httpx.MockTransport(handler))
    await api.challenge(23, True, [3, 4])
    assert json.loads(requests[0].content) == {"teamId": 23, "ranked": True}
    await api.challenge(23, False, [3, 4])
    assert json.loads(requests[1].content)["mapIds"] == [3, 4]
    await api.close()


async def test_upload_uses_official_multipart_fields():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"version": 2})

    api = BattlecodeAPI(Credential(KEY), httpx.MockTransport(handler))
    await api.upload("test-v2", "description", "cpp", b"zip-fixture")
    body = requests[0].content
    for name in (b'name="name"', b'name="language"', b'name="description"', b'name="zip"'):
        assert name in body
    assert b"zip-fixture" in body
    await api.close()


async def test_signed_download_drops_key_even_on_same_host(tmp_path):
    seen = []

    def handler(request):
        seen.append(request)
        if len(seen) == 1:
            return httpx.Response(302, headers={"Location": "/signed/file?signature=fixture"})
        return httpx.Response(200, content=b"replay-fixture")

    api = BattlecodeAPI(Credential(KEY), httpx.MockTransport(handler))
    path = await api.download("/battles/12/replay", tmp_path / "12.replay")
    assert path.read_bytes() == b"replay-fixture"
    assert "authorization" in seen[0].headers
    assert "authorization" not in seen[1].headers
    assert not list(tmp_path.glob(".download-*"))
    await api.close()


async def test_cross_host_download_drops_key(tmp_path):
    seen = []

    def handler(request):
        seen.append(request)
        if len(seen) == 1:
            return httpx.Response(
                302, headers={"Location": "https://files.example.test/replay?signature=fixture"}
            )
        return httpx.Response(200, content=b"replay-fixture")

    api = BattlecodeAPI(Credential(KEY), httpx.MockTransport(handler))
    await api.download("/battles/12/replay", tmp_path / "12.replay")
    assert seen[1].url.host == "files.example.test"
    assert "authorization" not in seen[1].headers
    await api.close()


@pytest.mark.parametrize(
    "location",
    [
        "http://files.example.test/file",
        "file:///etc/passwd",
        "https://user:password@files.example.test/file",
    ],
)
async def test_download_refuses_unsafe_redirects(tmp_path, location):
    api = BattlecodeAPI(
        Credential(KEY),
        httpx.MockTransport(lambda _: httpx.Response(302, headers={"Location": location})),
    )
    with pytest.raises(APIError, match="unsafe"):
        await api.download("/battles/12/replay", tmp_path / "12.replay")
    assert not list(tmp_path.iterdir())
    await api.close()


async def test_oversized_download_preserves_existing_file(tmp_path):
    destination = tmp_path / "12.replay"
    destination.write_bytes(b"previous")
    api = BattlecodeAPI(
        Credential(KEY), httpx.MockTransport(lambda _: httpx.Response(200, content=b"too-big"))
    )
    with pytest.raises(APIError, match="safety limit"):
        await api.download("/battles/12/replay", destination, max_bytes=2)
    assert destination.read_bytes() == b"previous"
    assert not list(tmp_path.glob(".download-*"))
    await api.close()


async def test_download_error_reads_streamed_json(tmp_path):
    api = BattlecodeAPI(
        Credential(KEY),
        httpx.MockTransport(lambda _: httpx.Response(404, json={"error": "Replay not ready"})),
    )
    with pytest.raises(APIError, match="Replay not ready"):
        await api.download("/battles/12/replay", tmp_path / "12.replay")
    await api.close()


async def test_external_api_path_is_rejected():
    api = BattlecodeAPI(Credential(KEY))
    with pytest.raises(ValueError):
        await api.get("https://evil.example.test")
    with pytest.raises(ValueError):
        await api.download("//evil.example.test", Path("unused"))
    await api.close()
