"""Telemetria viva di un subagent: eventi, formattazione, digest persistito.

Perche esiste: l'unica informazione per-tool che arrivava al pannello era
``{"name", "status", "detail"}`` con ``detail = str(result)`` a newline
schiacciate e tagliato a 120 caratteri — output grezzo del tool, decapitato:
insieme poco informativo *e* non fidato. Qui la riga leggibile viene costruita
dai **metadati che possediamo** (nome del tool, argomenti, dimensioni,
conteggi, durate), non dal contenuto prodotto.

Tre livelli, indipendenti e testabili separatamente:

* :func:`format_tool_start` / :func:`format_tool_end` — funzioni **pure** di
  (nome del tool, argomenti, risultato-o-errore) che producono la riga.
* :class:`SubagentActivityLog` — ring buffer in RAM, :data:`RING_CAPACITY`
  eventi per task, con ``seq`` monotono. Il ``seq`` non e decorazione: e cio
  che rende lo stream affidabile, perche il client puo *accorgersi* di un buco
  invece di fidarsi. Uno stream che perde eventi in silenzio e peggio di un
  pannello statico, perche si guadagna una fiducia che non merita.
* :func:`build_digest` + :class:`SubagentDigestStore` — la condensa che
  sopravvive al task: un file per task sotto ``<workspace>/subagents/activity/``,
  scritto una volta alla transizione terminale e letto solo quando qualcuno
  espande il blocco "cosa ha fatto davvero".

Forma dell'evento (piatta, sola-JSON, una forma con campi opzionali, cosi un
renderer non deve mai ramificare sulla *struttura*)::

    {"seq": int, "ts": float, "kind": str, "name": str | None,
     "call_id": str | None, "status": str | None, "summary": str,
     "duration_ms": int | None}

``call_id`` e l'id di chiamata del provider, presente sugli eventi
``tool_start``/``tool_end`` e ``None`` su tutti gli altri. Non e decorazione:
e cio che rende **esatto** l'accoppiamento in :func:`build_digest` quando lo
stesso tool e in volo piu volte — tre ``web_fetch`` nello stesso batch sono il
caso normale, e per nome sarebbero accoppiati a caso. Resta opzionale: un evento
senza ``call_id`` (produttore che non lo conosce, digest scritto da una versione
precedente) accoppia FIFO per nome come prima.

**Tre regole di sicurezza**, non di stile, applicate nei punti indicati:

1. *Mai il contenuto.* Nessun byte del file letto o scritto, del testo della
   pagina, del sorgente passato a ``python_exec`` o del corpo del risultato
   entra in un summary. Il risultato viene **misurato, non letto**: all'ingresso
   e ridotto a :class:`_Outcome` (dimensione, righe, blocchi, conteggi) e solo
   le sonde ``_probe_*`` possono guardarne ``head``/``tail``.
2. *Query string via dalle URL.* Token e chiavi vivono nei query param, e il
   summary finisce in una UI e su disco. :func:`_display_url` **ricostruisce**
   ``host[:port]/path`` da zero invece di ripulire la stringa, quindi query,
   fragment e userinfo non possono sopravvivere per dimenticanza.
3. *Prima i nostri metadati.* Il risultato di un subagent contiene contenuto web
   non fidato, e tutto cio che ne deriva e superficie di injection appena arriva
   alla UI. Quindi da un risultato escono solo **interi** (conteggi, dimensioni)
   o **costanti nostre** (:data:`_ERROR_PHRASES`). L'unica stringa estratta e il
   nome di una classe di eccezione, validato contro un charset di
   identificatori e troncato a :data:`_MAX_EXC_CHARS`.
"""

from __future__ import annotations

import json
import re
import threading
import time
from collections import deque
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from loguru import logger

from jenny.agent.subagent_records import SUBAGENTS_DIRNAME
from jenny.utils.path import abbreviate_path, atomic_write

__all__ = [
    "ACTIVITY_KINDS",
    "DIGEST_KIND_TOOL",
    "DIGEST_STATUS_INCOMPLETE",
    "KIND_ERROR",
    "KIND_ITERATION",
    "KIND_MESSAGE_IN",
    "KIND_PHASE",
    "KIND_RESULT",
    "KIND_THINKING",
    "KIND_TOOL_END",
    "KIND_TOOL_START",
    "MAX_DIGEST_EVENTS",
    "MAX_SUMMARY_CHARS",
    "MAX_TRACKED_TASKS",
    "RING_CAPACITY",
    "ActivityWindow",
    "DigestMeta",
    "SubagentActivityLog",
    "SubagentDigestStore",
    "build_digest",
    "classify_tool_result",
    "format_tool_end",
    "format_tool_start",
    "known_tools",
]

# ---------------------------------------------------------------------------
# Costanti di contratto
# ---------------------------------------------------------------------------

KIND_TOOL_START = "tool_start"
KIND_TOOL_END = "tool_end"
KIND_THINKING = "thinking"
KIND_ITERATION = "iteration"
KIND_PHASE = "phase"
KIND_MESSAGE_IN = "message_in"
KIND_RESULT = "result"
KIND_ERROR = "error"

ACTIVITY_KINDS = frozenset({
    KIND_TOOL_START,
    KIND_TOOL_END,
    KIND_THINKING,
    KIND_ITERATION,
    KIND_PHASE,
    KIND_MESSAGE_IN,
    KIND_RESULT,
    KIND_ERROR,
})

# ``kind`` sconosciuto: l'evento **non** viene scartato, viene registrato come
# ``phase``. Vedi :meth:`SubagentActivityLog.append` per il perche.
KIND_FALLBACK = KIND_PHASE

# Kind e status che esistono **solo** nel digest: una coppia start/end
# collassata diventa un ``tool``, e uno start senza end (subagent morto a meta
# chiamata) resta visibile con status ``incomplete``. Sono un'estensione
# deliberata dell'enum live, non un errore: il digest e una forma derivata e chi
# lo rende deve poter distinguere "fallito" da "non lo sappiamo".
DIGEST_KIND_TOOL = "tool"
DIGEST_STATUS_INCOMPLETE = "incomplete"

STATUS_OK = "ok"
STATUS_ERROR = "error"

# Capienza del ring per task. Il digest e l'artefatto durevole (piu il record
# Tier-1): questo e telemetria viva, quindi limitata e a perdere.
RING_CAPACITY = 200

# Tetto di un summary. Scelto, non ereditato:
#
# * il pannello e un telefono: ~40 caratteri per riga, quindi 160 sono quattro
#   righe nel caso peggiore e una o due in quello normale — leggibile senza far
#   scrollare la card;
# * il summary piu lungo che *generiamo* (path abbreviato + intervallo di righe,
#   o una query di ricerca capata a 60) sta largamente sotto: il tetto tronca
#   solo input anomali, non output nostro legittimo;
# * limita il costo: 200 eventi x ~160 caratteri = ~32 KB di testo per task in
#   RAM, e un digest da ~60 tool call resta nell'ordine dei 15 KB su disco —
#   proprio il motivo per cui il digest non sta dentro il record Tier-1.
MAX_SUMMARY_CHARS = 160

# Tetto di eventi in un digest. Il digest nasce da un ring da 200 e li collassa,
# quindi non ci arriva mai vicino: e una garanzia contro un chiamante che passi
# una lista arbitraria, non una potatura attesa.
MAX_DIGEST_EVENTS = 300

# Quanti task il log segue insieme. La ``drop()`` la chiama il produttore dopo
# aver scritto il digest, ma un tetto serve comunque: il pool di subagent e
# piccolo (default 3) mentre un gateway vivo per giorni ne fa centinaia, e un
# dict che cresce per la vita del processo e una perdita silenziosa su un
# telefono. Sfrattato il ring toccato meno recentemente — cioe un task finito da
# tempo, il cui digest e gia su disco.
MAX_TRACKED_TASKS = 64

_DIGEST_VERSION = 1
# La radice ``subagents/`` arriva dai record: un letterale duplicato qui
# diventerebbe una seconda directory al primo refuso.
_ACTIVITY_DIRNAME = "activity"

# Guardia: un digest oltre questa soglia e patologico (non lo produciamo mai),
# quindi lo si ignora invece di caricarlo in RAM su un telefono.
_MAX_DIGEST_FILE_BYTES = 512_000

# Quanto testo del risultato resta disponibile alle sonde. Le informazioni
# strutturate dei tool di Jenny stanno in testa (``Error: ...``) o in coda (i
# trailer di ``read_file``/``list_dir``/``grep``, il traceback di
# ``python_exec``): due finestre corte bastano, e piu corte sono meno
# contenuto non fidato resta a portata di un bug futuro.
_PROBE_CHARS = 400

# Nessuna sonda numerica accetta valori oltre questo: un intero fuori scala
# viene da un parsing sbagliato, non da un tool.
_MAX_PROBE_INT = 1_000_000_000

# Tetto del nome di classe di eccezione, l'unica stringa che estraiamo dal
# risultato.
_MAX_EXC_CHARS = 48

