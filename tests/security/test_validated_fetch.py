"""``open_validated_stream``: ogni salto di redirect rivalidato, per tutti i chiamanti.

Stava scritto quattro volte (manifest, APK, ``download_file``, immagini
remote); qui si provano una volta le proprietà che contano per la sicurezza.
La rete non si tocca: ``httpx.MockTransport`` al posto del server, e il
validatore passato esplicitamente, come fa ogni chiamante.
"""

from __future__ import annotations

import httpx
import pytest

from jenny.security import fetch
from jenny.security.fetch import MAX_REDIRECTS, open_validated_stream, read_capped


def _client(routes: dict[str, httpx.Response], seen: list[str]) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return routes.get(str(request.url), httpx.Response(404))

    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)


def _allow_public(url: str) -> tuple[bool, str | None]:
    """Un validatore che si comporta come il vero sui nomi dei test."""
    if "192.168." in url or "169.254." in url:
        return False, "private address"
    return True, None


def _redirect(to: str) -> httpx.Response:
    return httpx.Response(302, headers={"location": to})


async def test_a_redirect_into_the_lan_is_refused_at_that_hop() -> None:
    seen: list[str] = []
    client = _client({"https://pub.example/a": _redirect("http://192.168.1.1/admin")}, seen)
    async with client:
        with pytest.raises(ValueError, match="URL blocked"):
            async with open_validated_stream(client, "https://pub.example/a", validate=_allow_public):
                pass
    assert seen == ["https://pub.example/a"], "l'hop privato non va nemmeno chiesto"


async def test_the_final_url_is_the_one_that_answered() -> None:
    seen: list[str] = []
    client = _client({
        "https://pub.example/latest": _redirect("https://cdn.example/app.apk"),
        "https://cdn.example/app.apk": httpx.Response(200, content=b"ok"),
    }, seen)
    async with client:
        async with open_validated_stream(
            client, "https://pub.example/latest", validate=_allow_public
        ) as (response, final_url):
            assert final_url == "https://cdn.example/app.apk"
            assert await read_capped(response, 10, "big") == b"ok"


async def test_relative_locations_are_resolved_before_validation() -> None:
    seen: list[str] = []
    client = _client({
        "https://pub.example/a/b": _redirect("../c"),
        "https://pub.example/c": httpx.Response(200, content=b"x"),
    }, seen)
    async with client:
        async with open_validated_stream(
            client, "https://pub.example/a/b", validate=_allow_public
        ) as (_resp, final_url):
            assert final_url == "https://pub.example/c"


async def test_https_only_refuses_a_downgrade_mid_chain() -> None:
    seen: list[str] = []
    client = _client({"https://pub.example/a": _redirect("http://pub.example/a")}, seen)
    async with client:
        with pytest.raises(ValueError, match="must be https"):
            async with open_validated_stream(
                client, "https://pub.example/a", validate=_allow_public, https_only=True
            ):
                pass
    assert seen == ["https://pub.example/a"]


async def test_without_https_only_a_plain_http_hop_is_allowed() -> None:
    """``download_file`` e le immagini accettano http: il parametro non e' implicito."""
    seen: list[str] = []
    client = _client({
        "https://pub.example/a": _redirect("http://pub.example/b"),
        "http://pub.example/b": httpx.Response(200, content=b"x"),
    }, seen)
    async with client:
        async with open_validated_stream(client, "https://pub.example/a", validate=_allow_public):
            pass


async def test_too_many_redirects() -> None:
    seen: list[str] = []
    routes = {
        f"https://pub.example/{i}": _redirect(f"https://pub.example/{i + 1}")
        for i in range(MAX_REDIRECTS + 1)
    }
    client = _client(routes, seen)
    async with client:
        with pytest.raises(ValueError, match="too many redirects"):
            async with open_validated_stream(client, "https://pub.example/0", validate=_allow_public):
                pass
    assert len(seen) == MAX_REDIRECTS + 1


async def test_exactly_the_maximum_number_of_redirects_still_arrives() -> None:
    seen: list[str] = []
    routes = {
        f"https://pub.example/{i}": _redirect(f"https://pub.example/{i + 1}")
        for i in range(MAX_REDIRECTS)
    }
    routes[f"https://pub.example/{MAX_REDIRECTS}"] = httpx.Response(200, content=b"x")
    client = _client(routes, seen)
    async with client:
        async with open_validated_stream(client, "https://pub.example/0", validate=_allow_public):
            pass


async def test_a_redirect_without_location_is_an_error() -> None:
    seen: list[str] = []
    client = _client({"https://pub.example/a": httpx.Response(302)}, seen)
    async with client:
        with pytest.raises(ValueError, match="Location"):
            async with open_validated_stream(client, "https://pub.example/a", validate=_allow_public):
                pass


async def test_a_non_200_final_answer_is_an_error() -> None:
    seen: list[str] = []
    client = _client({"https://pub.example/a": httpx.Response(503)}, seen)
    async with client:
        with pytest.raises(ValueError, match="HTTP 503"):
            async with open_validated_stream(client, "https://pub.example/a", validate=_allow_public):
                pass


async def test_the_default_validator_is_the_real_one(monkeypatch) -> None:
    """Senza *validate* si usa ``network.validate_url_target``, letto al momento."""
    calls: list[str] = []

    def spy(url: str):
        calls.append(url)
        return False, "nope"

    monkeypatch.setattr(fetch.network, "validate_url_target", spy)
    client = _client({}, [])
    async with client:
        with pytest.raises(ValueError, match="URL blocked: nope"):
            async with open_validated_stream(client, "https://pub.example/a"):
                pass
    assert calls == ["https://pub.example/a"]


async def test_the_body_cap_is_enforced_while_reading() -> None:
    seen: list[str] = []
    client = _client({"https://pub.example/a": httpx.Response(200, content=b"x" * 11)}, seen)
    async with client:
        async with open_validated_stream(client, "https://pub.example/a", validate=_allow_public) as (resp, _):
            with pytest.raises(ValueError, match="too big"):
                await read_capped(resp, 10, "too big")
