"""Le regole che l'utente ha dato a Jenny: la parte di ``SOUL.md`` che è sua.

**Perché non si scrive e basta dentro ``SOUL.md``.** Dream lo riscrive, e non
di rado. Misurato sugli snapshot del dispositivo di prova — che il runtime
scatta prima di ogni passata — il 19/09/2026: 58 snapshot su 6,6 giorni, sette
versioni distinte del file, **sei riscritture**, una ogni 1,1 giorni. E le
riscritture sono potature *dentro* le sezioni: in tutti e sei i cambi nessuna
intestazione è stata tolta o aggiunta, mentre le righe dentro sì — nel cambio
più grosso +4 e -8 su 31. Una casella di testo che salvasse ``SOUL.md`` così
com'è offrirebbe quindi di scrivere qualcosa che dura circa un giorno, e chi
l'ha scritto lo prenderebbe per un difetto dell'app.

Quindi due posti e una proiezione:

* ``.jenny/soul_rules.md`` è **la verità**: le parole dell'utente. Sta fuori
  dal registro di scrittura di Dream, che ammette esattamente ``SOUL.md``,
  ``USER.md``, ``memory/MEMORY.md`` e ``skills/<nome>/SKILL.md``
  (v. ``agent/memory.py``, dove quel registro si costruisce). Nessuna passata
  può toccarlo: non è una richiesta gentile nel prompt, è un perimetro.
* dentro ``SOUL.md`` c'è un **blocco proiettato**, ed è lì che il modello lo
  legge insieme al resto di chi è lei — senza aggiungere un file al bootstrap,
  cioè senza aggiungere una voce al prompt di ogni turno.

Dopo ogni passata di Dream la proiezione si rifà (:func:`sync_soul`). È una
riscrittura deterministica e non una riga di prompt che chiede di non toccare:
regge anche il giorno in cui il modello decide che quella frase è ridondante.

Il blocco si riconosce in due modi, e non è cintura più bretelle a caso: i due
marcatori HTML sono il modo preciso, l'intestazione è il ripiego per il giorno
in cui una potatura porta via un commento ma lascia il testo — che è
esattamente la forma di modifica che le misure mostrano.
"""

from __future__ import annotations

from pathlib import Path

from loguru import logger

from jenny.utils.path import atomic_write

# Dove stanno le parole dell'utente, relativo alla radice del workspace.
RULES_FILE = Path(".jenny") / "soul_rules.md"

MARK_START = "<!-- user-rules -->"
MARK_END = "<!-- /user-rules -->"
# L'intestazione dice al modello *di chi* è quel che segue. «Standing rules the
# user has given her» è la formula che ``dream.md`` usa già per descrivere cosa
# va in ``SOUL.md``: qui si nomina la stessa cosa con le stesse parole.
HEADING = "## Standing rules from the user"


def _blank(text: str | None) -> bool:
    return not (text or "").strip()


def _block(rules: str) -> str:
    return f"{MARK_START}\n{HEADING}\n\n{rules.strip()}\n{MARK_END}"


def find_block(soul: str) -> tuple[int, int] | None:
    """Gli estremi del blocco dentro *soul*, o ``None`` se non c'è.

    Prima i marcatori. Se manca uno dei due si ricade sull'intestazione, e il
    blocco arriva fino alla prossima di pari livello o alla fine del file: è il
    caso in cui una potatura ha portato via un commento ma non il testo.
    """
    start = soul.find(MARK_START)
    end = soul.find(MARK_END)
    if start != -1 and end > start:
        return start, end + len(MARK_END)

    rows = soul.splitlines(keepends=True)
    offset = 0
    opening = None
    for row in rows:
        if opening is None and row.strip() == HEADING:
            opening = offset
        elif opening is not None and row.startswith("## "):
            return opening, offset
        offset += len(row)
    if opening is not None:
        return opening, len(soul)
    if start != -1:
        # Marcatore d'apertura orfano: senza questo ramo resterebbe lì per
        # sempre, e ogni proiezione ne aggiungerebbe uno nuovo sotto.
        return start, len(soul)
    return None


