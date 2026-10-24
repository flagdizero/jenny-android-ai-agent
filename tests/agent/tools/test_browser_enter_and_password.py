"""``press Enter`` e i campi password nella sessione del browser.

Due buchi nella politica di ``browser_do``:

* un click su «Paga ora» chiede conferma, ma ``type`` nel campo + ``press Enter``
  inviava lo stesso modulo senza passare dal lessico dei verbi che costano;
* il divieto di scrivere in un campo password guardava il **ruolo**, e
  ``<input type=password role=textbox>`` passava per un textbox.

La parte Python si prova col bridge finto di ``test_browser.py``; la parte nella
pagina (``browser_agent.js``) ritagliando le funzioni vere ed eseguendole in node
su elementi finti.
"""

from __future__ import annotations

import json
import pathlib

import pytest
from support.js_harness import member, requires_node, run_js

from jenny.agent.tools import browser
from jenny.agent.tools.browser import BrowserDoTool
from tests.agent.tools.test_browser import _install, _tool

_JS = pathlib.Path(__file__).resolve().parents[3] / "android/app/src/main/res/raw/browser_agent.js"


@pytest.fixture(autouse=True)
def _clean_state():
    browser.reset_browser_state()
    yield
    browser.reset_browser_state()


def _snapshot_index(index: dict[str, list[str]], version: int = 1) -> None:
    browser._render_snapshot(browser._state(browser.MAIN_OWNER),
                             {"url": "https://x.test/", "version": version,
                              "index": index, "text": ""})


def _sent_steps(holder) -> list[dict]:
    acts = [c for c in holder["bridge"].calls if c[0] == "act"]
    assert acts, "la pagina non è stata toccata"
    return json.loads(acts[0][1])


