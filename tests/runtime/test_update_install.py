"""Test del layer di installazione (``jenny/runtime/update_install.py``).

Il bridge Kotlin è sempre finto: quello che si verifica qui è il *contratto* con
cui il lato Python lo interroga — una installazione alla volta, le stringhe
``error:<causa>`` tradotte in stato invece che in eccezioni, e i rifiuti che
arrivano prima di scaricare mezzo APK (niente in cache, versione già installata,
non siamo su Android).

Un asse attraversa quasi tutti i test: ``install_status()`` deve raccontare la
stessa cosa che ``start_install`` ha restituito. È il contratto su cui la WebUI
fa polling, e l'unica eccezione — un rifiuto che trova la fase occupata da
un'altra richiesta — è verificata in fondo, nella sezione sulla serializzazione.

Due sezioni in fondo coprono quello che non riguarda il bridge: la validazione
SSRF dell'URL dell'APK (dato che arriva dal manifest, quindi non fidato) e il
wakelock che tiene sveglio il telefono per tutta l'installazione.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest

from jenny.runtime import update_install
from jenny.runtime.update_check import UpdateInfo
from jenny.security import fetch

_INFO = UpdateInfo(
    version_code=9,
    version_name="0.7.0",
    apk_url="https://example.invalid/jenny-0.7.0.apk",
    sha256="a" * 64,
    size=48210944,
    notes_url="https://example.invalid/notes",
    summary="Aggiornamenti in-app.",
    critical=False,
)


class FakeBridge:
    """Doppio di ``UpdateBridge``: metodi bloccanti che tornano stringhe."""

    def __init__(
        self,
        *,
        download: str = "/data/cache/updates/jenny-update.apk",
        install: str = "silent",
        gate: asyncio.Event | None = None,
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        self.download_result = download
        self.install_result = install
        self._gate = gate
        self._loop = loop
        self.downloads: list[tuple[str, str, int]] = []
        self.installs: list[str] = []

    def downloadApk(self, url: str, sha256: str, expected_size: int) -> str:  # noqa: N802
        self.downloads.append((url, sha256, expected_size))
        if self._gate is not None and self._loop is not None:
            # Il thread resta fermo finché il test non lo libera: è la finestra
            # in cui la seconda ``start_install`` deve trovare la prima in volo.
            asyncio.run_coroutine_threadsafe(self._gate.wait(), self._loop).result(5)
        return self.download_result

    def installApk(self, path: str) -> str:  # noqa: N802
        self.installs.append(path)
        return self.install_result


@pytest.fixture(autouse=True)
def clean_state() -> Any:
    """Stato del modulo azzerato prima e dopo ogni test (è globale al processo)."""
    update_install.reset_install_state()
    yield
    update_install.reset_install_state()


@pytest.fixture(autouse=True)
def installed_at_8(monkeypatch: pytest.MonkeyPatch) -> None:
    """Il device finge di avere installato il versionCode 8."""
    monkeypatch.setattr(
        update_install.update_check, "installed_version_code", lambda: 8
    )


@pytest.fixture(autouse=True)
def no_cached_update(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default: nessun update in cache. I test che ne vogliono uno lo iniettano."""
    monkeypatch.setattr(
        update_install.update_check, "cached_update", lambda language=None: None
    )


# La funzione vera, catturata prima che la fixture qui sotto la sostituisca: la
# sezione sulla SSRF la rimette al suo posto per esercitarla davvero.
_REAL_RESOLVE = update_install._resolve_apk_url


@pytest.fixture(autouse=True)
def resolve_without_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default: la risoluzione dell'URL non tocca la rete e non cambia l'URL.

    Serve a tenere i test sul bridge concentrati sul bridge: senza, ognuno
    dovrebbe montare un finto CDN per arrivare a chiamare ``downloadApk``.
    """

    async def identity(url: str, **kwargs: Any) -> str:
        return url

    monkeypatch.setattr(update_install, "_resolve_apk_url", identity)


def _use_bridge(monkeypatch: pytest.MonkeyPatch, bridge: Any) -> None:
    monkeypatch.setattr(update_install, "_bridge", lambda: bridge)


def _cache(monkeypatch: pytest.MonkeyPatch, info: UpdateInfo | None) -> None:
    monkeypatch.setattr(
        update_install.update_check, "cached_update", lambda language=None: info
    )


# --------------------------------------------------------------------------
# Rifiuti prima di toccare il bridge
# --------------------------------------------------------------------------


async def test_no_cached_update_is_a_clean_error(monkeypatch: pytest.MonkeyPatch) -> None:
    bridge = FakeBridge()
    _use_bridge(monkeypatch, bridge)

    result = await update_install.start_install()

    assert result.ok is False
    assert result.state == "error"
    assert "No update is available" in result.detail
    assert bridge.downloads == []
    # Il rifiuto è osservabile: chi guarda solo ``install_status`` (la UI in
    # polling, l'altro chiamante) deve leggere lo stesso motivo, non un "idle"
    # che sembra un pulsante premuto a vuoto.
    status = update_install.install_status()
    assert status["phase"] == "error"
    assert "No update is available" in status["detail"]
    assert status["progress"] == 0


async def test_version_already_installed_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fra la notifica e il "sì" dell'utente l'update può essere già stato applicato."""
    _use_bridge(monkeypatch, FakeBridge())
    monkeypatch.setattr(
        update_install.update_check, "installed_version_code", lambda: 9
    )

    result = await update_install.start_install(_INFO)

    assert result.ok is False
    assert result.state == "error"
    assert "not newer" in result.detail
    status = update_install.install_status()
    assert status["phase"] == "error"
    assert "not newer" in status["detail"]


