"""Dove gira un piano: un quaderno, o la chat personale.

Un piano di quaderno (``nb:<nome>``) vive nella chat del quaderno, che resta in
sola lettura mentre corre, chiude con un passo ``check`` che il codice verifica,
annota le lacune in ``raw/notes/open-gaps.md`` e, da bloccato, prende come
risposta il prossimo messaggio. Un piano personale (``personal``) vive nella
chat principale, dove arrivano anche la mascotte, le notifiche e Telegram: non
tiene niente, non ha il controllo a macchina (il suo passo di controllo è un
turno come gli altri), e da bloccato si risponde dalla scheda.

Il driver chiede i **tratti** del posto invece di chiedersi «è un quaderno?»:
un ramo nuovo nasce dal tratto, non da un ``if`` in più sparso.

I percorsi qui sono relativi al **workspace**, come li scrive il registro
dell'annulla (``agent.write_journal``); ``shown`` li dice come li mostra la
scheda (relativi al quaderno, per un piano di quaderno).
"""

from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatch

from jenny.board.model import BOARD_DIR
from jenny.bus.events import WEBUI_DEFAULT_CHAT_ID
from jenny.config.paths import DEFAULT_WIKIS_DIR
from jenny.session.keys import UNIFIED_SESSION_KEY, is_project_session_key, project_name
from jenny.utils.wiki_paths import JOURNAL_DIRNAME

PERSONAL = "personal"
NOTEBOOK_PREFIX = "nb:"

# ── Gruppi ───────────────────────────────────────────────────────────────────
#
# Il conto del ritorno ha una casella per gruppo («Tieni le 12 ricerche»). In un
# quaderno la mappa viene dalla sua forma (``webui/project_scaffold.py``) e dalla
# bacheca (``board/model.py``):
#
#   wiki/…            → pages    le pagine
#   raw/journal/…     → journal  il diario: mai nel ritorno (v. ``never``)
#   raw/…             → sources  quel che arriva da fuori: ``raw/research/`` del
#                                ricercatore, e ``raw/articles|papers|notes|refs``
#   board/…           → board    la bacheca: ``board.json``, promemoria, schede
#   tutto il resto    → other    ``AGENTS.md``, ``log/``, ``audit/``, …
#
# Nella chat personale il gruppo è il quaderno toccato (il suo nome), ``apps``
# per le app, ``other`` per il resto.
GROUP_PAGES = "pages"
GROUP_SOURCES = "sources"
GROUP_BOARD = "board"
GROUP_OTHER = "other"
GROUP_JOURNAL = "journal"
GROUP_APPS = "apps"
KEEPABLE_GROUPS: tuple[str, ...] = (GROUP_PAGES, GROUP_SOURCES, GROUP_BOARD, GROUP_OTHER)

_JOURNAL_PREFIX = JOURNAL_DIRNAME.rstrip("/") + "/"


def notebook_group(path: str) -> str:
    """Il gruppo di un percorso relativo alla radice di un quaderno."""
    if path.startswith(_JOURNAL_PREFIX):
        return GROUP_JOURNAL
    head = path.split("/", 1)[0]
    if head == "wiki":
        return GROUP_PAGES
    if head == "raw":
        return GROUP_SOURCES
    if head == BOARD_DIR:
        return GROUP_BOARD
    return GROUP_OTHER


@dataclass(frozen=True)
class PlaceTraits:
    holds: bool            # la cartella resta in sola lettura mentre il piano corre
    machine_check: bool    # ultimo passo ``check``, audit e fatti persi del codice
    answer_by_chat: bool   # da bloccato, il prossimo messaggio è la risposta


NOTEBOOK_TRAITS = PlaceTraits(holds=True, machine_check=True, answer_by_chat=True)
PERSONAL_TRAITS = PlaceTraits(holds=False, machine_check=False, answer_by_chat=False)


@dataclass(frozen=True)
class PlanPlace:
    id: str
    session_key: str
    notebook: str | None
    traits: PlaceTraits

    @property
    def prefix(self) -> str:
        """Il pezzo di percorso del quaderno nel workspace (vuoto in personale)."""
        return f"{DEFAULT_WIKIS_DIR}/{self.notebook}/" if self.notebook else ""

    @property
    def chat_id(self) -> str:
        """La chat della WebUI dove parla il piano.

        In un quaderno è la chiave stessa (``project:<nome>``); la chat personale
        invece è ``default``, non ``unified:default``: un turno mandato lì
        finiva in una trascrizione che la casa non mostra (prova del 04/11/2026,
        le risposte del piano scritte nella sessione e mai viste).
        """
        return self.session_key if self.notebook else WEBUI_DEFAULT_CHAT_ID

    @property
    def here(self) -> str:
        """Il posto detto al modello: i testi non scrivono «notebook» a mano."""
        return "this notebook" if self.notebook else "the personal chat"

    @property
    def subject(self) -> str:
        """Quel che il piano tocca, detto al modello."""
        return "the notebook" if self.notebook else "the workspace"

    def shown(self, rel: str) -> str:
        """*rel* (relativo al workspace) come lo mostra la scheda."""
        if self.prefix and rel.startswith(self.prefix):
            return rel[len(self.prefix):]
        return rel

    def group_of(self, rel: str) -> str:
        if self.notebook:
            return notebook_group(self.shown(rel))
        parts = rel.split("/")
        if len(parts) >= 3 and parts[0] == DEFAULT_WIKIS_DIR:
            return parts[1]
        if parts[0] == GROUP_APPS:
            return GROUP_APPS
        return GROUP_OTHER

    def keepable(self, group: str) -> bool:
        """Un gruppo che l'utente può tenere fuori da un ritorno."""
        if self.notebook:
            return group in KEEPABLE_GROUPS
        return bool(group) and group != GROUP_JOURNAL and len(group) <= 120

    def never(self, rel: str) -> bool:
        """Il diario di un quaderno non torna mai indietro.

        È append-only, e il giardiniere lo legge come immobile: riscriverlo
        all'indietro ne romperebbe il contratto. Quel che Jenny ha annotato
        durante il piano resta annotato.
        """
        return fnmatch(rel, f"{DEFAULT_WIKIS_DIR}/*/{_JOURNAL_PREFIX}*")


def notebook_place(name: str) -> PlanPlace:
    return PlanPlace(f"{NOTEBOOK_PREFIX}{name}", f"project:{name}", name, NOTEBOOK_TRAITS)


PERSONAL_PLACE = PlanPlace(PERSONAL, UNIFIED_SESSION_KEY, None, PERSONAL_TRAITS)


def place_of_session(session_key: str | None) -> PlanPlace | None:
    """Il posto dei piani di una sessione, se ne può avere."""
    if not session_key:
        return None
    if is_project_session_key(session_key):
        name = project_name(session_key)
        return notebook_place(name) if name else None
    if session_key == UNIFIED_SESSION_KEY:
        return PERSONAL_PLACE
    return None


def place_from_id(place_id: str | None) -> PlanPlace | None:
    if not place_id:
        return None
    if place_id == PERSONAL:
        return PERSONAL_PLACE
    if place_id.startswith(NOTEBOOK_PREFIX) and len(place_id) > len(NOTEBOOK_PREFIX):
        return notebook_place(place_id[len(NOTEBOOK_PREFIX):])
    return None
