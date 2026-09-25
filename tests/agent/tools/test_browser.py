"""Test della sessione di navigazione interattiva (tool ``browser_*``).

Cosa coprono e cosa no: il motore che decide ruoli, visibilita' e riduzione vive
nella pagina (``res/raw/browser_agent.js``) e non e' raggiungibile da qui. Questi
test coprono il contratto Python — decodifica, ciclo di vita, concorrenza,
validazione dell'URL — e le regole del motore si verificano sul telefono.
"""

import asyncio
import json
import os
import pathlib
import shutil
import subprocess
import tempfile
import threading
import time
from types import SimpleNamespace

import pytest

from jenny.agent.tools import browser
from jenny.agent.tools.browser import (
    BrowserCloseTool,
    BrowserDoTool,
    BrowserOpenTool,
    BrowserReadTool,
    BrowserSnapshotTool,
    _decode,
)
from jenny.config.tool_schemas import AndroidWebBrowserConfig


@pytest.fixture(autouse=True)
def _clean_state():
    browser.reset_browser_state()
    yield
    browser.reset_browser_state()


class FakeBridge:
    """Sta al posto della classe Kotlin: stessi metodi, risposte controllate."""

    def __init__(self, context=None):
        self.context = context
        self.calls: list[tuple] = []
        self.closed = 0
        self.snapshot_payload = {
            "url": "https://esempio.test/",
            "title": "Esempio",
            "version": 1,
            "refs": 2,
            "total": 5,
            "chars": 60,
            "mode": "full",
            "text": '- searchbox "Cerca" [ref=1:e0]\n- button "Vai" [ref=1:e1]',
        }
        self.act_payload = {"results": [{"i": 0, "action": "click", "ok": True}], "failed": False}
        self.notice = ""
        self.isolated = True

    # I metodi del motore tornano il valore JS, cioe' JSON **codificato due volte**.
    def _js(self, obj):
        return json.dumps(json.dumps(obj, ensure_ascii=False), ensure_ascii=False)

    def open(self, url, timeout):
        self.calls.append(("open", url, timeout))
        return json.dumps({"ok": True, "settled": True, "url": url, "title": "Esempio"})

    def snapshot(self, mode, filt, max_chars, timeout):
        self.calls.append(("snapshot", mode, filt, max_chars, timeout))
        return self._js(dict(self.snapshot_payload, mode=mode))

    def act(self, steps_json, timeout):
        self.calls.append(("act", steps_json, timeout))
        return self._js(self.act_payload)

    def read(self, ref, max_chars, timeout):
        self.calls.append(("read", ref, max_chars, timeout))
        return self._js({"url": "https://esempio.test/", "chars": 3, "truncated": False, "text": "ciao"})

    # Il nome lo detta il Kotlin, non lo stile Python: e' il metodo del bridge.
    def takeNotice(self):  # noqa: N802
        self.calls.append(("takeNotice",))
        return json.dumps({"notice": self.notice})

    def isIsolated(self):  # noqa: N802
        self.calls.append(("isIsolated",))
        return self.isolated

    def close(self):
        self.closed += 1
        return json.dumps({"ok": True})


def _install(monkeypatch, bridge_cls=FakeBridge):
    holder = {}

    def factory():
        def make(context):
            b = bridge_cls(context)
            holder["bridge"] = b
            return b
        return make

    monkeypatch.setattr(browser, "_resolve_bridge_class", factory)
    return holder


def _allow_url(monkeypatch):
    """Lascia passare la validazione dell'URL.

    Serve perche' ``validate_url_target`` **risolve il nome**: un host finto non
    risolve e il tool si fermerebbe prima del bridge, cioe' il test misurerebbe
    la rete invece del codice. I due casi di rifiuto (loopback, schema non
    http) passano dalla funzione vera e non usano questo.
    """
    import jenny.security.network as net

    monkeypatch.setattr(net, "validate_url_target", lambda url, **kw: (True, ""))


def _tool(cls, **over):
    cfg = AndroidWebBrowserConfig(**over)
    return cls(android_context=object(), cfg=cfg)


