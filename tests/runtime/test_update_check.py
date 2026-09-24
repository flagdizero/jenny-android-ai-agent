"""Test del nucleo dell'updater (``jenny/runtime/update_check.py``).

Il manifest è dato che arriva dalla rete: la maggior parte di questi test
descrive che cosa succede quando **non** è quello che ci si aspetta — schema di
un'era futura, campi del tipo sbagliato, JSON troncato, niente rete. L'esito
richiesto è sempre lo stesso: ``None`` e una riga di log, mai un'eccezione che
risalga al cron.

La rete non viene mai toccata davvero: ``httpx.MockTransport`` sta al posto di
GitHub, e il ``versionCode`` installato è sempre iniettato.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from jenny.runtime import update_check
from jenny.runtime.update_check import UpdateInfo

# Riferimenti presi prima di qualunque monkeypatch: alcuni test sostituiscono
# proprio queste funzioni nel modulo, e chi le rimpiazza deve poter chiamare
# l'originale invece della propria sostituzione.
_REAL_FETCH = update_check._fetch_manifest
_REAL_INSTALLED_VERSION_CODE = update_check.installed_version_code

_MANIFEST: dict[str, Any] = {
    "schema": 1,
    "version_code": 9,
    "version_name": "0.7.0",
    "apk_url": (
        "https://github.com/flagdizero/jenny-android-ai-agent/releases/download/"
        "v0.7.0/jenny-0.7.0.apk"
    ),
    "sha256": "a" * 64,
    "size": 48210944,
    "notes_url": "https://github.com/flagdizero/jenny-android-ai-agent/releases/tag/v0.7.0",
    "summary_it": "Aggiornamenti in-app e meno consumo a schermo spento.",
    "summary_en": "In-app updates and less battery drain.",
    "min_supported_code": 6,
    "rollout": 100,
    "critical": False,
}


def _manifest(**overrides: Any) -> dict[str, Any]:
    return {**_MANIFEST, **overrides}


def _broken_android_context() -> Any:
    """Siamo su Android, ma il PackageManager non risponde."""

    def explode() -> Any:
        raise RuntimeError("no such service")

    return SimpleNamespace(getPackageManager=explode)


def _config(**updates: Any) -> Any:
    """Doppio esplicito del Config: l'updater legge due soli campi."""
    return SimpleNamespace(
        updates=SimpleNamespace(
            enabled=True,
            manifest_url=updates.get("manifest_url", "https://example.invalid/latest.json"),
            check_interval_h=24,
            notify_in_chat=True,
        ),
        agents=SimpleNamespace(
            defaults=SimpleNamespace(language=updates.get("language", "it"))
        ),
    )


@pytest.fixture(autouse=True)
def isolated_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Stato dell'updater in una directory usa-e-getta, per test."""
    path = tmp_path / "update_state.json"
    monkeypatch.setattr(update_check, "_state_path", lambda: path)
    return path


@pytest.fixture(autouse=True)
def installed_at_8(monkeypatch: pytest.MonkeyPatch) -> None:
    """Il device finge di avere installata la 0.6.6 (versionCode 8)."""
    monkeypatch.setattr(update_check, "installed_version_code", lambda: 8)


def _serve(payload: Any, *, status: int = 200) -> httpx.AsyncClient:
    """Client httpx che risponde *payload* a qualunque URL."""
    body = payload if isinstance(payload, (bytes, str)) else json.dumps(payload)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, content=body)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def _check(monkeypatch: pytest.MonkeyPatch, manifest: Any, **cfg: Any):
    """Esegue ``check_for_update`` con la rete finta che serve *manifest*."""
    client = _serve(manifest)

    async def patched(url: str, **_kwargs: Any):
        return await _REAL_FETCH(url, client=client)

    monkeypatch.setattr(update_check, "_fetch_manifest", patched)
    monkeypatch.setattr(update_check, "validate_url_target", lambda _url: (True, ""))
    try:
        return await update_check.check_for_update(_config(**cfg))
    finally:
        await client.aclose()


