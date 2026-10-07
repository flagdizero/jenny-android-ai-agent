"""Il prompt e gli strumenti dicono a un modello piccolo una cosa sola, e vera.

Misure del 07/10/2026 sul Titan (``deepseek-v4.1-flash``): un messaggio scritto
a meta' turno si mescolava al compito in corso; l'orchestratore leggeva file da
16 k che restavano nella conversazione e riceveva consigli su ``python_exec``,
che non ha; le cause si dichiaravano senza verificarle.
"""

from __future__ import annotations

from types import SimpleNamespace

from jenny.agent.context_governor import MID_TURN_LABEL, label_mid_turn_messages
from jenny.agent.tools.filesystem import ReadFileTool
from jenny.session.history_meta import MID_TURN_META
from jenny.utils.prompt_templates import render_template


def test_a_mid_turn_message_is_labelled_in_the_request_only() -> None:
    typed = {"role": "user", "content": "puoi lanciare l'aggiornamento?", MID_TURN_META: True}
    other = {"role": "user", "content": "domanda normale"}
    out = label_mid_turn_messages([other, typed])
    assert out[1]["content"].startswith(MID_TURN_LABEL)
    assert out[0] is other and typed["content"] == "puoi lanciare l'aggiornamento?"
    image = {"role": "user", MID_TURN_META: True,
             "content": [{"type": "image_url", "image_url": {"url": "x"}}]}
    assert label_mid_turn_messages([image])[0]["content"][0]["text"] == MID_TURN_LABEL


def _read_tool(tmp_path, orchestrator: bool):
    ctx = SimpleNamespace(
        workspace=str(tmp_path), orchestrator=orchestrator, file_states=None,
        config=SimpleNamespace(restrict_to_workspace=True, file=SimpleNamespace()))
    return ReadFileTool.create(ctx)


async def test_the_orchestrator_reads_a_bounded_slice(tmp_path) -> None:
    big = tmp_path / "big.txt"
    big.write_text("riga lunga di testo\n" * 3000, encoding="utf-8")
    orchestrator = _read_tool(tmp_path, True)
    out = await orchestrator.execute(path=str(big))
    assert len(out) < 14_000 and "offset=" in out
    assert "delegate" in orchestrator.description and "python_exec" not in orchestrator.description
    doer = _read_tool(tmp_path, False)
    assert len(await doer.execute(path=str(big))) > 50_000


def test_the_contract_asks_to_say_checked_or_guessed() -> None:
    text = render_template("agent/tool_contract.md", orchestrator=True, has=lambda _n: True,
                           output_path="/x", project=True)
    assert "**checked** it or are **guessing**" in text
    assert "never ask them to\n  change permissions" in text
    assert "**values** are the ones expected" in text


def test_the_orchestrator_knows_its_two_writes() -> None:
    text = render_template("agent/orchestrator.md")
    assert "Two writes are yours" in text and "`app` tool" in text and "still a spawn" in text
    assert len(text) < 3_200


def test_the_message_rule_has_no_wrong_example() -> None:
    text = render_template("agent/identity.md", runtime="x", workspace_path="/w",
                           install_path="/w", platform_policy="", channel="", orchestrator=True,
                           installation_files=True, has=lambda _n: True)
    assert 'chat_id="default"' not in text
    assert "Do not use the 'message' tool for normal replies in the current chat" in text
