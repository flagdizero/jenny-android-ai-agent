"""Gli id che arrivano dal filo: un ``\\n`` finale non li rende validi.

Quattro moduli validavano con ``^…$`` e ``re.match``, e in Python ``$`` combacia
anche prima di un ``\\n`` finale: ``"abc\\n"`` passava, e finiva come id rimandato
al client, chiave di dizionario o argomento di una route. Solo ``ssh_jobs``
usava ``\\A…\\Z``. Dal 24/09/2026 tutti usano la stessa regex (``WIRE_ID_RE``).
"""

from __future__ import annotations

import pytest

from jenny.agent.tools import ssh_jobs
from jenny.channels import subagent_activity_wire, ui_query, ws_rpc


@pytest.mark.parametrize("bad", ["abc\n", "abc\r\n", "a\nb", "", "a" * 65, "a/b", "a b", "a:b"])
def test_an_rpc_id_with_a_newline_or_junk_is_refused(bad: str) -> None:
    with pytest.raises(ws_rpc.RpcFrameError):
        ws_rpc.parse_rpc_frame({"id": bad, "method": "x", "params": {}})


def test_a_good_rpc_id_is_accepted() -> None:
    assert ws_rpc.parse_rpc_frame({"id": "rpc-0af3_Z", "method": "x", "params": {}})[0] == "rpc-0af3_Z"


def test_a_ui_result_with_a_newline_correlation_id_is_ignored() -> None:
    coordinator = ui_query.UiQueryCoordinator()
    # Non solleva e non risolve niente: un id malformato si scarta con un warning.
    coordinator.handle_ui_result("conn", {"correlation_id": "uiq-1\n", "result": {}})


def test_the_ui_query_regex_refuses_a_trailing_newline() -> None:
    assert ui_query._CORRELATION_RE.match("uiq-1\n") is None
    assert ui_query._CORRELATION_RE.match("uiq-1") is not None


def test_a_task_id_with_an_inner_newline_is_refused() -> None:
    assert subagent_activity_wire.normalize_task_id("a\nb") is None
    # Gli spazi e il \\n ai bordi si tolgono prima del controllo: comportamento di sempre.
    assert subagent_activity_wire.normalize_task_id(" abc\n") == "abc"


def test_the_ssh_job_id_check_was_already_strict() -> None:
    with pytest.raises(ssh_jobs.SshJobError):
        ssh_jobs._check_job_id("abc\n")
    assert ssh_jobs._check_job_id("job_01") == "job_01"