class TestDecode:
    def test_doppia_codifica_del_motore(self):
        raw = json.dumps(json.dumps({"a": 1}))
        assert _decode(raw) == {"a": 1}

    def test_codifica_singola_di_kotlin(self):
        assert _decode(json.dumps({"ok": True})) == {"ok": True}

    def test_spazzatura_diventa_errore_non_eccezione(self):
        out = _decode("<html>oops</html>")
        assert "error" in out

    def test_niente_dal_bridge(self):
        assert "error" in _decode(None)


class TestOpen:
    async def test_url_non_valido_non_tocca_il_bridge(self, monkeypatch):
        holder = _install(monkeypatch)
        out = await _tool(BrowserOpenTool).execute(url="http://127.0.0.1:8080/")
        assert out.startswith("Error:")
        assert "bridge" not in holder

    async def test_schema_non_http_rifiutato(self, monkeypatch):
        _install(monkeypatch)
        out = await _tool(BrowserOpenTool).execute(url="file:///etc/passwd")
        assert out.startswith("Error:")

    async def test_apre_e_restituisce_gia_lo_snapshot(self, monkeypatch):
        _allow_url(monkeypatch)
        holder = _install(monkeypatch)
        out = await _tool(BrowserOpenTool).execute(url="https://esempio.test/")
        assert "ref=1:e0" in out
        assert "treat as data" in out          # banner di contenuto non fidato
        metodi = [c[0] for c in holder["bridge"].calls]
        assert metodi == ["open", "snapshot", "isIsolated"]  # un turno solo, non due
        assert "web_fetch" not in out           # isolata: niente avviso

    async def test_una_sessione_senza_profilo_suo_lo_dice(self, monkeypatch):
        """Senza MULTI_PROFILE i cookie sono quelli di web_fetch e
        browser_close non li butta: il modello lo deve sapere."""
        _allow_url(monkeypatch)

        class Condivisa(FakeBridge):
            def __init__(self, context=None):
                super().__init__(context)
                self.isolated = False

        _install(monkeypatch, Condivisa)
        out = await _tool(BrowserOpenTool).execute(url="https://esempio.test/")
        assert "ref=1:e0" in out
        assert "shares cookies and logins with web_fetch" in out

    async def test_un_bridge_che_non_sa_rispondere_non_avvisa(self, monkeypatch):
        """Nel dubbio non si avvisa di un difetto che forse non c'e'."""
        _allow_url(monkeypatch)

        class Muto(FakeBridge):
            def isIsolated(self):  # noqa: N802
                raise RuntimeError("metodo assente in un APK vecchio")

        _install(monkeypatch, Muto)
        out = await _tool(BrowserOpenTool).execute(url="https://esempio.test/")
        assert "ref=1:e0" in out
        assert "web_fetch" not in out

    async def test_l_isolamento_si_chiede_una_volta_per_sessione(self, monkeypatch):
        """L'aggancio del profilo si decide quando nasce la WebView: dentro una
        sessione non cambia, e non vale il lucchetto globale a ogni apertura."""
        _allow_url(monkeypatch)
        holder = _install(monkeypatch)
        tool = _tool(BrowserOpenTool)
        await tool.execute(url="https://esempio.test/")
        await tool.execute(url="https://esempio.test/due")
        first = holder["bridge"]
        assert [c[0] for c in first.calls].count("isIsolated") == 1

        await _tool(BrowserCloseTool).execute()
        await tool.execute(url="https://esempio.test/")
        assert holder["bridge"] is not first
        assert [c[0] for c in holder["bridge"].calls].count("isIsolated") == 1

    async def test_un_bridge_senza_il_metodo_non_si_richiede(self, monkeypatch):
        _allow_url(monkeypatch)
        tentativi = []

        class Muto(FakeBridge):
            def isIsolated(self):  # noqa: N802
                tentativi.append(1)
                raise RuntimeError("metodo assente in un APK vecchio")

        _install(monkeypatch, Muto)
        tool = _tool(BrowserOpenTool)
        for _ in range(3):
            out = await tool.execute(url="https://esempio.test/")
            assert "web_fetch" not in out
        assert len(tentativi) == 1

    async def test_errore_di_apertura_non_chiede_lo_snapshot(self, monkeypatch):
        _allow_url(monkeypatch)
        class Rotto(FakeBridge):
            def open(self, url, timeout):
                return json.dumps({"error": "WebView error: net::ERR_NAME_NOT_RESOLVED"})

        holder = _install(monkeypatch, Rotto)
        out = await _tool(BrowserOpenTool).execute(url="https://esempio.test/")
        assert out.startswith("Error:")
        assert [c[0] for c in holder["bridge"].calls] == []