# Tetto di un ``call_id``. Gli id reali stanno largamente sotto (``toolu_01...``,
# ``call_...``): il tetto esiste perche l'id lo scrive il modello e finisce in una
# chiave di accoppiamento e in un file su disco.
_MAX_CALL_ID_CHARS = 64


# ---------------------------------------------------------------------------
# Igiene del testo
# ---------------------------------------------------------------------------

# Controlli C0/C1 piu i caratteri invisibili e di override bidirezionale: un
# summary e reso in una UI, e U+202E puo far leggere a un umano un nome di file
# al contrario. Vengono normalizzati a spazio *prima* del collasso whitespace.
_CONTROL_RE = re.compile(
    "[\x00-\x1f\x7f-\x9f​-‏  ‪-‮⁦-⁩﻿]"
)
_WS_RE = re.compile(r"\s+")

# Charset di un identificatore: usato per il nome di eccezione e per i nomi di
# funzione/chiave che finiscono in un summary.
_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _clean(value: Any, *, limit: int = MAX_SUMMARY_CHARS) -> str:
    """Rende una stringa a riga singola, senza controlli, capata a *limit*."""
    if not isinstance(value, str):
        value = "" if value is None else str(value)
    text = _WS_RE.sub(" ", _CONTROL_RE.sub(" ", value)).strip()
    if limit > 0 and len(text) > limit:
        text = text[: max(1, limit - 3)].rstrip() + "..."
    return text


def _fmt_bytes(size: int) -> str:
    """Dimensione leggibile: ``912 B``, ``18 KB``, ``1.4 MB``."""
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.0f} KB" if size >= 10 * 1024 else f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def _fmt_duration(ms: int) -> str:
    """Durata leggibile: ``420 ms``, ``12.4s``, ``2m 5s``."""
    if ms < 1000:
        return f"{ms} ms"
    if ms < 60_000:
        return f"{ms / 1000:.1f}s"
    return f"{ms // 60_000}m {(ms % 60_000) // 1000}s"


def _plural(count: int, word: str) -> str:
    """Plurale inglese quanto basta ai nomi che usiamo davvero.

    Il ``+ "s"` ingenuo produceva ``3 entrys`` e ``2 matchs`` in due summary che
    compaiono a ogni ``list_dir`` e a ogni ``grep``: la riga la legge un utente,
    quindi le due regole irregolari che ci servono stanno qui.
    """
    if count == 1:
        return f"{count} {word}"
    if word.endswith("y") and not word.endswith(("ay", "ey", "oy", "uy")):
        return f"{count} {word[:-1]}ies"
    if word.endswith(("s", "x", "z", "ch", "sh")):
        return f"{count} {word}es"
    return f"{count} {word}s"


# ---------------------------------------------------------------------------
# Lettura degli argomenti (mai dei valori "contenuto")
# ---------------------------------------------------------------------------


def _as_mapping(value: Any) -> Mapping[str, Any]:
    """Argomenti come mapping, degradando su ``{}``: garbage non deve sollevare."""
    return value if isinstance(value, Mapping) else {}


def _arg_text(args: Mapping[str, Any], *names: str, limit: int = 60) -> str:
    """Primo argomento stringa non vuoto tra *names*, ripulito e capato."""
    for name in names:
        value = args.get(name)
        if isinstance(value, str) and value.strip():
            return _clean(value, limit=limit)
    return ""


def _arg_int(args: Mapping[str, Any], *names: str) -> int | None:
    for name in names:
        value = args.get(name)
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            return value
        if isinstance(value, str):
            try:
                return int(value.strip())
            except ValueError:
                continue
    return None


def _arg_bool(args: Mapping[str, Any], name: str) -> bool:
    value = args.get(name)
    if isinstance(value, bool):
        return value
    return isinstance(value, str) and value.strip().lower() in ("true", "1", "yes")


def _arg_lines(args: Mapping[str, Any], name: str) -> int | None:
    """Numero di righe di un argomento testuale — la *misura*, non il testo.

    Serve per ``python_exec`` (quante righe di codice) e ``edit_file``/
    ``apply_patch`` (quante righe entrano e quante escono). Il valore non viene
    mai emesso: solo il suo conteggio.
    """
    value = args.get(name)
    if not isinstance(value, str):
        return None
    return len(value.splitlines()) or (1 if value else 0)


def _arg_bytes(args: Mapping[str, Any], name: str) -> int | None:
    value = args.get(name)
    if not isinstance(value, str):
        return None
    return len(value.encode("utf-8", "replace"))


def _basename(path: Any, *, limit: int = 48) -> str:
    """Nome file di un path. Metadato, non contenuto."""
    text = _clean(path, limit=300)
    if not text:
        return "(no path)"
    name = Path(text.replace("\\", "/")).name
    return _clean(name or text, limit=limit)


def _display_path(path: Any, *, limit: int = 48) -> str:
    """Path abbreviato (per le directory, dove il solo basename e ambiguo)."""
    text = _clean(path, limit=300)
    if not text:
        return "(no path)"
    return _clean(abbreviate_path(text, limit), limit=limit)


_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*://")


def _display_url(raw: Any, *, limit: int = 64) -> str:
    """``host[:port]/path`` **ricostruito**: query, fragment e userinfo sparisco.

    Regola di sicurezza 2. La stringa non viene ripulita ma *riscritta* dai soli
    componenti che vogliamo mostrare, quindi non esiste un caso in cui un query
    param sopravvive perche ce ne siamo dimenticati. Una URL senza host (``data:``,
    ``javascript:``, spazzatura) non viene echeggiata affatto: un data URI *e*
    contenuto, e stamparlo violerebbe la regola 1.
    """
    text = _clean(raw, limit=2000)
    if not text:
        return "(no url)"
    candidate = text if _SCHEME_RE.match(text) else f"//{text}"
    try:
        parsed = urlparse(candidate)
        host = (parsed.hostname or "").lower()
        port = parsed.port
    except ValueError:
        return "(unparsable url)"
    if not host:
        scheme = _clean(parsed.scheme, limit=12)
        return f"({scheme} url)" if scheme else "(unparsable url)"
    display = host if port in (None, 80, 443) else f"{host}:{port}"
    path = parsed.path or ""
    if path and path != "/":
        display += path if path.startswith("/") else f"/{path}"
    if len(display) > limit:
        display = abbreviate_path(display, limit)
    return _clean(display, limit=limit)


def _ident(value: Any, *, limit: int = 40) -> str:
    """Primo identificatore dentro *value*, o ``""``.

    Usato per i nomi di funzione/chiave che finiscono in un summary: sono
    metadati scritti dal modello, ma restano stringhe influenzabili, quindi
    passano da un charset chiuso invece che da un semplice troncamento.
    """
    if not isinstance(value, str):
        return ""
    match = _IDENT_RE.search(value)
    return match.group(0)[:limit] if match else ""


# ---------------------------------------------------------------------------
# Esito di una tool call: misure, non testo
# ---------------------------------------------------------------------------

_RE_ENUMERATED = re.compile(r"^\s*\d+\.\s", re.MULTILINE)
_RE_EXC_LINE = re.compile(
    r"^([A-Za-z_][A-Za-z0-9_.]*"
    r"(?:Error|Exception|Interrupt|Warning|Exit|Iteration|Interrupted))\s*(?::|$)",
    re.MULTILINE,
)


@dataclass(frozen=True, slots=True)
class _Outcome:
    """Risultato di un tool ridotto alle sue *misure*.

    Regola di sicurezza 1, applicata qui: il corpo del risultato non attraversa
    questo confine. Restano dimensione, righe, blocchi di contenuto, il conteggio
    delle voci numerate e due finestre corte (``head``/``tail``) che **solo** le
    sonde ``_probe_*`` leggono, e da cui escono solo interi o costanti nostre.
    """

    ok: bool
    size: int = 0
    lines: int = 0
    enumerated: int = 0
    blocks: int | None = None
    head: str = ""
    tail: str = ""
    exc: str | None = None

    @property
    def exception_name(self) -> str | None:
        """Nome di classe dell'eccezione, se il risultato ne mostra una."""
        if self.exc:
            return self.exc
        match = _RE_EXC_LINE.search(self.tail)
        if match is None:
            return None
        return match.group(1).rsplit(".", 1)[-1][:_MAX_EXC_CHARS]


def _probe_int(text: str, pattern: re.Pattern[str], *, group: int = 1) -> int | None:
    """Intero estratto da un marker ancorato. Solo interi escono da qui."""
    match = pattern.search(text)
    if match is None:
        return None
    try:
        value = int(match.group(group))
    except (TypeError, ValueError, IndexError):
        return None
    return value if 0 <= value <= _MAX_PROBE_INT else None


