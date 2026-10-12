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
from collections.abc import Callable, Iterable, Mapping
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

# La bacheca e' un **muro** che si sposta e si ingrandisce, con l'origine al
# centro (decisioni dell'utente del 05/10/2026: «molto grande e scrollabile» e
# «l'origine e' il punto centrale, cosi' scrollo in tutte le direzioni»). Le
# posizioni restano in percentuale dello *schermo* — ``x`` della larghezza, ``y``
# dell'altezza — ma vanno da ``-WALL_MAX`` a ``WALL_MAX``: lo schermo dell'origine
# e' 0..100, quello a destra 100..200, quello a sinistra -100..0. Le bacheche di
# prima stanno tutte nello schermo dell'origine, dov'erano, senza migrazione.
WALL_MAX = 1000.0

# Il tetto degli elementi sulla bacheca, schede più l'agenda. Era 12, quando la
# bacheca stava in una schermata sola; sul muro c'e' posto, e il tetto resta solo
# perche' il blocco per turno (una riga per scheda) ha un tetto suo. Quando è
# piena, Jenny propone di togliere prima di proporre di aggiungere.
MAX_ITEMS = 40

# Il tetto dei fili. Con molte schede ne servono di piu', ma venti e oltre sono di
# nuovo il grafo che la bacheca sostituisce.
MAX_CONNECTIONS = 16

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

# Le schede che l'utente ha tolto dalla bacheca restano qui, ripristinabili, per
# questo numero di giorni e al più queste: «Togli» ha un Annulla, e un Annulla
# vale solo se rimette **tutto** (posto, fili, quel che la scheda faceva da sé).
# Poi si buttano davvero. Sono un cestino corto, non un archivio: la wiki resta
# comunque, e la ricerca trova le pagine.
MAX_REMOVED_CARDS = 5
REMOVED_KEEP_DAYS = 7

SUGGESTION_KINDS = (
    "initial_board",
    "add_card",
    "remove_card",
    "add_connection",
    "attach_page",
    "add_section",
    "add_app_card",
)
# ``cron`` e' un lavoro di Jenny a orario, nel cassetto della sua scheda.
# ``micro`` e' la micro app di prima del 06/10/2026: nessuno la crea, nessuno la
# disegna, ma ``board.json`` la deve ancora leggere perche' la migrazione
# all'avvio (``jenny/apps/migrate_micro.py``) la trovi e la faccia diventare
# una sezione ``app``.
SECTION_KINDS = ("note", "reminder", "app", "photo", "micro", "cron")
REMINDER_STATES = ("open", "done", "dropped")
WHEN_KINDS = ("date", "condition")
PRECISIONS = ("day", "month")
CONDITION_OPS = ("<", "<=", ">", ">=", "==", "!=")

# I glifi che una scheda puo' portare: icone del set della casa (Tabler 3.19),
# verificate nel suo foglio. Un elenco chiuso e non un campo libero, perche' il
# glifo lo sceglie il modello, e un nome che non esiste non disegna niente.
GLYPHS = (
    "leaf", "plant", "plant-2", "flower", "tree", "trees", "temperature", "droplet", "sun",
    "cloud-rain", "home", "home-2", "car", "bike", "plane", "truck", "book", "books",
    "notebook", "pencil", "coin", "wallet", "cash", "credit-card", "receipt", "building-bank",
    "chart-line", "user", "users", "friends", "baby-carriage", "map-pin", "world", "mountain",
    "beach", "tent", "tool", "hammer", "briefcase", "building", "school", "heart", "pill",
    "stethoscope", "music", "camera", "palette", "cpu", "device-mobile", "wifi", "router",
    "server", "code", "bug", "bulb", "bolt", "flame", "snowflake", "moon", "star", "flask",
    "calendar", "shopping-cart", "package", "gift", "key", "bed", "sofa", "dog", "cat", "paw",
    "fish", "run", "barbell", "ball-football", "pizza", "coffee", "chef-hat", "cake", "bread",
    "apple", "carrot",
)

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,47}$")
# Lo slug di un'app (``jenny/apps/manifest.py::SLUG_RE``).
_APP_SLUG_RE = re.compile(r"\A[a-z0-9]+(-[a-z0-9]+)*\Z")
# L'id con cui l'app di una scheda-app conta come una sua sezione (allarme,
# eventi, contesto): la polaroid *e'* l'app, il suo valore al posto della foto.
APP_CARD = "@card"
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


