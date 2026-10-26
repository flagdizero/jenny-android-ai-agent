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


def _announce(**kw) -> str:
    return render_template("agent/subagent_announce.md", label="editor",
                           status_text="completed successfully", task="Scrivi", silent=False,
                           result="Fatto.\n\n**To research**\n- felce: umidità", **kw)


def test_in_a_plan_the_announce_leaves_research_to_the_code() -> None:
    """La lista To research la legge il driver, e il giro lo decide il cancello:
    il template che diceva «research them» a ogni resoconto era il motore del
    giro infinito del Titan (26/10)."""
    text = _announce(research_items=1, in_plan=True)
    assert "The plan keeps this list" in text and "do not start research" in text
    assert "research them" not in text


def test_outside_a_plan_the_announce_still_asks_to_research() -> None:
    text = _announce(research_items=1, in_plan=False)
    assert "research them" in text and "The plan keeps this list" not in text


def test_a_mention_of_to_research_is_not_a_list() -> None:
    """Il renderer conta le voci col parser del driver: nominarla in una frase
    non e' una lista, dentro o fuori un piano."""
    text = render_template("agent/subagent_announce.md", label="editor",
                           status_text="completed successfully", task="Scrivi", silent=False,
                           result="Nothing left To research here.", research_items=0,
                           in_plan=False)
    assert "research them" not in text and "The plan keeps this list" not in text
