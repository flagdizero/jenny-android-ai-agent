"""L'aggiornamento dell'app visto dalla WebUI: payload versione e due rotte.

Il calcolo di "esiste una versione più nuova" sta altrove
(``jenny/runtime/update_check.py``, coperto da ``tests/runtime/test_update_check.py``):
qui si copre soltanto il tratto che porta quell'informazione in pagina e il
bottone che avvia l'installazione.

Il filo conduttore è che **un updater rotto non deve rompere le impostazioni**.
Stato assente, stato illeggibile, stato che dichiara una versione impossibile,
layer di installazione che non esiste in questa build: ogni caso deve degradare
in un payload che la pagina sa comunque disegnare, con ``current`` sempre
presente.
"""

from __future__ import annotations

import asyncio
import json
import sys
import types
import urllib.parse
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from websockets.http11 import Headers
from websockets.http11 import Request as WsRequest

from jenny import __version__
from jenny.channels.http_utils import check_api_secret, http_error, http_json_response, parse_query
from jenny.config.loader import save_config
from jenny.config.schema import Config
from jenny.runtime import update_check
from jenny.runtime.context import get_runtime_context
from jenny.webui.settings_api import _version_payload, settings_payload
from jenny.webui.settings_routes import WebUISettingsRouter

_SECRET = "s3cr3t-updates"
_INSTALL_ID = "3f2a1b4c-0000-4000-8000-abcdefabcdef"

_MANIFEST: dict[str, Any] = {
    "schema": 1,
    "version_code": 9,
    "version_name": "0.7.0",
    "apk_url": "https://example.invalid/jenny-0.7.0.apk",
    "sha256": "a" * 64,
    "size": 48210944,
    "notes_url": "https://example.invalid/releases/0.7.0",
    "summary_it": "Aggiornamenti in-app.",
    "summary_en": "In-app updates.",
    "min_supported_code": 6,
    "rollout": 100,
    "critical": False,
}


@pytest.fixture(autouse=True)
def isolated_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Stato dell'updater in un file usa-e-getta (mai il workspace reale)."""
    path = tmp_path / "update_state.json"
    monkeypatch.setattr(update_check, "_state_path", lambda: path)
    return path


@pytest.fixture(autouse=True)
def installed_at_8(monkeypatch: pytest.MonkeyPatch) -> None:
    """Il device finge di avere installata la 0.6.6 (versionCode 8)."""
    monkeypatch.setattr(update_check, "installed_version_code", lambda: 8)


def _write_state(path: Path, **overrides: Any) -> None:
    state = {
        "schema": 1,
        "install_id": _INSTALL_ID,
        "last_check_ms": 1_754_900_000_000,
        "language": "it",
        "latest": _MANIFEST,
    }
    state.update(overrides)
    path.write_text(json.dumps(state), encoding="utf-8")


# ---------------------------------------------------------------------------
# Payload versione
# ---------------------------------------------------------------------------


