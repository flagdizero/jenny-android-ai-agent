"""«Segnala»: dalla selezione agli offset nel markdown sorgente.

`/api/audit/create` vuole `selStart`/`selEnd`, che sono posizioni nel **`.md`**,
ma la selezione avviene nel reso. Il sorgente pero' e' gia' in mano — `/api/page`
manda `raw` — quindi non serve ricostruire la mappa fra i due: basta ritrovarci
dentro il testo scelto.

**Il valore di questo banco sta nei due rifiuti**, non nel caso che riesce. Un
commento attaccato al punto sbagliato e' peggio di un commento non scritto,
perche' nessuno dei due lati se ne accorge: il file esiste, il linter lo vede,
Jenny lo legge, e parla di una frase diversa da quella che avevi in mente.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ASSETS = Path(__file__).resolve().parents[2] / "jenny" / "templates" / "ui" / "assets"
AUDIT_JS = ASSETS / "casa-audit.js"
API_JS = ASSETS / "shared" / "api-client.js"

_NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(_NODE is None, reason="node non disponibile")


def _function(source: str, name: str) -> str:
    m = re.search(rf"(?ms)^export function {re.escape(name)}\(.*?^\}}$", source)
    assert m, f"function {name} non trovata"
    return m.group(0).replace("export function", "function")


def _run(script: str) -> None:
    src = AUDIT_JS.read_text(encoding="utf-8")
    harness = "import assert from 'node:assert/strict';\n" + _function(src, "offsetsIn")
    proc = subprocess.run(
        [str(_NODE), "--input-type=module", "-e", harness + "\n" + script],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout


def test_a_unique_selection_gives_its_offsets_in_the_source() -> None:
    _run("""
      const raw = '# Orto\\n\\nI pomodori vanno legati a giugno.\\n';
      const r = offsetsIn(raw, 'legati a giugno');
      assert.equal(r.ok, true);
      assert.equal(raw.slice(r.start, r.end), 'legati a giugno');
    """)


def test_a_selection_that_appears_twice_is_refused() -> None:
    """Ambigua vuol dire **non si ancora**, non «si prende la prima».

    Prendere la prima darebbe un audit ben formato, che il linter accetta e
    Jenny legge, attaccato a una frase che non e' quella che avevi scelto: un
    guasto che nessuno dei due lati puo' vedere.
    """
    _run("""
      const raw = 'legare a giugno.\\n\\nMa non tutto: legare a giugno.\\n';
      const r = offsetsIn(raw, 'legare a giugno');
      assert.equal(r.ok, false);
      assert.equal(r.reason, 'ambiguous');
    """)


def test_a_selection_across_formatting_is_refused_and_not_guessed() -> None:
    """Nel reso c'e' «molto importante», nel sorgente «**molto** importante».

    E' il caso vero piu' comune del rifiuto, ed e' la ragione per cui la
    risposta dice cosa fare — scegli un pezzo senza grassetti dentro — invece
    di limitarsi a fallire.
    """
    _run("""
      const raw = 'Questo e\\' **molto** importante.\\n';
      const r = offsetsIn(raw, 'molto importante');
      assert.equal(r.ok, false);
      assert.equal(r.reason, 'notFound');
    """)


def test_an_empty_selection_is_not_an_anchor() -> None:
    _run("""
      for (const s of ['', '   ', '\\n', null, undefined]) {
        assert.equal(offsetsIn('qualcosa', s).reason, 'empty', JSON.stringify(s));
      }
    """)


def test_the_client_does_not_send_the_markdown_the_route_ignores() -> None:
    """Il vecchio client mandava anche `rawMarkdown`, e la rotta lo **ignorava**:
    si rilegge il file da sola (`raw_path.read_text`) e calcola le ancore.

    Mandarlo sarebbe una pagina intera nella query string, cioe' nella riga di
    richiesta, dove `websockets` ne ammette 8192 byte in tutto: non un peso
    inutile, un errore di trasporto.
    """
    src = API_JS.read_text(encoding="utf-8")
    m = re.search(r"async createAudit\(.*?\n  \}", src, re.S)
    assert m, "createAudit non trovata"
    assert "rawMarkdown" not in m.group(0)
    assert "raw" not in m.group(0).replace("rawMarkdown", "")


def test_nothing_in_the_flow_asks_for_a_severity() -> None:
    """La gravita' e' uscita dal formato il 22/09/2026, e qui va misurata assente.

    Qui c'erano due banchi: uno teneva le quattro voci del client pari a quelle
    del linter della skill, l'altro le teneva tradotte in due lingue. Erano
    banchi giusti su una cosa sbagliata — un menu' che chiede a chi segnala di
    dare un voto alla propria lamentela, cioe' un campo da coda di smistamento
    in un posto dove chi segnala e chi corregge sono la stessa persona.

    **Quel che resta da misurare e' il verso opposto**, e in tre punti, perche'
    sono tre modi diversi di lasciarla rientrare: il client non la manda, il
    linter non la chiede, e le lingue non ne portano piu' le parole.
    """
    client = (AUDIT_JS.read_text(encoding="utf-8")
              + API_JS.read_text(encoding="utf-8"))
    assert "severity" not in client
    assert "SEVERITIES" not in client

    lint = (
        Path(__file__).resolve().parents[2]
        / "jenny" / "skills" / "llm-wiki" / "scripts" / "lint_wiki.py"
    ).read_text(encoding="utf-8")
    assert "VALID_SEVERITIES" not in lint
    m = re.search(r"AUDIT_REQUIRED_FIELDS = \{([^}]*)\}", lint)
    assert m, "AUDIT_REQUIRED_FIELDS non trovata"
    assert "severity" not in m.group(1)

    for lang in ("it", "en"):
        data = json.loads((ASSETS / "i18n" / f"{lang}.json").read_text(encoding="utf-8"))
        assert "sev" not in data["casa"]["audit"], lang