class TestAValidManifestBecomesAnUpdate:
    async def test_it_returns_the_parsed_release(self, monkeypatch) -> None:
        info = await _check(monkeypatch, _MANIFEST)

        assert isinstance(info, UpdateInfo)
        assert (info.version_code, info.version_name) == (9, "0.7.0")
        assert info.sha256 == "a" * 64
        assert info.size == 48210944
        assert info.critical is False

    async def test_the_summary_follows_the_configured_language(self, monkeypatch) -> None:
        italian = await _check(monkeypatch, _MANIFEST, language="it")
        english = await _check(monkeypatch, _MANIFEST, language="en")

        assert italian is not None and italian.summary == _MANIFEST["summary_it"]
        assert english is not None and english.summary == _MANIFEST["summary_en"]

    async def test_an_unknown_language_falls_back_to_english(self, monkeypatch) -> None:
        info = await _check(monkeypatch, _MANIFEST, language="de")

        assert info is not None
        assert info.summary == _MANIFEST["summary_en"]

    async def test_the_check_timestamp_is_persisted(self, monkeypatch, isolated_state) -> None:
        await _check(monkeypatch, _MANIFEST)

        assert isinstance(update_check.last_check_ms(), int)
        assert json.loads(isolated_state.read_text())["latest"]["version_code"] == 9


class TestAManifestThatMustNotBeBelieved:
    """Ogni caso qui vale ``None``, e nessuno solleva."""

    async def test_the_same_version_is_not_an_update(self, monkeypatch) -> None:
        assert await _check(monkeypatch, _manifest(version_code=8)) is None

    async def test_an_older_version_is_not_an_update(self, monkeypatch) -> None:
        assert await _check(monkeypatch, _manifest(version_code=7)) is None

    async def test_a_newer_schema_is_never_trusted(self, monkeypatch) -> None:
        """Un client vecchio non sa che cosa significhino i campi di uno schema 2."""
        assert await _check(monkeypatch, _manifest(schema=2)) is None

    async def test_malformed_json_is_not_an_update(self, monkeypatch) -> None:
        assert await _check(monkeypatch, "{ not json at all") is None

    @pytest.mark.parametrize(
        "broken",
        [
            {"version_code": "9"},
            {"version_code": True},
            {"version_code": 0},
            {"version_name": ""},
            {"version_name": 7},
            {"sha256": "z" * 64},
            {"sha256": "abc"},
            {"size": -1},
            {"size": None},
            {"apk_url": "http://example.com/jenny.apk"},
            {"apk_url": ""},
            {"critical": "yes"},
            {"rollout": "50"},
            {"min_supported_code": -1},
            {"schema": None},
        ],
    )
    async def test_a_field_of_the_wrong_type_rejects_the_manifest(
        self, monkeypatch, broken
    ) -> None:
        assert await _check(monkeypatch, _manifest(**broken)) is None

    @pytest.mark.parametrize("payload", ["[]", '"nope"', "null", "42"])
    async def test_a_manifest_that_is_not_an_object_is_rejected(
        self, monkeypatch, payload
    ) -> None:
        assert await _check(monkeypatch, payload) is None

    async def test_display_fields_degrade_instead_of_rejecting(self, monkeypatch) -> None:
        """Un sommario scritto male non è un motivo per non annunciare una fix."""
        info = await _check(
            monkeypatch,
            _manifest(summary_it=None, summary_en=42, notes_url="ftp://nope"),
        )

        assert info is not None
        assert info.summary == ""
        assert info.notes_url == ""

    async def test_a_build_below_min_supported_is_not_offered(self, monkeypatch) -> None:
        assert await _check(monkeypatch, _manifest(min_supported_code=99)) is None


