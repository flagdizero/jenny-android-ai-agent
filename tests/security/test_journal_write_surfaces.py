"""Ogni scrittore passa dal registro dell'annulla, o dice perché no.

Il registro delle scritture (``agent.write_journal``) vale quanto i punti in cui
è agganciato: un modo di scrivere nato fra sei mesi e mai agganciato è un file
che l'annulla lascerebbe com'è, mentre la casa promette «annullate le sue
modifiche». La lista degli scrittori esiste già — è
l'inventario della sola lettura (``test_readonly_write_surfaces``) — e questo
file la riusa: ogni modulo che lì risulta scrivere qui è o agganciato (con il
punto da cui passa) o fuori (con la ragione). Uno nuovo non classificato fa
fallire il test.

Le prove di comportamento, con i file veri, stanno in
``tests/agent/test_write_journal.py``.
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
# chi lavora sotto un registro, o non scrive file del lavoro. Sotto un registro
# lavorano gli aiutanti **e** il turno di Jenny in un passo di un piano
# (``plan-<id>``): per quel turno ``cron`` e le azioni delle app sono raggiungibili,
# e restano fuori perché non sono file — il foglio «Torna a prima» lo dice
# all'utente (``home.plan.rollbackSheet.sub``).
_NOT_JOURNALED: dict[str, str] = {
    "agent/tools/cron.py": (
        "i job sono righe di state.sqlite, non file: il registro rimette file, e un job "
        "si annulla togliendolo. Un job nato in un passo di un piano resta dopo il ritorno"
    ),
    "agent/tools/app_update.py": "non è nello scope di un aiutante; installa un APK",
    "agent/tools/memory_entries.py": "ci scrive solo Dream, che non è un aiutante",
    "agent/tools/ssh_jobs.py": "stato locale dei job SSH, non un file del lavoro",
    "agent/tools/ssh_transport.py": "stato locale del trasporto SSH",
    "apps/storage.py": (
        "i dati delle app li scrivono le azioni delle app (``use_app``), che ha solo il "
        "turno di Jenny: sono lo stato dell'app, che l'utente cambia anche da sé, e "
        "rimetterli indietro cancellerebbe i suoi tocchi. Restano dopo il ritorno di un piano"
    ),
    "apps/executor.py": (
        "chiede alla sola lettura per le azioni http, che non scrivono file sul telefono; "
        "le scritture sono di apps/storage.py"
    ),
    "session/manager.py": "il magazzino delle sessioni, scritto dal runtime",
    "webui/transcript_store.py": "il verbale dei turni, scritto dal runtime",
    "agent/subagent_records.py": "la contabilità degli aiutanti, scritta dal runtime",
    "config/store.py": "la configurazione, che nessun aiutante scrive (è SECRET)",
}

# Chi scrive fuori dal registro ed è raggiungibile da un turno di Jenny lo dice
# all'utente: «Ho fatto» elenca quel che non torna indietro, e lo dichiara lo
# strumento (``Tool.outside_undo``, annotato dal runner in ``agent.turn_work``).
# Per ogni voce di ``_NOT_JOURNALED``: il modulo dello strumento che lo
# dichiara e il suo nome, o la ragione per cui non serve.
_OUTSIDE_UNDO: dict[str, tuple[str, str] | str] = {
    "agent/tools/cron.py": (
        "i promemoria non si dichiarano: il tool li annota con l'id nella riga del turno "
        "(``turn_work.note_job``), e il ritorno al turno li toglie"
    ),
    "agent/tools/app_update.py": ("agent/tools/app_update.py", "install_update"),
    "agent/tools/memory_entries.py": "lo usa solo Dream, che non è un turno con un id",
    "agent/tools/ssh_jobs.py": ("agent/tools/ssh.py", "ssh_job"),
    "agent/tools/ssh_transport.py": ("agent/tools/ssh.py", "ssh_exec, ssh_job, ssh_transfer"),
    "apps/storage.py": ("agent/tools/app_actions.py", "use_app, le azioni che scrivono"),
    "apps/executor.py": ("agent/tools/app_actions.py", "use_app, le azioni che scrivono"),
    "session/manager.py": "il runtime, non un'azione del turno",
    "webui/transcript_store.py": "il runtime, non un'azione del turno",
    "agent/subagent_records.py": "il runtime, non un'azione del turno",
    "config/store.py": ("agent/tools/self.py", "my set"),
}

# Gli strumenti che non scrivono file ma fanno uscire qualcosa dal telefono, o
# lo cambiano fuori dal workspace: l'inventario degli scrittori non li vede, e
# senza questa lista uno nuovo resterebbe muto in «Ho fatto».
_OUTWARD_TOOLS = {
    "agent/tools/message.py": "message",
    "agent/tools/android_web.py": "web_search, web_fetch",
    "agent/tools/browser.py": "browser_open, browser_do",
    "agent/tools/phone.py": "phone_do",
    "agent/tools/peer.py": "peer_ask, peer_line",
    "agent/tools/ssh.py": "ssh_exec, ssh_job, ssh_transfer up",
    "agent/tools/app_actions.py": "use_app",
    "agent/tools/self.py": "my",
    "agent/tools/app_update.py": "install_update",
}

_DECLARES = re.compile(r"^\s+def outside_undo\(self", re.M)

_HOOK = re.compile(
    r"\bjournal_touch\(|\bcheck_subagent_write\(|\b_check_subagent_write\("
    r"|\b_refuse_guarded_write\(|\bjournal\.touch\("
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


def _body(src: str, name: str) -> str:
    return src.split(f"def {name}(", 1)[1].split("\ndef ", 1)[0]


def test_the_common_fence_notes_the_before() -> None:
    """``check_write`` è l'aggancio di quasi tutti: se smette di annotare, il
    test sopra resta verde e l'annulla diventa vuoto. E annota anche fuori da un
    aiutante (il turno di Jenny in un piano): l'annotazione non sta dentro il
    ramo della politica, che vale solo per gli aiutanti."""
    src = _strip_prose((ROOT / "security/subagent_policy.py").read_text(encoding="utf-8"))
    last = _body(src, "check_write").rstrip().splitlines()[-1]
    assert last.strip().startswith("journal_touch("), last
    assert "current_journal()" in _body(src, "journal_touch")


def test_an_installed_draft_is_noted_in_its_writer_journal() -> None:
    """``staged_writes.install`` scrive con ``atomic_write``, che non passa da
    nessun recinto: l'annotazione è a mano, sul registro di chi scrisse la bozza."""
    src = _strip_prose((ROOT / "agent/staged_writes.py").read_text(encoding="utf-8"))
    body = _body(src, "install")
    assert "journal.touch(" in body and "journal.settle(" in body