def _build_outcome(tool: str, result: Any, error: Any) -> _Outcome:
    """Costruisce l'esito misurato di una tool call, senza mai sollevare."""
    if isinstance(error, BaseException):
        return _Outcome(ok=False, exc=type(error).__name__[:_MAX_EXC_CHARS])
    if error:
        return _Outcome(ok=False, head=_head_text(error), tail=_head_text(error))

    if isinstance(result, str):
        text = result
    elif isinstance(result, (list, tuple)):
        # Blocchi di contenuto (immagine/PDF): stringificarli materializzerebbe
        # il base64 in RAM per niente. Ne misuriamo solo la cardinalita.
        return _Outcome(ok=True, blocks=len(result))
    elif result is None:
        text = ""
    else:
        text = str(result)

    ok = classify_tool_result(tool, text)
    return _Outcome(
        ok=ok == STATUS_OK,
        size=len(text.encode("utf-8", "replace")),
        lines=len(text.splitlines()),
        enumerated=len(_RE_ENUMERATED.findall(text)),
        head=text[:_PROBE_CHARS],
        tail=text[-_PROBE_CHARS:],
    )


def _head_text(value: Any) -> str:
    return value[:_PROBE_CHARS] if isinstance(value, str) else str(value)[:_PROBE_CHARS]


# Convenzione degli errori JSON di ``web_fetch``: il tool ritorna
# ``{"error": ...}`` invece del prefisso ``Error:``, quindi senza questa voce un
# fetch fallito verrebbe classificato ``ok`` e riassunto come "0 B".
_JSON_ERROR_TOOLS = frozenset({"web_fetch"})


def classify_tool_result(tool: str, result: Any, error: Any = None) -> str:
    """``"ok"`` o ``"error"`` secondo le convenzioni dei tool di Jenny.

    La conoscenza sta qui e non nei chiamanti: un tool di Jenny segnala il
    fallimento con una stringa che inizia per ``Error`` (vedi
    ``agent/tool_execution.py``), tranne ``web_fetch`` che ritorna un oggetto
    JSON con chiave ``error``.
    """
    if error:
        return STATUS_ERROR
    if isinstance(result, str):
        stripped = result.lstrip()
        if stripped.startswith("Error"):
            return STATUS_ERROR
        if tool in _JSON_ERROR_TOOLS and stripped.startswith('{"error"'):
            return STATUS_ERROR
    return STATUS_OK


# ---------------------------------------------------------------------------
# Frasi d'errore: costanti nostre, mai il messaggio del tool
# ---------------------------------------------------------------------------

# Regola di sicurezza 3: un errore puo contenere un path, una URL con token o
# testo di una pagina. Quindi non ne copiamo il messaggio: lo si riconosce da un
# marker e si emette una frase **nostra**. Primo match vince, quindi i marker
# piu specifici stanno sopra.
_ERROR_PHRASES: tuple[tuple[str, str], ...] = (
    ("old_text not found", "target text not found"),
    ("appears multiple times", "target text is ambiguous"),
    ("not found in namespace", "unknown function"),
    ("url validation failed", "url rejected by policy"),
    ("empty response body", "empty response"),
    ("file not found", "file not found"),
    ("path not found", "path not found"),
    ("directory not found", "directory not found"),
    ("not a directory", "not a directory"),
    ("not a file", "not a file"),
    ("already exists", "already exists"),
    ("permission denied", "permission denied"),
    ("is blocked", "blocked by policy"),
    ("outside", "outside the allowed workspace"),
    ("invalid regex", "invalid pattern"),
    ("invalid page range", "invalid page range"),
    ("out of bounds", "out of range"),
    ("beyond end of file", "out of range"),
    ("timed out", "timed out"),
    ("timeout", "timed out"),
    ("too large", "too large"),
    ("exceeds", "size limit exceeded"),
    ("not utf-8", "not utf-8 text"),
    ("binary file", "binary file"),
    ("unsupported", "unsupported input"),
    ("unavailable", "service unavailable"),
    ("must be", "invalid argument"),
    ("required", "missing argument"),
)

_RE_TIMEOUT_S = re.compile(r"timed out after (\d+) second")

# Tool per cui il fallimento *e* un'eccezione del codice eseguito: lì "raised
# ValueError" e la riga giusta, mentre una frase generica butterebbe via l'unica
# informazione utile. Per tutti gli altri il nome di classe resta un ripiego
# dopo :data:`_ERROR_PHRASES`, perche un errore di un tool di Jenny e meglio
# descritto da una frase nostra che dal nome della sua eccezione interna.
_EXC_FIRST_TOOLS = frozenset({"python_exec", "write_stdin"})


def _error_summary(outcome: _Outcome, tool: str = "") -> str:
    """Riga d'errore costruita da costanti nostre + nome di eccezione."""
    if tool in _EXC_FIRST_TOOLS and (raised := outcome.exception_name):
        return f"raised {raised}"
    lowered = outcome.head.lower()
    for marker, phrase in _ERROR_PHRASES:
        if marker in lowered:
            if phrase == "timed out":
                seconds = _probe_int(outcome.head, _RE_TIMEOUT_S)
                if seconds is not None:
                    return f"timed out after {seconds}s"
            return phrase
    name = outcome.exception_name
    return f"failed ({name})" if name else "failed"


# ---------------------------------------------------------------------------
# Formatter per-tool
# ---------------------------------------------------------------------------

_StartFn = Callable[[Mapping[str, Any]], str]
_EndFn = Callable[[Mapping[str, Any], _Outcome], str]

_RE_READ_RANGE = re.compile(r"\(Showing lines (\d+)-(\d+) of (\d+)\.")
_RE_READ_EOF = re.compile(r"(\d+) lines total\)")
_RE_LIST_TRUNC = re.compile(r"showing first (\d+) of (\d+) entries")
_RE_GREP_TOTAL = re.compile(r"total matches: (\d+) in (\d+) files")


def _generic_end(outcome: _Outcome) -> str:
    """Coda onesta quando non sappiamo altro: solo misure."""
    if outcome.blocks is not None:
        return _plural(outcome.blocks, "content block")
    if outcome.size == 0:
        return "no output"
    if outcome.lines > 1:
        return f"{_plural(outcome.lines, 'line')}, {_fmt_bytes(outcome.size)}"
    return _fmt_bytes(outcome.size)


# -- read_file ---------------------------------------------------------------


def _start_read_file(args: Mapping[str, Any]) -> str:
    target = _basename(args.get("path"))
    pages = _arg_text(args, "pages", limit=20)
    if pages:
        return f"reading {target}, pages {pages}"
    offset = _arg_int(args, "offset")
    limit = _arg_int(args, "limit")
    if offset is not None and limit is not None and offset >= 1 and limit >= 1:
        return f"reading {target}, lines {offset}-{offset + limit - 1}"
    if offset is not None and offset > 1:
        return f"reading {target}, from line {offset}"
    return f"reading {target}"


def _end_read_file(args: Mapping[str, Any], outcome: _Outcome) -> str:
    if outcome.blocks is not None:
        return _plural(outcome.blocks, "content block")
    first = _probe_int(outcome.tail, _RE_READ_RANGE, group=1)
    last = _probe_int(outcome.tail, _RE_READ_RANGE, group=2)
    total = _probe_int(outcome.tail, _RE_READ_RANGE, group=3)
    if first is not None and last is not None and total is not None:
        return f"{last - first + 1} of {total} lines"
    total = _probe_int(outcome.tail, _RE_READ_EOF)
    if total is not None:
        offset = _arg_int(args, "offset") or 1
        shown = total - offset + 1 if 1 <= offset <= total else total
        return f"{total} lines" if shown >= total else f"{shown} of {total} lines"
    if "empty file" in outcome.head.lower():
        return "empty file"
    if "unchanged since last read" in outcome.head.lower():
        return "unchanged since last read"
    return _generic_end(outcome)


# -- write_file --------------------------------------------------------------


def _start_write_file(args: Mapping[str, Any]) -> str:
    return f"writing {_basename(args.get('path'))}"


def _end_write_file(args: Mapping[str, Any], outcome: _Outcome) -> str:
    size = _arg_bytes(args, "content")
    if size is None:
        return _generic_end(outcome)
    lines = _arg_lines(args, "content") or 0
    return f"{_fmt_bytes(size)} written, {_plural(lines, 'line')}"


# -- app (la faccia del costruttore) -----------------------------------------


def _start_app(args: Mapping[str, Any]) -> str:
    action = _arg_text(args, "action", limit=12) or "app"
    slug = _arg_text(args, "slug", limit=32)
    target = _arg_text(args, "key", limit=40) if action == "set" else _basename(args.get("path"))
    where = f"{slug} {target}".strip() if target and target != "?" else slug
    return f"app {action} {where}".strip()


def _end_app(args: Mapping[str, Any], outcome: _Outcome) -> str:
    # Solo una misura: il testo della risposta porta i valori letti, che sono
    # contenuto dell'utente.
    return _generic_end(outcome)


# -- board (l'editore dei quaderni) ------------------------------------------


def _start_board(args: Mapping[str, Any]) -> str:
    action = _arg_text(args, "action", limit=16) or "show"
    card = _arg_text(args, "card", limit=32)
    return f"board {action} {card}".strip()


def _end_board(args: Mapping[str, Any], outcome: _Outcome) -> str:
    # Solo una misura: la risposta porta titoli e pagine del quaderno.
    return _generic_end(outcome)


# -- edit_file ---------------------------------------------------------------