class TestEnterGoesThroughTheSameGate:
    async def test_enter_in_a_payment_form_asks_first(self, monkeypatch):
        holder = _install(monkeypatch)
        _snapshot_index({"1:e0": ["textbox", "Importo", "Paga ora"]})
        out = await _tool(BrowserDoTool).execute(steps=[
            {"action": "type", "ref": "1:e0", "text": "100"},
            {"action": "press"},
        ])
        assert out.startswith("Error:") and "confirm" in out, out
        assert "Paga ora" in out
        assert "bridge" not in holder

    async def test_with_consent_it_submits(self, monkeypatch):
        holder = _install(monkeypatch)
        _snapshot_index({"1:e0": ["textbox", "Importo", "Paga ora"]})
        await _tool(BrowserDoTool).execute(steps=[
            {"action": "type", "ref": "1:e0", "text": "100"},
            {"action": "press", "key": "Enter", "confirm": True},
        ])
        assert _sent_steps(holder)[1].get("submit") is not False

    async def test_a_harmless_form_submits_without_asking(self, monkeypatch):
        holder = _install(monkeypatch)
        _snapshot_index({"1:e0": ["searchbox", "Cerca", "Cerca"]})
        await _tool(BrowserDoTool).execute(steps=[
            {"action": "type", "ref": "1:e0", "text": "meteo"},
            {"action": "press"},
        ])
        assert _sent_steps(holder)[1].get("submit") is not False

    async def test_the_focus_is_remembered_across_calls(self, monkeypatch):
        holder = _install(monkeypatch)
        _snapshot_index({"1:e0": ["textbox", "Importo", "Paga ora"]})
        holder_bridge_result = {"results": [{"i": 0, "action": "type", "ok": True}]}
        tool = _tool(BrowserDoTool)
        # Primo giro: si scrive e basta.
        monkeypatch.setattr(
            "tests.agent.tools.test_browser.FakeBridge.act",
            lambda self, steps_json, timeout: (
                self.calls.append(("act", steps_json, timeout)),
                self._js(holder_bridge_result),
            )[1],
        )
        await tool.execute(steps=[{"action": "type", "ref": "1:e0", "text": "100"}])
        # Secondo giro: Enter da solo, sullo stesso campo.
        before = len(holder["bridge"].calls)
        out = await tool.execute(steps=[{"action": "press"}])
        assert out.startswith("Error:") and "Paga ora" in out, out
        assert len(holder["bridge"].calls) == before

    async def test_an_unknown_focus_cannot_submit_by_enter(self, monkeypatch):
        """Senza sapere dove sta il cursore, l'invio passa dal bottone (e dal suo controllo)."""
        holder = _install(monkeypatch)
        await _tool(BrowserDoTool).execute(steps=[{"action": "press"}])
        assert _sent_steps(holder)[0]["submit"] is False

    async def test_a_field_outside_a_form_is_not_a_harmless_form(self, monkeypatch):
        """Etichetta vuota vuol dire «nessun <form>», non «modulo innocuo».

        Fuori da un modulo l'Enter lo gestisce il JavaScript della pagina, e in
        una pagina con «Paga ora» può essere lui a pagare.
        """
        holder = _install(monkeypatch)
        _snapshot_index({
            "1:e0": ["textbox", "Codice sconto", ""],
            "1:e1": ["button", "Paga ora", ""],
        })
        out = await _tool(BrowserDoTool).execute(steps=[
            {"action": "type", "ref": "1:e0", "text": "ESTATE"},
            {"action": "press"},
        ])
        assert out.startswith("Error:") and "confirm" in out, out
        assert "bridge" not in holder

    async def test_a_field_outside_a_form_on_a_harmless_page_still_presses(self, monkeypatch):
        holder = _install(monkeypatch)
        _snapshot_index({
            "1:e0": ["searchbox", "Cerca", ""],
            "1:e1": ["link", "Accedi", ""],
        })
        await _tool(BrowserDoTool).execute(steps=[
            {"action": "type", "ref": "1:e0", "text": "meteo"},
            {"action": "press"},
        ])
        assert _sent_steps(holder)[1].get("submit") is not False

    async def test_an_unknown_focus_on_a_costly_page_asks_first(self, monkeypatch):
        holder = _install(monkeypatch)
        _snapshot_index({"1:e1": ["button", "Conferma ordine", "Conferma ordine"]})
        out = await _tool(BrowserDoTool).execute(steps=[{"action": "press"}])
        assert out.startswith("Error:") and "confirm" in out, out
        assert "bridge" not in holder

    async def test_confirm_lets_enter_through_outside_a_form(self, monkeypatch):
        holder = _install(monkeypatch)
        _snapshot_index({
            "1:e0": ["textbox", "Codice sconto", ""],
            "1:e1": ["button", "Paga ora", ""],
        })
        await _tool(BrowserDoTool).execute(steps=[
            {"action": "type", "ref": "1:e0", "text": "ESTATE"},
            {"action": "press", "confirm": True},
        ])
        assert _sent_steps(holder)[1].get("submit") is not False

    async def test_other_keys_are_untouched(self, monkeypatch):
        holder = _install(monkeypatch)
        await _tool(BrowserDoTool).execute(steps=[{"action": "press", "key": "Escape"}])
        assert "submit" not in _sent_steps(holder)[0]


def _js_functions(*names: str) -> str:
    src = _JS.read_text(encoding="utf-8")
    return "\n".join(member(src, n, prefixes=("function ",)) for n in names)


def _fake_el(tag: str, attrs: dict[str, str], **extra) -> str:
    return (
        "({tagName: %s, getAttribute: (k) => (%s)[k] ?? null, %s})"
        % (json.dumps(tag.upper()), json.dumps(attrs),
           ", ".join(f"{k}: {v}" for k, v in extra.items()))
    )


