"""Test per l'ingestione di immagini remote nel media store locale."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from jenny.security import fetch
from jenny.webui import media_ingest
from jenny.webui.media_ingest import ingest_remote_image

_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
_HTML = b"<!doctype html><html><body>not an image</body></html>"


def _media_dir(root: Path):
    def _provider(channel: str | None = None) -> Path:
        target = root if channel is None else root / channel
        target.mkdir(parents=True, exist_ok=True)
        return target

    return _provider


class _FakeResp:
    def __init__(self, *, status: int = 200, chunks=(_PNG,), location: str | None = None):
        self.status_code = status
        self.headers: dict[str, str] = {}
        if location is not None:
            self.headers["location"] = location
        self._chunks = chunks

    @property
    def is_redirect(self) -> bool:
        return self.status_code in (301, 302, 303, 307, 308)

    async def aiter_bytes(self):
        for chunk in self._chunks:
            yield chunk


class _FakeStreamCtx:
    def __init__(self, resp: _FakeResp):
        self._resp = resp

    async def __aenter__(self) -> _FakeResp:
        return self._resp

    async def __aexit__(self, *exc) -> bool:
        return False


class _FakeClient:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[str] = []
        self.closed = False

    def stream(self, method: str, url: str, headers=None):
        self.calls.append(url)
        return _FakeStreamCtx(self._responses.pop(0))

    async def aclose(self) -> None:
        self.closed = True


@pytest.fixture()
def allow_ssrf(monkeypatch):
    """Evita DNS reale: per default ogni URL è permesso."""
    monkeypatch.setattr(media_ingest, "validate_url_target", lambda url: (True, ""))


@pytest.fixture()
def media_root(tmp_path: Path) -> Path:
    return tmp_path / "media"


class TestIngestRemoteImage:
    async def test_success_png(self, media_root, allow_ssrf):
        client = _FakeClient([_FakeResp(chunks=[_PNG])])
        path = await ingest_remote_image(
            "https://host.test/kumamon.png",
            media_dir=_media_dir(media_root),
            logger=MagicMock(),
            client=client,
        )
        assert path is not None
        assert path.parent == media_root / "remote"
        assert path.suffix == ".png"
        assert path.read_bytes() == _PNG

    async def test_extension_from_magic_bytes_not_url(self, media_root, allow_ssrf):
        # URL senza estensione (tipico dei risultati di ricerca): l'estensione
        # deve venire dal magic byte, non dall'URL.
        client = _FakeClient([_FakeResp(chunks=[_PNG])])
        path = await ingest_remote_image(
            "https://host.test/image?id=42",
            media_dir=_media_dir(media_root),
            logger=MagicMock(),
            client=client,
        )
        assert path is not None and path.suffix == ".png"

    async def test_ssrf_blocked_never_fetches(self, media_root, monkeypatch):
        monkeypatch.setattr(media_ingest, "validate_url_target", lambda url: (False, "blocked"))
        client = _FakeClient([_FakeResp(chunks=[_PNG])])
        path = await ingest_remote_image(
            "https://169.254.169.254/latest/meta-data/",
            media_dir=_media_dir(media_root),
            logger=MagicMock(),
            client=client,
        )
        assert path is None
        assert client.calls == []

    async def test_non_image_rejected(self, media_root, allow_ssrf):
        client = _FakeClient([_FakeResp(chunks=[_HTML])])
        path = await ingest_remote_image(
            "https://host.test/page.html",
            media_dir=_media_dir(media_root),
            logger=MagicMock(),
            client=client,
        )
        assert path is None
        assert list((media_root / "remote").glob("*")) == []

    async def test_non_200_rejected(self, media_root, allow_ssrf):
        client = _FakeClient([_FakeResp(status=404, chunks=[b""])])
        path = await ingest_remote_image(
            "https://host.test/missing.png",
            media_dir=_media_dir(media_root),
            logger=MagicMock(),
            client=client,
        )
        assert path is None

    async def test_oversize_rejected(self, media_root, allow_ssrf, monkeypatch):
        monkeypatch.setattr(media_ingest, "MAX_IMAGE_BYTES", 16)
        client = _FakeClient([_FakeResp(chunks=[_PNG, b"x" * 32])])
        path = await ingest_remote_image(
            "https://host.test/big.png",
            media_dir=_media_dir(media_root),
            logger=MagicMock(),
            client=client,
        )
        assert path is None

    async def test_redirect_followed(self, media_root, allow_ssrf):
        client = _FakeClient([
            _FakeResp(status=302, chunks=[b""], location="https://cdn.test/real.png"),
            _FakeResp(chunks=[_PNG]),
        ])
        path = await ingest_remote_image(
            "https://host.test/redir",
            media_dir=_media_dir(media_root),
            logger=MagicMock(),
            client=client,
        )
        assert path is not None and path.read_bytes() == _PNG
        assert client.calls == ["https://host.test/redir", "https://cdn.test/real.png"]

    async def test_redirect_to_blocked_host_rejected(self, media_root, monkeypatch):
        def _validate(url: str):
            return (False, "blocked") if "169.254" in url else (True, "")

        monkeypatch.setattr(media_ingest, "validate_url_target", _validate)
        client = _FakeClient([
            _FakeResp(status=302, chunks=[b""], location="https://169.254.169.254/x"),
            _FakeResp(chunks=[_PNG]),
        ])
        path = await ingest_remote_image(
            "https://host.test/redir",
            media_dir=_media_dir(media_root),
            logger=MagicMock(),
            client=client,
        )
        assert path is None
        # Ha aperto solo il primo hop; il redirect bloccato non è stato seguito.
        assert client.calls == ["https://host.test/redir"]

    async def test_too_many_redirects_rejected(self, media_root, allow_ssrf):
        responses = [
            _FakeResp(status=302, chunks=[b""], location=f"https://host.test/{i}")
            for i in range(fetch.MAX_REDIRECTS + 2)
        ]
        client = _FakeClient(responses)
        path = await ingest_remote_image(
            "https://host.test/start",
            media_dir=_media_dir(media_root),
            logger=MagicMock(),
            client=client,
        )
        assert path is None

    async def test_dedup_skips_second_fetch(self, media_root, allow_ssrf):
        provider = _media_dir(media_root)
        url = "https://host.test/dup.png"
        first = await ingest_remote_image(
            url, media_dir=provider, logger=MagicMock(), client=_FakeClient([_FakeResp(chunks=[_PNG])])
        )
        # Secondo client che NON verrà usato (il file esiste già).
        second_client = _FakeClient([_FakeResp(chunks=[_PNG])])
        second = await ingest_remote_image(
            url, media_dir=provider, logger=MagicMock(), client=second_client
        )
        assert first == second
        assert second_client.calls == []

    async def test_non_http_url_returns_none(self, media_root):
        path = await ingest_remote_image(
            "ftp://host.test/x.png",
            media_dir=_media_dir(media_root),
            logger=MagicMock(),
        )
        assert path is None


class TestEnforceRemoteBudget:
    def test_evicts_oldest_until_under_cap(self, tmp_path, monkeypatch):
        remote = tmp_path / "remote"
        remote.mkdir()
        old = remote / "old.png"
        old.write_bytes(b"x" * 100)
        new = remote / "new.png"
        new.write_bytes(b"y" * 100)
        os.utime(old, (1_000, 1_000))
        os.utime(new, (2_000_000_000, 2_000_000_000))
        monkeypatch.setattr(media_ingest, "REMOTE_MEDIA_BUDGET_BYTES", 150)
        media_ingest._enforce_remote_budget(remote, logger=MagicMock())
        assert not old.exists()
        assert new.exists()

    def test_noop_when_under_cap(self, tmp_path, monkeypatch):
        remote = tmp_path / "remote"
        remote.mkdir()
        f = remote / "a.png"
        f.write_bytes(b"x" * 100)
        monkeypatch.setattr(media_ingest, "REMOTE_MEDIA_BUDGET_BYTES", 10_000)
        media_ingest._enforce_remote_budget(remote, logger=MagicMock())
        assert f.exists()