def instant_order(value: Any) -> tuple[int, float, str]:
    """La chiave per ordinare dei ``ts`` nel tempo, non come testo.

    Lo stesso istante scritto con fusi diversi (``14:07+02:00`` dall'orologio,
    ``13:18+00:00`` da un giro a mano) come stringa si ordina al contrario: la
    lettura piu' vecchia sembrava l'ultima e il cassetto mostrava un numero
    superato. Un valore che non e' un istante va prima di tutti, e fra loro si
    ordina come testo — com'era.
    """
    stamp = parse_iso(value)
    if stamp is None:
        return (0, 0.0, "" if value is None else str(value))
    return (1, stamp.timestamp(), "")


# ── board.json ───────────────────────────────────────────────────────────────


def empty_board(skeleton: Iterable[str] = ()) -> dict[str, Any]:
    return normalize_board({"skeleton": list(skeleton)})


def _normalize_pos(raw: Any, default_by: str = JENNY) -> dict[str, Any] | None:
    if not isinstance(raw, Mapping):
        return None
    known = {
        "x": _clamp(raw.get("x"), -WALL_MAX, WALL_MAX, 0.0),
        "y": _clamp(raw.get("y"), -WALL_MAX, WALL_MAX, 0.0),
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
    elif kind == "micro":
        if not is_valid_id(raw.get("micro")):
            return None
        known["micro"] = raw["micro"]
        known["pending"] = raw.get("pending") is True
    elif kind == "cron":
        if not is_valid_id(raw.get("job")):
            return None
        known["job"] = raw["job"]
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
    # La taglia della striscia, se la scheda e' un'app che ne ha una.
    if raw.get("size") in SLIP_ROWS:
        known["size"] = raw["size"]
    # La scheda-app: la polaroid *e'* un'app generale, con i parametri della posa.
    app = raw.get("app")
    if isinstance(app, Mapping) and isinstance(app.get("slug"), str) and _APP_SLUG_RE.match(
            app["slug"]):
        params = app.get("params") if isinstance(app.get("params"), Mapping) else {}
        known["app"] = {"slug": app["slug"], "params": {
            k: v for k, v in params.items()
            if isinstance(k, str) and isinstance(v, (str, int, float, bool))}}
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


def _normalize_removed(raw: Any) -> dict[str, Any] | None:
    """Una scheda tolta dall'utente: la scheda com'era, i suoi fili, quando, e
    quel che il codice ha messo in pausa togliendola (per riprenderlo)."""
    if not isinstance(raw, Mapping) or not isinstance(raw.get("at"), str):
        return None
    card = _normalize_card(raw.get("card"))
    if card is None:
        return None
    connections = [
        c for c in (_normalize_connection(x) for x in raw.get("connections") or [])
        if c is not None
    ]
    paused = raw.get("paused") if isinstance(raw.get("paused"), Mapping) else {}
    known = {
        "card": card,
        "connections": connections,
        "at": raw["at"],
        "paused": {"jobs": _str_list(paused.get("jobs"))},
    }
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
        "removedCards": [
            r for r in (_normalize_removed(x) for x in data.get("removedCards") or [])
            if r is not None and r["card"]["id"] not in seen_ids
        ][-MAX_REMOVED_CARDS:],
    }
    if isinstance(data.get("lastSeen"), str):
        known["lastSeen"] = data["lastSeen"]
    # L'agenda delle attese: un foglio solo per bacheca. Del suo posto si salva
    # solo quello scelto dall'utente; senza, lo calcola la vista.
    agenda = data.get("agenda")
    if isinstance(agenda, Mapping):
        pos = _normalize_pos(agenda.get("pos"), USER)
        known["agenda"] = _keep_unknown(agenda, {"pos": pos} if pos else {})
    return _keep_unknown(data, known)


def card_by_id(board: Mapping[str, Any], card_id: str) -> dict[str, Any] | None:
    for card in board.get("cards") or []:
        if card.get("id") == card_id:
            return card
    return None


def removed_card_ids(board: Mapping[str, Any]) -> set[str]:
    """Gli id delle schede tolte dall'utente e ancora ripristinabili."""
    return {r["card"]["id"] for r in board.get("removedCards") or []
            if isinstance(r.get("card"), Mapping)}


def expired_removed(board: Mapping[str, Any], now: datetime | None = None) -> list[dict[str, Any]]:
    """Le schede tolte da più di :data:`REMOVED_KEEP_DAYS` giorni: da buttare."""
    current = now or datetime.now().astimezone()
    out = []
    for entry in board.get("removedCards") or []:
        at = parse_iso(entry.get("at"))
        if at is None or current - at > timedelta(days=REMOVED_KEEP_DAYS):
            out.append(entry)
    return out