@requires_node
class TestInThePage:
    def test_a_password_field_is_a_password_whatever_its_role(self):
        script = _js_functions("isPassword", "role") + f"""
        const ACTIONABLE = {{textbox: 1, password: 1}}, LANDMARK = {{}};
        const el = {_fake_el("input", {"type": "password", "role": "textbox"})};
        const plain = {_fake_el("input", {"type": "text", "role": "textbox"})};
        console.log(JSON.stringify([role(el), role(plain)]));
        """
        assert json.loads(run_js(script)) == ["password", "textbox"]

    def test_enter_does_not_submit_when_python_says_no(self):
        script = _js_functions("isPassword", "act") + """
        let submitted = 0;
        const form = {requestSubmit() { submitted++; }, getAttribute: () => null,
                      querySelector: () => null};
        globalThis.KeyboardEvent = class { constructor(t, o) { this.type = t; } };
        globalThis.document = {activeElement: {form, dispatchEvent() {}}, body: {}};
        function formLabel() { return 'Paga ora'; }
        const blocked = act({steps: [{action: 'press', submit: false}]});
        const allowed = act({steps: [{action: 'press'}]});
        console.log(JSON.stringify({blocked, allowed, submitted}));
        """
        out = json.loads(run_js(script))
        assert out["submitted"] == 1
        assert out["blocked"]["results"][0]["ok"] is False
        assert "Paga ora" in out["blocked"]["results"][0]["error"]
        assert out["allowed"]["results"][0]["ok"] is True

    def test_a_refused_enter_sends_no_key_to_the_page(self):
        """Il keydown lo legge anche il JavaScript della pagina: rifiutato, non parte."""
        script = _js_functions("isPassword", "act") + """
        const keys = [];
        let submitted = 0;
        const form = {requestSubmit() { submitted++; }, getAttribute: () => null,
                      querySelector: () => null};
        globalThis.KeyboardEvent = class { constructor(t, o) { this.type = t; this.key = o.key; } };
        globalThis.document = {
          activeElement: {form, dispatchEvent(ev) { keys.push(ev.type + ':' + ev.key); }},
          body: {},
        };
        function formLabel() { return 'Paga ora'; }
        const blocked = act({steps: [{action: 'press', submit: false}]});
        const refusedKeys = keys.length;
        const allowed = act({steps: [{action: 'press'}]});
        console.log(JSON.stringify({blocked, allowed, refusedKeys, keys, submitted}));
        """
        out = json.loads(run_js(script))
        assert out["blocked"]["results"][0]["ok"] is False
        assert out["refusedKeys"] == 0, out["keys"]
        assert out["keys"] == ["keydown:Enter", "keyup:Enter"]
        assert out["submitted"] == 1

    def test_submit_false_outside_a_form_still_sends_the_keys(self):
        """Senza <form> non c'e' niente da rifiutare nella pagina: il giudizio l'ha dato Python."""
        script = _js_functions("isPassword", "act") + """
        const keys = [];
        globalThis.KeyboardEvent = class { constructor(t, o) { this.type = t; this.key = o.key; } };
        globalThis.document = {
          activeElement: {form: null, dispatchEvent(ev) { keys.push(ev.type); }}, body: {},
        };
        function formLabel() { return ''; }
        const out = act({steps: [{action: 'press', submit: false}]});
        console.log(JSON.stringify({out, keys}));
        """
        out = json.loads(run_js(script))
        assert out["out"]["results"][0]["ok"] is True
        assert out["keys"] == ["keydown", "keyup"]

    def test_typing_into_a_password_field_is_refused_in_the_page(self):
        script = _js_functions("isPassword", "act") + f"""
        const el = {_fake_el("input", {"type": "password", "role": "textbox"},
                             value="''", focus="() => {}")};
        function resolve() {{ return {{el}}; }}
        function fire() {{}}
        const out = act({{steps: [{{action: 'type', ref: '1:e0', text: 'segreto'}}]}});
        console.log(JSON.stringify({{out, value: el.value}}));
        """
        out = json.loads(run_js(script))
        assert out["value"] == ""
        assert out["out"]["results"][0]["ok"] is False