class TestWithoutNetwork:
    async def test_a_failing_request_returns_none(self, monkeypatch) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("no route to host")

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        monkeypatch.setattr(update_check, "validate_url_target", lambda _url: (True, ""))
        try:
            assert await update_check._fetch_manifest(
                "https://example.invalid/latest.json", client=client
            ) is None
        finally:
            await client.aclose()

    async def test_the_whole_check_survives_a_dead_network(self, monkeypatch) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("no route to host")

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

        async def patched(url: str, **_kwargs: Any):
            return await _REAL_FETCH(url, client=client)

        monkeypatch.setattr(update_check, "_fetch_manifest", patched)
        monkeypatch.setattr(update_check, "validate_url_target", lambda _url: (True, ""))
        try:
            assert await update_check.check_for_update(_config()) is None
        finally:
            await client.aclose()

    async def test_an_http_error_status_returns_none(self, monkeypatch) -> None:
        client = _serve("nope", status=503)
        monkeypatch.setattr(update_check, "validate_url_target", lambda _url: (True, ""))
        try:
            assert await update_check._fetch_manifest(
                "https://example.invalid/latest.json", client=client
            ) is None
        finally:
            await client.aclose()

    async def test_a_blocked_target_returns_none(self, monkeypatch) -> None:
        """La validazione SSRF vale anche qui: l'URL del manifest è configurabile."""
        client = _serve(_MANIFEST)
        monkeypatch.setattr(
            update_check, "validate_url_target", lambda _url: (False, "blocked")
        )
        try:
            assert await update_check._fetch_manifest(
                "https://169.254.169.254/latest.json", client=client
            ) is None
        finally:
            await client.aclose()

    async def test_a_non_https_manifest_url_is_refused(self) -> None:
        assert await update_check._fetch_manifest("http://example.com/latest.json") is None

    async def test_a_redirect_down_to_http_is_refused(self, monkeypatch) -> None:
        """Https a ogni salto, non solo al primo (24/09/2026).

        Il controllo stava solo sull'URL di partenza: un redirect verso http
        passava, e il manifest — che dice quale APK installare e con che hash —
        arrivava in chiaro. Come per il download dell'APK in
        ``update_install``, il downgrade a meta' catena e' un rifiuto.
        """
        served: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            served.append(str(request.url))
            if request.url.scheme == "https":
                return httpx.Response(302, headers={"location": "http://cdn.example/latest.json"})
            return httpx.Response(200, content=json.dumps(_MANIFEST))

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        monkeypatch.setattr(update_check, "validate_url_target", lambda _url: (True, ""))
        try:
            assert await update_check._fetch_manifest(
                "https://example.invalid/latest.json", client=client
            ) is None
        finally:
            await client.aclose()
        assert served == ["https://example.invalid/latest.json"], "l'hop in chiaro non va chiesto"

    async def test_an_oversized_manifest_is_refused(self, monkeypatch) -> None:
        client = _serve("x" * (update_check._MAX_MANIFEST_BYTES + 1))
        monkeypatch.setattr(update_check, "validate_url_target", lambda _url: (True, ""))
        try:
            assert await update_check._fetch_manifest(
                "https://example.invalid/latest.json", client=client
            ) is None
        finally:
            await client.aclose()

    async def test_an_unexpected_failure_never_escapes_to_the_cron(
        self, monkeypatch
    ) -> None:
        def explode(*_a: Any, **_k: Any) -> Any:
            raise RuntimeError("disk on fire")

        monkeypatch.setattr(update_check, "_fetch_manifest", explode)

        assert await update_check.check_for_update(_config()) is None

    async def test_the_last_check_is_recorded_even_when_the_fetch_fails(
        self, monkeypatch
    ) -> None:
        async def no_manifest(_url: str, **_kw: Any) -> None:
            return None

        monkeypatch.setattr(update_check, "_fetch_manifest", no_manifest)

        assert await update_check.check_for_update(_config()) is None
        assert isinstance(update_check.last_check_ms(), int)


class TestTheLastSuccessfulCheck:
    """``last_success_ms`` esiste per un guasto che nessuno vedrebbe altrimenti.

    ``last_check_ms`` si muove anche quando il fetch fallisce, ed è giusto così:
    dice quando ci si è provato. Ma da solo non distingue "guardato, niente di
    nuovo" da "sono mesi che non riesco a guardare", e su un telefono headless
    quella è la differenza fra un updater sano e uno morto in silenzio.
    """

    async def test_nothing_succeeded_before_the_first_check(self) -> None:
        assert update_check.last_success_ms() is None

    async def test_a_successful_check_records_both_timestamps(self, monkeypatch) -> None:
        await _check(monkeypatch, _MANIFEST)

        assert isinstance(update_check.last_check_ms(), int)
        assert update_check.last_success_ms() == update_check.last_check_ms()

    async def test_a_failed_fetch_moves_only_the_attempt(self, monkeypatch) -> None:
        async def no_manifest(_url: str, **_kw: Any) -> None:
            return None

        monkeypatch.setattr(update_check, "_fetch_manifest", no_manifest)

        assert await update_check.check_for_update(_config()) is None
        assert isinstance(update_check.last_check_ms(), int)
        assert update_check.last_success_ms() is None

    async def test_a_later_failure_does_not_erase_the_last_success(
        self, monkeypatch
    ) -> None:
        """È il caso che rende utile il campo: l'ultima volta che ha funzionato."""
        await _check(monkeypatch, _MANIFEST)
        succeeded_at = update_check.last_success_ms()

        async def no_manifest(_url: str, **_kw: Any) -> None:
            return None

        monkeypatch.setattr(update_check, "_fetch_manifest", no_manifest)
        assert await update_check.check_for_update(_config()) is None

        assert update_check.last_success_ms() == succeeded_at
        last_check = update_check.last_check_ms()
        assert last_check is not None and succeeded_at is not None
        assert last_check >= succeeded_at

    def test_a_state_written_before_the_field_existed_reads_none(
        self, isolated_state: Path
    ) -> None:
        """Nessuna migrazione: chi aggiorna ha uno stato senza il campo."""
        isolated_state.write_text(
            json.dumps({"schema": 1, "install_id": "abc", "last_check_ms": 1700000000000}),
            encoding="utf-8",
        )

        assert update_check.last_check_ms() == 1700000000000
        assert update_check.last_success_ms() is None


