"""Un rifiuto del gateway, detto a parole — e a chi appartiene.

Il difetto da cui veniamo: l'officina mostrava «Errore: image_rejected», cioè il
nome che quel rifiuto ha nel codice sorgente, a chi stava mandando una foto; e
la casa non mostrava niente. Il motivo vero (`decode`, `size`, `too_many_files`)
c'era nel frame e veniva scartato.

Qui si misura **quel che si legge**, non che una funzione sia stata chiamata: il
test centrale è che la frase mostrata non sia mai il codice da sola.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "jenny" / "templates" / "ui" / "assets"
WIRE_ERROR_JS = ASSETS / "shared" / "wire-error.js"
I18N = ASSETS / "i18n"

_NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(_NODE is None, reason="node non disponibile")


def _locale(name: str) -> dict:
    return json.loads((I18N / f"{name}.json").read_text(encoding="utf-8"))


def _harness() -> str:
    """Il modulo vero, con le traduzioni vere: la coppia è ciò che si misura."""
    words = {name: _locale(name)["common"]["wireError"] for name in ("it", "en")}
    return f"""
import assert from 'node:assert/strict';
const {{ describeWireError, WIRE_ERROR_PREFIX }} = await import('{WIRE_ERROR_JS.as_uri()}');

const WORDS = {json.dumps(words, ensure_ascii=False)};
/* Come `i18n.t`: chiave trovata → la frase, chiave assente → **la chiave**.
   Quel ritorno è il difetto che questo banco deve poter vedere. */
const translator = (locale) => (key) => {{
  const rest = key.startsWith(WIRE_ERROR_PREFIX + '.') ? key.slice(WIRE_ERROR_PREFIX.length + 1) : null;
  const word = rest === null ? undefined : WORDS[locale][rest];
  return typeof word === 'string' ? word : key;
}};
const it = translator('it');
const en = translator('en');
"""


def _run_js(script: str) -> None:
    proc = subprocess.run(
        [str(_NODE), "--input-type=module", "-e", _harness() + "\n" + script],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout


# ── Quel che si legge ────────────────────────────────────────────────────────


def test_a_rejected_photo_is_explained_in_words() -> None:
    _run_js("""
      const out = describeWireError({ detail: 'image_rejected', reason: 'decode' }, it);
      assert.equal(out.text, 'Non sono riuscita ad aprire questo file');
      assert.equal(out.blocksSend, true, 'la foto non è entrata: il messaggio deve tornare indietro');
    """)


def test_the_raw_code_is_never_the_message() -> None:
    """Il test che avrebbe preso lo stato dell'officina prima di questo lavoro."""
    _run_js("""
      const codes = ['decode', 'size', 'malformed', 'too_many_images', 'too_many_videos',
                     'too_many_files', 'missing_content', 'unknown_type', 'invalid_task_id',
                     'invalid_project_name'];
      for (const reason of codes) {
        for (const t of [it, en]) {
          const { text } = describeWireError({ detail: 'image_rejected', reason }, t);
          assert.ok(!text.includes(reason), `${reason}: il codice è finito a schermo → ${text}`);
          assert.ok(!text.includes(WIRE_ERROR_PREFIX), `${reason}: chiave non tradotta → ${text}`);
          assert.ok(text.length > 4, `${reason}: frase vuota`);
        }
      }
    """)


def test_an_unknown_code_still_says_something_and_keeps_the_thread() -> None:
    """Un codice nuovo non deve sparire né mostrarsi da solo: frase + codice."""
    _run_js("""
      const { text, code } = describeWireError({ reason: 'heic_unsupported' }, it);
      assert.equal(text, 'Qualcosa non ha funzionato (heic_unsupported)');
      assert.equal(code, 'heic_unsupported');
    """)


def test_a_frame_with_nothing_in_it_does_not_print_empty_parens() -> None:
    _run_js("""
      assert.equal(describeWireError({}, it).text, 'Qualcosa non ha funzionato');
      assert.equal(describeWireError(null, it).text, 'Qualcosa non ha funzionato');
    """)


# ── A chi appartiene ─────────────────────────────────────────────────────────


def test_an_attachment_rejected_for_a_reason_we_do_not_know_still_comes_back() -> None:
    """La ragione per cui la famiglia non si decide solo dal `reason`.

    Se domani il server inventa un motivo nuovo per un allegato, con una tabella
    sola quel messaggio non tornerebbe indietro: perderesti quel che avevi
    scritto per colpa di un elenco non aggiornato. `detail` lo dice comunque.
    """
    _run_js("""
      const out = describeWireError({ detail: 'image_rejected', reason: 'codec_zzz' }, it);
      assert.equal(out.blocksSend, true);
    """)


def test_what_is_not_about_your_message_does_not_take_it_back() -> None:
    _run_js("""
      for (const reason of ['unknown_type', 'invalid_task_id', 'invalid_project_name']) {
        const out = describeWireError({ reason }, it);
        assert.equal(out.blocksSend, false, reason);
      }
    """)


# ── Le chiavi esistono davvero ───────────────────────────────────────────────


def test_every_code_the_module_knows_has_words_in_both_languages() -> None:
    """`t` ritorna **la chiave** quando non la trova.

    Una voce dimenticata non è un errore: è `common.wireError.decode` stampato a
    schermo, che è peggio del codice da cui veniamo.
    """
    known = set(re.findall(r"'([a-z_]+)',", re.search(
        r"const KNOWN = new Set\(\[(.*?)\]\)", WIRE_ERROR_JS.read_text(encoding="utf-8"), re.S
    ).group(1)))
    assert len(known) >= 10, known
    for name in ("it", "en"):
        words = _locale(name)["common"]["wireError"]
        missing = sorted(known - set(words))
        assert not missing, f"{name}: {missing}"
        assert "unknown" in words, f"{name}: manca il ripiego"


def test_the_two_languages_say_the_same_things() -> None:
    assert set(_locale("it")["common"]["wireError"]) == set(_locale("en")["common"]["wireError"])


def test_the_server_codes_are_all_covered() -> None:
    """L'elenco del client contro quello del server, che è la fonte.

    Un `reason` nuovo aggiunto nel gateway senza una parola qui cadrebbe sul
    ripiego generico — che è accettabile a runtime e non lo è a mente fredda.
    """
    ws = (ROOT / "jenny" / "channels" / "websocket.py").read_text(encoding="utf-8")
    server_codes = set(re.findall(r'reason="([a-z_]+)"', ws))
    server_codes |= set(re.findall(r'_REASON = "([a-z_]+)"', ws))
    server_codes |= set(re.findall(r'return \[\], "([a-z_]+)"', ws))
    server_codes |= set(re.findall(r'_abort\("([a-z_]+)"\)', ws))
    words = set(_locale("it")["common"]["wireError"])
    missing = sorted(server_codes - words)
    assert not missing, f"il gateway manda codici senza parole: {missing}"
