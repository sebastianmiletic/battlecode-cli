"""Async toolkit API client. No automatic retries of state-changing requests."""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

from . import __version__
from .config import API_URL, SERVER, Credential, redact


class APIError(Exception):
    def __init__(self, message: str, status: int = 0, retry_after: int = 0):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


class BattlecodeAPI:
    demo = False

    def __init__(
        self, credential: Credential | None, transport: httpx.AsyncBaseTransport | None = None
    ):
        self.credential = credential
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(25, connect=10), follow_redirects=False, transport=transport
        )
        self._slots = asyncio.Semaphore(4)

    async def close(self) -> None:
        await self._client.aclose()

    def _headers(self) -> dict[str, str]:
        if not self.credential:
            raise APIError("No API key. Connect your account in API keys (6).", 401)
        return {
            "Authorization": f"Bearer {self.credential.token}",
            "Origin": SERVER,
            "User-Agent": f"battlecode-cli/{__version__}",
        }

    def _check(self, response: httpx.Response) -> None:
        if response.is_success:
            return
        try:
            message = response.json().get("error", f"Server returned HTTP {response.status_code}.")
        except (ValueError, AttributeError):
            message = f"Server returned HTTP {response.status_code}."
        if response.status_code == 401:
            message = (
                "API key rejected. Make a new key on your team page, then connect in API keys (6)."
            )
        retry = response.headers.get("Retry-After", "0")
        seconds = int(retry) if retry.isdigit() else 60
        if response.status_code == 429:
            message = f"Rate limited. Try again in {seconds} seconds."
        token = self.credential.token if self.credential else ""
        raise APIError(redact(str(message), token), response.status_code, seconds)

    async def request(self, method: str, path: str, **kwargs: Any) -> Any:
        if not path.startswith("/") or path.startswith("//"):
            raise ValueError("An API-relative path is required.")
        try:
            async with self._slots:
                response = await self._client.request(
                    method, API_URL + path, headers=self._headers(), **kwargs
                )
            self._check(response)
            return response.json() if response.content else {}
        except httpx.HTTPError as error:
            message = "The server could not be reached."
            if method != "GET":
                message += " The action may have succeeded; refresh before trying again."
            raise APIError(message) from error
        except ValueError as error:
            raise APIError("The server returned an unreadable response.") from error

    async def get(self, path: str) -> Any:
        return await self.request("GET", path)

    async def activate(self, submission_id: int) -> Any:
        return await self.request("POST", f"/submissions/{int(submission_id)}/activate")

    async def challenge(self, team_id: int, ranked: bool, map_ids: list[int]) -> Any:
        body: dict[str, Any] = {"teamId": int(team_id), "ranked": ranked}
        if not ranked and map_ids:
            body["mapIds"] = map_ids
        return await self.request("POST", "/battles", json=body)

    async def upload(self, name: str, description: str, language: str, blob: bytes) -> Any:
        return await self.request(
            "POST",
            "/submissions",
            data={"name": name, "description": description, "language": language},
            files={"zip": ("bot.zip", blob, "application/zip")},
        )

    async def download(
        self, path: str, destination: Path, max_bytes: int = 128 * 1024 * 1024
    ) -> Path:
        """Resolve signed URLs without forwarding the bearer key, then atomically save."""
        if not path.startswith("/") or path.startswith("//"):
            raise ValueError("An API-relative path is required.")
        destination.parent.mkdir(parents=True, exist_ok=True)
        url, headers = API_URL + path, self._headers()
        descriptor, temporary = tempfile.mkstemp(prefix=".download-", dir=destination.parent)
        os.close(descriptor)
        try:
            for _ in range(6):
                async with self._client.stream("GET", url, headers=headers) as response:
                    if response.status_code in (301, 302, 303, 307, 308):
                        location = response.headers.get("Location")
                        if not location:
                            raise APIError("Download redirect did not contain a location.")
                        url = urljoin(url, location)
                        parsed = urlsplit(url)
                        if parsed.scheme != "https" or not parsed.hostname or parsed.username:
                            raise APIError("Refused an unsafe download redirect.")
                        # Even same-origin signed links do not need our API key.
                        headers = {"User-Agent": f"battlecode-cli/{__version__}"}
                        continue
                    if not response.is_success:
                        await response.aread()
                    self._check(response)
                    size = 0
                    with Path(temporary).open("wb") as handle:
                        async for chunk in response.aiter_bytes():
                            size += len(chunk)
                            if size > max_bytes:
                                raise APIError("Download exceeded the safety limit.")
                            handle.write(chunk)
                    os.replace(temporary, destination)
                    return destination
            raise APIError("Too many download redirects.")
        except httpx.HTTPError as error:
            raise APIError("Download interrupted. No partial file was saved.") from error
        finally:
            Path(temporary).unlink(missing_ok=True)
