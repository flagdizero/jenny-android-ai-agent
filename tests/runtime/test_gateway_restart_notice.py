"""Un gateway che non può ripartire da background lo dice con una notifica.

Da Android 12 un FGS non parte da background, salvo un'allowlist che lo
permetta. Senza ``SCHEDULE_EXACT_ALARM`` e senza esenzione dalla batteria
nessuna sveglia e nessun job la concede (v.
``test_gateway_fgs_fallback_needs_exact_alarms``): un gateway morto restava
morto finché l'utente non apriva l'app, e nessuno glielo diceva.

Il tocco su una notifica la concede invece: ``NotificationManagerService``
registra ogni ``PendingIntent`` di una notifica con
``TEMPORARY_ALLOWLIST_TYPE_FOREGROUND_SERVICE_ALLOWED`` (AOSP
``android14-release`` e ``main``), e ``PendingIntentRecord.sendInner`` applica
l'allowlist prima di ``startServiceInPackage``. Quindi, quando l'avvio viene
rifiutato e nessuna sveglia esatta lo riproverà, Jenny posta «Jenny è ferma —
tocca per riavviarla», e la toglie quando il service torna in foreground.

Il Kotlin in CI non gira: si leggono i sorgenti col solo codice
(``support.kotlin_source``), le risorse col parser XML.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from support.kotlin_source import (
    ANDROID_SRC,
    block_after,
    function_body,
    read_code,
    read_source,
)

_RES = Path(__file__).resolve().parents[2] / "android/app/src/main/res"


def _strings(folder: str) -> dict[str, str]:
    path = _RES / folder / "strings.xml"
    if not path.is_file():
        pytest.skip("risorse Android non presenti in questo checkout")
    root = ET.parse(path).getroot()
    return {el.get("name", ""): (el.text or "") for el in root.iter("string")}


def test_the_notice_lives_in_its_own_file() -> None:
    """Senza, ogni altro test di questo file salterebbe invece di fallire."""
    if not (ANDROID_SRC / "GatewayService.kt").is_file():
        pytest.skip("sorgente Android non presente in questo checkout")
    assert (ANDROID_SRC / "RestartNotice.kt").is_file()


def _starter_catch() -> str:
    body = function_body(read_code("GatewayStarter"), "ensureUp")
    return block_after(body, r"catch \(e: Exception\)")


def test_a_refused_start_with_no_recovery_alarm_posts_the_notice() -> None:
    catch = _starter_catch()
    assert "recoveryArmed = PowerBridge.scheduleWake(" in catch, (
        "la sveglia di recupero deve dire se è armata: è ciò che decide la notifica"
    )
    notice = catch.index("RestartNotice.showIfRefused(appContext, e, reason)")
    guard = block_after(catch, r"if \(!recoveryArmed\)")
    assert "RestartNotice.showIfRefused(" in guard, (
        "con una sveglia esatta in arrivo la notifica è rumore: la riprova ce la fa da sé"
    )
    assert catch.index("if (alarmFallback)") < notice, (
        "la notifica si decide dopo aver provato ad armare la sveglia, non prima"
    )


def test_only_a_background_start_refusal_is_announced() -> None:
    """Il tocco concede l'avvio da background, e cura solo quel rifiuto."""
    body = function_body(read_code("RestartNotice"), "showIfRefused")
    check = re.search(
        r"val refused = Build\.VERSION\.SDK_INT\s*>=\s*Build\.VERSION_CODES\.S\s*&&\s*"
        r"e is ForegroundServiceStartNotAllowedException",
        body,
    )
    assert check, "la classe esiste da API 31: il controllo di versione la precede"
    bail = re.search(r"if \(!refused\) return false", body)
    assert bail and bail.start() < body.index("post("), (
        "un'eccezione diversa non deve mostrare una notifica che non la ripara"
    )


def test_a_live_service_is_not_announced_as_stopped() -> None:
    """Col service vivo nessun avvio passerebbe da ``onStartCommand`` a toglierla."""
    body = function_body(read_code("RestartNotice"), "showIfRefused")
    alive = block_after(
        body, r"if \(GatewayService\.isRunning && !GatewayService\.isGatewayThreadDead\)"
    )
    assert "return false" in alive
    assert body.index("GatewayService.isRunning") < body.index("post(")


def test_the_tap_is_an_immutable_explicit_start_of_the_service() -> None:
    body = function_body(read_code("RestartNotice"), "tapIntent")
    assert "PendingIntent.getForegroundService(" in body
    assert "Intent(context, GatewayService::class.java)" in body, "intent esplicito"
    assert "PendingIntent.FLAG_IMMUTABLE" in body
    assert "FLAG_MUTABLE" not in body.replace("FLAG_IMMUTABLE", "")
    post = function_body(read_code("RestartNotice"), "post")
    assert ".setContentIntent(tapIntent(appContext))" in post


def test_the_notice_is_one_and_is_replaced_not_stacked() -> None:
    code = read_code("RestartNotice")
    post = function_body(code, "post")
    assert re.search(r"\.notify\(NOTICE_ID,", post), "id fisso: un secondo post la sostituisce"
    assert ".setOnlyAlertOnce(true)" in post
    assert ".setAutoCancel(false)" in post, (
        "la toglie il service quando riparte davvero, non il tocco che potrebbe fallire"
    )
    clear = function_body(code, "clear")
    assert "cancel(NOTICE_ID)" in clear


def test_the_notice_goes_away_when_the_service_is_in_foreground() -> None:
    body = function_body(read_code("GatewayService"), "onStartCommand")
    refused = block_after(body, r"if \(!startForegroundCompat\(")
    assert "RestartNotice.clear(" not in refused
    after = body[body.index(refused) + len(refused) :]
    assert "RestartNotice.clear(this)" in after, (
        "ogni avvio riuscito — anche l'app aperta a mano — toglie la notifica"
    )


def test_without_post_notifications_it_logs_and_does_not_crash() -> None:
    post = function_body(read_code("RestartNotice"), "post")
    permission = block_after(post, r"if \(!JennyChannels\.canPostNotifications\(appContext\)\)")
    assert "Log.w(" in permission and "return false" in permission
    assert post.index("canPostNotifications") < post.index(".notify(")
    check = read_code("JennyChannels")
    check = check[check.index("fun canPostNotifications(") : check.index("fun openJenny(")]
    assert "Manifest.permission.POST_NOTIFICATIONS" in check and "TIRAMISU" in check
    guarded = block_after(post, r"\btry\b")
    assert ".notify(" in guarded, "un rifiuto del sistema non deve risalire a chi ripara"
    assert re.search(r"catch \(e: Exception\)", post)


def test_the_channel_is_its_own_quiet_but_visible_one() -> None:
    code = read_code("RestartNotice")
    channel = function_body(code, "ensureChannel")
    assert "NotificationManager.IMPORTANCE_LOW" in channel
    assert "JennyChannels.ensure(" in channel and "silent = true" in channel
    assert "createNotificationChannel(" in function_body(read_code("JennyChannels"), "ensure")
    src = read_source("RestartNotice")
    assert re.search(r'CHANNEL_ID\s*=\s*"jenny_restart"', src)
    for key in ("restart_channel_name", "restart_channel_description"):
        assert f"R.string.{key}" in channel


@pytest.mark.parametrize("folder", ["values", "values-it"])
def test_the_notice_words_exist_in_both_languages(folder: str) -> None:
    strings = _strings(folder)
    for key in (
        "restart_channel_name",
        "restart_channel_description",
        "restart_notice_title",
        "restart_notice_text",
    ):
        assert strings.get(key, "").strip(), f"{folder}/strings.xml senza {key}"
