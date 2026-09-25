"""Le due sezioni nuove di Impostazioni, dal lato del client.

Le manopole dei tre lavoratori periodici sono arrivate qui da due slash command,
e una schermata puo' mentire in modi che un comando non aveva: un interruttore
che resta acceso dopo un rifiuto del server dice all'utente che il giardiniere e'
spento quando non lo e'. Questi test tengono chiusi i tre punti in cui quella
bugia entrerebbe:

- **rollback**: un salvataggio fallito riporta indietro il controllo;
- **il messaggio vero**: il corpo di un 400 nomina il range sforato, e finisce
  nel toast invece di essere sostituito da "update failed";
- **la conferma della cadenza di review**: il pavimento e' del server, ma il
  dialogo che lo giustifica e' qui, e senza un "si" esplicito non parte niente.

**Come sono fatti.** I membri si estraggono dal sorgente e si eseguono in
**node**, come in ``test_commands_chip_client.py``: niente e' riscritto a mano.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from support.js_harness import requires_node, run_module

ASSETS = Path(__file__).resolve().parents[2] / "jenny" / "templates" / "ui" / "assets"
SETTINGS_JS = ASSETS / "mobile-settings.js"


pytestmark = requires_node


def _member(name: str) -> str:
    """Il corpo di un metodo, dal sorgente e non riscritto."""
    source = SETTINGS_JS.read_text(encoding="utf-8")
    match = re.search(
        rf"\n  ((?:async |get )?{re.escape(name)}\([^)]*\)\s*\{{.*?)\n  \}}",
        source,
        re.S,
    )
    assert match, f"{name} non trovato in mobile-settings.js"
    return match.group(1) + "\n  }"


_HARNESS = """
import assert from 'node:assert/strict';

/* Un elemento e' quel poco che il cablaggio tocca. */
function makeEl(extra = {}) {
  return {
    checked: false,
    value: '',
    dataset: {},
    _handlers: {},
    addEventListener(type, fn) { this._handlers[type] = fn; },
    async fire(type) { await this._handlers[type]?.(); },
    ...extra,
  };
}

const toasts = [];
const calls = [];
const dialogs = [];
let dialogAnswer = true;
let failWith = null;

const i18n = { t: (key, params) => (params ? `${key}:${JSON.stringify(params)}` : key) };
const showToast = (text, kind) => toasts.push({ text, kind });
const confirmDialog = (text) => { dialogs.push(text); return Promise.resolve(dialogAnswer); };
const escapeHtml = (s) => String(s);

const api = {
  async updateMemorySettings(params) { return record('memory', params); },
  async updateWorkerSettings(params) { return record('workers', params); },
};

function record(family, params) {
  calls.push({ family, params });
  if (failWith) throw new Error(failWith);
  return PAYLOAD;
}

const PAYLOAD = __PAYLOAD__;

class Screen {
  constructor(elements) {
    this.data = JSON.parse(JSON.stringify(PAYLOAD));
    this._elements = elements;
    this.contentEl = {
      querySelector: (sel) => this._elements[sel] || null,
      querySelectorAll: (sel) =>
        sel === '[data-worker-key]' ? Object.values(this._elements).filter(e => e.dataset.workerKey) : [],
    };
  }
__MEMBERS__
}