async def test_off_android_degrades_cleanly(monkeypatch: pytest.MonkeyPatch) -> None:
    """Senza contesto Android ``_bridge()`` è None e non si scarica niente."""
    monkeypatch.setattr(update_install, "get_android_context", lambda: None)
    _cache(monkeypatch, _INFO)

    result = await update_install.start_install()

    assert result.ok is False
    assert result.state == "error"
    assert "Android" in result.detail
    status = update_install.install_status()
    assert status["phase"] == "error"
    assert "Android" in status["detail"]


def test_status_is_readable_without_android() -> None:
    """Il percorso di lettura HTTP non deve dipendere da niente."""
    assert update_install.install_status() == {
        "phase": "idle",
        "progress": 0,
        "detail": "",
    }


# --------------------------------------------------------------------------
# Esiti del bridge
# --------------------------------------------------------------------------


async def test_silent_commit_reports_done(monkeypatch: pytest.MonkeyPatch) -> None:
    bridge = FakeBridge(install="silent")
    _use_bridge(monkeypatch, bridge)
    _cache(monkeypatch, _INFO)

    result = await update_install.start_install()

    assert result.ok is True
    assert result.state == "silent"
    assert bridge.downloads == [(_INFO.apk_url, _INFO.sha256, _INFO.size)]
    assert bridge.installs == ["/data/cache/updates/jenny-update.apk"]
    status = update_install.install_status()
    assert status["phase"] == "done"
    assert status["progress"] == 100


async def test_prompt_reports_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    _use_bridge(monkeypatch, FakeBridge(install="prompt"))
    _cache(monkeypatch, _INFO)

    result = await update_install.start_install()

    assert result.ok is True
    assert result.state == "prompt"
    assert update_install.install_status()["phase"] == "prompt"


async def test_download_error_becomes_error_phase(monkeypatch: pytest.MonkeyPatch) -> None:
    bridge = FakeBridge(download="error:sha256 mismatch")
    _use_bridge(monkeypatch, bridge)
    _cache(monkeypatch, _INFO)

    result = await update_install.start_install()

    assert result.ok is False
    assert result.state == "error"
    assert "sha256 mismatch" in result.detail
    # La causa del bridge deve arrivare fino a chi guarda lo stato.
    status = update_install.install_status()
    assert status["phase"] == "error"
    assert "sha256 mismatch" in status["detail"]
    # Fallito il download, non si tenta l'installazione.
    assert bridge.installs == []


async def test_install_error_becomes_error_phase(monkeypatch: pytest.MonkeyPatch) -> None:
    _use_bridge(monkeypatch, FakeBridge(install="error:install status 4"))
    _cache(monkeypatch, _INFO)

    result = await update_install.start_install()

    assert result.state == "error"
    assert "install status 4" in result.detail
    assert update_install.install_status()["phase"] == "error"


async def test_unexpected_bridge_value_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _use_bridge(monkeypatch, FakeBridge(install="maybe?"))
    _cache(monkeypatch, _INFO)

    result = await update_install.start_install()

    assert result.state == "error"
    assert "unexpected value" in result.detail


async def test_bridge_exception_never_escapes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Un'eccezione dal confine Chaquopy diventa uno stato, non un traceback."""

    class Exploding:
        def downloadApk(self, *args: Any) -> str:  # noqa: N802
            raise RuntimeError("java.lang.IllegalStateException")

    _use_bridge(monkeypatch, Exploding())
    _cache(monkeypatch, _INFO)

    result = await update_install.start_install()

    assert result.ok is False
    assert result.state == "error"
    assert "IllegalStateException" in result.detail
    assert update_install.install_status()["phase"] == "error"


# --------------------------------------------------------------------------
# Serializzazione
# --------------------------------------------------------------------------