def page_owners(board: Mapping[str, Any]) -> dict[str, str]:
    """``{pagina: id della scheda}`` per ogni pagina che sta in un cassetto."""
    owners: dict[str, str] = {}
    for card in board.get("cards") or []:
        for page in card.get("pages") or []:
            owners.setdefault(page, card["id"])
    return owners


def first_board_hold(board: Mapping[str, Any]) -> str | None:
    """Chi tiene ferma la prima bacheca di un quaderno senza schede, o ``None``.

    ``"proposed"`` se la proposta aspetta gia' nella busta, ``"declined"`` se
    l'utente l'ha rifiutata, ``"later"`` se ha detto «piu' avanti» e il rinvio
    vale ancora. ``None`` vuol dire che niente la trattiene: la propone la
    prossima passata. Ha senso solo su una bacheca senza schede — una con le
    schede la sua prima l'ha gia' avuta.
    """
    if any(s.get("kind") == "initial_board" for s in board.get("suggestions") or []):
        return "proposed"
    if any(d.get("kind") == "initial_board" for d in board.get("declined") or []):
        return "declined"
    passes = int(board.get("passes") or 0)
    if any(
        x.get("kind") == "initial_board" and int(x.get("untilPass") or 0) > passes
        for x in board.get("later") or []
    ):
        return "later"
    return None