export { Screen, makeEl, toasts, calls, dialogs, api };
export function setDialogAnswer(v) { dialogAnswer = v; }
export function setFailure(v) { failWith = v; }
"""

_PAYLOAD = """{
  "memory": {
    "enabled": true,
    "schedule": "every 2h",
    "interval_h": {"value": 2, "min": 1, "max": null},
    "review_every_runs": {"value": 12, "min": 1, "max": null},
    "review_floor": 12,
    "memory_budget_chars": {"value": 3000, "min": 0, "max": null},
    "user_budget_chars": {"value": 3000, "min": 0, "max": null},
    "soul_budget_chars": {"value": 0, "min": 0, "max": null},
    "files": [
      {"label": "MEMORY.md", "chars": 100, "budget": 3000, "exists": true},
      {"label": "USER.md", "chars": 0, "budget": 3000, "exists": false},
      {"label": "SOUL.md", "chars": 50, "budget": 0, "exists": true}
    ],
    "review_state": {"runs_since_review": 3, "stuck_runs": 0, "nothing_new_runs": 0}
  },
  "workers": {
    "gardener": {"enabled": true, "schedule": "every 30min",
                 "interval_min": {"value": 30, "min": 1, "max": 1440},
                 "idle_min": {"value": 30, "min": 0, "max": 1440},
                 "min_hours_between_passes": {"value": 6, "min": 0, "max": 8760}},
    "compact_projects_when_idle": false
  },
  "requires_restart": false
}"""

_MEMBERS = (
    "_scheduleText",
    "_wireWorkerSettings",
    "_saveWorkerNumber",
    "_saveWorkerParams",
    "_workerValue",
    "_repaintWorkerDerived",
    "_budgetMeasure",
    # `_renderBudget` era la riga con dentro il campo modificabile. In
    # cassetto i tetti ora si **leggono** (`_misuraTetto`) e i campi stanno
    # nel pannello «Cambia i tetti»: cambia chi disegna, non cosa si salva.
    "_misuraTetto",
    "_statoTetto",
    "_numberField",
)


def _run(script: str, tmp_path: Path) -> str:
    members = "\n".join(f"  {_member(name)}" for name in _MEMBERS)
    harness = _HARNESS.replace("__MEMBERS__", members).replace("__PAYLOAD__", _PAYLOAD)
    (tmp_path / "harness.mjs").write_text(harness, encoding="utf-8")
    (tmp_path / "test.mjs").write_text(script, encoding="utf-8")
    return run_module(tmp_path / "test.mjs", timeout=30)


def _toggles() -> str:
    return """
  const gardener = makeEl();
  gardener.checked = true;
  const screen = new Screen({ '#gardener-enabled-toggle': gardener });
  screen._wireWorkerSettings();
"""


def test_a_toggle_saves_to_the_right_family(tmp_path) -> None:
    out = _run(
        """
import { Screen, makeEl, calls } from './harness.mjs';
"""
        + _toggles()
        + """
gardener.checked = false;
await gardener.fire('change');
console.log(JSON.stringify(calls));
""",
        tmp_path,
    )
    assert '"family":"workers"' in out
    assert '"gardener_enabled":"0"' in out


def test_a_failed_toggle_rolls_back_and_shows_the_server_message(tmp_path) -> None:
    """Un interruttore che resta acceso dopo un rifiuto e' la bugia piu' facile."""
    out = _run(
        """
import { Screen, makeEl, toasts, setFailure } from './harness.mjs';
setFailure('gardener_enabled must be a boolean');
"""
        + _toggles()
        + """
gardener.checked = false;
await gardener.fire('change');
console.log(JSON.stringify({ checked: gardener.checked, toasts }));
""",
        tmp_path,
    )
    assert '"checked":true' in out
    assert "must be a boolean" in out
    assert '"kind":"error"' in out


def _number(key: str, value: str) -> str:
    return f"""
  const input = makeEl();
  input.dataset.workerKey = '{key}';
  input.value = '{value}';
  const screen = new Screen({{ '[data-worker-key]': input }});
  screen._wireWorkerSettings();
"""


def test_a_number_is_saved_with_its_family(tmp_path) -> None:
    out = _run(
        """
import { Screen, makeEl, calls } from './harness.mjs';
"""
        + _number("gardener_idle_min", "45")
        + """
await input.fire('change');
console.log(JSON.stringify(calls));
""",
        tmp_path,
    )
    assert '"family":"workers"' in out
    assert '"gardener_idle_min":"45"' in out


def test_a_budget_goes_to_the_memory_family(tmp_path) -> None:
    out = _run(
        """
import { Screen, makeEl, calls } from './harness.mjs';
"""
        + _number("soul_budget_chars", "4000")
        + """
