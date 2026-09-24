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
from pathlib import Path

from support.js_harness import function, member, requires_node, run_js

ASSETS = Path(__file__).resolve().parents[2] / "jenny" / "templates" / "ui" / "assets"
AUDIT_JS = ASSETS / "casa-audit.js"
APP_JS = ASSETS / "casa-app.js"
API_JS = ASSETS / "shared" / "api-client.js"


pytestmark = requires_node


def _run(script: str) -> None:
    src = AUDIT_JS.read_text(encoding="utf-8")
    harness = "import assert from 'node:assert/strict';\n" + function(src, "offsetsIn")
    run_js(harness + "\n" + script)


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


# ── L'atterraggio in chat ───────────────────────────────────────────────────


def _member(source: str, name: str) -> str:
    return member(source, name, prefixes=("async ",))


_HARNESS = """
import assert from 'node:assert/strict';

const parole = {
  'casa.audit.msgHead': 'In «{page}», dove dice «{quote}»:',
  'casa.audit.msgRef': 'segnalazione',
};
const i18n = { t: (k) => parole[k] ?? k };

__MESSAGGIO__

const mandati = [];
class Casa {
  constructor() {
    this.input = { value: '', };
    this.view = 'reader';
    this.autosize = 0;
  }
  _setView(v) { this.view = v; }
  _send() { mandati.push(this.input.value); this.input.value = ''; }
  _autosize() { this.autosize += 1; }
  __PORTA__
}
"""


def _run_app(script: str) -> None:
    harness = (
        _HARNESS
        .replace("__MESSAGGIO__", function(AUDIT_JS.read_text(encoding="utf-8"),
                                            "messaggioSegnalazione"))
        .replace("__PORTA__", _member(APP_JS.read_text(encoding="utf-8"), "_portaInChat"))
    )
    run_js(harness + "\n" + script)


def test_the_message_carries_the_page_the_quote_and_the_id() -> None:
    """I tre pezzi, e ognuno serve a una cosa sola.

    **L'id e' quello che si dimentica**, ed e' l'unico che lei non puo'
    ricostruire: senza, corregge la pagina e il file resta aperto per sempre —
    cioe' esattamente il vicolo cieco che questo atterraggio esiste per
    chiudere.
    """
    _run_app("""
      const m = messaggioSegnalazione({
        title: 'Orto', quote: 'legare a giugno',
        comment: 'e\\' marzo', id: '20260922-143012-a1b2',
      });
      assert.ok(m.includes('Orto'), m);
      assert.ok(m.includes('legare a giugno'), m);
      assert.ok(m.includes("e\\' marzo"), m);
      assert.ok(m.includes('20260922-143012-a1b2'), m);
    """)


def test_without_an_id_there_is_no_empty_parenthesis() -> None:
    _run_app("""
      const m = messaggioSegnalazione({ title: 'X', quote: 'y', comment: 'z', id: '' });
      assert.ok(!m.includes('('), m);
    """)


def test_filing_lands_in_the_chat_with_the_message_already_sent() -> None:
    """Il file e' gia' nato quando si arriva qui.

    Lasciare il messaggio nella casella senza inviarlo riporterebbe nel vuoto
    proprio quella segnalazione — che e' il difetto per cui questo atterraggio
    esiste. Quindi le due asserzioni sono due: **la stanza** e **la partenza**.
    """
    _run_app("""
      const c = new Casa();
      c._portaInChat({ title: 'Orto', quote: 'q', comment: 'non va', id: 'abc' });
      assert.equal(c.view, 'chat');
      assert.equal(mandati.length, 1);
      assert.ok(mandati[0].includes('non va'), mandati[0]);
      mandati.length = 0;
    """)


def test_a_draft_in_the_composer_is_not_eaten() -> None:
    """Quel che stavi scrivendo non e' un danno collaterale.

    La casella viene usata come veicolo — e' l'unico modo di far disegnare la
    bolla da chi la disegna sempre — quindi la bozza va tolta e rimessa.
    """
    _run_app("""
      const c = new Casa();
      c.input.value = 'stavo scrivendo questo';
      c._portaInChat({ title: 'X', quote: 'q', comment: 'w', id: 'abc' });
      assert.equal(mandati.length, 1);
      assert.ok(!mandati[0].includes('stavo scrivendo'), mandati[0]);
      assert.equal(c.input.value, 'stavo scrivendo questo');
      mandati.length = 0;
    """)
