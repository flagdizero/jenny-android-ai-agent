"""Ogni scrittore passa dal registro dell'annulla, o dice perché no.

Il registro delle scritture degli aiutanti (``agent.subagent_journal``) vale
quanto i punti in cui è agganciato: un modo di scrivere nato fra sei mesi e mai
agganciato è un file che l'annulla lascerebbe com'è, mentre la casa promette
«annullate le sue modifiche». La lista degli scrittori esiste già — è
l'inventario della sola lettura (``test_readonly_write_surfaces``) — e questo
file la riusa: ogni modulo che lì risulta scrivere qui è o agganciato (con il
punto da cui passa) o fuori (con la ragione). Uno nuovo non classificato fa
fallire il test.

Le prove di comportamento, con i file veri, stanno in
``tests/agent/test_subagent_journal.py``.
"""

from __future__ import annotations

import re
from pathlib import Path

from tests.security.test_readonly_write_surfaces import _signals, _sources, _strip_prose

ROOT = Path(__file__).resolve().parents[2] / "jenny"

# Il modulo che scrive → il file in cui sta l'aggancio (spesso lo stesso), e
# come ci passa. L'aggancio è ``check_write`` (il recinto comune delle
# scritture degli aiutanti, che annota il «prima») o ``journal_touch`` (per gli
# imbuti con recinti propri).
_JOURNALED: dict[str, tuple[str, str]] = {
    "agent/tools/filesystem.py": (
        "security/subagent_policy.py", "_resolve_write → check_write, per write/edit/patch",
    ),
    "agent/tools/apply_patch.py": (
        "agent/tools/filesystem.py", "passa da _resolve_write dei tool file",
    ),
    "agent/tools/python_exec.py": (
        "agent/tools/python_exec.py", "_refuse_guarded_write → check_write, per ogni mutatore",
    ),
    "agent/tools/python_exec_builtins.py": (
        "agent/tools/python_exec_builtins.py", "_write_path → _refuse_guarded_write",
    ),
    "agent/tools/notebook.py": (
        "agent/tools/notebook.py", "_fenced → check_write, per remove_pages e la traccia",
    ),
    "agent/tools/board.py": (
        "board/store.py", "la bacheca passa dal suo imbuto, che chiama journal_touch",
    ),
    "agent/tools/app_tool.py": (
        "agent/tools/app_tool.py", "journal_touch sulla cartella della bozza",
    ),
    "agent/tools/download.py": ("agent/tools/download.py", "journal_touch sul file scaricato"),
    "agent/tools/journal.py": ("agent/tools/journal.py", "journal_touch sulla pagina del diario"),
    "agent/tools/ssh.py": ("agent/tools/ssh.py", "journal_touch sulla destinazione di down"),
}

# Chi scrive e non passa dal registro, con la ragione: o non è raggiungibile da
# un aiutante, o non scrive file del lavoro.
_NOT_JOURNALED: dict[str, str] = {
    "agent/tools/cron.py": (
        "non è nello scope di un aiutante; e cron/jobs.json lo riscrive lo scheduler: un job "
        "si annulla togliendolo, non rimettendo il file"
    ),
    "agent/tools/app_update.py": "non è nello scope di un aiutante; installa un APK",
    "agent/tools/memory_entries.py": "ci scrive solo Dream, che non è un aiutante",
    "agent/tools/ssh_jobs.py": "stato locale dei job SSH, non un file del lavoro",
    "agent/tools/ssh_transport.py": "stato locale del trasporto SSH",
    "apps/storage.py": (
        "i dati delle app li scrivono le azioni delle app, che ha solo il turno di Jenny "
        "(lo strumento per-app si sincronizza sul registro del loop principale)"
    ),
    "session/manager.py": "il magazzino delle sessioni, scritto dal runtime",
    "webui/transcript_store.py": "il verbale dei turni, scritto dal runtime",
    "agent/subagent_records.py": "la contabilità degli aiutanti, scritta dal runtime",
    "config/store.py": "la configurazione, che nessun aiutante scrive (è SECRET)",
}

_HOOK = re.compile(
    r"\bjournal_touch\(|\bcheck_subagent_write\(|\b_check_subagent_write\("
    r"|\b_refuse_guarded_write\(|\.journal\.touch\("
)


def test_every_writer_is_journaled_or_says_why() -> None:
    writers = {name for name, src in _sources().items() if _signals(src)}
    known = set(_JOURNALED) | set(_NOT_JOURNALED)
    assert not set(_JOURNALED) & set(_NOT_JOURNALED)
    unclassified = sorted(writers - known)
    assert not unclassified, (
        f"questi moduli scrivono e il registro dell'annulla non li conosce: {unclassified}. "
        "Se un aiutante li raggiunge, agganciali (journal_touch prima della scrittura, o "
        "passando da check_write) e mettili in _JOURNALED; se no, _NOT_JOURNALED con la ragione"
    )


def test_the_journaled_ones_really_reach_the_journal() -> None:
    """Il punto dichiarato contiene davvero l'aggancio, nel codice e non in un commento."""
    missing = []
    for name, (via, _how) in _JOURNALED.items():
        src = _strip_prose((ROOT / via).read_text(encoding="utf-8"))
        if not _HOOK.search(src):
            missing.append(f"{name} (via {via})")
    assert not missing, f"agganci dichiarati ma assenti: {missing}"


def test_the_common_fence_notes_the_before() -> None:
    """``check_write`` è l'aggancio di quasi tutti: se smette di annotare, il
    test sopra resta verde e l'annulla diventa vuoto."""
    src = _strip_prose((ROOT / "security/subagent_policy.py").read_text(encoding="utf-8"))
    body = src.split("def check_write(", 1)[1].split("\ndef ", 1)[0]
    assert ".journal.touch(" in body
