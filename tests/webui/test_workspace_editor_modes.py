"""Ogni linguaggio dell'editor deve puntare a un modo che esiste ed è caricato.

CodeMirror 5 non solleva su un modo sconosciuto: ripiega sul modo nullo, cioè
mostra il file senza evidenziazione. Un errore di battitura o un modo mai
vendorizzato è quindi **invisibile** — si nota solo aprendo quel tipo di file e
accorgendosi che è tutto grigio.

Cinque delle quattordici voci erano in quello stato: ``json``/``jsonl``
(``config.json`` si apriva senza evidenziazione, il caso visibile), ``ts``,
``html`` e ``rs``. Nessuna di esse mancava davvero: il modo *javascript* già
registra ``application/json`` e ``text/typescript``, ``xml`` registra
``text/html`` e ``rust`` ``text/x-rustsrc``. Mancava solo il nome giusto. Il
modo ``shell`` era invece vendorizzato e spedito nell'APK ma non caricato da
``workshop.html``.

Il controllo è in due passi perché ci sono due modi di sbagliare: un nome che
nessun file registra, e un nome registrato da un file che nessuno carica.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

UI = Path(__file__).resolve().parents[2] / "jenny" / "templates" / "ui"
WORKSPACE_JS = UI / "assets" / "mobile-workspace.js"
WORKSHOP_HTML = UI / "workshop.html"
MODE_DIR = UI / "assets" / "vendor" / "codemirror@5.65.16" / "mode"

# ``text`` non è un modo: è il modo nullo, chiesto di proposito per i file che
# non si vogliono evidenziare (log, txt).
_PLAIN = {"text"}


def _ext_lang() -> dict[str, str]:
    src = WORKSPACE_JS.read_text("utf-8")
    block = re.search(r"const EXT_LANG = \{(.*?)\};", src, re.S)
    assert block, "EXT_LANG non trovato in mobile-workspace.js"
    return dict(re.findall(r"(\w+):\s*'([^']+)'", block.group(1)))


def _loaded_mode_files() -> set[str]:
    """I file di modo che ``workshop.html`` carica davvero, per nome di cartella."""
    html = WORKSHOP_HTML.read_text("utf-8")
    return set(re.findall(r"codemirror@[\d.]+/mode/([a-z]+)/", html))


def _registered_by(mode_dir_name: str) -> set[str]:
    path = next((MODE_DIR / mode_dir_name).glob("*.min.js"), None)
    if path is None:
        return set()
    src = path.read_text("utf-8", errors="replace")
    names = set(re.findall(r'defineMode\("([^"]+)"', src))
    names |= set(re.findall(r'defineMIME\("([^"]+)"', src))
    return names


@pytest.fixture(scope="module")
def available() -> dict[str, str]:
    """Nome-di-modo → cartella che lo registra, per i soli modi caricati."""
    out: dict[str, str] = {}
    for folder in _loaded_mode_files():
        for name in _registered_by(folder):
            out.setdefault(name, folder)
    return out


def test_every_mapped_language_resolves_to_a_loaded_mode(available) -> None:
    unresolved = sorted(
        f"{ext} -> {mode}"
        for ext, mode in _ext_lang().items()
        if mode not in _PLAIN and mode not in available
    )

    assert not unresolved, (
        f"voci di EXT_LANG che nessun modo caricato registra: {unresolved}. "
        "CodeMirror ripiega in silenzio sul modo nullo, quindi il file si apre "
        "senza evidenziazione e nessuno se ne accorge."
    )


def test_shipped_modes_are_all_loaded() -> None:
    """Un modo nell'APK e non in ``workshop.html`` è peso spedito per niente."""
    shipped = {p.parent.name for p in MODE_DIR.glob("*/*.min.js")}
    unloaded = sorted(shipped - _loaded_mode_files())

    assert not unloaded, (
        f"modi vendorizzati e spediti ma mai caricati da officina.html: {unloaded}. "
        "O si agganciano, o si tolgono dal manifest in utils/android_assets.py."
    )


def test_plain_text_is_a_deliberate_choice_not_a_missing_mode(available) -> None:
    """``log`` e ``txt`` chiedono il modo nullo perché così si vuole.

    Se un domani un'altra estensione finisse su ``'text'``, o è la stessa scelta
    — e allora va nominata qui — o è un modo che qualcuno non ha trovato e ha
    rinunciato, che è il caso da non lasciar passare in silenzio.
    """
    plain = sorted(ext for ext, mode in _ext_lang().items() if mode in _PLAIN)
    assert plain == ["log", "txt"], f"estensioni sul modo nullo: {plain}"
