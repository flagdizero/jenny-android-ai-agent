"""Ogni errore che il gateway manda al client porta una parola per la macchina.

Un frame ``error`` ha due campi e facevano due mestieri diversi senza dirlo:
``detail`` era a volte un codice (``image_rejected``), a volte una frase inglese
intera (il progetto col nome non valido), a volte una stringa da debug
(``unknown type: 'foo'``). ``reason`` — la sola parte su cui un client possa
decidere cosa scrivere — c'era su due errori su sei.

Il risultato a schermo, in officina, era «Errore: image_rejected»: un codice
sorgente mostrato a chi sta mandando una foto. E in casa niente del tutto.

L'invariante, da qui in avanti:

* **``reason`` è per la macchina e c'è sempre.** È un identificatore stabile in
  ``snake_case``, e la WebUI ci appende sopra le proprie parole tradotte.
* **``detail`` è per il log.** Può restare quel che è — anche una frase, anche
  un ``repr`` — e il client lo mostra solo quando il codice non lo conosce.

Questo test guarda l'albero sintattico e non il testo: un ``grep`` passerebbe su
una chiamata scritta su più righe, che è esattamente come sono scritte quelle
nuove.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHANNELS = ROOT / "jenny" / "channels"

# ``snake_case``: niente spazi, niente maiuscole, niente punteggiatura. È ciò che
# distingue una parola per la macchina da una frase per una persona.
_MACHINE_WORD = str.isidentifier


def _error_calls(path: Path) -> list[tuple[int, ast.Call]]:
    """Le chiamate a ``_send_event(..., "error", ...)`` di un modulo."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[tuple[int, ast.Call]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "_send_event"):
            continue
        # Il nome dell'evento è il secondo argomento posizionale.
        if len(node.args) < 2:
            continue
        event = node.args[1]
        if isinstance(event, ast.Constant) and event.value == "error":
            found.append((node.lineno, node))
    return found


def _keyword(call: ast.Call, name: str) -> ast.expr | None:
    for kw in call.keywords:
        if kw.arg == name:
            return kw.value
    return None


def _all_error_calls() -> list[tuple[Path, int, ast.Call]]:
    calls: list[tuple[Path, int, ast.Call]] = []
    for path in sorted(CHANNELS.glob("*.py")):
        calls.extend((path, lineno, call) for lineno, call in _error_calls(path))
    return calls


def test_there_are_error_frames_to_check() -> None:
    """Un test che non confronta niente passa sempre: qui si conta prima.

    Se ``_send_event`` venisse rinominato, i due test sotto diventerebbero
    verdi a vuoto invece che rossi — ed è il modo preciso in cui un contratto
    smette di esistere senza che nessuno se ne accorga.
    """
    assert len(_all_error_calls()) >= 6


def test_every_error_frame_carries_a_reason() -> None:
    missing = [
        f"{path.name}:{lineno}"
        for path, lineno, call in _all_error_calls()
        if _keyword(call, "reason") is None
    ]
    assert not missing, (
        "questi errori non danno al client nessuna parola su cui decidere cosa "
        f"scrivere, quindi finiranno a schermo come codice o come niente: {missing}"
    )


def test_every_reason_is_a_machine_word_not_a_sentence() -> None:
    """``reason`` è la chiave su cui il client cerca la traduzione.

    Una frase lì dentro non si può tradurre e non si può confrontare: diventa
    inglese a schermo, che è il difetto da cui veniamo. Le frasi stanno in
    ``detail``, che nessuno traduce perché nessuno lo mostra se non come ripiego.
    """
    wrong: list[str] = []
    for path, lineno, call in _all_error_calls():
        reason = _keyword(call, "reason")
        if reason is None:
            continue  # se ne occupa il test sopra
        # Una costante di classe (``self._INVALID_PROJECT_REASON``) va letta dove
        # è definita; qui basta sapere che non è una stringa scritta a mano male.
        if isinstance(reason, ast.Constant):
            if not isinstance(reason.value, str) or not _MACHINE_WORD(reason.value):
                wrong.append(f"{path.name}:{lineno} -> {reason.value!r}")
        elif isinstance(reason, ast.Name | ast.Attribute):
            continue
        else:
            wrong.append(f"{path.name}:{lineno} -> espressione non costante")
    assert not wrong, wrong


def test_the_named_reasons_are_machine_words_too() -> None:
    """Le costanti che finiscono in ``reason``, lette dove sono definite."""
    source = (CHANNELS / "websocket.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            name = getattr(target, "id", None) or getattr(target, "attr", None)
            if not name or not name.endswith("_REASON"):
                continue
            assert isinstance(node.value, ast.Constant), name
            assert _MACHINE_WORD(node.value.value), (name, node.value.value)
