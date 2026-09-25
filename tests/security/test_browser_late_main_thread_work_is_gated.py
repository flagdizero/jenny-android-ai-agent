"""Il lavoro in coda sul main thread non tocca la pagina dopo un «no».

Un tetto scaduto non toglie un blocco dalla coda del main: gira dopo. In
``JennyBrowserBridge.open`` un cancello decide già chi vince; due buchi restavano
(basse della revisione profonda, 25/09):

- ``evaluate`` (quindi ``act``) non aveva cancello: dopo il «timeout» detto al
  modello, il blocco in ritardo eseguiva lo script — un **click tardivo**;
- in ``open``, se ``loadUrl`` sollevava dopo che il main aveva preso il
  cancello, il chiamante credeva la pagina in partenza e aspettava il tetto
  intero con ``loading`` acceso, per poi rispondere «ok» sulla pagina vecchia.

Il Kotlin non gira in CI: la regola si fissa sul **codice**, commenti e
stringhe esclusi (``support/kotlin_source.py``).
"""

from __future__ import annotations

import re

from support.kotlin_source import block_after, block_at, function_body, read_code


def _code() -> str:
    return read_code("JennyBrowserBridge")


def test_evaluate_takes_the_gate_before_touching_the_page() -> None:
    body = function_body(_code(), "evaluate")
    posted = block_after(body, r"handler\.post\s*")
    take = posted.find("gate.compareAndSet(GATE_OPEN, GATE_LOADING)")
    run = posted.find(".evaluateJavascript(")
    assert take != -1, "il main deve prendere il cancello"
    assert take < run, "prima il cancello, poi lo script"


def test_evaluate_closes_the_gate_before_saying_timeout() -> None:
    body = function_body(_code(), "evaluate")
    m = re.search(r"if\s*\(\s*!\s*done\.await\([^)]*\)\s*\)\s*", body)
    assert m, "manca il ramo del tetto scaduto"
    timeout_branch = block_at(body, m.end())
    assert "gate.compareAndSet(GATE_OPEN, GATE_ABANDONED)" in timeout_branch


def test_evaluate_posted_block_cannot_crash_the_process() -> None:
    posted = block_after(function_body(_code(), "evaluate"), r"handler\.post\s*")
    assert posted[1:-1].strip().startswith("try"), "il blocco sul main apre con un try"
    m = re.search(r"catch\s*\(\s*e\s*:\s*Exception\s*\)\s*", posted)
    assert m and "done.countDown()" in block_at(posted, m.end())


def test_a_failing_load_url_is_written_in_the_gate() -> None:
    body = function_body(_code(), "open")
    hop = block_after(body, r"MainHop\.call\(10_000L,\s*false,\s*TAG\)\s*")
    m = re.search(r"try\s*", hop)
    assert m, "loadUrl sta dentro un try"
    guarded = block_at(hop, m.end())
    assert ".loadUrl(" in guarded
    c = re.search(r"catch\s*\(\s*e\s*:\s*Exception\s*\)\s*", hop[m.end():])
    assert c, "manca il catch intorno a loadUrl"
    handler = block_at(hop, m.end() + c.end())
    assert "gate.set(GATE_FAILED)" in handler
    assert "loading.set(false)" in handler, "senza, l'attesa dura il tetto intero"


def test_open_reads_the_failed_gate_after_waiting() -> None:
    body = function_body(_code(), "open")
    wait = body.index("awaitSettled(")
    m = re.compile(r"if\s*\(\s*gate\.get\(\)\s*==\s*GATE_FAILED\s*\)\s*").search(body, wait)
    assert m, "dopo l'attesa si rilegge il cancello"
    assert re.search(r"\breturn\b", block_at(body, m.end())), "e si risponde con un errore"
    assert m.start() < body.index("currentUrlAndTitle()", wait), (
        "prima di leggere la pagina e dare la risposta di successo"
    )