await input.fire('change');
console.log(JSON.stringify(calls));
""",
        tmp_path,
    )
    assert '"family":"memory"' in out


def test_a_cadence_below_the_floor_asks_first(tmp_path) -> None:
    out = _run(
        """
import { Screen, makeEl, calls, dialogs } from './harness.mjs';
"""
        + _number("review_every_runs", "1")
        + """
await input.fire('change');
console.log(JSON.stringify({ dialogs, calls }));
""",
        tmp_path,
    )
    assert "settings.memory.reviewConfirm" in out
    assert '"confirm_back_to_back":"1"' in out


def test_a_declined_confirmation_saves_nothing_and_restores_the_field(tmp_path) -> None:
    out = _run(
        """
import { Screen, makeEl, calls, setDialogAnswer } from './harness.mjs';
setDialogAnswer(false);
"""
        + _number("review_every_runs", "1")
        + """
await input.fire('change');
console.log(JSON.stringify({ calls, value: input.value }));
""",
        tmp_path,
    )
    assert '"calls":[]' in out
    assert '"value":12' in out or '"value":"12"' in out


def test_a_cadence_at_the_floor_does_not_ask(tmp_path) -> None:
    out = _run(
        """
import { Screen, makeEl, calls, dialogs } from './harness.mjs';
"""
        + _number("review_every_runs", "12")
        + """
await input.fire('change');
console.log(JSON.stringify({ dialogs, calls }));
""",
        tmp_path,
    )
    assert '"dialogs":[]' in out
    assert '"review_every_runs":"12"' in out


def test_a_refused_number_is_rolled_back_to_the_saved_value(tmp_path) -> None:
    out = _run(
        """
import { Screen, makeEl, toasts, setFailure } from './harness.mjs';
setFailure('gardener_idle_min must be between 0-1440');
"""
        + _number("gardener_idle_min", "99999")
        + """
await input.fire('change');
console.log(JSON.stringify({ value: input.value, toasts }));
""",
        tmp_path,
    )
    assert "must be between 0-1440" in out
    assert '"value":30' in out or '"value":"30"' in out


@pytest.mark.parametrize("bad", ["12.5", "molto", "45px", "-"])
def test_something_that_is_not_a_whole_number_is_refused_out_loud(
    tmp_path, bad: str
) -> None:
    """Rifiutato **e detto**: il campo torna indietro da solo, e senza una riga
    di spiegazione sembrerebbe che il tocco non sia stato registrato."""
    out = _run(
        """
import { Screen, makeEl, calls, toasts } from './harness.mjs';
"""
        + _number("gardener_idle_min", bad)
        + """
await input.fire('change');
console.log(JSON.stringify({ calls, toasts, value: input.value }));
""",
        tmp_path,
    )
    assert '"calls":[]' in out
    assert "settings.workers.notAWholeNumber" in out
    assert '"value":30' in out or '"value":"30"' in out


@pytest.mark.parametrize("blank", ["", "  "])
def test_a_cleared_field_goes_back_quietly(tmp_path, blank: str) -> None:
    """Svuotare un campo non e' un errore dell'utente: si rimette il valore e si
    tace. Un toast qui sarebbe rumore su un gesto che capita scrivendo."""
    out = _run(
        """
import { Screen, makeEl, calls, toasts } from './harness.mjs';
"""
        + _number("gardener_idle_min", blank)
        + """
await input.fire('change');
console.log(JSON.stringify({ calls, toasts, value: input.value }));
""",
        tmp_path,
    )
    assert '"calls":[]' in out
    assert '"toasts":[]' in out
    assert '"value":30' in out or '"value":"30"' in out


def test_the_exponent_form_of_an_input_type_number_is_normalised(tmp_path) -> None:
    """``<input type=number>`` accetta ``1e3``, e il server fa ``int(raw)`` — che
    su quella stringa solleva. Il client manda il valore normalizzato, quindi le
    due estremita' non litigano su una forma che il browser considera valida."""
    out = _run(
        """
import { Screen, makeEl, calls } from './harness.mjs';
"""
        + _number("gardener_idle_min", "1e3")
        + """
await input.fire('change');
console.log(JSON.stringify(calls));
""",
        tmp_path,
    )
    assert '"gardener_idle_min":"1000"' in out


