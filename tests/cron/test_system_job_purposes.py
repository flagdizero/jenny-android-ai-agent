"""La tabella dei propositi copre i job di sistema che il container registra.

Il difetto che questo file esiste per non far tornare: la tabella viveva dentro
``CronTool`` con sopra un commento — *«Chi ne aggiunge un altro lo aggiunga anche
qui»* — e il giardiniere non ci era mai stato aggiunto. Chiedendo l'elenco dei job
si presentava come «System-managed internal job.» pur avendo una pagina di
documentazione sua. Una regola che chiede disciplina senza un controllo la perde,
e questo e' il controllo.

**Si legge il sorgente di ``container.py`` invece di costruire un container.**
Costruirlo vorrebbe dire un provider, un workspace e un event loop per rispondere
a una domanda che e' statica: *quali id passa questo file a
``register_system_job``?* L'AST la risponde in modo deterministico, e sbaglia
soltanto se qualcuno registra un job con un id calcolato a runtime — caso in cui
il test va aggiornato di proposito, non aggirato.
"""

from __future__ import annotations

import ast
from pathlib import Path

from jenny.cron.purposes import (
    SYSTEM_JOB_PURPOSES,
    UNKNOWN_SYSTEM_JOB_PURPOSE,
    system_job_purpose,
)

CONTAINER = Path(__file__).resolve().parents[2] / "jenny" / "runtime" / "container.py"


def _registered_system_job_ids() -> set[str]:
    """Gli ``id`` letterali passati a ``register_system_job(CronJob(id=...))``."""
    tree = ast.parse(CONTAINER.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "register_system_job"):
            continue
        for arg in node.args:
            if not (isinstance(arg, ast.Call) and isinstance(arg.func, ast.Name)):
                continue
            if arg.func.id != "CronJob":
                continue
            for kw in arg.keywords:
                if kw.arg == "id" and isinstance(kw.value, ast.Constant):
                    found.add(str(kw.value.value))
    return found


def _retired_system_job_ids() -> set[str]:
    """Il contenuto di ``_RETIRED_SYSTEM_JOBS``, letto dallo stesso sorgente."""
    tree = ast.parse(CONTAINER.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        targets = []
        if isinstance(node, ast.AnnAssign):
            targets = [node.target]
        elif isinstance(node, ast.Assign):
            targets = node.targets
        for target in targets:
            if isinstance(target, ast.Name) and target.id == "_RETIRED_SYSTEM_JOBS":
                value = node.value
                if isinstance(value, (ast.Tuple, ast.List)):
                    return {
                        str(el.value) for el in value.elts if isinstance(el, ast.Constant)
                    }
    return set()


def test_the_ast_reader_actually_finds_something() -> None:
    """Guardia sulla guardia: un lettore che non trova nulla passerebbe a vuoto.

    Senza questa riga, un refactor che rinomina ``register_system_job`` o sposta la
    registrazione altrove renderebbe il test *verde per assenza di dati* — la forma
    di falso negativo che questo repo ha gia' pagato altrove (un fixture che
    costruiva il prompt senza la cassetta).
    """
    assert len(_registered_system_job_ids()) >= 4


def test_every_registered_system_job_can_present_itself() -> None:
    registered = _registered_system_job_ids()
    missing = sorted(registered - set(SYSTEM_JOB_PURPOSES))
    assert not missing, (
        f"job di sistema registrati senza una riga in SYSTEM_JOB_PURPOSES: {missing}. "
        "Girano da soli e spendono token o rete: devono sapersi presentare "
        "(v. jenny/cron/purposes.py)."
    )


def test_the_table_has_no_rows_for_jobs_nobody_registers() -> None:
    """L'altra direzione: una riga per un job che non esiste piu' e' testo morto.

    I ritirati sono l'eccezione dichiarata — il loro job sparisce dallo store al
    primo avvio della versione che li ritira, e nel frattempo il fallback dice di
    loro la cosa giusta — quindi non devono avere una riga.
    """
    registered = _registered_system_job_ids()
    stale = sorted(set(SYSTEM_JOB_PURPOSES) - registered)
    assert not stale, f"righe in SYSTEM_JOB_PURPOSES per job non registrati: {stale}"

    retired = _retired_system_job_ids()
    assert retired, "_RETIRED_SYSTEM_JOBS non letto: il controllo sotto passerebbe a vuoto"
    overlap = sorted(retired & set(SYSTEM_JOB_PURPOSES))
    assert not overlap, f"job ritirati con una riga di presentazione: {overlap}"


def test_an_unknown_job_falls_back_instead_of_raising() -> None:
    assert system_job_purpose("atlas") == UNKNOWN_SYSTEM_JOB_PURPOSE
    assert UNKNOWN_SYSTEM_JOB_PURPOSE.strip()


def test_no_purpose_is_empty_or_a_placeholder() -> None:
    for job_id, purpose in SYSTEM_JOB_PURPOSES.items():
        assert purpose.strip(), job_id
        assert purpose != UNKNOWN_SYSTEM_JOB_PURPOSE, (
            f"{job_id} ha come proposito il fallback: e' come non averlo"
        )