async def test_concurrent_calls_start_a_single_download(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate = asyncio.Event()
    bridge = FakeBridge(gate=gate, loop=asyncio.get_running_loop())
    _use_bridge(monkeypatch, bridge)
    _cache(monkeypatch, _INFO)

    first = asyncio.create_task(update_install.start_install())
    # Lascia partire il primo fino dentro il thread di download.
    for _ in range(500):
        if bridge.downloads:
            break
        await asyncio.sleep(0.01)
    assert bridge.downloads, "the first install never reached the bridge"

    second = await update_install.start_install()

    assert second.ok is False
    assert second.state == "error"
    assert "already in progress" in second.detail
    assert "downloading" in second.detail
    # Il rifiuto non scrive sulla fase: appartiene all'installazione in volo, e
    # chi la sta seguendo in polling non deve vederla diventare "error".
    assert update_install.install_status()["phase"] == "downloading"

    gate.set()
    assert (await first).ok is True
    assert len(bridge.downloads) == 1


async def test_a_committed_update_is_not_installed_twice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dopo un commit silenzioso il processo sta per morire: non si riparte."""
    bridge = FakeBridge(install="silent")
    _use_bridge(monkeypatch, bridge)
    _cache(monkeypatch, _INFO)

    assert (await update_install.start_install()).ok is True
    again = await update_install.start_install()

    assert again.ok is False
    assert "already been committed" in again.detail
    assert len(bridge.downloads) == 1
    # Anche qui la fase è di qualcun altro (il commit riuscito) e resta intatta.
    assert update_install.install_status()["phase"] == "done"


async def test_a_failed_attempt_can_be_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    bridge = FakeBridge(download="error:not enough free space")
    _use_bridge(monkeypatch, bridge)
    _cache(monkeypatch, _INFO)

    assert (await update_install.start_install()).state == "error"
    # Il fallimento non deve incastrare il modulo: liberato spazio, si ritenta.
    bridge.download_result = "/data/cache/updates/jenny-update.apk"
    assert (await update_install.start_install()).state == "silent"
    assert len(bridge.downloads) == 2


# --------------------------------------------------------------------------
# Validazione SSRF dell'URL dell'APK
# --------------------------------------------------------------------------
#
# ``apk_url`` viene dal manifest, cioè da dato non fidato, e il bridge Kotlin sa
# controllare solo lo schema. Senza la validazione qui il download diventa una
# sonda verso la LAN e la rete Tailscale del telefono, con l'esito leggibile nel
# messaggio d'errore che risale in chat.


def _use_real_resolver(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(update_install, "_resolve_apk_url", _REAL_RESOLVE)


def _client(handler: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=False
    )


async def test_a_blocked_apk_url_never_reaches_the_bridge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Un manifest che punta alla LAN non fa partire nemmeno la richiesta."""
    _use_real_resolver(monkeypatch)
    bridge = FakeBridge()
    _use_bridge(monkeypatch, bridge)
    _cache(monkeypatch, _INFO)
    monkeypatch.setattr(
        update_install,
        "validate_url_target",
        lambda url: (False, "Blocked: host resolves to private/internal address 192.168.1.1"),
    )

    result = await update_install.start_install()

    assert result.ok is False
    assert result.state == "error"
    assert "URL blocked" in result.detail
    assert bridge.downloads == []
    status = update_install.install_status()
    assert status["phase"] == "error"
    # La progress resta a quella del download: ci si è provato, non è un rifiuto
    # arrivato prima di muovere un dito.
    assert status["progress"] == 10


async def test_every_redirect_hop_is_validated(monkeypatch: pytest.MonkeyPatch) -> None:
    """GitHub redirige verso un CDN: ogni salto va rivalidato, non solo il primo."""
    seen: list[str] = []
    monkeypatch.setattr(
        update_install,
        "validate_url_target",
        lambda url: (seen.append(url), (True, ""))[1],
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "github.invalid":
            return httpx.Response(
                302, headers={"location": "https://cdn.invalid/asset/jenny.apk"}
            )
        return httpx.Response(200)

    async with _client(handler) as client:
        final = await _REAL_RESOLVE(
            "https://github.invalid/releases/latest/download/jenny.apk", client=client
        )

    assert final == "https://cdn.invalid/asset/jenny.apk"
    assert seen == [
        "https://github.invalid/releases/latest/download/jenny.apk",
        "https://cdn.invalid/asset/jenny.apk",
    ]


async def test_a_redirect_into_the_lan_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """Il salto che conta è il secondo: il primo host può essere innocuo."""
    monkeypatch.setattr(
        update_install,
        "validate_url_target",
        lambda url: (True, "") if "cdn.invalid" not in url else (False, "Blocked: 10.0.0.1"),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://cdn.invalid/x.apk"})

    async with _client(handler) as client:
        with pytest.raises(ValueError, match="URL blocked"):
            await _REAL_RESOLVE("https://github.invalid/x.apk", client=client)


async def test_a_redirect_to_plain_http_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """``validate_url_target`` accetta anche http: il downgrade lo blocchiamo qui."""
    monkeypatch.setattr(update_install, "validate_url_target", lambda url: (True, ""))

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "http://cdn.invalid/x.apk"})

    async with _client(handler) as client:
        with pytest.raises(ValueError, match="must be https"):
            await _REAL_RESOLVE("https://github.invalid/x.apk", client=client)


async def test_an_endless_redirect_chain_gives_up(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(update_install, "validate_url_target", lambda url: (True, ""))
    hops = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        hops["n"] += 1
        return httpx.Response(
            302, headers={"location": f"https://cdn.invalid/{hops['n']}.apk"}
        )

    async with _client(handler) as client:
        with pytest.raises(ValueError, match="too many redirects"):
            await _REAL_RESOLVE("https://github.invalid/x.apk", client=client)
    assert hops["n"] == fetch.MAX_REDIRECTS + 1


async def test_the_bridge_downloads_the_resolved_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """Al bridge va l'URL finale — l'unico validato fino in fondo — non quello del manifest."""

    async def resolved(url: str, **kwargs: Any) -> str:
        return "https://cdn.invalid/signed/jenny.apk?token=abc"

    monkeypatch.setattr(update_install, "_resolve_apk_url", resolved)
    bridge = FakeBridge()
    _use_bridge(monkeypatch, bridge)
    _cache(monkeypatch, _INFO)

    assert (await update_install.start_install()).ok is True
    assert bridge.downloads == [
        ("https://cdn.invalid/signed/jenny.apk?token=abc", _INFO.sha256, _INFO.size)
    ]


async def test_an_unreachable_url_is_an_error_not_a_traceback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_real_resolver(monkeypatch)
    monkeypatch.setattr(update_install, "validate_url_target", lambda url: (True, ""))
    bridge = FakeBridge()
    _use_bridge(monkeypatch, bridge)
    _cache(monkeypatch, _INFO)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    monkeypatch.setattr(update_install.httpx, "AsyncClient", lambda **kw: _client(handler))

    result = await update_install.start_install()

    assert result.ok is False
    assert result.state == "error"
    assert bridge.downloads == []
    assert update_install.install_status()["phase"] == "error"


# --------------------------------------------------------------------------
# Wakelock
# --------------------------------------------------------------------------
#
# Il telefono è un server in un cassetto a schermo spento: senza wakelock la CPU
# si sospende in mezzo al download. Il lock si prende dentro ``start_install``,
# così copre sia il tool dell'agente sia la route HTTP del pulsante "Installa
# ora" — che è la strada normale e non ne aveva nessuno.


def _record_keep_awake(monkeypatch: pytest.MonkeyPatch) -> tuple[list[Any], dict[str, Any]]:
    events: list[Any] = []
    live: dict[str, Any] = {"held": False}

    @asynccontextmanager
    async def fake_keep_awake(tag: str, *, timeout_s: float = 0.0) -> AsyncIterator[bool]:
        events.append(("acquire", tag, timeout_s))
        live["held"] = True
        try:
            yield True
        finally:
            live["held"] = False
            events.append(("release", tag))

    monkeypatch.setattr(update_install, "keep_awake", fake_keep_awake)
    return events, live


async def test_the_whole_install_runs_under_a_wakelock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events, live = _record_keep_awake(monkeypatch)
    seen: dict[str, bool] = {}

    class WatchingBridge(FakeBridge):
        def downloadApk(self, url: str, sha256: str, expected_size: int) -> str:  # noqa: N802
            seen["download"] = live["held"]
            return super().downloadApk(url, sha256, expected_size)

        def installApk(self, path: str) -> str:  # noqa: N802
            seen["install"] = live["held"]
            return super().installApk(path)

    _use_bridge(monkeypatch, WatchingBridge())
    _cache(monkeypatch, _INFO)

    assert (await update_install.start_install()).ok is True

    assert seen == {"download": True, "install": True}
    assert events == [
        ("acquire", "update", update_install._INSTALL_WAKELOCK_TIMEOUT_S),
        ("release", "update"),
    ]
    assert live["held"] is False


async def test_the_wakelock_is_released_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Anche quando il bridge esplode: un lock non rilasciato è batteria a zero."""
    events, live = _record_keep_awake(monkeypatch)

    class Exploding:
        def downloadApk(self, *args: Any) -> str:  # noqa: N802
            raise RuntimeError("java.lang.IllegalStateException")

    _use_bridge(monkeypatch, Exploding())
    _cache(monkeypatch, _INFO)

    assert (await update_install.start_install()).state == "error"

    assert events[-1] == ("release", "update")
    assert live["held"] is False