def open_reminders(reminders: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [r for r in reminders.get("reminders") or [] if r.get("state") == "open"]


def board_reminders(board: Mapping[str, Any], reminders: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Le attese aperte delle schede sulla bacheca, dalla più vicina (v. :func:`sorted_reminders`)."""
    cards = {c["id"] for c in board.get("cards") or []}
    return sorted_reminders([r for r in open_reminders(reminders) if r.get("card") in cards])


def items_on_board(board: Mapping[str, Any], reminders: Mapping[str, Any]) -> int:
    """Quanti oggetti sono sulla bacheca: le schede, e l'agenda se ci sono attese.

    Le attese non sono più un post-it ciascuna: stanno tutte nell'agenda, che è
    un oggetto solo. Così un'attesa nuova non trova mai la bacheca piena.
    """
    cards = {c["id"] for c in board.get("cards") or []}
    agenda = any(r.get("card") in cards for r in open_reminders(reminders))
    return len(cards) + int(agenda)


def is_full(board: Mapping[str, Any], reminders: Mapping[str, Any]) -> bool:
    return items_on_board(board, reminders) >= MAX_ITEMS


# L'ingombro degli oggetti, in percentuale della bacheca. La larghezza viene dal
# CSS (una polaroid e' il 19% della larghezza, l'agenda il 30%); l'altezza
# dipende dalla forma dello schermo, e qui si prende quella del quadrato basso
# del Titan 2 (590x~400 px di bacheca), il caso in cui gli oggetti sono piu' alti
# in proporzione: su uno schermo piu' alto avanza spazio, non ne manca.
CARD_BOX = (20.0, 40.0)
# L'agenda: larga 30 (il CSS di ``.home-board-agenda``), alta quanto intestazione,
# tre righe e «+N più avanti» sul quadrato basso del Titan 2.
AGENDA_BOX = (30.0, 48.0)
# Quante attese mostra l'agenda; le altre stanno dietro «+N più avanti».
AGENDA_ROWS = 3
# La striscia: la forma compatta di un'app (``jenny/apps/slip_spec.py``). Larga
# due polaroid, alta una di quattro taglie fisse, contate in righe di contenuto
# (una riga = 3,55% della larghezza: il 28 px del mockup sulla polaroid da 150).
# Le altezze sono ricavate come quella di ``CARD_BOX``: la striscia del CSS sul
# Titan 2, piu' il margine che la polaroid ha (+6). Una taglia sola per app, dal
# suo manifest: la bacheca non salta quando cambiano i dati.
SLIP_ROWS = {"S": 2, "M": 4, "L": 6, "XL": 9}
SLIP_BOX = {"S": (40.0, 29.0), "M": (40.0, 39.0), "L": (40.0, 50.0), "XL": (40.0, 66.0)}


def card_box(card: Mapping[str, Any]) -> tuple[float, float]:
    """L'ingombro di una scheda: la sua taglia di striscia, o la polaroid."""
    return SLIP_BOX.get(str(card.get("size") or ""), CARD_BOX)


# Quanto lontano da *near* si cerca un posto (in percento dello schermo): un
# po' piu' di uno schermo per lato, cosi' su un muro affollato si trova sempre.
NEAR_SPAN_X = 120.0
NEAR_SPAN_Y = 100.0

# Gli angoli del primo schermo che non sono della bacheca: in basso a sinistra la
# busta, in basso a destra Jenny, che sporge dal bordo e si prende i tocchi. Stanno
# fermi sullo schermo mentre il muro scorre, quindi contano solo li'.
_RESERVED = ((0.0, 76.0, 20.0, 100.0), (80.0, 62.0, 100.0, 100.0))


def _boxes(board: Mapping[str, Any], reminders: Mapping[str, Any] | None) -> list[tuple[float, float, float, float]]:
    out = []
    for card in board.get("cards") or []:
        x, y = float(card["pos"]["x"]), float(card["pos"]["y"])
        w, h = card_box(card)
        out.append((x, y, x + w, y + h))
    # L'agenda c'è solo con delle attese, e occupa il muro solo dove l'ha messa
    # l'utente: il posto calcolato lo sceglie la vista *dopo* le schede.
    pos = (board.get("agenda") or {}).get("pos")
    if isinstance(pos, Mapping) and board_reminders(board, reminders or {}):
        x, y = float(pos.get("x", 0)), float(pos.get("y", 0))
        out.append((x, y, x + AGENDA_BOX[0], y + AGENDA_BOX[1]))
    return out


def _overlap(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    return w * h if w > 0 and h > 0 else 0.0


def free_slot(
    board: Mapping[str, Any],
    reminders: Mapping[str, Any] | None = None,
    *,
    box: tuple[float, float] = CARD_BOX,
    near: tuple[float, float] | None = None,
    extra: list[tuple[float, float, float, float]] | None = None,
) -> tuple[float, float]:
    """Il posto per un oggetto nuovo: quello che copre di meno quel che c'e' gia'.

    Si provano le caselle di una griglia fitta, si scartano gli angoli che non
    sono della bacheca, e vince la casella con la sovrapposizione minore; a
    parita', la piu' vicina a *near* (le schede, per l'agenda) o, senza, la
    prima in ordine di lettura. Deterministico: due aperture della stessa
    bacheca danno lo stesso posto.
    """
    taken = _boxes(board, reminders) + list(extra or [])
    best: tuple[float, float, float] | None = None
    if near is not None:
        # **Vicino a dove si guarda** (o accanto alle schede, per l'agenda): una finestra
        # attorno a *near*, ovunque sia sul muro. Gli angoli riservati qui non
        # contano: sono angoli della finestra, non di un posto del muro.
        cx, cy = float(near[0]), float(near[1])
        xs = [x for x in range(int(cx - NEAR_SPAN_X), int(cx + NEAR_SPAN_X) + 1, 4)
              if -WALL_MAX <= x <= WALL_MAX - box[0]]
        ys = [y for y in range(int(cy - NEAR_SPAN_Y), int(cy + NEAR_SPAN_Y) + 1, 4)
              if -WALL_MAX <= y <= WALL_MAX - box[1]]
        best_pos = (xs[0] if xs else 0.0, ys[0] if ys else 0.0)
        for y in ys:
            for x in xs:
                rect = (float(x), float(y), x + box[0], y + box[1])
                cost = sum(_overlap(rect, t) for t in taken)
                key = (cost, (x - cx) ** 2 + (y - cy) ** 2, 0.0)
                if best is None or key < best:
                    best, best_pos = key, (float(x), float(y))
        return best_pos
    best_pos = (2.0, 3.0)
    # Il primo schermo, e sotto quanto serve: quando il primo e' pieno il muro
    # cresce verso il basso, che su un telefono e' la direzione in cui si scorre.
    bottom = max([100.0, *(r[3] for r in taken)])
    rows = min(WALL_MAX, bottom + box[1] + 8)
    for y in range(3, int(rows - box[1]) + 1, 4):
        for x in range(2, int(100 - box[0]) + 1, 4):
            rect = (float(x), float(y), x + box[0], y + box[1])
            if any(_overlap(rect, r) for r in _RESERVED):
                continue
            cost = sum(_overlap(rect, t) for t in taken)
            key = (cost, float(y * 100 + x), 0.0)
            if best is None or key < best:
                best, best_pos = key, (float(x), float(y))
    return best_pos


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
            "app": card.get("app"),
            "size": card.get("size"),
        }
    return {
        "cards": cards,
        "order": [c["id"] for c in board.get("cards") or []],
        "connections": board.get("connections"),
        "skeleton": board.get("skeleton"),
        "lastSeen": board.get("lastSeen"),
        "agenda": board.get("agenda"),
        "declined": board.get("declined"),
        "removedCards": board.get("removedCards"),
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
        for field_name in ("pos", "pinned", "userSections", "lastOpened", "titleBy", "title",
                           "app", "size"):
            if view[field_name] != other[field_name]:
                problems.append(f"card {card_id}: {field_name} changed")
    old_pages = {c["id"]: c.get("pages") or [] for c in before.get("cards") or []}
    for card in after.get("cards") or []:
        missing = [p for p in old_pages.get(card["id"], []) if p not in (card.get("pages") or [])]
        if missing:
            problems.append(f"card {card['id']}: pages removed ({', '.join(missing)})")
    for key in ("connections", "skeleton", "lastSeen", "agenda", "declined", "removedCards"):
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
    fld, op = raw.get("field"), raw.get("op")
    value = raw.get("value")
    if not isinstance(fld, str) or not fld or op not in CONDITION_OPS:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    # La sorgente del dato: una Jenny App, o (solo finche' la migrazione non la
    # converte) una micro app di prima.
    if is_valid_id(raw.get("micro")):
        return _keep_unknown(raw, {
            "kind": "condition", "micro": raw["micro"], "field": fld, "op": op, "value": value,
        })
    app = raw.get("app")
    if not is_valid_id(app):
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


def _without(mapping: Mapping[str, Any], *keys: str) -> dict[str, Any]:
    return {k: v for k, v in mapping.items() if k not in keys}


# I timbri che l'orologio del quaderno scrive anche sulle attese dell'utente:
# l'avviso partito, l'ultimo annuncio (il freno delle dodici ore) e quante volte
# di fila la condizione e' risultata falsa (l'isteresi del riarmo). Non sono
# modifiche dell'attesa.
CLOCK_STAMPS = ("notifiedAt", "lastAnnouncedAt", "falseStreak")


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
        # I timbri dell'orologio (``CLOCK_STAMPS``) li scrive il job ``reminders``
        # anche sulle attese dell'utente: non sono una modifica dell'attesa.
        if rem.get("by") == USER and (
            _without(other, *CLOCK_STAMPS) != _without(rem, *CLOCK_STAMPS)
        ):
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


def _reminder_key(rem: Mapping[str, Any]) -> tuple[date, str]:
    # Quelle a data prima, per giorno; quelle a condizione (senza giorno) in fondo.
    return (reminder_due(rem) or date.max, rem.get("id", ""))


def sorted_reminders(rems: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Le attese dalla più vicina: l'ordine dell'agenda e del calendario di cura."""
    return [dict(r) for r in sorted(rems, key=_reminder_key)]


def next_reminder(
    reminders: Mapping[str, Any], card_id: str
) -> dict[str, Any] | None:
    """L'attesa aperta più vicina di una scheda (quelle a data prima, per giorno)."""
    mine = [r for r in open_reminders(reminders) if r.get("card") == card_id]
    return min(mine, key=_reminder_key) if mine else None


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


# Un wikilink: ``[[pagina]]``, ``[[cartella/pagina.md#titolo]]``, ``[[pagina|etichetta]]``.
_WIKILINK_RE = re.compile(r"\[\[([^\[\]|]+)(?:\|([^\[\]]*))?\]\]")


def plain_wikilinks(value: Any, title_of: Callable[[str], str | None] | None = None) -> Any:
    """Il testo di un cassetto senza ``[[link]]``: al loro posto, parole.

    Il cassetto si disegna come testo semplice, e le sue pagine le mostra gia'
    come fonti sotto ogni sezione: un ``[[Coltivazione-Prunus]]`` li' e' solo
    rumore (visto sull'emulatore l'11/10/2026, tre cassetti su diciassette).
    Resta l'etichetta, se c'e'; altrimenti il titolo che *title_of* trova per
    il bersaglio; altrimenti il nome della pagina, senza cartelle ne' ``.md``.
    Un valore che non e' testo torna com'e'.
    """
    if not isinstance(value, str) or "[[" not in value:
        return value

    def plain(m: re.Match[str]) -> str:
        target, label = m.group(1).strip(), (m.group(2) or "").strip()
        if label:
            return label
        title = title_of(target) if title_of is not None else None
        if title:
            return title
        name = target.split("#", 1)[0].rstrip("/").rsplit("/", 1)[-1]
        return name.removesuffix(".md") or target

    return _WIKILINK_RE.sub(plain, value)


def normalize_card_summary(raw: Any, card_id: str) -> dict[str, Any] | None:
    if not isinstance(raw, Mapping):
        return None
    sections = []
    for item in raw.get("sections") or []:
        if not isinstance(item, Mapping):
            continue
        title = clean_text(item.get("title"), MAX_TITLE)
        text = clean_block(plain_wikilinks(item.get("text")), MAX_TEXT)
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
        "brief": clean_block(plain_wikilinks(raw.get("brief")), MAX_TEXT),
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
