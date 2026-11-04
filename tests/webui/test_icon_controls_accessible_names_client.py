"""I controlli dell'officina e della casa hanno un nome, e le icone non ne hanno.

Un lettore di schermo legge quel che trova: un ``<i class="ti ...">`` senza
``aria-hidden`` è un glifo della webfont, cioè un carattere dell'area privata
Unicode, e si sente come «immagine» o come niente; un campo senza etichetta
associata si sente come «campo di testo», senza dire quale. Qui si fissa:

- le righe dell'officina (``_field``, ``_select``, ``_numberField``): la
  ``<label>`` è legata al proprio controllo con ``for``/``id``, cosa che
  ``label.control`` verifica nel DOM vero (jsdom), non nel testo;
- il bottone solo-icona dell'intestazione dell'officina ha un ``aria-label``
  uguale al ``title``, e la sua icona è nascosta;
- nei gusci, ogni icona scritta come markup (``<i class="ti ...">``) porta
  ``aria-hidden="true"``: il nome sta sul bottone, non sul glifo.
"""

from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path

import pytest
from support.home_dom import requires_jsdom
from support.js_harness import ASSETS, member, requires_node, run_module

SETTINGS = (ASSETS / "mobile-settings.js").read_text(encoding="utf-8")
HEADER = (ASSETS / "mobile-header.js").read_text(encoding="utf-8")


def _dom_run(body: str) -> dict:
    """Esegue *body* con un ``document`` jsdom e ritorna il JSON che stampa."""
    with tempfile.TemporaryDirectory() as tmp:
        entry = Path(tmp) / "prova.mjs"
        entry.write_text(
            "import { createRequire } from 'node:module';\n"
            "const require = createRequire(import.meta.url);\n"
            "const { JSDOM } = require('jsdom');\n"
            "const dom = new JSDOM('<!doctype html><body><div id=\"root\"></div></body>');\n"
            "const document = dom.window.document;\n"
            "const root = document.getElementById('root');\n"
            "const escapeHtml = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;')"
            ".replace(/>/g, '&gt;').replace(/\"/g, '&quot;');\n" + body,
            encoding="utf-8",
        )
        out = run_module(entry)
    return json.loads(out.strip().splitlines()[-1])


@requires_node
@requires_jsdom
def test_each_workshop_row_labels_its_own_control() -> None:
    """Etichetta e controllo sono legati: ``label.control`` è il campo accanto."""
    result = _dom_run(f"""
class Fake {{
{member(SETTINGS, "_field")}
{member(SETTINGS, "_select")}
{member(SETTINGS, "_numberField")}
}}
const f = new Fake();
root.innerHTML =
  f._field('Max results', 'number', 'ws_max', 5, '5') +
  f._select('Reasoning', 'reasoning_effort', 'low', ['low', 'high']) +
  f._numberField('MEMORY.md', 'memory_budget_chars', {{ value: 3000, min: 0 }});
const rows = [...root.querySelectorAll('.settings-row')].map((row) => {{
  const label = row.querySelector('label.settings-label');
  const control = row.querySelector('input, select');
  return {{
    label: label.textContent,
    bound: label.control === control,
    id: control.id,
  }};
}});
const ids = rows.map((r) => r.id);
console.log(JSON.stringify({{ rows, unique: new Set(ids).size === ids.length }}));
""")
    assert result["rows"] == [
        {"label": "Max results", "bound": True, "id": "settings-ws_max"},
        {"label": "Reasoning", "bound": True, "id": "settings-reasoning_effort"},
        {"label": "MEMORY.md", "bound": True, "id": "settings-worker-memory_budget_chars"},
    ], "una riga dell'officina ha un'etichetta che non nomina il suo controllo"
    assert result["unique"]


@requires_node
@requires_jsdom
def test_the_icon_only_header_button_has_a_name() -> None:
    """Il bottone solo-icona si chiama come il suo ``title``; le icone tacciono."""
    result = _dom_run(f"""
class Fake {{
  constructor() {{ this.actionsEl = root; }}
  wireActions() {{}}
{member(HEADER, "renderActions")}
}}
new Fake().renderActions([
  {{ icon: 'ti-refresh', title: 'Refresh', action: 'refresh' }},
  {{ icon: 'ti-home', title: 'Back home', action: 'home', pill: 'Jenny' }},
]);
const [bare, pill] = root.querySelectorAll('button');
console.log(JSON.stringify({{
  bareLabel: bare.getAttribute('aria-label'),
  bareIconHidden: bare.querySelector('i').getAttribute('aria-hidden'),
  pillIconHidden: pill.querySelector('i').getAttribute('aria-hidden'),
  pillText: pill.textContent.trim(),
}}));
""")
    assert result == {
        "bareLabel": "Refresh",
        "bareIconHidden": "true",
        "pillIconHidden": "true",
        "pillText": "Jenny",
    }


_ICON_TAG = re.compile(r"<i class=\"ti[^\"]*\"[^>]*>")

# I file dove ogni icona scritta come markup deve essere nascosta: i due punti
# dove mancava (intestazione e minichat dell'officina, «Aggiungi
# provider») e l'intero guscio della casa, che oggi ne è già quasi tutto pulito
# e così resta.
_SHELL_FILES = sorted(p.name for p in ASSETS.glob("home-*.js")) + [
    "mobile-header.js",
    "mobile-jenny.js",
]


@pytest.mark.parametrize("name", _SHELL_FILES)
def test_markup_icons_are_hidden_from_screen_readers(name: str) -> None:
    source = (ASSETS / name).read_text(encoding="utf-8")
    loud = [tag for tag in _ICON_TAG.findall(source) if 'aria-hidden="true"' not in tag]
    assert not loud, f"{name}: icone lette dal lettore di schermo: {loud}"


# Un'icona seguita dalla sua etichetta tradotta: il nome e' la parola, e il
# glifo letto dal lettore di schermo sarebbe solo rumore davanti a lei.
_ICON_BEFORE_LABEL = re.compile(
    r"(<i class=\"ti[^\"]*\"[^>]*>)</i>[ \t]*"
    r"(?:\$\{i18n\.t\(|\$\{t\(|<span>\$\{i18n\.t\(|<span class=\"chat-thinking-label\">)"
)


@pytest.mark.parametrize("name", ["mobile-settings.js", "mobile-chat.js"])
def test_an_icon_beside_its_label_is_hidden_from_screen_readers(name: str) -> None:
    source = (ASSETS / name).read_text(encoding="utf-8")
    tags = _ICON_BEFORE_LABEL.findall(source)
    assert tags, f"{name}: nessuna icona accanto a un'etichetta, il banco non misura niente"
    loud = [tag for tag in tags if 'aria-hidden="true"' not in tag]
    assert not loud, f"{name}: icone lette prima della loro etichetta: {loud}"


def test_the_add_buttons_of_the_workshop_hide_their_plus() -> None:
    """«Aggiungi provider» e «Aggiungi host»: il nome è la parola, non il «+»."""
    for button_id in ("btn-add-provider", "btn-ssh-add"):
        m = re.search(rf'id="{button_id}">(.*?)</button>', SETTINGS, re.S)
        assert m, f"{button_id} non trovato"
        assert '<i class="ti ti-plus" aria-hidden="true"></i>' in m.group(1), button_id
