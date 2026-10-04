"""La forma dei dati della bacheca, senza I/O.

Qui stanno i nomi persistiti, le soglie e le funzioni pure: normalizzare un JSON
letto da disco, trovare a quale scheda appartiene una pagina, scegliere un posto
libero, e — la più importante — dire se una scrittura di Jenny ha toccato
qualcosa che è dell'utente (:func:`ownership_violations`).

**I nomi su disco sono un contratto.** ``board.json``, ``reminders.json``,
``cards/`` e i campi qui sotto sono persistiti nel quaderno dell'utente (e nel
suo backup): una rinomina vuole una migrazione, come ``Config._migrate_casa_to_home``.

**Le chiavi che non conosciamo si conservano**, a ogni livello che normalizziamo:
una versione vecchia dell'app che riscrive la bacheca non deve cancellare un
campo che una versione più nuova ci aveva messo. È la stessa regola di
``config/store.py``, per lo stesso motivo.

Le posizioni sono in **percentuale** della bacheca (0–100), non in pixel: la
stessa bacheca si apre sul quadrato del Titan 2 e su un telefono alto.
"""

from __future__ import annotations

import copy
import hashlib
import re
import unicodedata
from collections.abc import Iterable, Mapping
from datetime import date, datetime, timedelta
from typing import Any

BOARD_DIR = "board"
BOARD_FILE = "board.json"
REMINDERS_FILE = "reminders.json"
CARDS_DIR = "cards"

BOARD_VERSION = 1
REMINDERS_VERSION = 1

# Chi possiede un pezzo. Il giardiniere scrive come ``jenny``; l'interfaccia
# come ``user``.
USER = "user"
JENNY = "jenny"
ACTORS = (USER, JENNY)

# ── Le soglie ────────────────────────────────────────────────────────────────
# Costanti e non manopole: sono la forma del prodotto, non una taratura per
# installazione. Se una va tarata col telefono in mano, si cambia qui.

# Il tetto degli elementi sulla bacheca, schede più post-it. La bacheca sta in
# una schermata sola (niente tela infinita): sul quadrato del Titan 2 dodici
# oggetti ci stanno ancora senza coprirsi, e sette-nove sono l'ideale. Quando è
# piena, Jenny propone di togliere prima di proporre di aggiungere.
MAX_ITEMS = 12

# Il tetto dei fili. Venti fili sono di nuovo il grafo che la bacheca sostituisce.
MAX_CONNECTIONS = 8

# Quante proposte nuove può aggiungere una passata. Una busta che si riempie a
# ogni passata è una busta che non si apre più.
MAX_SUGGESTIONS_PER_PASS = 2

# Quante proposte possono aspettare nella busta, in tutto.
MAX_PENDING_SUGGESTIONS = 4

# «Più avanti» rimanda di questo numero di passate della bacheca.
LATER_PASSES = 3

# Una scheda senza novità da tanto, e senza attese aperte, si può proporre di
# metterla via.
REMOVE_AFTER_DAYS = 90

# Un soggetto fuori bacheca merita una scheda da questo numero di pagine in su.
NEW_CARD_MIN_PAGES = 3

# Un «no» torna proponibile solo se il soggetto è cresciuto di tanto: il doppio
# delle pagine che aveva quando l'utente l'ha rifiutato.
DECLINE_REGROWTH = 2

# Le sezioni dello scheletro di un quaderno: poche, perché ogni cassetto le
# mostra tutte nello stesso ordine.
MAX_SKELETON = 8

# Le righe di «dall'ultima volta» che si tengono. Sono un racconto recente, non
# un registro: quello è ``log/``.
MAX_SINCE = 60

# La prima bacheca proposta a un quaderno che non ne ha una: al massimo nove
# schede, l'ideale del tetto.
MAX_INITIAL_CARDS = 9

# Un'attesa a data accende l'angolo giallo della sua scheda quando manca meno di
# così.
SOON_DAYS = 14