def extract_rules(soul: str) -> str:
    """Il testo dell'utente dentro *soul*. Stringa vuota se non c'è blocco."""
    ends = find_block(soul or "")
    if not ends:
        return ""
    start, end = ends
    inside = (soul or "")[start:end]
    for mark in (MARK_START, MARK_END, HEADING):
        inside = inside.replace(mark, "")
    return inside.strip()


def project(soul: str, rules: str) -> str:
    """*soul* col blocco portato a *rules*.

    Regole vuote **tolgono** il blocco: chi svuota la casella non si aspetta di
    ritrovarsi un'intestazione con niente sotto. Il posto del blocco si
    conserva se c'era già — riscriverlo in fondo a ogni salvataggio lo
    sposterebbe sotto a quel che Dream ha aggiunto nel frattempo.
    """
    text = soul or ""
    ends = find_block(text)
    if _blank(rules):
        if not ends:
            return text
        start, end = ends
        return (text[:start].rstrip() + "\n" + text[end:].lstrip("\n")).rstrip() + "\n"
    block = _block(rules)
    if ends:
        start, end = ends
        return text[:start] + block + text[end:]
    tail = text.rstrip()
    return (tail + "\n\n" + block + "\n") if tail else block + "\n"


# ── Su disco ────────────────────────────────────────────────────────────────


def rules_path(workspace: Path) -> Path:
    return Path(workspace) / RULES_FILE


def read_rules(workspace: Path) -> str:
    """Le parole dell'utente, o stringa vuota se non ne ha ancora scritte."""
    path = rules_path(workspace)
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""
    except OSError:
        logger.opt(exception=True).warning("soul rules unreadable at {}", path)
        return ""


def write_rules(workspace: Path, rules: str) -> None:
    """Scrive le parole dell'utente. Vuote = il file sparisce."""
    path = rules_path(workspace)
    if _blank(rules):
        path.unlink(missing_ok=True)
        return
    atomic_write(path, rules.strip() + "\n")


def sync_soul(workspace: Path, soul_file: Path | None = None) -> bool:
    """Rifà la proiezione dentro ``SOUL.md``. Torna ``True`` se ha scritto.

    Chiamata dopo ogni passata di Dream. Non solleva mai: una proiezione che
    non si rifà è una regola che torna a mancare dal prompt fino alla prossima
    passata, e non vale il prezzo di far fallire il ciclo che l'ha chiamata.
    """
    soul = Path(soul_file) if soul_file else Path(workspace) / "SOUL.md"
    try:
        rules = read_rules(workspace)
        if _blank(rules) and not soul.exists():
            return False
        text = soul.read_text(encoding="utf-8")
        fresh = project(text, rules)
        if fresh == text:
            return False
        atomic_write(soul, fresh)
        logger.info("SOUL.md: user rules re-projected ({} chars)", len(rules.strip()))
        return True
    except FileNotFoundError:
        # SOUL.md non c'è: lo ricrea il bootstrap, e la proiezione si rifà al
        # giro dopo. Crearlo qui vorrebbe dire scrivere un'identità fatta di
        # sole regole dell'utente.
        return False
    except OSError:
        logger.opt(exception=True).warning("SOUL.md: user rules not re-projected")
        return False


def save_rules(workspace: Path, rules: str) -> str:
    """Salva le regole **e** le proietta. Torna il testo salvato, normalizzato.

    L'ordine conta: prima la verità, poi la copia. Se la seconda scrittura
    fallisce, quel che l'utente ha scritto è comunque su disco e la proiezione
    si rifà da sé alla prossima passata di Dream.
    """
    text = (rules or "").strip()
    write_rules(workspace, text)
    sync_soul(workspace)
    return text