class TestTheVersionPayload:
    def test_without_any_state_only_the_defaults_are_reported(self) -> None:
        payload = _version_payload()

        assert payload["current"] == __version__
        assert payload["update_available"] is False
        assert payload["critical"] is False
        assert payload["latest"] is None
        assert payload["latest_code"] is None
        assert payload["notes_url"] is None
        assert payload["summary"] is None
        assert payload["last_check"] is None
        # La versione installata si legge dal PackageManager, non dallo stato:
        # c'è anche quando l'updater non ha mai girato.
        assert payload["current_code"] == 8

    def test_a_cached_update_fills_every_field(self, isolated_state: Path) -> None:
        _write_state(isolated_state)

        payload = _version_payload()

        assert payload["current"] == __version__
        assert payload["current_code"] == 8
        assert payload["latest"] == "0.7.0"
        assert payload["latest_code"] == 9
        assert payload["update_available"] is True
        assert payload["critical"] is False
        assert payload["notes_url"] == "https://example.invalid/releases/0.7.0"
        # Lingua registrata all'ultimo controllo: il payload non ricarica la config.
        assert payload["summary"] == "Aggiornamenti in-app."
        assert payload["last_check"] == 1_754_900_000_000

    def test_a_critical_update_is_flagged_as_such(self, isolated_state: Path) -> None:
        _write_state(isolated_state, latest={**_MANIFEST, "critical": True})

        assert _version_payload()["critical"] is True

    def test_empty_presentation_fields_become_absent(self, isolated_state: Path) -> None:
        """Il manifest degrada note e sommario a stringa vuota; qui è ``None``.

        Per la UI "assente" e "presente ma vuoto" non sono la stessa cosa: sul
        secondo disegnerebbe un link senza destinazione.
        """
        _write_state(
            isolated_state,
            latest={**_MANIFEST, "notes_url": "", "summary_it": "", "summary_en": ""},
        )

        payload = _version_payload()

        assert payload["update_available"] is True
        assert payload["notes_url"] is None
        assert payload["summary"] is None

    def test_an_already_installed_version_is_not_announced(self, isolated_state: Path) -> None:
        _write_state(isolated_state, latest={**_MANIFEST, "version_code": 8})

        assert _version_payload()["update_available"] is False

    def test_unreadable_state_degrades_instead_of_raising(self, isolated_state: Path) -> None:
        isolated_state.write_text("{ this is not json", encoding="utf-8")

        payload = _version_payload()

        assert payload["current"] == __version__
        assert payload["update_available"] is False
        assert payload["last_check"] is None
        assert payload["last_success"] is None

    def test_a_corrupt_manifest_in_the_state_is_ignored(self, isolated_state: Path) -> None:
        """Lo stato è locale, ma resta un file: la validazione si riapplica."""
        _write_state(isolated_state, latest={**_MANIFEST, "version_code": "nine"})

        payload = _version_payload()

        assert payload["update_available"] is False
        # Il resto dello stato resta leggibile: l'ultimo controllo c'è stato.
        assert payload["last_check"] == 1_754_900_000_000

    def test_an_exploding_updater_cannot_break_the_settings_page(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def boom() -> None:
            raise RuntimeError("workspace not initialised")

        monkeypatch.setattr(update_check, "last_check_ms", boom)

        payload = _version_payload()

        assert payload == {
            "current": __version__,
            "current_code": 8,
            "latest": None,
            "latest_code": None,
            "update_available": False,
            "critical": False,
            "notes_url": None,
            "summary": None,
            "last_check": None,
            "last_success": None,
        }

    def test_the_settings_payload_carries_it(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, isolated_state: Path
    ) -> None:
        config_path = tmp_path / "config.json"
        save_config(Config(), config_path)
        monkeypatch.setattr(get_runtime_context(), "config_path", config_path)
        _write_state(isolated_state)

        version = settings_payload()["version"]

        assert version["current"] == __version__
        assert version["update_available"] is True
        assert version["latest"] == "0.7.0"


class TestTheLastSuccessfulCheck:
    """``last_success``: l'unico campo che distingue "aggiornato" da "cieco".

    ``last_check`` è scritto anche quando il fetch fallisce, quindi da solo non
    dice niente sulla salute del meccanismo. Questo sì, e il payload lo porta in
    pagina — tollerando che l'updater non lo esponga affatto, perché la funzione
    è arrivata dopo e uno stato scritto prima non la conosce.
    """

    def test_it_travels_all_the_way_from_the_real_state_file(
        self, isolated_state: Path
    ) -> None:
        """Senza doppi: dallo stato su disco fino al payload della pagina.

        Il resto di questa classe descrive i casi con un ``last_success_ms``
        finto; qui si verifica che il campo letto sia davvero quello che
        ``update_check`` scrive.
        """
        _write_state(isolated_state, last_success_ms=1_754_800_000_000)

        assert _version_payload()["last_success"] == 1_754_800_000_000

    def test_it_is_reported_when_the_updater_knows_it(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            update_check, "last_success_ms", lambda: 1_754_800_000_000, raising=False
        )

        assert _version_payload()["last_success"] == 1_754_800_000_000

    def test_an_updater_without_the_function_still_renders(
        self, monkeypatch: pytest.MonkeyPatch, isolated_state: Path
    ) -> None:
        """Build in cui ``last_success_ms`` non esiste ancora.

        L'accesso è a parte proprio per questo: se stesse nell'``import`` degli
        altri tre, l'``ImportError`` porterebbe via anche ``current_code`` e
        ``last_check``, che invece ci sono e servono.
        """
        monkeypatch.delattr(update_check, "last_success_ms", raising=False)
        _write_state(isolated_state)

        payload = _version_payload()

        assert payload["last_success"] is None
        assert payload["last_check"] == 1_754_900_000_000
        assert payload["current_code"] == 8

    def test_an_exploding_last_success_does_not_take_the_rest_with_it(
        self, monkeypatch: pytest.MonkeyPatch, isolated_state: Path
    ) -> None:
        def boom() -> int:
            raise RuntimeError("workspace not initialised")

        monkeypatch.setattr(update_check, "last_success_ms", boom, raising=False)
        _write_state(isolated_state)

        payload = _version_payload()

        assert payload["last_success"] is None
        assert payload["update_available"] is True

    @pytest.mark.parametrize("value", ["yesterday", 1.5, True, None])
    def test_a_value_that_is_not_a_timestamp_is_ignored(
        self, monkeypatch: pytest.MonkeyPatch, value: Any
    ) -> None:
        """``True`` compreso: in Python è un ``int``, in pagina sarebbe il 1970."""
        monkeypatch.setattr(update_check, "last_success_ms", lambda: value, raising=False)

        assert _version_payload()["last_success"] is None


# ---------------------------------------------------------------------------
# Rotte
# ---------------------------------------------------------------------------


def _request(path: str, token: str | None = _SECRET) -> WsRequest:
    if token is not None and "token=" not in path:
        sep = "&" if "?" in path else "?"
        path = f"{path}{sep}token={urllib.parse.quote(token)}"
    return WsRequest(path=path, headers=Headers())


def _router(**overrides: Any) -> WebUISettingsRouter:
    kwargs: dict[str, Any] = dict(
        bus=MagicMock(),
        logger=MagicMock(),
        check_api_token=lambda request: check_api_secret(request.headers, request.path, _SECRET),
        parse_query=parse_query,
        json_response=http_json_response,
        error_response=http_error,
    )
    kwargs.update(overrides)
    return WebUISettingsRouter(**kwargs)


def _json(response: Any) -> dict[str, Any]:
    return json.loads(response.body.decode("utf-8"))


class _Result:
    """Doppio di ``update_install.InstallResult`` (ok/state/detail)."""

    def __init__(self, ok: bool, state: str, detail: str = "") -> None:
        self.ok = ok
        self.state = state
        self.detail = detail


def _install_module(
    monkeypatch: pytest.MonkeyPatch,
    *,
    start: Any = None,
    status: Any = None,
) -> types.ModuleType:
    """Installa un finto ``jenny.runtime.update_install`` per la durata del test.

    Il modulo vero è scritto in parallelo e tocca il PackageInstaller di
    Android: qui interessa solo il contratto che le rotte consumano.
    """
    from jenny import runtime

    module = types.ModuleType("jenny.runtime.update_install")

    async def default_start(info: Any = None) -> _Result:
        return _Result(True, "silent", "session committed")

    module.start_install = start or default_start  # type: ignore[attr-defined]
    module.install_status = status or (  # type: ignore[attr-defined]
        lambda: {"phase": "downloading", "progress": 42, "detail": "20 MB"}
    )
    monkeypatch.setitem(sys.modules, "jenny.runtime.update_install", module)
    monkeypatch.setattr(runtime, "update_install", module, raising=False)
    return module


def _no_install_module(monkeypatch: pytest.MonkeyPatch) -> None:
    """Build senza il layer di installazione: l'import deve fallire pulito."""
    from jenny import runtime

    monkeypatch.setitem(sys.modules, "jenny.runtime.update_install", None)
    monkeypatch.delattr(runtime, "update_install", raising=False)


class TestTheInstallRoute:
    async def test_it_requires_a_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_module(monkeypatch)
        response = await _router().dispatch(
            _request("/api/updates/install", token=None), "/api/updates/install"
        )
        assert response.status_code == 401

    async def test_a_silent_install_reports_its_state(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_module(monkeypatch)

        response = await _router().dispatch(
            _request("/api/updates/install"), "/api/updates/install"
        )

        assert response.status_code == 200
        assert _json(response) == {
            "ok": True,
            "state": "silent",
            "detail": "session committed",
        }

    async def test_a_prompt_install_is_a_success_not_an_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Confermare nell'installer di sistema è lo scenario normale."""

        async def start(info: Any = None) -> _Result:
            return _Result(True, "prompt", "installer opened")

        _install_module(monkeypatch, start=start)

        response = await _router().dispatch(
            _request("/api/updates/install"), "/api/updates/install"
        )

        assert response.status_code == 200
        assert _json(response)["state"] == "prompt"
        assert _json(response)["ok"] is True

    async def test_a_refused_install_comes_back_as_a_payload_not_a_500(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """"L'installer ha detto di no" è da mostrare, non da nascondere.

        La UI stampa ``detail`` nello stesso riquadro in cui poi arriva il
        progresso: un 500 le lascerebbe solo un errore generico.
        """

        async def start(info: Any = None) -> _Result:
            return _Result(False, "error", "not enough free space")

        _install_module(monkeypatch, start=start)

        response = await _router().dispatch(
            _request("/api/updates/install"), "/api/updates/install"
        )

        assert response.status_code == 200
        assert _json(response) == {
            "ok": False,
            "state": "error",
            "detail": "not enough free space",
        }

    async def test_an_unknown_state_is_reported_as_an_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def start(info: Any = None) -> _Result:
            return _Result(True, "teleported")

        _install_module(monkeypatch, start=start)

        response = await _router().dispatch(
            _request("/api/updates/install"), "/api/updates/install"
        )

        assert _json(response)["state"] == "error"

    async def test_an_exploding_installer_maps_to_500(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def start(info: Any = None) -> _Result:
            raise RuntimeError("PackageInstaller unavailable")

        _install_module(monkeypatch, start=start)
        logger = MagicMock()

        response = await _router(logger=logger).dispatch(
            _request("/api/updates/install"), "/api/updates/install"
        )

        assert response.status_code == 500
        # Il messaggio dell'eccezione resta nel log, non nel corpo della risposta.
        assert "PackageInstaller" not in response.body.decode("utf-8")
        logger.exception.assert_called_once()

    async def test_a_build_without_the_installer_says_so(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _no_install_module(monkeypatch)

        response = await _router().dispatch(
            _request("/api/updates/install"), "/api/updates/install"
        )

        assert response.status_code == 503


class TestAgainstTheRealInstaller:
    """Un giro senza doppi, contro ``jenny/runtime/update_install.py`` vero.

    Tutto il resto di questo file lavora su un finto modulo, che è quello che
    serve per descrivere i casi: qui si verifica invece che il contratto
    assunto (``start_install`` awaitable con ``.ok/.state/.detail``,
    ``install_status`` con ``phase/progress/detail``) sia davvero quello del
    modulo. Fuori da Android l'installazione si rifiuta da sola, quindi il test
    non tocca né rete né PackageInstaller.
    """

    @pytest.fixture(autouse=True)
    def clean_installer(self):
        from jenny.runtime import update_install

        update_install.reset_install_state()
        yield
        update_install.reset_install_state()

    async def test_off_android_the_install_route_answers_with_a_reason(self) -> None:
        response = await _router().dispatch(
            _request("/api/updates/install"), "/api/updates/install"
        )

        assert response.status_code == 200
        body = _json(response)
        assert body["ok"] is False
        assert body["state"] == "error"
        # Un rifiuto muto sarebbe indistinguibile da un bottone rotto.
        assert body["detail"]

    async def test_the_status_route_reads_the_real_idle_state(self) -> None:
        response = await _router().dispatch(
            _request("/api/updates/status"), "/api/updates/status"
        )

        assert response.status_code == 200
        assert _json(response) == {"phase": "idle", "progress": 0, "detail": ""}


class TestTheManualCheckRoute:
    """``/api/updates/check``: l'unico modo di chiedere "sei ancora viva?".

    Senza, si aspetta il cron — ventiquattr'ore — e un manifest irraggiungibile
    resta indistinguibile da "sei aggiornato". Due cose vanno tenute ferme: la
    rotta fa **rete**, quindi non può stare nel payload delle impostazioni e
    deve reggere il doppio tap; e la risposta porta il payload versione fresco,
    perché la pagina si deve aggiornare senza reload.
    """

    @pytest.fixture(autouse=True)
    def config_on_disk(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        path = tmp_path / "config.json"
        save_config(Config(), path)
        monkeypatch.setattr(get_runtime_context(), "config_path", path)

    async def test_it_requires_a_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def never_called(config: Any) -> None:
            raise AssertionError("the check must not run without a token")

        monkeypatch.setattr(update_check, "check_for_update", never_called)

        response = await _router().dispatch(
            _request("/api/updates/check", token=None), "/api/updates/check"
        )

        assert response.status_code == 401

    async def test_it_runs_the_check_and_returns_the_fresh_version(
        self, monkeypatch: pytest.MonkeyPatch, isolated_state: Path
    ) -> None:
        seen: list[Any] = []

        async def fake(config: Any) -> None:
            seen.append(config)
            _write_state(isolated_state, last_check_ms=1_754_910_000_000)

        monkeypatch.setattr(update_check, "check_for_update", fake)
        monkeypatch.setattr(
            update_check, "last_success_ms", lambda: 1_754_910_000_000, raising=False
        )

        response = await _router().dispatch(
            _request("/api/updates/check"), "/api/updates/check"
        )

        assert response.status_code == 200
        body = _json(response)
        assert body["status"] == "ok"
        # Il payload versione è quello *dopo* il controllo: è ciò che permette
        # alla pagina di mostrare la novità senza ricaricarsi.
        assert body["version"]["latest"] == "0.7.0"
        assert body["version"]["update_available"] is True
        assert isinstance(seen[0], Config)

    async def test_a_check_that_never_reached_the_manifest_says_so(
        self, monkeypatch: pytest.MonkeyPatch, isolated_state: Path
    ) -> None:
        """Il tentativo c'è stato, il manifest no: è il caso che va nominato.

        ``check_for_update`` non solleva mai — rete assente e JSON malformato
        tornano ``None`` — quindi il fallimento si deduce solo dai timestamp.
        """

        async def fake(config: Any) -> None:
            _write_state(isolated_state, last_check_ms=1_754_910_000_000)

        monkeypatch.setattr(update_check, "check_for_update", fake)
        monkeypatch.setattr(
            update_check, "last_success_ms", lambda: 1_754_000_000_000, raising=False
        )

        response = await _router().dispatch(
            _request("/api/updates/check"), "/api/updates/check"
        )

        assert _json(response)["status"] == "failed"

    async def test_a_check_that_never_succeeded_is_a_failure_too(
        self, monkeypatch: pytest.MonkeyPatch, isolated_state: Path
    ) -> None:
        async def fake(config: Any) -> None:
            _write_state(isolated_state, last_check_ms=1_754_910_000_000)

        monkeypatch.setattr(update_check, "check_for_update", fake)
        monkeypatch.setattr(update_check, "last_success_ms", lambda: None, raising=False)

        response = await _router().dispatch(
            _request("/api/updates/check"), "/api/updates/check"
        )

        assert _json(response)["status"] == "failed"

    async def test_without_the_success_signal_it_keeps_quiet(
        self, monkeypatch: pytest.MonkeyPatch, isolated_state: Path
    ) -> None:
        """Build che non espone ``last_success_ms``: la domanda non ha risposta.

        Annunciare "non riuscito" senza saperlo brucerebbe la credibilità
        dell'unico avviso che l'utente ha su questo meccanismo.
        """

        async def fake(config: Any) -> None:
            _write_state(isolated_state, last_check_ms=1_754_910_000_000)

        monkeypatch.setattr(update_check, "check_for_update", fake)
        monkeypatch.delattr(update_check, "last_success_ms", raising=False)

        response = await _router().dispatch(
            _request("/api/updates/check"), "/api/updates/check"
        )

        assert _json(response)["status"] == "ok"

    async def test_a_second_check_does_not_pile_onto_the_first(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Doppio tap: il secondo torna subito con ``busy``, senza fare rete.

        Non si mette in coda di proposito — aspettare in silenzio un controllo
        già in corso non aggiunge niente a chi ha appena premuto due volte.
        """
        started = asyncio.Event()
        release = asyncio.Event()
        calls = 0

        async def slow(config: Any) -> None:
            nonlocal calls
            calls += 1
            started.set()
            await release.wait()

        monkeypatch.setattr(update_check, "check_for_update", slow)
        router = _router()
        first = asyncio.create_task(
            router.dispatch(_request("/api/updates/check"), "/api/updates/check")
        )
        await started.wait()

        second = await router.dispatch(
            _request("/api/updates/check"), "/api/updates/check"
        )

        assert _json(second)["status"] == "busy"
        # Anche il rifiuto porta lo stato corrente: la pagina ha comunque
        # qualcosa da ridisegnare.
        assert "version" in _json(second)
        release.set()
        await first
        assert calls == 1

    async def test_the_lock_is_released_even_when_the_check_explodes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Un lock rimasto chiuso spegnerebbe il bottone fino al riavvio."""

        async def boom(config: Any) -> None:
            raise RuntimeError("httpx blew up")

        monkeypatch.setattr(update_check, "check_for_update", boom)
        logger = MagicMock()

        response = await _router(logger=logger).dispatch(
            _request("/api/updates/check"), "/api/updates/check"
        )

        assert response.status_code == 500
        assert "httpx" not in response.body.decode("utf-8")
        logger.exception.assert_called_once()

        async def fine(config: Any) -> None:
            return None

        monkeypatch.setattr(update_check, "check_for_update", fine)
        again = await _router().dispatch(
            _request("/api/updates/check"), "/api/updates/check"
        )

        assert again.status_code == 200
        assert _json(again)["status"] != "busy"


class TestTheStringsTheUIAsksFor:
    """Ogni chiave usata dal riquadro aggiornamento esiste in entrambe le lingue.

    ``i18n.t`` non fallisce su una chiave assente: ritorna la chiave grezza, e
    l'utente si ritrova ``settings.update.phaseDownloading`` stampato in mezzo
    alle impostazioni. Questo è il posto dove quella dimenticanza si nota.
    """

    _UI = Path(__file__).resolve().parents[2] / "jenny" / "templates" / "ui" / "assets"

    def _keys_used_by_the_settings_page(self) -> set[str]:
        """Ogni file della WebUI, non piu' solo la pagina delle impostazioni.

        La macchina a stati e' uscita di li' (``shared/update-flow.js``) e la
        casa ne e' diventata la seconda vista: cercare in un file solo
        direbbe che meta' delle chiavi sono orfane, e la promessa che questo
        banco custodisce — nessuna chiave senza traduzione, nessuna
        traduzione senza chi la usa — vale sull'interfaccia intera.
        """
        import re

        chiavi: set[str] = set()
        for js in self._UI.rglob("*.js"):
            chiavi |= set(re.findall(r"'(settings\.update\.[A-Za-z]+)'", js.read_text(encoding="utf-8")))
        return chiavi

    def _keys_defined_in(self, locale: str) -> set[str]:
        data = json.loads((self._UI / "i18n" / f"{locale}.json").read_text(encoding="utf-8"))
        return {f"settings.update.{key}" for key in data["settings"]["update"]}

    def test_the_page_uses_at_least_the_headline_and_the_button(self) -> None:
        used = self._keys_used_by_the_settings_page()
        assert "settings.update.install" in used
        assert "settings.update.available" in used
        assert "settings.update.availableCritical" in used
        # "silent" non è "installato": la UI deve avere una stringa per il
        # riavvio in arrivo, altrimenti fingerebbe che sia tutto finito.
        assert "settings.update.restarting" in used
        # Il controllo manuale e la data dell'ultimo esito positivo: senza
        # queste due, un meccanismo morto non ha nessun posto in cui vedersi.
        assert "settings.update.checkNow" in used
        assert "settings.update.lastSuccess" in used

    @pytest.mark.parametrize("locale", ["it", "en"])
    def test_every_key_it_asks_for_is_translated(self, locale: str) -> None:
        missing = self._keys_used_by_the_settings_page() - self._keys_defined_in(locale)
        assert missing == set(), f"chiavi mancanti in {locale}.json"

    @pytest.mark.parametrize("locale", ["it", "en"])
    def test_no_translation_is_left_behind(self, locale: str) -> None:
        """E nessuna chiave tradotta resta senza chi la usa.

        L'altro verso della stessa promessa. Una chiave orfana non rompe niente
        oggi, ma è una stringa che nessuno rilegge: sopravvive alle riscritture
        della UI e alla revisione dei testi, e la prima volta che qualcuno prova
        a riusarla si porta dietro un copy scritto per un'altra schermata.
        """
        orphans = self._keys_defined_in(locale) - self._keys_used_by_the_settings_page()
        assert orphans == set(), f"chiavi non usate in {locale}.json"


class TestThePromptOutcomeIsTerminal:
    """Regressione: la fase ``prompt`` non deve lasciare la UI in attesa.

    Lato server quella fase è *sticky* per costruzione — resta finché l'utente
    non risponde all'installer di sistema. Se il client la tratta come "lavoro
    in corso" continua a fare polling e tiene il bottone disabilitato fino al
    ricaricamento della SPA, e su Android 14+ con update ownership quel ramo con
    conferma è la strada normale, non l'eccezione: la UI sarebbe rotta proprio
    nel caso atteso.

    Il controllo è sul sorgente. Dal 19/09/2026 la macchina sta in
    ``shared/update-flow.js``, estratta perché la casa ne è diventata la
    seconda vista — e c'è anche un banco che la fa girare davvero
    (``test_update_flow_client.py``). Questo resta perché legge le due righe
    che *devono* stare nel ramo terminale, e le legge dove sono scritte.
    """

    _SOURCE = (
        Path(__file__).resolve().parents[2]
        / "jenny" / "templates" / "ui" / "assets" / "shared" / "update-flow.js"
    ).read_text(encoding="utf-8")

    def _body_of(self, name: str) -> str:
        start = self._SOURCE.index(f"  {name}(")
        return self._SOURCE[start:self._SOURCE.index("\n  }\n", start)]

    def test_reaching_the_prompt_settles_the_ui(self) -> None:
        body = self._body_of("_settleAtPrompt")

        assert "this.stop()" in body
        assert "busy: false" in body
        # Riprovare deve restare possibile: la conferma può essere arrivata come
        # notifica e l'utente può averla scartata.
        assert "settings.update.promptNote" in body

    def test_both_the_reply_and_the_polling_route_into_it(self) -> None:
        assert "_settleAtPrompt" in self._body_of("async start")
        assert "_settleAtPrompt" in self._body_of("async _poll")


class TestTheStatusRoute:
    async def test_it_requires_a_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_module(monkeypatch)
        response = await _router().dispatch(
            _request("/api/updates/status", token=None), "/api/updates/status"
        )
        assert response.status_code == 401

    async def test_it_returns_phase_progress_and_detail(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_module(monkeypatch)

        response = await _router().dispatch(
            _request("/api/updates/status"), "/api/updates/status"
        )

        assert response.status_code == 200
        assert _json(response) == {
            "phase": "downloading",
            "progress": 42,
            "detail": "20 MB",
        }

    @pytest.mark.parametrize(
        "raw",
        [
            {},
            {"phase": "teleporting"},
            {"phase": None, "progress": "many", "detail": 7},
            "not a dict",
        ],
    )
    async def test_a_status_it_cannot_read_becomes_idle(
        self, monkeypatch: pytest.MonkeyPatch, raw: Any
    ) -> None:
        """Una fase sconosciuta non deve finire stampata in pagina.

        La UI sceglie la stringa da mostrare *dalla* fase: senza questo taglio
        l'utente leggerebbe ``settings.update.phaseTeleporting``.
        """
        _install_module(monkeypatch, status=lambda: raw)

        response = await _router().dispatch(
            _request("/api/updates/status"), "/api/updates/status"
        )

        assert _json(response) == {"phase": "idle", "progress": 0, "detail": ""}

    async def test_progress_is_clamped_into_range(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_module(
            monkeypatch,
            status=lambda: {"phase": "installing", "progress": 420, "detail": ""},
        )

        response = await _router().dispatch(
            _request("/api/updates/status"), "/api/updates/status"
        )

        assert _json(response)["progress"] == 100

    async def test_an_exploding_status_maps_to_500(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def boom() -> dict[str, Any]:
            raise RuntimeError("state file vanished")

        _install_module(monkeypatch, status=boom)
        logger = MagicMock()

        response = await _router(logger=logger).dispatch(
            _request("/api/updates/status"), "/api/updates/status"
        )

        assert response.status_code == 500
        logger.exception.assert_called_once()

    async def test_a_build_without_the_installer_says_so(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _no_install_module(monkeypatch)

        response = await _router().dispatch(
            _request("/api/updates/status"), "/api/updates/status"
        )

        assert response.status_code == 503