def test_a_file_that_cannot_be_opened_does_not_read_as_empty(tmp_path) -> None:
    """La barra e la riga sotto non devono inventare uno zero.

    Misurato sul telefono con un ``chmod 000``: il payload portava ``chars: 0``
    per un file da 2.407 byte, e la riga diceva «0 caratteri su 3.000». Il
    ``readable`` del server la fa diventare "non misurabile".
    """
    out = _run(
        """
import { Screen, makeEl } from './harness.mjs';
const screen = new Screen({});
const memory = JSON.parse(JSON.stringify(screen.data.memory));
memory.files = [{ label: 'SOUL.md', chars: 0, budget: 3000, exists: true, readable: false }];
console.log(screen._budgetMeasure(memory, 'SOUL.md', 'soul_budget_chars'));
""",
        tmp_path,
    )
    assert "settings.memory.unmeasured" in out


def test_a_worker_that_is_off_does_not_advertise_a_schedule(tmp_path) -> None:
    """Sotto un interruttore su OFF, «ogni 30min» si legge come se girasse ancora.

    Visto sul telefono il 31/08/2026: spegnendo il giardiniere la riga della
    pianificazione restava. Lato server `describe_schedule()` fa bene a
    descriverla — e' quella che verrebbe armata — ma in interfaccia, spento, la
    riga tace: a dire cosa resta possibile c'e' gia' l'aiuto sotto.
    """
    out = _run(
        """
import { Screen } from './harness.mjs';
const screen = new Screen({});
console.log(JSON.stringify({
  on: screen._scheduleText(true, 'every 30min'),
  off: screen._scheduleText(false, 'every 30min'),
}));
""",
        tmp_path,
    )
    assert '"on":"every 30min"' in out
    assert '"off":""' in out


def test_after_a_save_the_measure_and_the_headroom_stay_in_their_rows(tmp_path) -> None:
    """Regressione di ``013f93c``: dopo un salvataggio ``_repaintWorkerDerived``
    scriveva la misura nella riga di «quanto resta», che quindi compariva due
    volte mentre «restano N» spariva. Il ridisegno deve dire le stesse cose
    del disegno."""
    out = _run(
        """
import assert from 'node:assert/strict';
import { Screen, makeEl } from './harness.mjs';

/* Il disegno vero: si legge dall'HTML di _misuraTetto cosa sta in quale riga. */
const screen0 = new Screen({});
const html = screen0._misuraTetto(screen0.data.memory, 'MEMORY.md', 'memory_budget_chars');
const cella = (attr) => html.match(new RegExp(attr + '="MEMORY.md"[^>]*>([^<]*)<'))[1];
const primaValore = cella('data-measure-value');
const primaResto = cella('data-measure');
assert.match(primaValore, /ofBudget/);
assert.match(primaResto, /headroom/);

const valore = makeEl({ textContent: primaValore });
const resto = makeEl({ textContent: primaResto });
const fill = { style: {} };
const meter = makeEl({
  classList: { toggle() {} },
  querySelector: () => fill,
});
const screen = new Screen({
  '[data-measure-value="MEMORY.md"]': valore,
  '[data-measure="MEMORY.md"]': resto,
  '[data-meter="MEMORY.md"]': meter,
});
await screen._saveWorkerParams('memory', { memory_budget_chars: '3000' });
assert.equal(valore.textContent, primaValore, 'la misura non e\\' piu\\' al suo posto');
assert.equal(resto.textContent, primaResto, '«quanto resta» e\\' stato sostituito');
assert.notEqual(resto.textContent, valore.textContent, 'la misura compare due volte');
console.log('ok');
""",
        tmp_path,
    )
    assert out.strip() == "ok"