class TestSnapshot:
    async def test_default_e_la_differenza(self, monkeypatch):
        holder = _install(monkeypatch)
        await _tool(BrowserSnapshotTool).execute()
        assert holder["bridge"].calls[0][1] == "diff"

    async def test_modo_sconosciuto_ricade_su_differenza(self, monkeypatch):
        holder = _install(monkeypatch)
        await _tool(BrowserSnapshotTool).execute(mode="pieno")
        assert holder["bridge"].calls[0][1] == "diff"

    async def test_il_tetto_arriva_dalla_config(self, monkeypatch):
        holder = _install(monkeypatch)
        await _tool(BrowserSnapshotTool, max_snapshot_chars=777).execute()
        assert holder["bridge"].calls[0][3] == 777


class TestDo:
    async def test_senza_passi_non_tocca_il_bridge(self, monkeypatch):
        holder = _install(monkeypatch)
        out = await BrowserDoTool(object(), AndroidWebBrowserConfig()).execute(steps=[])
        assert out.startswith("Error:")
        assert "bridge" not in holder

    async def test_passi_inoltrati_come_json_e_poi_una_differenza(self, monkeypatch):
        holder = _install(monkeypatch)
        steps = [{"action": "type", "ref": "1:e0", "text": "meteo"}, {"action": "click", "ref": "1:e1"}]
        out = await _tool(BrowserDoTool).execute(steps=steps)
        calls = holder["bridge"].calls
        assert json.loads(calls[0][1]) == steps
        assert [c[0] for c in calls] == ["act", "takeNotice", "snapshot"]
        assert calls[2][1] == "diff"
        assert "0. click: ok" in out

    async def test_un_passo_fallito_si_vede(self, monkeypatch):
        class ConErrore(FakeBridge):
            def __init__(self, context=None):
                super().__init__(context)
                self.act_payload = {
                    "results": [{"i": 0, "action": "click", "ok": False,
                                 "error": 'ref "1:e3" e\' della versione 1, lo snapshot corrente e\' 2'}],
                    "failed": True,
                }

        _install(monkeypatch, ConErrore)
        out = await _tool(BrowserDoTool).execute(steps=[{"action": "click", "ref": "1:e3"}])
        assert "FALLITO" in out
        assert "versione" in out


class TestCicloDiVita:
    async def test_timeout_butta_la_sessione(self, monkeypatch):
        class Lenta(FakeBridge):
            def snapshot(self, mode, filt, max_chars, timeout):
                import time
                time.sleep(0.4)
                return "{}"

        _install(monkeypatch, Lenta)
        out = await browser._call(object(), "snapshot", "full", "", 100, 30, timeout=-9.9)
        assert "error" in out
        assert browser._BROWSER_INSTANCE is None   # sessione buttata, non lasciata appesa

    async def test_eccezione_butta_la_sessione(self, monkeypatch):
        class Esplode(FakeBridge):
            def snapshot(self, *a):
                raise RuntimeError("renderer morto")

        _install(monkeypatch, Esplode)
        out = await browser._call(object(), "snapshot", "full", "", 100, 30, timeout=1)
        assert "error" in out
        assert browser._BROWSER_INSTANCE is None

    async def test_close_chiude_e_dimentica(self, monkeypatch):
        _allow_url(monkeypatch)
        holder = _install(monkeypatch)
        await _tool(BrowserOpenTool).execute(url="https://esempio.test/")
        b = holder["bridge"]
        await _tool(BrowserCloseTool).execute()
        assert b.closed == 1
        assert browser._BROWSER_INSTANCE is None

    async def test_la_sessione_si_riusa_fra_chiamate(self, monkeypatch):
        _allow_url(monkeypatch)
        holder = _install(monkeypatch)
        await _tool(BrowserOpenTool).execute(url="https://esempio.test/")
        first = holder["bridge"]
        await _tool(BrowserSnapshotTool).execute()
        assert holder["bridge"] is first

    def test_reset_ricrea_il_lucchetto(self):
        old = browser._BROWSER_LOCK
        browser.reset_browser_state()
        assert browser._BROWSER_LOCK is not old