class TestTheRolloutGate:
    def test_it_is_deterministic_for_the_same_install_and_version(self) -> None:
        first = update_check._rollout_bucket("install-a", 9)
        second = update_check._rollout_bucket("install-a", 9)

        assert first == second
        assert 0 <= first < 100

    def test_it_depends_on_the_version_too(self) -> None:
        """Senza la versione, lo stesso device sarebbe per sempre in coda."""
        buckets = {update_check._rollout_bucket("install-a", code) for code in range(9, 40)}

        assert len(buckets) > 1

    def test_a_full_rollout_includes_everyone(self) -> None:
        assert update_check._rollout_allows("x", 9, rollout=100, critical=False)
        assert update_check._rollout_allows("x", 9, rollout=1000, critical=False)

    def test_a_zero_rollout_includes_nobody(self) -> None:
        assert not update_check._rollout_allows("x", 9, rollout=0, critical=False)

    def test_critical_skips_the_wave(self) -> None:
        """Una fix di sicurezza non si consegna a scaglioni."""
        assert update_check._rollout_allows("x", 9, rollout=1, critical=True)

    def test_the_kill_switch_stops_a_critical_release_too(self) -> None:
        """``rollout: 0`` è il freno d'emergenza, e ``critical`` non lo scavalca.

        Saltare l'ondata e ignorare lo stop sono due cose diverse. La procedura
        d'emergenza documentata (``docs/contribute/publish-a-release.md``) è
        ripubblicare il manifest a zero, e la fix di una build rotta si pubblica
        proprio con ``--critical``: se lo zero cedesse davanti a ``critical``, il
        freno mancherebbe esattamente sulle release per cui esiste.
        """
        assert not update_check._rollout_allows("x", 9, rollout=0, critical=True)

    async def test_an_excluded_install_gets_no_update(self, monkeypatch) -> None:
        monkeypatch.setattr(update_check, "_rollout_bucket", lambda *_a: 99)

        assert await _check(monkeypatch, _manifest(rollout=10)) is None

    async def test_an_excluded_install_still_gets_a_critical_update(
        self, monkeypatch
    ) -> None:
        monkeypatch.setattr(update_check, "_rollout_bucket", lambda *_a: 99)

        info = await _check(monkeypatch, _manifest(rollout=10, critical=True))

        assert info is not None and info.critical is True

    async def test_a_withdrawn_critical_release_is_not_offered(
        self, monkeypatch
    ) -> None:
        """Il freno vale fino in fondo alla catena, non solo nel gate."""
        monkeypatch.setattr(update_check, "_rollout_bucket", lambda *_a: 0)

        assert await _check(monkeypatch, _manifest(rollout=0, critical=True)) is None


class TestTheInstallId:
    def test_it_is_generated_once_and_persisted(self) -> None:
        first = update_check.install_id()

        assert update_check.install_id() == first
        assert len(first) == 36

    def test_it_survives_a_reread_from_disk(self, isolated_state: Path) -> None:
        generated = update_check.install_id()

        assert json.loads(isolated_state.read_text())["install_id"] == generated


class TestTheNotifiedMarker:
    def test_nothing_is_notified_at_first(self) -> None:
        assert update_check.notified_version_code() is None

    def test_marking_survives_a_reread(self) -> None:
        update_check.mark_notified(9)

        assert update_check.notified_version_code() == 9

    def test_marking_does_not_lose_the_install_id(self) -> None:
        install = update_check.install_id()
        update_check.mark_notified(9)

        assert update_check.install_id() == install


class TestCorruptedState:
    @pytest.mark.parametrize(
        "content",
        ["", "{ truncated", "[]", '"a string"', '{"schema": 99, "install_id": "x"}'],
    )
    def test_it_starts_clean_instead_of_failing(
        self, isolated_state: Path, content: str
    ) -> None:
        isolated_state.write_text(content, encoding="utf-8")

        assert update_check.last_check_ms() is None
        assert update_check.last_success_ms() is None
        assert update_check.notified_version_code() is None
        assert update_check.cached_update() is None
        # E resta scrivibile: lo stato rotto viene semplicemente rimpiazzato.
        assert update_check.install_id()

    def test_a_missing_state_file_is_not_an_error(self, isolated_state: Path) -> None:
        assert not isolated_state.exists()
        assert update_check.cached_update() is None
        assert update_check.last_check_ms() is None