def _start_edit_file(args: Mapping[str, Any]) -> str:
    target = _basename(args.get("path"))
    if _arg_bool(args, "replace_all"):
        return f"editing {target}, all occurrences"
    occurrence = _arg_int(args, "occurrence")
    if occurrence is not None and occurrence > 1:
        return f"editing {target}, occurrence {occurrence}"
    return f"editing {target}"


def _end_edit_file(args: Mapping[str, Any], outcome: _Outcome) -> str:
    old = _arg_lines(args, "old_text")
    new = _arg_lines(args, "new_text")
    created = "successfully created" in outcome.head.lower()
    if old is None or new is None:
        return "created" if created else _generic_end(outcome)
    verb = "created" if created else "replaced"
    return f"{verb}, {old} lines -> {new} lines"


# -- list_dir ----------------------------------------------------------------


def _start_list_dir(args: Mapping[str, Any]) -> str:
    target = _display_path(args.get("path"))
    return f"listing {target} recursively" if _arg_bool(args, "recursive") else f"listing {target}"


def _end_list_dir(args: Mapping[str, Any], outcome: _Outcome) -> str:
    shown = _probe_int(outcome.tail, _RE_LIST_TRUNC, group=1)
    total = _probe_int(outcome.tail, _RE_LIST_TRUNC, group=2)
    if shown is not None and total is not None:
        return f"{shown} of {total} entries"
    if " is empty" in outcome.head:
        return "empty directory"
    return _plural(outcome.lines, "entry") if outcome.lines else "empty directory"


# -- journal_append ---------------------------------------------------------


def _start_journal_append(args: Mapping[str, Any]) -> str:
    text = _arg_text(args, "text", limit=48)
    return f"noting “{text}”" if text else "noting a fact"


def _end_journal_append(args: Mapping[str, Any], outcome: _Outcome) -> str:
    head = outcome.head
    if head.startswith("Appended to"):
        return "noted in the journal"
    if "not one" in head or "No journal here" in head:
        return "no project to note it in"
    if "read-only" in head.lower():
        return "read-only: nothing noted"
    return "not noted"


# -- apply_patch -------------------------------------------------------------


def _patch_paths(args: Mapping[str, Any]) -> list[str]:
    edits = args.get("edits")
    if not isinstance(edits, (list, tuple)):
        return []
    paths: list[str] = []
    for edit in edits:
        if isinstance(edit, Mapping):
            paths.append(_basename(edit.get("path")))
    return paths


def _start_apply_patch(args: Mapping[str, Any]) -> str:
    paths = _patch_paths(args)
    unique = list(dict.fromkeys(paths))
    if not unique:
        target = "files"
    elif len(unique) == 1:
        target = unique[0]
    else:
        target = f"{len(unique)} files"
    return f"patching {target} (dry run)" if _arg_bool(args, "dry_run") else f"patching {target}"


def _end_apply_patch(args: Mapping[str, Any], outcome: _Outcome) -> str:
    hunks = len(_patch_paths(args))
    if not hunks:
        return _generic_end(outcome)
    verb = "validated" if _arg_bool(args, "dry_run") else "applied"
    files = len(set(_patch_paths(args)))
    tail = f" in {files} files" if files > 1 else ""
    return f"{_plural(hunks, 'hunk')} {verb}{tail}"


# -- web_search --------------------------------------------------------------


def _start_web_search(args: Mapping[str, Any]) -> str:
    query = _arg_text(args, "query", limit=60)
    return f'searching "{query}"' if query else "searching the web"


def _end_web_search(args: Mapping[str, Any], outcome: _Outcome) -> str:
    if "no results for" in outcome.head.lower():
        return "no results"
    return _plural(outcome.enumerated, "result") if outcome.enumerated else "no results"


# -- web_fetch ---------------------------------------------------------------


def _start_web_fetch(args: Mapping[str, Any]) -> str:
    return f"opening {_display_url(args.get('url'))}"


def _end_web_fetch(args: Mapping[str, Any], outcome: _Outcome) -> str:
    if outcome.size == 0:
        return "empty page"
    return f"{_fmt_bytes(outcome.size)}, {_plural(outcome.lines, 'line')}"


# -- browser_* ---------------------------------------------------------------
#
# La sessione interattiva parla di **pagine**, non di byte: quel che interessa a
# chi guarda scorrere il subagent e' dove si trova e quanti appigli ha, non
# quanto pesa la risposta.


def _start_browser_open(args: Mapping[str, Any]) -> str:
    return f"opening {_display_url(args.get('url'))} in the browser"


def _end_browser(args: Mapping[str, Any], outcome: _Outcome) -> str:
    return _plural(outcome.lines, "element") if outcome.lines else "empty page"


def _start_browser_snapshot(args: Mapping[str, Any]) -> str:
    filt = _arg_text(args, "filter", limit=40)
    return f'looking for "{filt}" on the page' if filt else "looking at the page"


def _start_browser_do(args: Mapping[str, Any]) -> str:
    steps = args.get("steps")
    n = len(steps) if isinstance(steps, list) else 0
    if n == 1 and isinstance(steps[0], Mapping):
        return f"{steps[0].get('action', 'acting')} on the page"
    return f"running {_plural(n, 'step')} on the page" if n else "acting on the page"


def _start_browser_read(args: Mapping[str, Any]) -> str:
    ref = _arg_text(args, "ref", limit=16)
    return f"reading {ref}" if ref else "reading the page"


def _end_browser_read(args: Mapping[str, Any], outcome: _Outcome) -> str:
    return _fmt_bytes(outcome.size) if outcome.size else "empty"


def _start_browser_close(args: Mapping[str, Any]) -> str:
    return "closing the browser"


def _end_browser_close(args: Mapping[str, Any], outcome: _Outcome) -> str:
    return "closed"


# -- download_file -----------------------------------------------------------


def _start_download_file(args: Mapping[str, Any]) -> str:
    host = _display_url(args.get("url"), limit=48)
    name = _basename(args.get("filename"), limit=32)
    if name and name != "(no path)":
        return f"downloading {name} from {host}"
    return f"downloading from {host}"


def _end_download_file(args: Mapping[str, Any], outcome: _Outcome) -> str:
    return "saved to workspace downloads"


# -- python_exec -------------------------------------------------------------


def _start_python_exec(args: Mapping[str, Any]) -> str:
    function = _ident(args.get("function"))
    if function:
        return f"calling {function}()"
    lines = _arg_lines(args, "code")
    if lines:
        return f"running python ({_plural(lines, 'line')})"
    return "running python"


def _end_python_exec(args: Mapping[str, Any], outcome: _Outcome) -> str:
    name = outcome.exception_name
    if name:
        return f"raised {name}"
    if "session_id:" in outcome.tail:
        return "still running in a session"
    if outcome.size == 0 or "(no output)" in outcome.head:
        return "ok, no output"
    return f"ok, {_plural(outcome.lines, 'line')} of output"


# -- write_stdin / list_exec_sessions ---------------------------------------


def _start_write_stdin(args: Mapping[str, Any]) -> str:
    session = _ident(args.get("session_id"), limit=16)
    label = f"exec session {session}" if session else "exec session"
    if _arg_bool(args, "terminate"):
        return f"terminating {label}"
    if _arg_text(args, "wait_for", limit=1):
        return f"waiting on {label}"
    return f"polling {label}"


def _end_write_stdin(args: Mapping[str, Any], outcome: _Outcome) -> str:
    name = outcome.exception_name
    if name:
        return f"raised {name}"
    state = "still running" if "session_id:" in outcome.tail else "session finished"
    return f"{state}, {_plural(outcome.lines, 'line')} of output"


def _start_list_exec_sessions(args: Mapping[str, Any]) -> str:
    return "listing exec sessions"


def _end_list_exec_sessions(args: Mapping[str, Any], outcome: _Outcome) -> str:
    if "no active exec sessions" in outcome.head.lower():
        return "no active sessions"
    return _plural(outcome.lines, "active session")


# -- find_files / grep -------------------------------------------------------


def _search_filter(args: Mapping[str, Any]) -> str:
    glob = _arg_text(args, "glob", limit=24)
    if glob:
        return f" matching {glob}"
    kind = _arg_text(args, "type", limit=12)
    if kind:
        return f" of type {kind}"
    query = _arg_text(args, "query", limit=32)
    return f' named "{query}"' if query else ""


def _start_find_files(args: Mapping[str, Any]) -> str:
    where = _display_path(args.get("path") or ".")
    return f"finding files in {where}{_search_filter(args)}"


def _end_find_files(args: Mapping[str, Any], outcome: _Outcome) -> str:
    if "no files found" in outcome.head.lower():
        return "no matches"
    return _plural(outcome.lines, "match")


def _start_grep(args: Mapping[str, Any]) -> str:
    pattern = _arg_text(args, "pattern", limit=50)
    where = _display_path(args.get("path") or ".")
    if pattern:
        return f'grepping {where} for "{pattern}"'
    return f"grepping {where}"


def _end_grep(args: Mapping[str, Any], outcome: _Outcome) -> str:
    if "no matches found" in outcome.head.lower():
        return "no matches"
    total = _probe_int(outcome.tail, _RE_GREP_TOTAL, group=1)
    files = _probe_int(outcome.tail, _RE_GREP_TOTAL, group=2)
    if total is not None and files is not None:
        return f"{_plural(total, 'match')} in {_plural(files, 'file')}"
    mode = _arg_text(args, "output_mode", limit=24)
    unit = "file" if mode in ("", "files_with_matches") else "match"
    return _plural(outcome.lines, unit)