class TestChiusuraFuoriDalLoop:
    """``close`` in Kotlin aspetta il main thread fino a 10 s: mai sul loop.

    Chiamata sul thread del loop, fermava il gateway intero (chat, cron) proprio
    nei casi — fermo scaduto, turno annullato — in cui Android e' gia' lento.
    """

    class _Registra(FakeBridge):
        def close(self):
            self.close_thread = threading.get_ident()
            return super().close()

    async def _assert_off_loop(self, holder):
        b = holder["bridge"]
        assert b.closed == 1
        assert b.close_thread != threading.get_ident()
        assert browser._BROWSER_INSTANCE is None

    async def test_browser_close(self, monkeypatch):
        holder = _install(monkeypatch, self._Registra)
        await browser._call(object(), "snapshot", "full", "", 100, 30, timeout=5)
        await _tool(BrowserCloseTool).execute()
        await self._assert_off_loop(holder)

    async def test_dopo_un_fermo_scaduto(self, monkeypatch):
        class Lenta(self._Registra):
            def snapshot(self, *a):
                time.sleep(0.4)
                return "{}"

        holder = _install(monkeypatch, Lenta)
        out = await browser._call(object(), "snapshot", "full", "", 100, 30, timeout=-9.9)
        assert "error" in out
        await self._assert_off_loop(holder)

    async def test_dopo_un_eccezione(self, monkeypatch):
        class Esplode(self._Registra):
            def snapshot(self, *a):
                raise RuntimeError("renderer morto")

        holder = _install(monkeypatch, Esplode)
        await browser._call(object(), "snapshot", "full", "", 100, 30, timeout=1)
        await self._assert_off_loop(holder)

    async def test_dopo_un_annullamento(self, monkeypatch):
        entered = threading.Event()

        class Appesa(self._Registra):
            def snapshot(self, *a):
                entered.set()
                time.sleep(0.3)
                return "{}"

        holder = _install(monkeypatch, Appesa)
        task = asyncio.create_task(
            browser._call(object(), "snapshot", "full", "", 100, 30, timeout=5)
        )
        await asyncio.to_thread(entered.wait, 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await self._assert_off_loop(holder)

    async def test_per_inattivita(self, monkeypatch):
        monkeypatch.setattr(browser, "_IDLE_POLL_S", 0.02)
        holder = _install(monkeypatch, self._Registra)
        await browser._call(object(), "snapshot", "full", "", 100, 30, timeout=5, idle_s=0.1)
        await asyncio.sleep(0.35)
        await self._assert_off_loop(holder)

    async def test_il_loop_resta_libero_mentre_si_chiude(self, monkeypatch):
        class ChiusuraLenta(FakeBridge):
            def close(self):
                time.sleep(0.3)
                return super().close()

        _install(monkeypatch, ChiusuraLenta)
        await browser._call(object(), "snapshot", "full", "", 100, 30, timeout=5)
        ticks = 0

        async def orologio():
            nonlocal ticks
            while True:
                await asyncio.sleep(0.01)
                ticks += 1

        clock = asyncio.create_task(orologio())
        await _tool(BrowserCloseTool).execute()
        clock.cancel()
        assert ticks >= 5, "il loop e' rimasto fermo durante la chiusura"

    async def test_browser_close_aspetta_la_chiamata_in_volo(self, monkeypatch):
        """Senza lucchetto la chiusura strappava la WebView a una chiamata in corso."""
        holder = _install(monkeypatch)
        await browser._call(object(), "snapshot", "full", "", 100, 30, timeout=5)
        async with browser._BROWSER_LOCK:
            closing = asyncio.create_task(_tool(BrowserCloseTool).execute())
            await asyncio.sleep(0.05)
            assert holder["bridge"].closed == 0
            assert not closing.done()
        await closing
        assert holder["bridge"].closed == 1


class TestConcorrenza:
    @pytest.mark.parametrize(
        "cls", [BrowserOpenTool, BrowserSnapshotTool, BrowserDoTool, BrowserReadTool, BrowserCloseTool]
    )
    def test_nessuno_e_parallelizzabile(self, cls):
        """`read_only` da solo non basta: e' `exclusive` che li tiene in fila.

        La sessione e' una pagina condivisa e mutabile — due chiamate nello stesso
        batch descriverebbero stati diversi.
        """
        t = _tool(cls)
        assert t.exclusive is True
        assert t.concurrency_safe is False


class TestRegistrazione:
    def test_il_modulo_e_nella_lista_fissa(self):
        from jenny.agent.tools.loader import _HARDCODED_TOOL_MODULES

        assert "browser" in _HARDCODED_TOOL_MODULES

    def test_i_cinque_nomi(self):
        assert [c.name for c in browser.TOOLS] == [
            "browser_open", "browser_snapshot", "browser_do", "browser_read", "browser_close",
        ]

    def test_spenti_senza_android(self):
        ctx = SimpleNamespace(android_context=None, config=SimpleNamespace(android_web=None))
        assert BrowserOpenTool.enabled(ctx) is False
        assert BrowserOpenTool.disabled_reason(ctx) is None


class TestChiusuraPerInattivita:
    """La sessione viva tiene ~100 MB: se nessuno la chiude, la chiude il guardiano."""

    async def test_una_sessione_dimenticata_si_chiude(self, monkeypatch):
        monkeypatch.setattr(browser, "_IDLE_POLL_S", 0.02)
        holder = _install(monkeypatch)
        await browser._call(object(), "snapshot", "full", "", 100, 30, timeout=5, idle_s=0.1)
        b = holder["bridge"]
        assert browser._BROWSER_INSTANCE is not None
        await asyncio.sleep(0.35)
        assert browser._BROWSER_INSTANCE is None
        assert b.closed == 1

    async def test_l_attivita_rimanda_la_chiusura(self, monkeypatch):
        monkeypatch.setattr(browser, "_IDLE_POLL_S", 0.02)
        _install(monkeypatch)
        for _ in range(6):
            await browser._call(object(), "snapshot", "full", "", 100, 30, timeout=5, idle_s=0.2)
            await asyncio.sleep(0.05)
        assert browser._BROWSER_INSTANCE is not None

    async def test_un_solo_guardiano_per_sessione(self, monkeypatch):
        monkeypatch.setattr(browser, "_IDLE_POLL_S", 0.02)
        _install(monkeypatch)
        await browser._call(object(), "snapshot", "full", "", 100, 30, timeout=5, idle_s=5)
        first = browser._IDLE_TASK
        await browser._call(object(), "snapshot", "full", "", 100, 30, timeout=5, idle_s=5)
        assert browser._IDLE_TASK is first

    async def test_il_reset_lo_ferma(self, monkeypatch):
        monkeypatch.setattr(browser, "_IDLE_POLL_S", 0.02)
        _install(monkeypatch)
        await browser._call(object(), "snapshot", "full", "", 100, 30, timeout=5, idle_s=5)
        task = browser._IDLE_TASK
        browser.reset_browser_state()
        await asyncio.sleep(0.05)
        assert task.cancelled() or task.done()
        assert browser._IDLE_TASK is None

    async def test_niente_guardiano_se_spento(self, monkeypatch):
        _install(monkeypatch)
        await browser._call(object(), "snapshot", "full", "", 100, 30, timeout=5, idle_s=0)
        assert browser._IDLE_TASK is None


class TestMotoreNellaPagina:
    """Il motore vive in un file JS che nessun test Python puo' eseguire.

    Due cose si possono comunque tenere ferme da qui, ed entrambe rompono la
    feature in modo invisibile: un errore di sintassi (che si vedrebbe solo come
    una sessione muta sul telefono) e il segnaposto che il Kotlin sostituisce.
    """

    JS = pathlib.Path(__file__).resolve().parents[3] / (
        "android/app/src/main/res/raw/browser_agent.js"
    )

    def test_il_file_c_e(self):
        assert self.JS.is_file(), f"motore non trovato in {self.JS}"

    def test_un_solo_segnaposto(self):
        # JennyBrowserBridge.runAgent fa `agentJs.replace("__ARGS__", args)`:
        # zero segnaposti significa argomenti ignorati, due significa JSON
        # incollato dove non deve stare.
        assert self.JS.read_text().count("__ARGS__") == 1

    def test_sintassi(self):
        node = shutil.which("node")
        if node is None:
            pytest.skip("node non disponibile")
        src = self.JS.read_text().replace("__ARGS__", '{"op":"snapshot"}')
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
            fh.write(src)
            tmp = fh.name
        try:
            proc = subprocess.run([node, "--check", tmp], capture_output=True, text=True)
            assert proc.returncode == 0, proc.stderr
        finally:
            os.unlink(tmp)


def _index(monkeypatch, mapping):
    """Finge l'indice ruolo/nome lasciato dall'ultimo snapshot."""
    browser._LAST_INDEX.clear()
    browser._LAST_INDEX.update(mapping)


class TestVerbiSensibili:
    """Un click che costa non parte da solo.

    Non e' il modello a giudicare: una pagina ostile convince un giudizio e non
    convince una lista.
    """

    @pytest.mark.parametrize(
        "nome",
        [
            "Paga ora", "Procedi al pagamento", "Acquista", "Compra subito",
            "Conferma ordine", "Abbonati", "Pay now", "Buy it now", "Checkout",
            "Place order", "Elimina definitivamente", "Cancella account",
            "Rimuovi dal carrello", "Delete", "Remove item", "Bonifico", "Entra",
            "Invia denaro", "Transfer funds", "Accedi", "Sign in", "Log in",
        ],
    )
    def test_li_riconosce(self, nome):
        assert browser._is_sensitive(nome) is True

    @pytest.mark.parametrize(
        "nome",
        [
            # "conferma" da sola no: sarebbe ogni banner dei cookie.
            "Conferma le preferenze", "Accetta tutti", "Gestisci i cookie",
            # confini di parola: nessuno di questi e' il verbo.
            "Ordinamento per data", "Cancelleria", "Rimozione automatica spiegata",
            "Rientra nella media", "Entrata principale", "Centrale elettrica",
            "Paginazione", "Pagina successiva",
        ],
    )
    def test_non_scatta_a_vuoto(self, nome):
        assert browser._is_sensitive(nome) is False

    @pytest.mark.parametrize("nome", ["Login page explained", "Accedi alla guida"])
    def test_scatta_anche_dove_non_servirebbe(self, nome):
        """Falsi positivi noti, e accettati.

        Il lessico non distingue il verbo dal sostantivo: un link intitolato
        "Login page explained" chiede conferma come la chiederebbe un bottone di
        accesso. Il prezzo e' una domanda in piu'; il prezzo dell'errore opposto
        e' un acquisto o una cancellazione fatti da soli. Si tara con i compiti
        veri della Fase 4, non a tavolino.
        """
        assert browser._is_sensitive(nome) is True

    async def test_un_click_sensibile_si_ferma_prima_di_toccare_la_pagina(self, monkeypatch):
        holder = _install(monkeypatch)
        _index(monkeypatch, {"1:e4": ("button", "Paga ora")})
        out = await _tool(BrowserDoTool).execute(steps=[{"action": "click", "ref": "1:e4"}])
        assert out.startswith("Error:")
        assert "confirm" in out
        assert "bridge" not in holder      # la pagina non e' stata toccata

    async def test_con_il_consenso_passa(self, monkeypatch):
        holder = _install(monkeypatch)
        _index(monkeypatch, {"1:e4": ("button", "Paga ora")})
        await _tool(BrowserDoTool).execute(
            steps=[{"action": "click", "ref": "1:e4", "confirm": True}]
        )
        assert [c[0] for c in holder["bridge"].calls][0] == "act"

    async def test_rifiuta_tutto_il_blocco_non_meta(self, monkeypatch):
        """Fermarsi a meta' lascerebbe la pagina in uno stato che nessuno descrive."""
        holder = _install(monkeypatch)
        _index(monkeypatch, {"1:e0": ("textbox", "Cerca"), "1:e9": ("button", "Elimina")})
        out = await _tool(BrowserDoTool).execute(steps=[
            {"action": "type", "ref": "1:e0", "text": "x"},
            {"action": "click", "ref": "1:e9"},
        ])
        assert out.startswith("Error:")
        assert "passo 1" in out
        assert "bridge" not in holder

    async def test_un_nome_che_non_conosciamo_non_blocca(self, monkeypatch):
        holder = _install(monkeypatch)
        _index(monkeypatch, {})
        await _tool(BrowserDoTool).execute(steps=[{"action": "click", "ref": "1:e4"}])
        assert holder["bridge"].calls


class TestPassword:
    async def test_non_ci_si_scrive(self, monkeypatch):
        holder = _install(monkeypatch)
        _index(monkeypatch, {"1:e7": ("password", "")})
        out = await _tool(BrowserDoTool).execute(
            steps=[{"action": "type", "ref": "1:e7", "text": "segreto"}]
        )
        assert out.startswith("Error:")
        assert "bridge" not in holder

    async def test_il_consenso_non_la_sblocca(self, monkeypatch):
        """`confirm` vale per i verbi, non per le credenziali: quelle non passano."""
        holder = _install(monkeypatch)
        _index(monkeypatch, {"1:e7": ("password", "")})
        out = await _tool(BrowserDoTool).execute(
            steps=[{"action": "type", "ref": "1:e7", "text": "segreto", "confirm": True}]
        )
        assert out.startswith("Error:")
        assert "bridge" not in holder

    async def test_non_si_legge(self, monkeypatch):
        holder = _install(monkeypatch)
        _index(monkeypatch, {"1:e7": ("password", "")})
        out = await _tool(BrowserReadTool).execute(ref="1:e7")
        assert out.startswith("Error:")
        assert "bridge" not in holder


class TestIndiceDeiRef:
    def test_lo_snapshot_lo_aggiorna(self):
        browser._LAST_INDEX.clear()
        browser._render_snapshot({
            "url": "https://x.test/", "version": 3, "refs": 1, "total": 1,
            "index": {"3:e0": ["button", "Paga ora"]}, "text": "- button ...",
        })
        assert browser._LAST_INDEX == {"3:e0": ("button", "Paga ora")}

    def test_i_ref_si_accumulano_nello_stesso_documento(self):
        """Guardare due volte non deve uccidere i ref della prima occhiata."""
        browser._LAST_INDEX.clear()
        browser._INDEX_VERSION = ""
        browser._render_snapshot({"url": "u", "version": 2, "index": {"2:e0": ["button", "A"]},
                                  "text": ""})
        browser._render_snapshot({"url": "u", "version": 2, "index": {"2:e1": ["link", "B"]},
                                  "text": ""})
        assert set(browser._LAST_INDEX) == {"2:e0", "2:e1"}

    def test_un_documento_nuovo_lo_svuota(self):
        browser._LAST_INDEX.clear()
        browser._INDEX_VERSION = ""
        browser._render_snapshot({"url": "u", "version": 2, "index": {"2:e0": ["button", "A"]},
                                  "text": ""})
        browser._render_snapshot({"url": "v", "version": 3, "index": {"3:e0": ["link", "B"]},
                                  "text": ""})
        assert set(browser._LAST_INDEX) == {"3:e0"}

    def test_uno_snapshot_senza_indice_non_lo_cancella(self):
        """Un bridge vecchio non deve disarmare la politica in silenzio."""
        browser._LAST_INDEX.clear()
        browser._LAST_INDEX["1:e0"] = ("button", "Paga")
        browser._render_snapshot({"url": "https://x.test/", "text": "..."})
        assert browser._LAST_INDEX == {"1:e0": ("button", "Paga")}

    def test_la_chiusura_lo_svuota(self, monkeypatch):
        _install(monkeypatch)
        browser._LAST_INDEX["1:e0"] = ("button", "Paga")
        browser.destroy_browser()
        assert browser._LAST_INDEX == {}


class TestLaGuardiaSiFaSentire:
    async def test_un_blocco_durante_la_navigazione_arriva_al_modello(self, monkeypatch):
        """La guardia lavora *durante* la navigazione, quindi non sta nei passi.

        Senza il ritiro esplicito, un click fermato lascia il modello davanti a
        una pagina che semplicemente non e' cambiata.
        """
        class ConBlocco(FakeBridge):
            def __init__(self, context=None):
                super().__init__(context)
                self.notice = "navigazione fermata: la sessione e' aperta su esempio.test"

        _install(monkeypatch, ConBlocco)
        out = await _tool(BrowserDoTool).execute(steps=[{"action": "click", "ref": "1:e1"}])
        assert "navigazione fermata" in out
