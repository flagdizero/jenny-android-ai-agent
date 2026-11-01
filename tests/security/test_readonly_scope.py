"""Il flag della sola lettura: dove vive, da dove arriva, cosa non tocca.

Passi **4.2** e **4.3** del piano dei progetti.

Due trabocchetti scritti prima di toccare il codice, e sono la ragione per cui
metà di questo file esiste:

1. ``ToolWorkspace.allowed_root`` torna ``None`` per dire «nessuna
   restrizione». Esprimere la sola lettura come "radice nulla" **aprirebbe
   tutto** invece di chiudere tutto, e nessun test sui percorsi lo prenderebbe:
   il confine tornerebbe a dire sì.
2. Dentro un progetto ``for_turn`` torna prima di guardare i metadati — la
   cartella si deduce dalla chiave (deciso il 21/08), così non può divergere.
   Un flag letto solo là verrebbe ignorato **proprio nei progetti**, cioè dove
   serve di più.

Le due domande hanno due sorgenti diverse, e devono restare separate: *quale
cartella* dalla chiave (non può divergere, nessun client può chiederne un'altra),
*se si può scrivere* dal messaggio (quel che vedi è quel che mandi).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jenny.security.workspace_access import (
    WORKSPACE_READONLY_METADATA_KEY,
    WorkspaceScopeResolver,
    build_workspace_scope,
    current_turn_is_readonly,
    enter_workspace_scope,
    readonly_from_metadata,
)


@pytest.fixture
def resolver(tmp_path: Path) -> WorkspaceScopeResolver:
    (tmp_path / "wikis" / "palestra" / "wiki").mkdir(parents=True)
    return WorkspaceScopeResolver(
        default_workspace=tmp_path, default_restrict_to_workspace=True
    )


def _turn(resolver: WorkspaceScopeResolver, key: str, meta: dict | None = None):
    return resolver.for_turn(
        channel="websocket",
        message_metadata=meta,
        session_metadata=None,
        session_key=key,
    )


# ── Il trabocchetto della radice nulla ───────────────────────────────────


def test_read_only_is_a_flag_and_not_a_missing_root() -> None:
    """Un ``allowed_root`` nullo significa *nessun confine*, non *nessuna scrittura*.

    Se la sola lettura si esprimesse così, il cancello dei percorsi tornerebbe a
    dire sì a tutto — e lo direbbe in silenzio, perché è esattamente la forma
    che ha un'installazione senza restrizioni.
    """
    from jenny.security.workspace_access import current_tool_workspace

    scope = build_workspace_scope("/tmp", "restricted").without_write_access()
    with enter_workspace_scope(scope):
        access = current_tool_workspace("/tmp", restrict_to_workspace=True)
        assert access.writable is False
        assert access.allowed_root is not None, (
            "la sola lettura non deve passare dall'assenza di una radice: quella forma "
            "significa già 'nessuna restrizione'"
        )
        assert access.restrict_to_workspace is True, "gli assi sono due e restano due"


def test_the_two_axes_are_independent() -> None:
    """Un progetto in sola lettura è ``restricted`` **e** non scrivibile.

    È la ragione per cui non c'è un terzo valore di ``access_mode``: un enum non
    sa dire due cose insieme senza rendere ambiguo ``restrict_to_workspace``,
    che governa anche il confine di *lettura*.
    """
    for mode, restrict in (("restricted", True), ("full", False)):
        scope = build_workspace_scope("/tmp", mode).without_write_access()
        assert scope.access_mode == mode
        assert scope.restrict_to_workspace is restrict
        assert scope.writable is False


# ── Il trabocchetto del progetto ─────────────────────────────────────────


def test_a_project_turn_reads_the_flag_from_the_message(resolver) -> None:
    """Il ramo del progetto torna prima dei metadati: la sola lettura si applica dopo."""
    scope = _turn(resolver, "project:palestra", {WORKSPACE_READONLY_METADATA_KEY: True})
    assert scope.writable is False
    assert scope.project_path.name == "palestra", "la cartella resta quella dedotta dalla chiave"


def test_the_folder_still_comes_from_the_key_and_not_from_the_message(resolver) -> None:
    """Il flag non è una porta per chiedere un'altra cartella."""
    scope = _turn(
        resolver,
        "project:palestra",
        {
            WORKSPACE_READONLY_METADATA_KEY: True,
            "workspace_scope": {"project_path": "/etc", "access_mode": "full"},
        },
    )
    assert scope.project_path.name == "palestra"
    assert scope.writable is False


@pytest.mark.parametrize("key", ["unified:default", "project:palestra"])
def test_without_the_flag_the_turn_writes(resolver, key: str) -> None:
    assert _turn(resolver, key).writable is True
    assert _turn(resolver, key, {}).writable is True


# ── Solo ``True`` accende ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw", [True, "true", "1", 1, None, "yes", {}, [], 0, False],
    ids=["bool-true", "str-true", "str-1", "int-1", "none", "yes", "dict", "list", "zero", "false"],
)
def test_only_a_real_true_turns_it_on(raw) -> None:
    """Verso giusto per un flag che arriva da un client.

    Il caso da difendere è l'opposto — l'utente lo chiede e il turno scrive
    comunque — e non può capitare, perché il client lo rimanda a ogni messaggio.
    Accendere su una stringa qualunque invece spegnerebbe le scritture per un
    campo malformato, senza che nessuno l'abbia chiesto.
    """
    assert readonly_from_metadata({WORKSPACE_READONLY_METADATA_KEY: raw}) is (raw is True)


@pytest.mark.parametrize("meta", [None, {}, "readonly", 42, []])
def test_a_malformed_metadata_leaves_the_turn_writable(meta) -> None:
    assert readonly_from_metadata(meta) is False


# ── Chi non lo vede mai ──────────────────────────────────────────────────


def test_the_default_is_writable_so_internal_sessions_keep_working() -> None:
    """Cron e Dream non hanno un messaggio da cui leggere il flag.

    Un default chiuso li avrebbe spenti tutti — e in silenzio, perché nessuno
    di loro riferisce di non aver scritto.
    """
    assert build_workspace_scope("/tmp", "restricted").writable is True
    assert current_turn_is_readonly() is False, "fuori da un turno legato: scrivibile"


def test_another_channel_is_untouched(resolver) -> None:
    """Telegram non ha l'interruttore: un flag che arrivasse da là non conta."""
    scope = resolver.for_turn(
        channel="telegram",
        message_metadata={WORKSPACE_READONLY_METADATA_KEY: True},
        session_metadata=None,
        session_key="unified:default",
    )
    assert scope.writable is True


# ── Il percorso fino al tool ─────────────────────────────────────────────


def test_the_flag_reaches_the_tools_through_the_bound_scope(resolver) -> None:
    scope = _turn(resolver, "project:palestra", {WORKSPACE_READONLY_METADATA_KEY: True})
    with enter_workspace_scope(scope):
        assert current_turn_is_readonly() is True
    assert current_turn_is_readonly() is False, "lo scope si slega all'uscita"


def test_the_payload_tells_the_client_which_mode_it_got(resolver) -> None:
    """Il client disegna l'interruttore: se il payload non lo dice, non lo sa."""
    scope = _turn(resolver, "project:palestra", {WORKSPACE_READONLY_METADATA_KEY: True})
    assert scope.payload()["writable"] is False
    assert _turn(resolver, "project:palestra").payload()["writable"] is True