# -- get_recent_logs / get_source / get_location ----------------------------


def _start_get_recent_logs(args: Mapping[str, Any]) -> str:
    module = _arg_text(args, "module_filter", limit=32)
    return f"reading recent logs (filter {module})" if module else "reading recent logs"


def _end_get_recent_logs(args: Mapping[str, Any], outcome: _Outcome) -> str:
    return _plural(outcome.lines, "log line")


def _start_get_source(args: Mapping[str, Any]) -> str:
    target = _arg_text(args, "target", limit=64)
    return f"reading source of {target}" if target else "reading jenny source"


def _end_get_source(args: Mapping[str, Any], outcome: _Outcome) -> str:
    return f"{_plural(outcome.lines, 'line')} of source"


def _start_get_location(args: Mapping[str, Any]) -> str:
    if _arg_bool(args, "precise"):
        return "getting device location (fresh GPS fix)"
    return "getting device location"


def _end_get_location(args: Mapping[str, Any], outcome: _Outcome) -> str:
    # Deliberatamente senza coordinate ne toponimo: sarebbero contenuto del
    # risultato *e* un dato personale. Il pannello dice che e stata risolta, non
    # dove si trova l'utente.
    return "location resolved"


# -- ssh_hosts / ssh_exec / ssh_job / ssh_transfer ---------------------------
#
# I quattro tool dello scope ``remote`` (tipo ``sysadmin``). Qui la regola 1 —
# mai il contenuto — vale piu che altrove: il risultato di ``ssh_exec`` e di un
# ``poll`` e output di una macchina di produzione, e l'activity stream finisce in
# una UI e su disco. Quindi da questi esiti escono solo misure (exit code,
# righe, byte) e frasi nostre; l'unica cosa che resta leggibile e cio che
# **abbiamo chiesto noi**: alias dell'host, comando, job_id, tutti letti dagli
# *argomenti*, mai dal risultato.

_RE_SSH_HOST_COUNT = re.compile(r"^(\d+) SSH host\(s\) registered")
_RE_SSH_EXIT_CODE = re.compile(r"^exit code: (\d+)", re.MULTILINE)
_RE_SSH_JOB_EXIT = re.compile(r"\(exit code (\d+)\)")
_RE_SSH_JOB_COUNT = re.compile(r"^(\d+) job\(s\) on ")
_RE_SSH_BYTES = re.compile(r"\((\d+) bytes\)")


def _ssh_host(args: Mapping[str, Any]) -> str:
    """Alias dell'host, o ``"a remote host"``. Mai un indirizzo: e un alias."""
    return _arg_text(args, "host", limit=32) or "a remote host"


# Un job id vero e ``<alias>-<8 hex>`` (vedi ``ssh_jobs._new_job_id``): comincia
# spesso per cifra e contiene un trattino, quindi ``_ident`` lo decapiterebbe.
# Il charset e chiuso ed e lo stesso che ``ssh_jobs`` gia impone.
_JOB_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")
_MAX_JOB_ID_CHARS = 24


def _ssh_job_id(args: Mapping[str, Any]) -> str:
    """Job id letto dagli *argomenti*, su charset chiuso. Mai dal risultato."""
    value = args.get("job_id")
    if not isinstance(value, str):
        return ""
    match = _JOB_ID_RE.search(value)
    return match.group(0)[:_MAX_JOB_ID_CHARS] if match else ""


def _start_ssh_hosts(args: Mapping[str, Any]) -> str:
    return "listing ssh hosts"


def _end_ssh_hosts(args: Mapping[str, Any], outcome: _Outcome) -> str:
    count = _probe_int(outcome.head, _RE_SSH_HOST_COUNT)
    if count is not None:
        return _plural(count, "host")
    if "switched off" in outcome.head.lower():
        return "ssh is switched off"
    return "no hosts configured"


def _start_ssh_exec(args: Mapping[str, Any]) -> str:
    command = _arg_text(args, "command", limit=60)
    host = _ssh_host(args)
    return f"running on {host}: {command}" if command else f"running a command on {host}"


def _end_ssh_exec(args: Mapping[str, Any], outcome: _Outcome) -> str:
    code = _probe_int(outcome.head, _RE_SSH_EXIT_CODE)
    head = f"exit {code}" if code is not None else "finished"
    if "(no output)" in outcome.head:
        return f"{head}, no output"
    # Le righe di intestazione che ``_render_exec`` aggiunge sempre ("exit
    # code: N" e le etichette) non sono output del comando: si scontano quelle
    # che si vedono, cosi il numero riferito e quello che l'utente conta a
    # schermo e non tre di piu.
    overhead = (
        1
        + ("stdout:" in outcome.head)
        + ("stderr:" in outcome.head or "stderr:" in outcome.tail)
    )
    text = f"{head}, {_plural(max(outcome.lines - overhead, 0), 'line')}"
    if "characters were dropped" in outcome.tail:
        text += ", truncated"
    return text


def _start_ssh_job(args: Mapping[str, Any]) -> str:
    action = _arg_text(args, "action", limit=8).lower()
    host = _ssh_host(args)
    if action == "start":
        command = _arg_text(args, "command", limit=60)
        return f"started job on {host}: {command}" if command else f"starting a job on {host}"
    job = _ssh_job_id(args)
    label = f"job {job}" if job else "job"
    if action == "poll":
        return f"polling {label} on {host}"
    if action == "stop":
        return f"stopping {label} on {host}"
    if action == "list":
        return f"listing jobs on {host}"
    return f"ssh job on {host}"


# Code di riga che ``_render_poll`` aggiunge dopo l'output (una riga ciascuna):
# non sono output del job e non vanno contate come tale.
_SSH_POLL_NOTES = (
    "poll again right away",
    "Do not poll in a tight loop",
    "The process disappeared",
)


def _ssh_job_state(outcome: _Outcome) -> str:
    """Stato di un job letto dai marker del render, non dal suo output."""
    code = _probe_int(outcome.head, _RE_SSH_JOB_EXIT)
    if code is not None:
        return f"finished, exit {code}"
    if "The process disappeared" in outcome.tail:
        return "process gone"
    return "still running"


def _end_ssh_job(args: Mapping[str, Any], outcome: _Outcome) -> str:
    action = _arg_text(args, "action", limit=8).lower()
    if action == "start":
        # Il job_id lo genera il risultato, non gli argomenti: non lo si estrae
        # (regola 3) e non serve — il pannello dice che il job e partito, l'id
        # ce l'ha il modello.
        return "job started"
    if action == "list":
        count = _probe_int(outcome.head, _RE_SSH_JOB_COUNT)
        return _plural(count, "job") if count is not None else "no jobs"
    job = _ssh_job_id(args)
    label = f"job {job}: " if job else ""
    if action == "stop":
        return f"{label}stop signalled"
    if "no new output since the last poll" in outcome.head:
        return f"{label}no new output, {_ssh_job_state(outcome)}"
    # Come sopra: intestazione, etichetta "new output:" e le note finali non
    # sono output del job.
    overhead = 2 + sum(note in outcome.tail for note in _SSH_POLL_NOTES)
    new_lines = max(outcome.lines - overhead, 0)
    return f"{label}{_plural(new_lines, 'new line')}, {_ssh_job_state(outcome)}"


def _start_ssh_transfer(args: Mapping[str, Any]) -> str:
    host = _ssh_host(args)
    if _arg_text(args, "direction", limit=8).lower() == "down":
        return f"downloading {_basename(args.get('remote_path'), limit=32)} from {host}"
    return f"uploading {_basename(args.get('local_path'), limit=32)} to {host}"


def _end_ssh_transfer(args: Mapping[str, Any], outcome: _Outcome) -> str:
    verb = "downloaded" if _arg_text(args, "direction", limit=8).lower() == "down" else "uploaded"
    size = _probe_int(outcome.head, _RE_SSH_BYTES)
    return f"{verb} {_fmt_bytes(size)}" if size is not None else verb


