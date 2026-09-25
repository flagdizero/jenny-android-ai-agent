"""I quattro file per-sessione, sotto un nome di progetto.

Passo **6** di ``roadmap/progetti-passi.md``.

Una conversazione lascia quattro tracce su disco, e ognuna nasce da un mapping
`chiave → nome di file` che sostituisce i caratteri scomodi con ``_``:

| Traccia | Dove | Radice |
| --- | --- | --- |
| la sessione (quel che Jenny rilegge) | ``sessions/<stem>.jsonl`` | installazione |
| la trascrizione (quel che vedi) | ``.jenny/webui/<stem>.jsonl`` + ``.segments/`` | installazione |
| i record dei subagent | ``subagents/records/<stem>.jsonl`` | installazione |
| i risultati dei tool troppo grandi | ``.jenny/tool-results/<stem>/`` | **turno** |

Quel mapping non è iniettivo: ``project:a/b``, ``project:a:b`` e ``project:a_b``
finiscono tutti su ``project_a_b``. Due conversazioni sullo stesso file
sarebbero due storie mescolate, ed è il guasto peggiore che questo disegno
possa produrre.

**Non succede perché il validatore rifiuta i nomi che collidono**, e quello è un
invariante portante che nessuno aveva scritto. Questi test lo pinnano: se un
giorno ``is_valid_project_name`` si allargasse a ``/`` o ``:`` — che a occhio
sembrano innocui in un nome di cartella — il file qui si romperebbe *prima* che
due chat si fondano sul telefono.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jenny.session.keys import (
    PROJECT_SESSION_PREFIX,
    UNIFIED_SESSION_KEY,
    WEBUI_CHANNEL,
    is_valid_project_name,
    session_key_for_channel,
)
from jenny.session.manager import SessionManager
from jenny.utils.helpers import safe_filename

# I nomi che il validatore accetta, scelti per essere adiacenti fra loro: se il
# mapping perdesse un carattere, due di questi finirebbero sullo stesso file.
_VALID = ["a", "ab", "a.b", "a-b", "a_b", "default", "project_a", "x" * 64]

# I nomi che collidono *fra loro o con i validi* una volta sanificati. Il
# mapping non li distingue, quindi la sola difesa è il rifiuto.
_COLLIDING = ["a/b", "a:b", "a\\b", "a|b", "a?b", "a*b", 'a"b', "a<b", "a>b"]


def _stems(key: str) -> dict[str, str]:
    """I quattro nomi che una chiave produce."""
    return {
        "sessione": SessionManager.safe_key(key),
        "trascrizione": SessionManager.safe_key(f"websocket:{key}"),
        "subagent": safe_filename(key.replace(":", "_")),
        "tool-results": safe_filename(key),
    }


# ── Nessuna collisione fra chiavi diverse ────────────────────────────────


@pytest.mark.parametrize("traccia", ["sessione", "trascrizione", "subagent", "tool-results"])
def test_no_two_valid_projects_share_a_file(traccia: str) -> None:
    keys = [f"{PROJECT_SESSION_PREFIX}{n}" for n in _VALID]
    seen: dict[str, str] = {}
    for key in keys:
        stem = _stems(key)[traccia]
        assert stem not in seen, f"{traccia}: {key} e {seen[stem]} finiscono su {stem}"
        seen[stem] = key


@pytest.mark.parametrize("traccia", ["sessione", "trascrizione", "subagent", "tool-results"])
def test_a_project_never_lands_on_the_personal_or_internal_files(traccia: str) -> None:
    """Il prefisso è ciò che tiene un progetto lontano dalla conversazione personale."""
    others = ["unified:default", "websocket:default", "cron:update_check", "dream:default"]
    reserved = {_stems(k)[traccia] for k in others}
    for name in _VALID:
        stem = _stems(f"{PROJECT_SESSION_PREFIX}{name}")[traccia]
        assert stem not in reserved, f"{traccia}: project:{name} finisce su un file riservato"


# ── L'invariante portante ────────────────────────────────────────────────


@pytest.mark.parametrize("name", _COLLIDING)
def test_the_names_that_would_collide_are_refused(name: str) -> None:
    """Il mapping non li distingue, quindi il validatore deve.

    Non è una regola di stile sui nomi di cartella: è la sola cosa che impedisce
    a due conversazioni di scrivere nello stesso file.
    """
    assert not is_valid_project_name(name)


@pytest.mark.parametrize("name", _COLLIDING)
def test_and_they_never_become_a_session_key(name: str) -> None:
    """La difesa deve stare anche sul percorso che i messaggi attraversano.

    ``session_key_for_channel`` è il punto in cui un ``chat_id`` arrivato dal
    client diventa una chiave di sessione: un nome rifiutato dal validatore ma
    accettato qui aprirebbe il buco un livello più sotto.
    """
    key = session_key_for_channel("websocket", f"{PROJECT_SESSION_PREFIX}{name}")
    assert not key.startswith(PROJECT_SESSION_PREFIX), (
        f"'{name}' è diventato una chiave di progetto: {key}"
    )


@pytest.mark.parametrize("name", _VALID)
def test_the_valid_ones_do_become_one(name: str) -> None:
    key = session_key_for_channel("websocket", f"{PROJECT_SESSION_PREFIX}{name}")
    assert key == f"{PROJECT_SESSION_PREFIX}{name}"


# ── Il nome più lungo che il validatore accetta ──────────────────────────


def test_the_longest_allowed_name_still_makes_a_usable_filename() -> None:
    """64 caratteri più il prefisso: sotto i 255 di ogni filesystem, con margine."""
    longest = "x" * 64
    assert is_valid_project_name(longest)
    for traccia, stem in _stems(f"{PROJECT_SESSION_PREFIX}{longest}").items():
        # ``.segments`` è il suffisso più lungo che si aggiunge a uno stem.
        assert len(stem) + len(".segments") < 255, f"{traccia}: {len(stem)} caratteri"
        assert "/" not in stem and "\\" not in stem, f"{traccia}: contiene un separatore"


def test_a_longer_name_is_refused_before_it_becomes_a_path() -> None:
    assert not is_valid_project_name("x" * 65)


def test_a_trailing_newline_is_refused_before_it_becomes_a_path() -> None:
    """Con ``re.match`` un ``$`` combacia anche prima di un a capo finale: fino al
    25/09/2026 ``project:viaggi\n`` diventava una sessione e una cartella col a
    capo nel nome. Il ``chat_id`` arriva dal client senza ``strip``."""
    assert not is_valid_project_name("viaggi\n")
    assert session_key_for_channel(
        WEBUI_CHANNEL, f"{PROJECT_SESSION_PREFIX}viaggi\n"
    ) == UNIFIED_SESSION_KEY


# ── Il quarto è l'unico che sta nel turno ────────────────────────────────


def test_tool_results_live_inside_the_project_and_not_beside_it() -> None:
    """Le altre tre stanno nella radice dell'installazione, questa nel turno.

    È voluto e va scritto: un risultato di tool troppo grande è lavoro di *quel*
    progetto, e tenerlo nella radice personale sarebbe la stessa famiglia di
    difetti di ``downloads/`` e dello storage delle app (chiusi nel passo 6).
    """
    src = Path("jenny/utils/helpers.py").read_text(encoding="utf-8")
    assert "root = ensure_dir(workspace / _TOOL_RESULTS_DIR)" in src, (
        "la radice dei tool-results deve venire dal workspace passato al chiamante, "
        "che dentro un progetto è la cartella del progetto"
    )
