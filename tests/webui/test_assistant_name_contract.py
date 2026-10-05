"""Il nome dell'assistente dove si parla con lei, «Jenny» dove si parla dell'app.

Dal collaudo del 27/09/2026: rinominata (nel collaudo «JennyUI2»), il campo
della casa diceva ancora «Write to Jenny», e l'officina la chiamava «Jenny»
nella riga d'identità, nel selettore della conversazione e nella mascotte,
perché nessun suo file leggeva ``bot_name``. Ora il nome sta in un posto solo,
``shared/bot-name.js``: chi legge le impostazioni lo scrive, chi lo mostra lo
legge e si iscrive ai cambi.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from support.js_harness import member, requires_node, run_js

ASSETS = Path(__file__).resolve().parents[2] / "jenny" / "templates" / "ui" / "assets"
BOT_NAME_JS = ASSETS / "shared" / "bot-name.js"


def _read(name: str) -> str:
    # `mobile-chat.js` contiene byte che grep legge come binari: si legge in
    # UTF-8 come gli altri, senza fidarsi di un grep.
    return (ASSETS / name).read_text(encoding="utf-8")


@requires_node
def test_the_shared_name_falls_back_and_tells_who_listens() -> None:
    run_js(
        "import assert from 'node:assert/strict';\n"
        f"const {{ botName, DEFAULT_BOT_NAME }} = await import('{BOT_NAME_JS.as_uri()}');\n"
        """
assert.equal(botName.get(), 'Jenny');
const heard = [];
const off = botName.onChange((n) => heard.push(n));
botName.set('Nora');
botName.set('Nora');
botName.set('   ');
assert.deepEqual(heard, ['Nora', DEFAULT_BOT_NAME], 'un cambio detto due volte, o uno perso');
off();
botName.set('Ada');
assert.deepEqual(heard, ['Nora', 'Jenny']);
assert.equal(botName.get(), 'Ada');
"""
    )


def test_the_home_field_invites_to_write_to_her_by_name() -> None:
    for locale in ("en", "it"):
        home = json.loads((ASSETS / "i18n" / f"{locale}.json").read_text(encoding="utf-8"))["home"]
        for key in ("placeholder", "placeholderNotebook"):
            assert "{name}" in home[key] and "Jenny" not in home[key], (locale, key)
    source = _read("home-app.js")
    texts = member(source, "_applyConversationTexts")
    assert "name: this._personalName" in texts
    apply = member(source, "_applyBotName")
    assert "this._applyConversationTexts()" in apply, "il campo cambia solo al prossimo cambio di chat"
    assert "botName.set(newName)" in apply


def test_the_workshop_reads_her_name_where_it_names_her() -> None:
    chat = _read("mobile-chat.js")
    assert "chat.jenny" not in chat
    identity = member(chat, "_ensureIdentity")
    assert "botName.get()" in identity and "botName.onChange(" in identity
    chip = _read("shared/scope-chip.js")
    assert re.search(r"get personalLabel\(\) \{\s*return botName\.get\(\);", chip)
    assert "botName.onChange(() => this.render())" in chip
    mascot = _read("shared/jenny-mascot.js")
    assert "setAttribute('aria-label', 'Jenny')" not in mascot
    # Il nome lo scrive `_paintLabel` (dal 05/10/2026 ci aggiunge il pallino
    # «risposta nuova»), e lo riscrive a ogni cambio di nome.
    label = member(mascot, "_paintLabel")
    assert "botName.get()" in label and "setAttribute('aria-label'" in label
    assert "botName.onChange(() => this._paintLabel())" in mascot
    # Chi scrive il nome: la lettura d'avvio dell'officina e ogni lettura
    # delle impostazioni.
    assert "botName.set(bootSettings?.agent?.bot_name)" in _read("mobile-app.js")
    assert "botName.set(settings?.agent?.bot_name)" in _read("mobile-settings.js")


def test_the_app_keeps_its_own_name() -> None:
    """La pillola «⌂ Jenny» porta alla casa, cioè all'app: quella resta Jenny."""
    for locale in ("en", "it"):
        data = json.loads((ASSETS / "i18n" / f"{locale}.json").read_text(encoding="utf-8"))
        assert "Jenny" in data["workshop"]["homePill"]


def test_what_speaks_of_her_carries_her_name_too() -> None:
    """Chiesto dall'utente il 29/09/2026 dopo la verifica: la riga «Jenny» delle
    Impostazioni, il titolo della sua stanza, il testo della finestra
    flottante e quello della posizione parlano di lei. Il titolo delle
    notifiche resta «Jenny»: e' l'app che avvisa."""
    for locale in ("en", "it"):
        data = json.loads((ASSETS / "i18n" / f"{locale}.json").read_text(encoding="utf-8"))
        assert data["home"]["jenny"]["title"] == "{name}"
        for text in (data["settings"]["floatingHint"], data["settings"]["location"]["hint"]):
            assert "{name}" in text and "Jenny" not in text, (locale, text)
    assert "i18n.t('home.jenny.title', { name: botName.get() })" in _read("home-you.js")
    assert "i18n.t('home.jenny.title', { name: this._personalName || DEFAULT_BOT_NAME })" in _read("home-app.js")
    assert "{ name: botName.get() }" in member(_read("home-jenny.js"), "_sayFloating")
    assert "i18n.t('settings.location.hint', { name: botName.get() })" in _read("mobile-settings.js")
    apply = member(_read("home-app.js"), "_applyBotName")
    assert "this.you?.applyTranslations()" in apply and "this.jennyRoom?.applyTranslations()" in apply