class TestCachedUpdate:
    async def test_it_repeats_the_last_answer_without_network(self, monkeypatch) -> None:
        live = await _check(monkeypatch, _MANIFEST)

        assert update_check.cached_update() == live

    async def test_it_uses_the_language_of_the_last_check(self, monkeypatch) -> None:
        await _check(monkeypatch, _MANIFEST, language="en")

        remembered = update_check.cached_update()
        overridden = update_check.cached_update("it")

        assert remembered is not None and remembered.summary == _MANIFEST["summary_en"]
        assert overridden is not None and overridden.summary == _MANIFEST["summary_it"]

    async def test_an_installed_version_stops_being_offered(self, monkeypatch) -> None:
        """Dopo l'installazione la stessa cache non deve più proporre nulla."""
        await _check(monkeypatch, _MANIFEST)
        monkeypatch.setattr(update_check, "installed_version_code", lambda: 9)

        assert update_check.cached_update() is None

    def test_a_tampered_cached_manifest_is_revalidated(self, isolated_state: Path) -> None:
        isolated_state.write_text(
            json.dumps(
                {
                    "schema": 1,
                    "install_id": "abc",
                    "latest": {**_MANIFEST, "sha256": "nope"},
                }
            ),
            encoding="utf-8",
        )

        assert update_check.cached_update() is None


class TestTheInstalledVersionCode:
    def test_it_reads_the_package_manager_on_android(self, monkeypatch) -> None:
        package_info = SimpleNamespace(getLongVersionCode=lambda: 12, versionCode=12)
        context = SimpleNamespace(
            getPackageManager=lambda: SimpleNamespace(
                getPackageInfo=lambda _name, _flags: package_info
            ),
            getPackageName=lambda: "com.flagdizero.jenny",
        )
        monkeypatch.setattr(update_check, "get_android_context", lambda: context)

        assert _REAL_INSTALLED_VERSION_CODE() == 12

    def test_it_falls_back_to_the_deprecated_field_below_api_28(self, monkeypatch) -> None:
        def missing() -> int:
            raise AttributeError("getLongVersionCode requires API 28")

        package_info = SimpleNamespace(getLongVersionCode=missing, versionCode=8)
        context = SimpleNamespace(
            getPackageManager=lambda: SimpleNamespace(
                getPackageInfo=lambda _name, _flags: package_info
            ),
            getPackageName=lambda: "com.flagdizero.jenny",
        )
        monkeypatch.setattr(update_check, "get_android_context", lambda: context)

        assert _REAL_INSTALLED_VERSION_CODE() == 8

    def test_off_android_it_degrades_to_zero(self, monkeypatch) -> None:
        monkeypatch.setattr(update_check, "get_android_context", lambda: None)

        assert _REAL_INSTALLED_VERSION_CODE() == 0

    def test_a_broken_package_manager_does_not_raise(self, monkeypatch) -> None:
        monkeypatch.setattr(
            update_check, "get_android_context", _broken_android_context
        )

        assert _REAL_INSTALLED_VERSION_CODE() == update_check._UNKNOWN_ANDROID_VERSION_CODE

    def test_a_broken_package_manager_does_not_look_like_a_fresh_install(
        self, monkeypatch
    ) -> None:
        """Su Android il fallback a zero sarebbe peggio dell'errore stesso.

        Zero significa "più vecchio di qualunque release": con quel valore ogni
        manifest risulterebbe più nuovo e il ri-controllo che l'installer fa
        prima di applicare — l'unico guard contro il downgrade — passerebbe
        sempre. Il valore di ripiego deve invece far *rifiutare* l'aggiornamento.
        """
        monkeypatch.setattr(
            update_check, "get_android_context", _broken_android_context
        )

        installed = _REAL_INSTALLED_VERSION_CODE()

        assert installed > _MANIFEST["version_code"]
        assert installed != 0

    async def test_no_update_is_offered_while_the_package_manager_is_broken(
        self, monkeypatch
    ) -> None:
        """Fail-closed end-to-end: manifest valido, ma niente da proporre."""
        monkeypatch.setattr(
            update_check, "installed_version_code", _REAL_INSTALLED_VERSION_CODE
        )
        monkeypatch.setattr(
            update_check, "get_android_context", _broken_android_context
        )

        assert await _check(monkeypatch, _MANIFEST) is None