# Registro: un tool coperto ha una riga qui. Copre tutto lo scope ``subagent``,
# tutto lo scope ``remote`` e gli scope ``apps`` e ``notebook`` di ``agent/tools/loader.py`` (e quindi ogni
# allowlist di ``agent/agent_types.py``, che ne e un sottoinsieme).
_FORMATTERS: dict[str, tuple[_StartFn, _EndFn]] = {
    "read_file": (_start_read_file, _end_read_file),
    "write_file": (_start_write_file, _end_write_file),
    "edit_file": (_start_edit_file, _end_edit_file),
    "list_dir": (_start_list_dir, _end_list_dir),
    "apply_patch": (_start_apply_patch, _end_apply_patch),
    "journal_append": (_start_journal_append, _end_journal_append),
    "app": (_start_app, _end_app),
    "board": (_start_board, _end_board),
    "web_search": (_start_web_search, _end_web_search),
    "web_fetch": (_start_web_fetch, _end_web_fetch),
    "browser_open": (_start_browser_open, _end_browser),
    "browser_snapshot": (_start_browser_snapshot, _end_browser),
    "browser_do": (_start_browser_do, _end_browser),
    "browser_read": (_start_browser_read, _end_browser_read),
    "browser_close": (_start_browser_close, _end_browser_close),
    "download_file": (_start_download_file, _end_download_file),
    "python_exec": (_start_python_exec, _end_python_exec),
    "write_stdin": (_start_write_stdin, _end_write_stdin),
    "list_exec_sessions": (_start_list_exec_sessions, _end_list_exec_sessions),
    "find_files": (_start_find_files, _end_find_files),
    "grep": (_start_grep, _end_grep),
    "get_recent_logs": (_start_get_recent_logs, _end_get_recent_logs),
    "get_source": (_start_get_source, _end_get_source),
    "get_location": (_start_get_location, _end_get_location),
    "ssh_hosts": (_start_ssh_hosts, _end_ssh_hosts),
    "ssh_exec": (_start_ssh_exec, _end_ssh_exec),
    "ssh_job": (_start_ssh_job, _end_ssh_job),
    "ssh_transfer": (_start_ssh_transfer, _end_ssh_transfer),
}


def known_tools() -> frozenset[str]:
    """Tool con un formatter dedicato. Gli altri passano dal fallback."""
    return frozenset(_FORMATTERS)


def _fallback_start(tool: str, args: Mapping[str, Any]) -> str:
    """Tool ignoto: nome del tool piu le **chiavi** degli argomenti.

    Onesto e utile senza inventare: i *valori* non entrano (sarebbero contenuto),
    le chiavi passano dal charset degli identificatori e sono al massimo tre.
    Un tool aggiunto in futuro degrada qui, non in un traceback ne in un dump.
    """
    keys = [k for key in list(args)[:3] if (k := _ident(key, limit=24))]
    label = _ident(tool, limit=40) or "tool"
    return f"calling {label} ({', '.join(keys)})" if keys else f"calling {label}"


def format_tool_start(tool: str, arguments: Any = None) -> str:
    """Riga che descrive l'*azione* iniziata. Funzione pura, non solleva mai."""
    args = _as_mapping(arguments)
    entry = _FORMATTERS.get(tool)
    try:
        text = entry[0](args) if entry is not None else _fallback_start(tool, args)
    except Exception as exc:  # noqa: BLE001 — un bug di formattazione non e un errore del tool
        logger.warning("Activity start formatter failed for tool {}: {}", tool, exc)
        text = f"calling {_ident(tool, limit=40) or 'tool'}"
    return _clean(text)


def format_tool_end(
    tool: str,
    arguments: Any = None,
    result: Any = None,
    *,
    error: Any = None,
) -> str:
    """Riga che descrive l'*esito*. Funzione pura, non solleva mai.

    ``result`` e ``error`` sono alternativi: se ``error`` e valorizzato il
    risultato viene ignorato. In entrambi i casi il corpo non viene letto —
    :func:`_build_outcome` lo riduce a misure prima che un formatter lo veda.
    """
    args = _as_mapping(arguments)
    try:
        outcome = _build_outcome(tool, result, error)
        if not outcome.ok:
            return _clean(_error_summary(outcome, tool))
        entry = _FORMATTERS.get(tool)
        text = entry[1](args, outcome) if entry is not None else _generic_end(outcome)
    except Exception as exc:  # noqa: BLE001 — vedi format_tool_start
        logger.warning("Activity end formatter failed for tool {}: {}", tool, exc)
        text = "completed"
    return _clean(text)


# ---------------------------------------------------------------------------
# Evento e ring buffer
# ---------------------------------------------------------------------------


def _coerce_status(value: Any) -> str | None:
    return value if value in (STATUS_OK, STATUS_ERROR, DIGEST_STATUS_INCOMPLETE) else None


