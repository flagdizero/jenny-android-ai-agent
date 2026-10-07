"""L'annuncio di un aiutante non ripete il compito intero.

Il testo intero e' gia' nella cronologia, negli argomenti della ``spawn``: sul
Titan un compito da 10 k caratteri tornava tale e quale nell'annuncio, e ogni
delega entrava due volte nel contesto.
"""

from jenny.agent.subagent import _ANNOUNCE_TASK_CHARS, _task_head
from jenny.utils.prompt_templates import render_template


def test_a_long_brief_comes_back_as_its_first_line() -> None:
    brief = "Verifica i prezzi dei quattro ETF.\n\n" + "dettaglio " * 1000
    head = _task_head(brief)
    assert head.startswith("Verifica i prezzi dei quattro ETF.")
    assert "full brief is in your spawn call" in head and "dettaglio" not in head


def test_a_long_first_line_is_cut() -> None:
    head = _task_head("x" * 5000)
    assert len(head) < _ANNOUNCE_TASK_CHARS + 60 and "…" in head


def test_a_one_line_task_is_unchanged() -> None:
    assert _task_head("Leggi l'umidita'") == "Leggi l'umidita'"


def test_the_announce_keeps_its_shape() -> None:
    text = render_template("agent/subagent_announce.md", label="prezzi",
                           status_text="completed successfully",
                           task=_task_head("Verifica\nmolto altro"), result="fatto", silent=False)
    assert text.startswith("[Subagent 'prezzi' completed successfully]\n\nTask: Verifica")
    assert "molto altro" not in text and "Result:\nfatto" in text