SUGGESTION_KINDS = (
    "initial_board",
    "add_card",
    "remove_card",
    "add_connection",
    "attach_page",
    "add_section",
)
SECTION_KINDS = ("note", "reminder", "app", "photo")
REMINDER_STATES = ("open", "done", "dropped")
WHEN_KINDS = ("date", "condition")
PRECISIONS = ("day", "month")
CONDITION_OPS = ("<", "<=", ">", ">=", "==", "!=")

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,47}$")
# Il glifo di una scheda è il nome di un'icona del set della casa (Tabler):
# validato come forma, non contro l'elenco — un nome che non esiste non disegna
# niente, e la scheda mostra l'iniziale.
_GLYPH_RE = re.compile(r"^[a-z0-9-]{1,40}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Tetti sulle stringhe che arrivano da fuori (il modello o l'interfaccia).
MAX_TITLE = 60
MAX_LABEL = 60
MAX_TEXT = 600
MAX_NOTE = 2000


# ── Funzioni di base ─────────────────────────────────────────────────────────


def slugify(text: str, *, fallback: str = "card") -> str:
    """Un id da un titolo: minuscole, trattini, niente accenti, al più 48 caratteri."""
    norm = unicodedata.normalize("NFKD", str(text or ""))
    ascii_text = norm.encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text).strip("-")[:48].strip("-")
    return slug if _ID_RE.match(slug) else fallback


def is_valid_id(value: Any) -> bool:
    return isinstance(value, str) and bool(_ID_RE.match(value))


def unique_id(base: str, taken: Iterable[str]) -> str:
    """*base*, o *base-2*, *base-3*… se è già preso."""
    used = set(taken)
    if base not in used:
        return base
    n = 2
    while f"{base}-{n}"[:48] in used:
        n += 1
    return f"{base}-{n}"[:48]


def clean_text(value: Any, limit: int) -> str:
    """Una stringa su una riga sola (per titoli ed etichette), tagliata a *limit*."""
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())[:limit]


def clean_block(value: Any, limit: int) -> str:
    """Un testo che può andare a capo (una nota, un riassunto), tagliato a *limit*."""
    if not isinstance(value, str):
        return ""
    lines = [" ".join(line.split()) for line in value.replace("\r\n", "\n").split("\n")]
    text = "\n".join(lines).strip()
    return re.sub(r"\n{3,}", "\n\n", text)[:limit]