def _coerce_duration(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        ms = int(value)
    except (OverflowError, ValueError):
        return None
    return ms if 0 <= ms <= _MAX_PROBE_INT else None


def _make_event(
    *,
    seq: int,
    ts: float,
    kind: str,
    summary: str,
    name: Any = None,
    call_id: Any = None,
    status: Any = None,
    duration_ms: Any = None,
) -> dict[str, Any]:
    """Evento nella forma di contratto. Tutti i campi sono JSON-serializzabili."""
    return {
        "seq": seq,
        "ts": ts,
        "kind": kind,
        "name": _clean(name, limit=64) or None if name is not None else None,
        # Id di chiamata del provider: passa dallo stesso ripulitore del nome
        # perche arriva da un payload del modello e finisce in un JSON su disco.
        "call_id": _clean(call_id, limit=_MAX_CALL_ID_CHARS) or None,
        "status": _coerce_status(status),
        "summary": _clean(summary) or "(no detail)",
        "duration_ms": _coerce_duration(duration_ms),
    }


def _normalize_event(raw: Any, *, seq: int = 0) -> dict[str, Any] | None:
    """Rilegge un evento arrivato da fuori (disco, transport) senza sollevare."""
    if not isinstance(raw, Mapping):
        return None
    kind = raw.get("kind")
    if not isinstance(kind, str) or (
        kind not in ACTIVITY_KINDS and kind != DIGEST_KIND_TOOL
    ):
        kind = KIND_FALLBACK
    ts = raw.get("ts")
    raw_seq = raw.get("seq")
    return _make_event(
        seq=raw_seq if isinstance(raw_seq, int) and not isinstance(raw_seq, bool) else seq,
        ts=float(ts) if isinstance(ts, (int, float)) and not isinstance(ts, bool) else 0.0,
        kind=kind,
        summary=raw.get("summary", ""),
        name=raw.get("name"),
        call_id=raw.get("call_id"),
        status=raw.get("status"),
        duration_ms=raw.get("duration_ms"),
    )


@dataclass(frozen=True, slots=True)
class ActivityWindow:
    """Finestra restituita da :meth:`SubagentActivityLog.tail_window`.

    Esiste perche ``tail()`` da sola non permette di distinguere "non e ancora
    successo niente" da "ti sei perso l'inizio": la seconda e un buco, e un buco
    va detto, non nascosto.
    """

    events: list[dict[str, Any]]
    since_seq: int = 0
    # ``seq`` del primo e dell'ultimo evento *restituito* (0 se nessuno).
    first_seq: int = 0
    last_seq: int = 0
    # ``seq`` massimo mai assegnato al task: sopravvive all'eviction, quindi dice
    # quanti eventi sono esistiti anche quando il ring li ha già buttati.
    latest_seq: int = 0
    # Eventi espulsi dal ring da quando il task e iniziato (diagnostica).
    dropped: int = 0

    @property
    def gap(self) -> bool:
        """``True`` se tra ``since_seq`` e il primo evento restituito manca qualcosa.

        Unica regola per le due cause possibili (eviction dal ring, oppure
        troncamento per ``limit``): il primo ``seq`` consegnato dovrebbe essere
        ``since_seq + 1``; se e maggiore, il client deve risincronizzare. Con
        zero eventi ``first_seq`` e 0 e ``gap`` e ``False``, che e esattamente il
        caso "non e ancora successo niente".
        """
        return self.first_seq > self.since_seq + 1

    def to_dict(self) -> dict[str, Any]:
        """Payload per il transport (fase successiva)."""
        return {
            "events": self.events,
            "since_seq": self.since_seq,
            "first_seq": self.first_seq,
            "last_seq": self.last_seq,
            "latest_seq": self.latest_seq,
            "dropped": self.dropped,
            "gap": self.gap,
        }


@dataclass(slots=True)
class _TaskRing:
    events: deque[dict[str, Any]] = field(
        default_factory=lambda: deque(maxlen=RING_CAPACITY)
    )
    next_seq: int = 1
    dropped: int = 0
    # Ultima ``append`` (orologio di parete), usata solo per scegliere chi
    # sfrattare quando si raggiunge :data:`MAX_TRACKED_TASKS`.
    touched_at: float = 0.0


class SubagentActivityLog:
    """Ring buffer in RAM dell'attivita per task. Nessuna persistenza.

    Il durevole e il digest (:class:`SubagentDigestStore`) piu il record Tier-1:
    qui c'e solo cio che serve a un pannello aperto adesso, con un tetto per task
    (:data:`RING_CAPACITY`) perche il gateway gira su un telefono.

    Protetto da un lock: gli eventi nascono in callback che possono arrivare da
    thread diversi (un tool eseguito in executor) e il costo di un lock su
    qualche operazione di dict e irrilevante rispetto al rischio di un ring
    corrotto.
    """

    def __init__(
        self,
        *,
        capacity: int = RING_CAPACITY,
        max_tasks: int = MAX_TRACKED_TASKS,
    ) -> None:
        self._capacity = max(1, capacity)
        self._max_tasks = max(1, max_tasks)
        self._tasks: dict[str, _TaskRing] = {}
        self._lock = threading.Lock()

    # -- write ---------------------------------------------------------------

    def append(
        self,
        task_id: str,
        kind: str,
        *,
        summary: str = "",
        name: Any = None,
        call_id: Any = None,
        status: Any = None,
        duration_ms: Any = None,
    ) -> dict[str, Any]:
        """Registra un evento e lo ritorna nella forma di contratto.

        **Non solleva per ragioni ordinarie**, per progetto: un bug della
        telemetria non deve poter uccidere un subagent. Task ignoto, argomenti
        di tipo sbagliato, summary vuoto, ``duration_ms`` negativo — tutto
        degrada a un evento valido.

        Un ``kind`` non riconosciuto e un bug del *produttore* (le fasi
        successive passano letterali), quindi:

        * l'evento **non** viene scartato — il summary e comunque l'informazione
          utile, e scartarlo consumerebbe un ``seq`` o, peggio, creerebbe un
          buco fantasma;
        * il ``kind`` viene portato a :data:`KIND_FALLBACK` (``phase``), il kind
          piu neutro: cosi ogni renderer sa disegnarlo senza un ramo nuovo;
        * il valore rifiutato finisce in un log WARNING, che e il canale giusto
          per un bug di programmazione — non il summary, che e testo per l'utente.
        """
        if not isinstance(task_id, str) or not task_id:
            # Nessun task a cui attribuirlo: l'evento non ha casa. Si logga e si
            # ritorna una forma valida, perche il chiamante puo star costruendo
            # un payload da spedire.
            logger.warning("Activity event without task id (kind={!r})", kind)
            return _make_event(
                seq=0, ts=time.time(), kind=KIND_FALLBACK, summary=summary,
                name=name, call_id=call_id, status=status, duration_ms=duration_ms,
            )
        if not isinstance(kind, str) or kind not in ACTIVITY_KINDS:
            logger.warning(
                "Unknown activity kind {!r} for task {}; recording as {}",
                kind, task_id, KIND_FALLBACK,
            )
            kind = KIND_FALLBACK

        now = time.time()
        with self._lock:
            ring = self._tasks.get(task_id)
            if ring is None:
                if len(self._tasks) >= self._max_tasks:
                    self._evict_locked()
                ring = _TaskRing(events=deque(maxlen=self._capacity))
                self._tasks[task_id] = ring
            ring.touched_at = now
            event = _make_event(
                seq=ring.next_seq,
                ts=now,
                kind=kind,
                summary=summary,
                name=name,
                call_id=call_id,
                status=status,
                duration_ms=duration_ms,
            )
            ring.next_seq += 1
            if len(ring.events) == self._capacity:
                # ``deque(maxlen=...)`` espelle da sola: contiamo l'espulsione
                # qui perche ``dropped`` e cio che rende il buco spiegabile.
                ring.dropped += 1
            ring.events.append(event)
            return event

    def _evict_locked(self) -> None:
        """Sfratta il ring toccato meno recentemente. Chiamare col lock preso."""
        victim = min(self._tasks, key=lambda key: self._tasks[key].touched_at)
        logger.debug("Activity ring evicted for task {} (tracking cap reached)", victim)
        self._tasks.pop(victim, None)

    def drop(self, task_id: str) -> None:
        """Dimentica il ring di un task (chiamata dopo aver scritto il digest)."""
        with self._lock:
            self._tasks.pop(task_id, None)

    # -- read ----------------------------------------------------------------

    def tail(
        self,
        task_id: str,
        *,
        since_seq: int = 0,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Eventi con ``seq > since_seq``.

        ``since_seq=0`` ritorna tutto cio che il ring ha ancora: e cio che fa
        apparire subito del contenuto quando si apre il modal, invece di una
        lista vuota in attesa del prossimo evento. Con ``limit`` si tengono i
        **piu recenti**, e il buco che ne risulta e visibile via
        :attr:`ActivityWindow.gap`.
        """
        return self.tail_window(task_id, since_seq=since_seq, limit=limit).events

    def tail_window(
        self,
        task_id: str,
        *,
        since_seq: int = 0,
        limit: int | None = None,
    ) -> ActivityWindow:
        """Come :meth:`tail` ma con i metadati di risincronizzazione."""
        since = since_seq if isinstance(since_seq, int) and since_seq > 0 else 0
        with self._lock:
            ring = self._tasks.get(task_id)
            if ring is None:
                return ActivityWindow(events=[], since_seq=since)
            selected = [dict(e) for e in ring.events if e["seq"] > since]
            latest = ring.next_seq - 1
            dropped = ring.dropped
        if limit is not None and limit >= 0:
            selected = selected[-limit:] if limit else []
        return ActivityWindow(
            events=selected,
            since_seq=since,
            first_seq=selected[0]["seq"] if selected else 0,
            last_seq=selected[-1]["seq"] if selected else 0,
            latest_seq=latest,
            dropped=dropped,
        )

    def digest(self, task_id: str) -> list[dict[str, Any]]:
        """Condensa post-mortem del task (vedi :func:`build_digest`)."""
        with self._lock:
            ring = self._tasks.get(task_id)
            events = [dict(e) for e in ring.events] if ring is not None else []
        return build_digest(events)

    def task_ids(self) -> list[str]:
        """Task con un ring vivo (diagnostica/shutdown)."""
        with self._lock:
            return list(self._tasks)


# ---------------------------------------------------------------------------
# Digest
# ---------------------------------------------------------------------------


def _delta_ms(start_ts: Any, end_ts: Any) -> int | None:
    if not isinstance(start_ts, (int, float)) or not isinstance(end_ts, (int, float)):
        return None
    delta = (float(end_ts) - float(start_ts)) * 1000.0
    return int(delta) if 0 <= delta <= _MAX_PROBE_INT else None


def _close_pending_slot(
    end: Mapping[str, Any],
    by_call: dict[str, int],
    by_name: dict[str, list[int]],
    closed: set[int],
) -> int | None:
    """Slot dello start che questo ``tool_end`` chiude, o ``None``.

    Precedenza al ``call_id``: e l'unica corrispondenza che resta corretta con
    piu chiamate dello stesso tool in volo. Il FIFO per nome e il ripiego per gli
    eventi che non lo portano — inclusi i digest scritti prima che il campo
    esistesse.

    INVARIANTE: ``closed`` e il registro dei soli slot gia accoppiati, ed e la
    ragione per cui i due indici possono restare indipendenti. Uno slot chiuso
    per ``call_id`` resta nella coda per nome (e viceversa), quindi senza questa
    guardia un secondo end lo accoppierebbe di nuovo e una chiamata diventerebbe
    due voci nel digest.
    """
    if (call_id := end.get("call_id")):
        slot = by_call.pop(call_id, None)
        if slot is not None and slot not in closed:
            closed.add(slot)
            return slot
    slots = by_name.get(end.get("name") or "")
    while slots:
        slot = slots.pop(0)
        if slot not in closed:
            closed.add(slot)
            return slot
    return None


def build_digest(events: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Condensa una sequenza di eventi vivi nella forma persistita.

    Il ring vivo e la cosa sbagliata da persistere: 200 eventi con estratti di
    ragionamento sono rumore in un post-mortem. Qui:

    * ogni coppia ``tool_start``/``tool_end`` diventa **un** evento
      ``kind="tool"`` con lo status e la durata della fine. L'accoppiamento usa
      il ``call_id`` del provider quando c'e — quindi e *esatto* anche con tre
      ``web_fetch`` in volo nello stesso batch — e degrada a FIFO per nome del
      tool quando manca, che e cio che facevano tutti gli eventi prima che il
      campo esistesse. Uno start rimasto senza fine (subagent morto a meta
      chiamata) resta comunque visibile con status
      :data:`DIGEST_STATUS_INCOMPLETE`;
    * i ``thinking`` spariscono e ne resta **uno** aggregato, posizionato dove
      stava il primo, con il tempo totale: gli estratti di ragionamento sono la
      parte piu voluminosa e meno utile a posteriori;
    * i ``phase`` restano solo quando cambiano (il ciclo
      ``awaiting_tools``/``tools_completed`` si ripete a ogni iterazione: tenerli
      tutti e rumore, tenere le transizioni conserva la struttura);
    * degli ``iteration`` resta solo l'ultimo, che porta il conteggio finale —
      quelli intermedi non aggiungono nulla ora che i tool sono collassati;
    * ``message_in``, ``result`` ed ``error`` restano tutti: sono pochi e
      spiegano perche il subagent ha cambiato comportamento o come e finito;
    * i ``seq`` sono rinumerati da 1.
    """
    out: list[dict[str, Any] | None] = []
    start_text: dict[int, str] = {}
    # Due indici sugli stessi start aperti, non due politiche: ``by_call`` e la
    # corrispondenza esatta, ``by_name`` il ripiego FIFO. ``closed`` e cio che
    # permette loro di restare indipendenti (vedi ``_close_pending_slot``).
    by_call: dict[str, int] = {}
    by_name: dict[str, list[int]] = {}
    closed: set[int] = set()
    think_slot: int | None = None
    # ``think_count`` conta i *segmenti* di ragionamento, non gli eventi: gli
    # eventi arrivano ogni ~400ms anche a testo invariato, quindi "167 steps" per
    # tre minuti di lavoro contava campioni e non passi. Un segmento e una pausa
    # di ragionamento vera.
    think_count = 0
    think_ms = 0
    think_segment_ms = 0
    iteration_slot: int | None = None
    last_phase: str | None = None

    for raw in events:
        event = _normalize_event(raw)
        if event is None:
            continue
        kind = event["kind"]

        if kind == KIND_TOOL_START:
            slot = len(out)
            start_text[slot] = event["summary"]
            out.append({
                **event,
                "kind": DIGEST_KIND_TOOL,
                "status": DIGEST_STATUS_INCOMPLETE,
                "summary": _clean(f"{event['summary']} (no result recorded)"),
            })
            by_name.setdefault(event["name"] or "", []).append(slot)
            if event["call_id"]:
                by_call[event["call_id"]] = slot
            continue

        if kind == KIND_TOOL_END:
            slot = _close_pending_slot(event, by_call, by_name, closed)
            if slot is not None:
                opened = out[slot]
                assert opened is not None  # noqa: S101 — lo slot e nostro
                out[slot] = {
                    "seq": opened["seq"],
                    "ts": opened["ts"],
                    "kind": DIGEST_KIND_TOOL,
                    "name": event["name"] or opened["name"],
                    "call_id": event["call_id"] or opened["call_id"],
                    "status": event["status"],
                    # Il solo summary di fine ("120 of 412 lines") non dice
                    # *cosa* e stato fatto: in un post-mortem serve l'azione.
                    "summary": _clean(f"{start_text[slot]} -> {event['summary']}"),
                    "duration_ms": event["duration_ms"] or _delta_ms(
                        opened["ts"], event["ts"]
                    ),
                }
            else:
                out.append({**event, "kind": DIGEST_KIND_TOOL})
            continue

        if kind == KIND_THINKING:
            # ``duration_ms`` di un evento thinking e l'elapsed *dall'inizio del
            # segmento*, non un delta: dentro un segmento cresce (1s, 2s, 3s...).
            # Sommarli dava un numero triangolare — un subagent da tre minuti
            # veniva riassunto come "263m 1s total". Il totale vero e la somma dei
            # massimi per segmento, e il confine di segmento si legge dai dati:
            # l'elapsed che *torna indietro* e un clock ripartito da zero.
            elapsed = event["duration_ms"] or 0
            if elapsed < think_segment_ms:
                think_ms += think_segment_ms
                think_count += 1
            think_segment_ms = elapsed
            if think_slot is None:
                think_slot = len(out)
                out.append(dict(event))
            continue

        if kind == KIND_ITERATION:
            if iteration_slot is not None:
                out[iteration_slot] = None
            iteration_slot = len(out)
            out.append(dict(event))
            continue

        if kind == KIND_PHASE:
            if event["summary"] == last_phase:
                continue
            last_phase = event["summary"]
            out.append(dict(event))
            continue

        out.append(dict(event))

    if think_slot is not None:
        aggregate = out[think_slot]
        assert aggregate is not None  # noqa: S101 — lo slot e nostro
        # L'ultimo segmento non ha un successore che lo chiuda: si chiude qui.
        think_ms += think_segment_ms
        think_count += 1
        label = _plural(think_count, "step")
        if think_ms:
            label += f", {_fmt_duration(think_ms)} total"
        aggregate["summary"] = _clean(f"thinking: {label}")
        aggregate["duration_ms"] = think_ms or None
        aggregate["name"] = None
        aggregate["status"] = None

    kept = [e for e in out if e is not None]
    if len(kept) > MAX_DIGEST_EVENTS:
        kept = kept[-MAX_DIGEST_EVENTS:]
    for index, event in enumerate(kept, 1):
        event["seq"] = index
    return kept


@dataclass(frozen=True, slots=True)
class DigestMeta:
    """Cio che il record Tier-1 deve sapere del digest senza aprirlo."""

    events: int = 0
    bytes: int = 0

    @property
    def exists(self) -> bool:
        return self.events > 0 and self.bytes > 0


# Traversal guard: il task id diventa un nome di file, quindi passa da un
# charset chiuso invece che da una sanitizzazione per sottrazione. ``..`` e ``/``
# non sopravvivono a questa sostituzione.
_UNSAFE_TASK_ID_RE = re.compile(r"[^A-Za-z0-9_-]")
_MAX_TASK_ID_CHARS = 120


class SubagentDigestStore:
    """Un file JSON per task sotto ``<workspace>/subagents/activity/``.

    Scritto **una volta** alla transizione terminale, riletto solo quando
    qualcuno espande il blocco: il digest non sta dentro il JSONL dei record
    Tier-1 proprio per questo — quel file viene riscritto per intero a ogni
    transizione, e ~16 KB di digest per subagent lo trasformerebbero in
    centinaia di KB riscritti ogni volta.

    Scritture atomiche con fsync come il resto del codebase
    (``agent/memory.py``, ``session/manager.py``). Letture tolleranti: un file
    troncato o corrotto degrada a "nessun digest", perche il gateway deve poter
    bootare comunque.
    """

    def __init__(self, workspace: Any) -> None:
        # ``workspace`` resta grezzo e viene risolto lazy, come in
        # ``SubagentRecordStore``: lo store va costruibile anche quando il
        # workspace non e ancora un path utilizzabile (bootstrap, test).
        self._workspace = workspace

    @property
    def root(self) -> Path | None:
        try:
            return Path(self._workspace) / SUBAGENTS_DIRNAME / _ACTIVITY_DIRNAME
        except TypeError:
            return None

    def path_for(self, task_id: str) -> Path | None:
        root = self.root
        if root is None or not isinstance(task_id, str):
            return None
        stem = _UNSAFE_TASK_ID_RE.sub("_", task_id)[:_MAX_TASK_ID_CHARS].strip("_")
        return root / f"{stem}.json" if stem else None

    # -- write ---------------------------------------------------------------

    def write(self, task_id: str, events: Sequence[Mapping[str, Any]]) -> DigestMeta:
        """Scrive il digest e ritorna cosa il record deve registrarne.

        Non solleva: siamo sul percorso della transizione terminale, dove un
        errore di I/O non deve poter impedire di chiudere il subagent. Un digest
        vuoto non produce file (il blocco in chat non va offerto per niente).
        """
        path = self.path_for(task_id)
        normalized = [e for e in (_normalize_event(x) for x in events) if e is not None]
        if path is None or not normalized:
            return DigestMeta()
        payload = json.dumps(
            {
                "version": _DIGEST_VERSION,
                "task_id": _clean(task_id, limit=_MAX_TASK_ID_CHARS),
                "written_at": time.time(),
                "events": normalized,
            },
            ensure_ascii=False,
        )
        try:
            atomic_write(path, payload)
        except OSError as e:
            logger.warning("Subagent activity digest write failed for {}: {}", task_id, e)
            return DigestMeta()
        return DigestMeta(events=len(normalized), bytes=len(payload.encode("utf-8")))

    def delete(self, task_id: str) -> bool:
        """Cancella il digest di un task. ``True`` se un file e stato rimosso."""
        path = self.path_for(task_id)
        if path is None:
            return False
        try:
            return path.unlink(missing_ok=True) is None and not path.exists()
        except OSError as e:
            logger.warning("Subagent activity digest delete failed for {}: {}", task_id, e)
            return False

    def keep_only(self, task_ids: Iterable[str]) -> int:
        """Rimuove i digest orfani, cioe senza record. Ritorna quanti.

        Su un telefono un file orfano e una perdita lenta: i digest vengono
        cancellati insieme al loro record (vedi ``SubagentRecordStore.append``),
        e questa e la rete di sicurezza per cio che e sfuggito — un crash tra la
        scrittura del digest e quella del record, o un file lasciato da una
        versione precedente.
        """
        root = self.root
        if root is None:
            return 0
        live = {
            stem for task_id in task_ids
            if (path := self.path_for(task_id)) is not None and (stem := path.stem)
        }
        removed = 0
        try:
            paths = list(root.glob("*.json"))
        except OSError as e:
            logger.warning("Subagent activity dir unreadable {}: {}", root, e)
            return 0
        for path in paths:
            if path.stem in live:
                continue
            try:
                path.unlink(missing_ok=True)
                removed += 1
            except OSError as e:
                logger.warning("Orphan digest unlink failed {}: {}", path, e)
        return removed

    # -- read ----------------------------------------------------------------

    def load(self, task_id: str) -> list[dict[str, Any]]:
        """Eventi del digest, o ``[]`` se non c'e o non e leggibile."""
        path = self.path_for(task_id)
        if path is None:
            return []
        try:
            if not path.is_file():
                return []
            if path.stat().st_size > _MAX_DIGEST_FILE_BYTES:
                logger.warning("Subagent activity digest too large, ignoring: {}", path)
                return []
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as e:
            # Un digest e un extra: troncato da un kill a meta scrittura o
            # scritto da una versione incompatibile, degrada a "nessun digest".
            logger.warning("Subagent activity digest unreadable {}: {}", path, e)
            return []
        events = raw.get("events") if isinstance(raw, Mapping) else None
        if not isinstance(events, list):
            return []
        return [e for e in (_normalize_event(x) for x in events) if e is not None]
