"""Tre superfici, un patto solo: chi disegna contenuto scritto lo disegna intero.

La chat dell'officina, la chat di casa e il lettore delle pagine mostrano tutte
testo che Jenny ha scritto, e in tutte e tre quel testo puo' contenere una
formula o un diagramma — la skill `llm-wiki` glieli **impone**: «ogni flusso,
gerarchia o stato deve essere mermaid», «ogni formula deve essere KaTeX».

Il difetto che questo banco chiude e' che una superficie resti indietro senza
che nessuno se ne accorga, ed e' successo due volte in due giorni:

* la chat di casa non ha **mai** disegnato una formula, mentre quella
  dell'officina si';
* e quando le librerie sono state cancellate, i quattro chiamanti rimasti in
  officina si sono spenti in silenzio, perche' ognuno cominciava con «se la
  libreria c'e'».

Le due meta' della stessa cosa: il *come* deve stare in un posto solo
(`shared/rich-content.js`), e chi disegna markdown deve chiamarlo. Qui si misura
la seconda; la prima la misura `test_vendor_contract.py`.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "jenny" / "templates" / "ui" / "assets"

# Le superfici, e come si riconosce in ognuna «ho appena scritto del contenuto».
SUPERFICI = {
    "mobile-chat.js": r"\.innerHTML = renderMarkdown\(",
    "home-chat.js": r"\.innerHTML = renderMarkdown\(",
    "home-reader.js": r"\.innerHTML = this\._safeHtml\(",
}

# La chiamata che disegna il resto, comunque si chiami localmente.
RICCO = re.compile(r"\brenderRich\w*\(")

# Le esenzioni, e portano il loro perche' addosso: **il testo che sta ancora
# arrivando**. Una formula a meta' non e' una formula, e un diagramma a meta' e'
# un errore di sintassi — mermaid pianterebbe a schermo il proprio messaggio in
# inglese, e lo rifarebbe a ogni pezzetto. Si disegna a testo finito, che in
# entrambe le chat e' il punto subito dopo (`_streamEnd` / `_handleStreamEnd`).
#
# Sono due perche' le due chat coalizzano in modo diverso: casa riscrive a ogni
# delta, l'officina una volta per frame dentro un rAF condiviso. Stessa cosa,
# due nomi.
ESENTI = {
    ("home-chat.js", "_delta"),
    ("mobile-chat.js", "_flushRender"),
    # Il ragionamento visibile passa di qui a ogni frame mentre arriva. Il suo
    # momento buono e' `_handleReasoningEnd`, che chiude il segmento e disegna.
    ("mobile-chat.js", "_renderReasoningBody"),
}


def _funzione_attorno(rows: list[str], i: int) -> str:
    """Il nome del metodo che contiene la riga *i*, guardando all'indietro."""
    for j in range(i, max(-1, i - 40), -1):
        m = re.match(r"\s{2,6}(?:async )?(_?\w+)\([^)]*\)\s*\{\s*$", rows[j])
        if m:
            return m.group(1)
    return "?"


def test_every_surface_that_draws_markdown_draws_the_rest_too() -> None:
    for name, mark in SUPERFICI.items():
        rows = (ASSETS / name).read_text(encoding="utf-8").splitlines()
        siti = [i for i, r in enumerate(rows) if re.search(mark, r)]
        assert siti, f"{name}: nessun punto che disegna markdown — il segno e' cambiato"
        for i in siti:
            # Quindici righe e non quattro: fra la scrittura e il disegno ci
            # sta il commento che spiega la scelta (il dollaro in riga nel
            # lettore, per dirne uno), e un banco che punisce la spiegazione
            # insegna a non scriverla.
            vicino = "\n".join(rows[i : i + 15])
            if RICCO.search(vicino):
                continue
            fn = _funzione_attorno(rows, i)
            assert (name, fn) in ESENTI, (
                f"{name}:{i + 1} (in `{fn}`) scrive markdown e non disegna formule "
                f"e diagrammi. Se e' voluto, l'esenzione va dichiarata nel banco "
                f"col suo motivo — non lasciata implicita, che e' come la chat di "
                f"casa e' rimasta senza formule per due giorni senza che si vedesse."
            )


def test_no_surface_talks_to_the_libraries_by_itself() -> None:
    """Il *come* in un posto solo. Due copie e' come e' cominciato il guaio: chat
    e lettore ne avevano una ciascuno, ne e' morta una, e la cancellazione delle
    librerie ha guardato solo quella."""
    for name in SUPERFICI:
        src = (ASSETS / name).read_text(encoding="utf-8")
        codice = re.sub(r"//.*", "", re.sub(r"/\*.*?\*/", "", src, flags=re.S))
        for diretto in ("renderMathInElement", "mermaid.render", "mermaid.initialize"):
            assert diretto not in codice, (
                f"{name} chiama {diretto} per conto suo: il come sta in "
                f"shared/rich-content.js, o le copie divergono"
            )
        assert "rich-content.js" in src, f"{name} non importa il modulo condiviso"


def test_the_inline_dollar_is_on_only_where_the_skill_mandates_it() -> None:
    """`$...$` in riga: acceso nelle pagine, dove la skill impone `$f(x)$` e chi
    scrive conosce la regola della casa; spento nelle due chat, dove «costa $5,
    forse $10» diventerebbe un tentativo di scrivere «5, forse » in matematica.
    """
    lettore = (ASSETS / "home-reader.js").read_text(encoding="utf-8")
    assert "inlineDollar: true" in lettore, (
        "il lettore non accende il dollaro in riga: le formule che la skill "
        "impone a Jenny resterebbero `$f(x)$` in chiaro"
    )
    for chat in ("mobile-chat.js", "home-chat.js"):
        src = (ASSETS / chat).read_text(encoding="utf-8")
        assert "inlineDollar" not in src, f"{chat} ha acceso il dollaro in riga"