def _clamp(value: Any, low: float, high: float, default: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    if value != value:  # NaN
        return default
    return round(max(low, min(high, float(value))), 2)


def _owner(value: Any, default: str = JENNY) -> str:
    return value if value in ACTORS else default


def _str_list(value: Any, *, limit: int = 200) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        if isinstance(item, str) and item and item not in out:
            out.append(item)
        if len(out) >= limit:
            break
    return out


def _keep_unknown(raw: Mapping[str, Any], known: Mapping[str, Any]) -> dict[str, Any]:
    """*known* più le chiavi di *raw* che non conosciamo, copiate così come sono."""
    out = {k: copy.deepcopy(v) for k, v in raw.items() if k not in known}
    out.update(known)
    return out


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def parse_iso(value: Any) -> datetime | None:
    """Un istante ISO, sempre con fuso (un istante senza fuso si legge come locale)."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.astimezone()


# ── board.json ───────────────────────────────────────────────────────────────


def empty_board(skeleton: Iterable[str] = ()) -> dict[str, Any]:
    return normalize_board({"skeleton": list(skeleton)})


def _normalize_pos(raw: Any, default_by: str = JENNY) -> dict[str, Any] | None:
    if not isinstance(raw, Mapping):
        return None
    known = {
        "x": _clamp(raw.get("x"), 0, 100, 0.0),
        "y": _clamp(raw.get("y"), 0, 100, 0.0),
        "by": _owner(raw.get("by"), default_by),
    }
    return _keep_unknown(raw, known)


def _normalize_user_section(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, Mapping):
        return None
    kind = raw.get("kind")
    sid = raw.get("id")
    if kind not in SECTION_KINDS or not is_valid_id(sid):
        return None
    known: dict[str, Any] = {"id": sid, "kind": kind}
    if isinstance(raw.get("created"), str):
        known["created"] = raw["created"]
    if kind == "note":
        known["text"] = clean_block(raw.get("text"), MAX_NOTE)
        if isinstance(raw.get("journalLine"), str):
            known["journalLine"] = raw["journalLine"]
    elif kind == "reminder":
        if not is_valid_id(raw.get("reminder")):
            return None
        known["reminder"] = raw["reminder"]
    elif kind == "app":
        if not is_valid_id(raw.get("app")):
            return None
        known["app"] = raw["app"]
        params = raw.get("params")
        known["params"] = (
            {str(k): v for k, v in params.items() if isinstance(v, (str, int, float, bool))}
            if isinstance(params, Mapping)
            else {}
        )
    elif kind == "photo":
        path = raw.get("path")
        if not isinstance(path, str) or not path or ".." in path or path.startswith("/"):
            return None
        known["path"] = path
        known["caption"] = clean_text(raw.get("caption"), MAX_TEXT)
    return _keep_unknown(raw, known)


def _normalize_card(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, Mapping) or not is_valid_id(raw.get("id")):
        return None
    title = clean_text(raw.get("title"), MAX_TITLE) or raw["id"]
    sections = [
        s for s in (_normalize_user_section(x) for x in raw.get("userSections") or [])
        if s is not None
    ]
    known: dict[str, Any] = {
        "id": raw["id"],
        "title": title,
        "titleBy": _owner(raw.get("titleBy")),
        "pages": _str_list(raw.get("pages")),
        "pos": _normalize_pos(raw.get("pos")) or {"x": 0.0, "y": 0.0, "by": JENNY},
        "pinned": bool(raw.get("pinned", False)),
        "glyph": raw["glyph"] if isinstance(raw.get("glyph"), str)
        and _GLYPH_RE.match(raw["glyph"]) else "",
        "userSections": sections,
    }
    for stamp in ("lastOpened", "created"):
        if isinstance(raw.get(stamp), str):
            known[stamp] = raw[stamp]
    return _keep_unknown(raw, known)


def _normalize_connection(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, Mapping):
        return None
    a, b = raw.get("a"), raw.get("b")
    if not is_valid_id(a) or not is_valid_id(b) or a == b:
        return None
    known = {
        "a": a,
        "b": b,
        "label": clean_text(raw.get("label"), MAX_LABEL),
        "by": _owner(raw.get("by")),
    }
    return _keep_unknown(raw, known)


def _normalize_suggestion(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, Mapping):
        return None
    if raw.get("kind") not in SUGGESTION_KINDS or not is_valid_id(raw.get("id")):
        return None
    known: dict[str, Any] = {
        "id": raw["id"],
        "kind": raw["kind"],
        "subject": clean_text(raw.get("subject"), MAX_TITLE),
        "text": clean_block(raw.get("text"), MAX_TEXT),
        "created": raw.get("created") if isinstance(raw.get("created"), str) else "",
    }
    if "pages" in raw:
        known["pages"] = _str_list(raw.get("pages"))
    return _keep_unknown(raw, known)


def normalize_board(raw: Any) -> dict[str, Any]:
    """Una bacheca ben formata da un JSON qualunque. Mai un'eccezione.

    Le voci rotte si scartano (una scheda senza id non si può disegnare né
    possedere); le chiavi che non conosciamo restano.
    """
    data: Mapping[str, Any] = raw if isinstance(raw, Mapping) else {}
    cards: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    owned_pages: set[str] = set()
    for item in data.get("cards") or []:
        card = _normalize_card(item)
        if card is None or card["id"] in seen_ids:
            continue
        # Una pagina appartiene al più a una scheda: la prima che la nomina.
        card["pages"] = [p for p in card["pages"] if p not in owned_pages]
        owned_pages.update(card["pages"])
        seen_ids.add(card["id"])
        cards.append(card)
    connections: list[dict[str, Any]] = []
    pairs: set[frozenset[str]] = set()
    for item in data.get("connections") or []:
        conn = _normalize_connection(item)
        if conn is None:
            continue
        pair = frozenset((conn["a"], conn["b"]))
        if pair in pairs:
            continue
        pairs.add(pair)
        connections.append(conn)
    skeleton = [
        s for s in (clean_text(x, MAX_TITLE) for x in data.get("skeleton") or []) if s
    ]
    skeleton = list(dict.fromkeys(skeleton))[:MAX_SKELETON]
    known: dict[str, Any] = {
        "version": BOARD_VERSION,
        "skeleton": skeleton,
        "cards": cards,
        "connections": connections,
        "suggestions": [
            s for s in (_normalize_suggestion(x) for x in data.get("suggestions") or [])
            if s is not None
        ],
        "declined": [
            dict(x) for x in data.get("declined") or [] if isinstance(x, Mapping)
        ],
        "later": [dict(x) for x in data.get("later") or [] if isinstance(x, Mapping)],
        "offboardWants": [
            dict(x) for x in data.get("offboardWants") or []
            if isinstance(x, Mapping) and isinstance(x.get("page"), str)
        ],
        "since": [dict(x) for x in data.get("since") or [] if isinstance(x, Mapping)][
            -MAX_SINCE:
        ],
        "orphansSeen": _str_list(data.get("orphansSeen"), limit=2000),
        "passes": int(data.get("passes") or 0)
        if isinstance(data.get("passes"), int) and not isinstance(data.get("passes"), bool)
        else 0,
    }
    if isinstance(data.get("lastSeen"), str):
        known["lastSeen"] = data["lastSeen"]
    return _keep_unknown(data, known)


def card_by_id(board: Mapping[str, Any], card_id: str) -> dict[str, Any] | None:
    for card in board.get("cards") or []:
        if card.get("id") == card_id:
            return card
    return None


def page_owners(board: Mapping[str, Any]) -> dict[str, str]:
    """``{pagina: id della scheda}`` per ogni pagina che sta in un cassetto."""
    owners: dict[str, str] = {}
    for card in board.get("cards") or []:
        for page in card.get("pages") or []:
            owners.setdefault(page, card["id"])
    return owners


def open_reminders(reminders: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [r for r in reminders.get("reminders") or [] if r.get("state") == "open"]


def items_on_board(board: Mapping[str, Any], reminders: Mapping[str, Any]) -> int:
    """Quanti oggetti sono sulla bacheca: le schede e i post-it delle attese aperte."""
    cards = {c["id"] for c in board.get("cards") or []}
    notes = [r for r in open_reminders(reminders) if r.get("card") in cards]
    return len(cards) + len(notes)


def is_full(board: Mapping[str, Any], reminders: Mapping[str, Any]) -> bool:
    return items_on_board(board, reminders) >= MAX_ITEMS


# Le caselle candidate per un oggetto nuovo, in percentuale. Una griglia larga e
# non casuale: la bacheca deve sembrare disposta da qualcuno, e due aperture
# della stessa bacheca devono dare lo stesso posto.
_SLOTS = [(x, y) for y in (5, 37, 62) for x in (4, 27, 50, 73)]


def free_slot(
    board: Mapping[str, Any], reminders: Mapping[str, Any] | None = None
) -> tuple[float, float]:
    """La casella più lontana da tutto quel che c'è già."""
    taken: list[tuple[float, float]] = [
        (c["pos"]["x"], c["pos"]["y"]) for c in board.get("cards") or []
    ]
    for rem in open_reminders(reminders or {}):
        pos = rem.get("pos")
        if isinstance(pos, Mapping):
            taken.append((float(pos.get("x", 0)), float(pos.get("y", 0))))
    if not taken:
        return _SLOTS[0]

    def distance(slot: tuple[float, float]) -> float:
        return min((slot[0] - x) ** 2 + (slot[1] - y) ** 2 for x, y in taken)

    best = max(_SLOTS, key=distance)
    return float(best[0]), float(best[1])


def add_since(board: dict[str, Any], kind: str, **params: Any) -> None:
    """Una riga di «dall'ultima volta». La scrive il codice, mai il modello.

    Strutturata (un tipo e i suoi parametri) e non una frase: la frase la
    compone l'interfaccia nella lingua di chi guarda.
    """
    entry = {"at": now_iso(), "kind": kind}
    entry.update({k: v for k, v in params.items() if v is not None})
    board.setdefault("since", []).append(entry)
    board["since"] = board["since"][-MAX_SINCE:]


# ── La proprietà ─────────────────────────────────────────────────────────────


def _user_view(board: Mapping[str, Any]) -> dict[str, Any]:
    """Quel che della bacheca Jenny non può cambiare, in una forma confrontabile.

    Le pagine di una scheda stanno fuori: Jenny può **aggiungerne** (il controllo
    a parte, in :func:`ownership_violations`), mai toglierne.
    """
    cards = {}
    for card in board.get("cards") or []:
        cards[card["id"]] = {
            "pos": card.get("pos"),
            "pinned": card.get("pinned"),
            "userSections": card.get("userSections"),
            "lastOpened": card.get("lastOpened"),
            "titleBy": card.get("titleBy"),
            "title": card.get("title") if card.get("titleBy") == USER else None,
        }
    return {
        "cards": cards,
        "order": [c["id"] for c in board.get("cards") or []],
        "connections": board.get("connections"),
        "skeleton": board.get("skeleton"),
        "lastSeen": board.get("lastSeen"),
        "declined": board.get("declined"),
    }


def ownership_violations(
    before: Mapping[str, Any], after: Mapping[str, Any]
) -> list[str]:
    """Cosa ha toccato una scrittura di Jenny che non era sua. Vuoto se niente.

    È il cuore della regola «la forma è tua»: Jenny non crea, non toglie e non
    sposta schede, non tocca le sezioni dell'utente, i fili, lo scheletro, i
    «no» già detti; di una scheda può cambiare il titolo solo se il titolo è
    suo, e le pagine solo aggiungendone. Lo controlla il codice a ogni
    scrittura, perché un prompt che sbaglia non deve poter muovere niente.
    """
    problems: list[str] = []
    old, new = _user_view(before), _user_view(after)
    if old["order"] != new["order"]:
        problems.append("cards added, removed or reordered")
    for card_id, view in old["cards"].items():
        other = new["cards"].get(card_id)
        if other is None:
            continue
        for field_name in ("pos", "pinned", "userSections", "lastOpened", "titleBy", "title"):
            if view[field_name] != other[field_name]:
                problems.append(f"card {card_id}: {field_name} changed")
    old_pages = {c["id"]: c.get("pages") or [] for c in before.get("cards") or []}
    for card in after.get("cards") or []:
        missing = [p for p in old_pages.get(card["id"], []) if p not in (card.get("pages") or [])]
        if missing:
            problems.append(f"card {card['id']}: pages removed ({', '.join(missing)})")
    for key in ("connections", "skeleton", "lastSeen", "declined"):
        if old[key] != new[key]:
            problems.append(f"{key} changed")
    return problems


# ── reminders.json ───────────────────────────────────────────────────────────


def _normalize_when(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, Mapping) or raw.get("kind") not in WHEN_KINDS:
        return None
    if raw["kind"] == "date":
        day = raw.get("date")
        if not isinstance(day, str) or not _DATE_RE.match(day):
            return None
        try:
            date.fromisoformat(day)
        except ValueError:
            return None
        precision = raw.get("precision") if raw.get("precision") in PRECISIONS else "day"
        return _keep_unknown(raw, {"kind": "date", "date": day, "precision": precision})
    app, fld, op = raw.get("app"), raw.get("field"), raw.get("op")
    value = raw.get("value")
    if not is_valid_id(app) or not isinstance(fld, str) or not fld or op not in CONDITION_OPS:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    params = raw.get("params")
    return _keep_unknown(raw, {
        "kind": "condition",
        "app": app,
        "params": dict(params) if isinstance(params, Mapping) else {},
        "field": fld,
        "op": op,
        "value": value,
    })


def _normalize_reminder(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, Mapping) or not is_valid_id(raw.get("id")):
        return None
    when = _normalize_when(raw.get("when"))
    text = clean_text(raw.get("text"), MAX_TITLE * 2)
    if when is None or not text:
        return None
    known: dict[str, Any] = {
        "id": raw["id"],
        "card": raw["card"] if is_valid_id(raw.get("card")) else "",
        "text": text,
        "when": when,
        "why": raw["why"] if isinstance(raw.get("why"), str) else "",
        "source": raw["source"] if isinstance(raw.get("source"), str) else "",
        "by": _owner(raw.get("by")),
        "state": raw.get("state") if raw.get("state") in REMINDER_STATES else "open",
    }
    pos = _normalize_pos(raw.get("pos"))
    if pos is not None:
        known["pos"] = pos
    for stamp in ("created", "notifiedAt", "closedAt"):
        if isinstance(raw.get(stamp), str):
            known[stamp] = raw[stamp]
    return _keep_unknown(raw, known)


def normalize_reminders(raw: Any) -> dict[str, Any]:
    data: Mapping[str, Any] = raw if isinstance(raw, Mapping) else {}
    seen: set[str] = set()
    items: list[dict[str, Any]] = []
    for item in data.get("reminders") or []:
        rem = _normalize_reminder(item)
        if rem is None or rem["id"] in seen:
            continue
        seen.add(rem["id"])
        items.append(rem)
    return _keep_unknown(data, {"version": REMINDERS_VERSION, "reminders": items})


def reminder_violations(
    before: Mapping[str, Any], after: Mapping[str, Any]
) -> list[str]:
    """Come :func:`ownership_violations`, per le attese.

    Jenny ne aggiunge, e chiude o corregge le sue; un'attesa dell'utente non la
    tocca, e nessuna attesa sparisce dal file (un'attesa fatta diventa ``done``,
    non si cancella). Le posizioni le sceglie il codice quando l'attesa nasce:
    da lì in poi Jenny non le sposta.
    """
    problems: list[str] = []
    after_by_id = {r["id"]: r for r in after.get("reminders") or []}
    for rem in before.get("reminders") or []:
        other = after_by_id.get(rem["id"])
        if other is None:
            problems.append(f"reminder {rem['id']} removed")
            continue
        if rem.get("by") == USER and other != rem:
            problems.append(f"reminder {rem['id']} belongs to the user")
            continue
        if rem.get("pos") != other.get("pos") or rem.get("by") != other.get("by"):
            problems.append(f"reminder {rem['id']}: position or owner changed")
    return problems


def reminder_due(rem: Mapping[str, Any]) -> date | None:
    """Il giorno in cui un'attesa a data va ricordata.

    Con ``precision: month`` («verso marzo») è il primo del mese: un mese non è
    un giorno, e l'avviso deve arrivare all'inizio della finestra, non alla fine.
    """
    when = rem.get("when") or {}
    if when.get("kind") != "date":
        return None
    try:
        day = date.fromisoformat(when["date"])
    except (KeyError, TypeError, ValueError):
        return None
    return day.replace(day=1) if when.get("precision") == "month" else day


def next_reminder(
    reminders: Mapping[str, Any], card_id: str
) -> dict[str, Any] | None:
    """L'attesa aperta più vicina di una scheda (quelle a data prima, per giorno)."""
    mine = [r for r in open_reminders(reminders) if r.get("card") == card_id]
    if not mine:
        return None
    far = date.max

    def key(rem: Mapping[str, Any]) -> tuple[date, str]:
        return (reminder_due(rem) or far, rem.get("id", ""))

    return min(mine, key=key)


def reminder_is_soon(rem: Mapping[str, Any], today: date | None = None) -> bool:
    due = reminder_due(rem)
    if due is None:
        return False
    return due - (today or date.today()) <= timedelta(days=SOON_DAYS)


# ── cards/<id>.json (derivato) ───────────────────────────────────────────────


def fingerprint(texts: Iterable[tuple[str, str]]) -> str:
    """L'impronta di un insieme di pagine: ``sha1`` di percorso e testo, in ordine."""
    h = hashlib.sha1()
    for path, text in sorted(texts):
        h.update(path.encode("utf-8"))
        h.update(b"\0")
        h.update(text.encode("utf-8"))
        h.update(b"\0")
    return "sha1:" + h.hexdigest()


def normalize_card_summary(raw: Any, card_id: str) -> dict[str, Any] | None:
    if not isinstance(raw, Mapping):
        return None
    sections = []
    for item in raw.get("sections") or []:
        if not isinstance(item, Mapping):
            continue
        title = clean_text(item.get("title"), MAX_TITLE)
        text = clean_block(item.get("text"), MAX_TEXT)
        if not title or not text:
            continue
        known = {
            "title": title,
            "text": text,
            "sources": _str_list(item.get("sources"), limit=8),
            "changed": item.get("changed") if isinstance(item.get("changed"), str) else "",
        }
        sections.append(_keep_unknown(item, known))
    return _keep_unknown(raw, {
        "card": card_id,
        "generated": raw.get("generated") if isinstance(raw.get("generated"), str) else "",
        "fingerprint": raw.get("fingerprint") if isinstance(raw.get("fingerprint"), str) else "",
        "brief": clean_block(raw.get("brief"), MAX_TEXT),
        "sections": sections,
    })


def compare(left: Any, op: str, right: Any) -> bool:
    """``left op right`` per le condizioni delle attese e gli allarmi delle app.

    Numeri contro numeri; altrimenti solo uguaglianza e disuguaglianza, sulle
    stringhe. Un confronto che non ha senso (un numero contro una parola, un
    valore assente) è **falso**: una condizione che non si può valutare non
    scatta.
    """
    if left is None or right is None or op not in CONDITION_OPS:
        return False
    numeric = (int, float)
    if isinstance(left, numeric) and isinstance(right, numeric) and not (
        isinstance(left, bool) or isinstance(right, bool)
    ):
        a, b = float(left), float(right)
        return {
            "<": a < b, "<=": a <= b, ">": a > b, ">=": a >= b, "==": a == b, "!=": a != b,
        }[op]
    if op == "==":
        return str(left) == str(right)
    if op == "!=":
        return str(left) != str(right)
    return False