def test_every_writer_outside_the_journal_declares_it_or_says_why() -> None:
    assert set(_OUTSIDE_UNDO) == set(_NOT_JOURNALED), (
        "ogni modulo di _NOT_JOURNALED dice in _OUTSIDE_UNDO quale strumento dichiara "
        "outside_undo per lui, o perché non serve"
    )
    missing = []
    for name, how in _OUTSIDE_UNDO.items():
        if isinstance(how, tuple):
            src = _strip_prose((ROOT / how[0]).read_text(encoding="utf-8"))
            if not _DECLARES.search(src):
                missing.append(f"{name} (via {how[0]})")
    assert not missing, f"dichiarazioni promesse ma assenti: {missing}"


def test_every_outward_tool_declares_what_cannot_be_undone() -> None:
    missing = [
        name for name in _OUTWARD_TOOLS
        if not _DECLARES.search(_strip_prose((ROOT / name).read_text(encoding="utf-8")))
    ]
    assert not missing, f"strumenti che escono dal telefono senza outside_undo: {missing}"


def test_the_runner_notes_the_declaration() -> None:
    """Il runner chiede la dichiarazione a ogni chiamata riuscita: se smette, i test
    sopra restano verdi e «Ho fatto» non elenca più niente fuori dal registro."""
    src = _strip_prose((ROOT / "agent/tool_execution.py").read_text(encoding="utf-8"))
    assert "note_outside_undo(tool, params)" in src
