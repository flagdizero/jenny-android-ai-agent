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

**Quel che sta dentro il blocco e non è dell'utente non si butta.** Dream può
scriverci dentro — una riga aggiunta sotto l'intestazione, o un paragrafo in
coda a un file dove i marcatori sono caduti e il blocco arriva fino in fondo —
e il suo cursore a quel punto è già avanzato: una riga tolta qui è persa per
sempre. Quindi la proiezione riscrive solo le righe che riconosce (marcatori,
intestazione, regole attuali e quelle che sta sostituendo) e sposta tutte le
altre subito dopo il blocco, con un WARNING che dice quante.
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


def _rule_lines(*texts: str | None) -> set[str]:
    """Le righe non vuote di *texts*, spogliate: ciò che il blocco può contenere."""
    return {row.strip() for text in texts for row in (text or "").splitlines() if row.strip()}


def find_block(soul: str, rules: str | None = None) -> tuple[int, int] | None:
    """Gli estremi del blocco dentro *soul*, o ``None`` se non c'è.

    Prima i marcatori. Se manca uno dei due si ricade sull'intestazione, e il
    blocco arriva fino alla prossima di pari livello o alla fine del file: è il
    caso in cui una potatura ha portato via un commento ma non il testo.

    *rules*, se dato, sono le righe che l'utente ha scritto: una loro riga che
    comincia con ``## `` non chiude il blocco nel ripiego, perché è sua e non
    l'inizio della sezione dopo — altrimenti la coda delle regole resterebbe
    fuori e la proiezione la duplicherebbe.

    I marcatori valgono solo nell'ordine giusto: una chiusura si cerca *dopo*
    l'apertura, così una chiusura orfana rimasta più su non nasconde il blocco
    buono che le sta sotto.
    """
    start = soul.find(MARK_START)
    end = soul.find(MARK_END, start) if start != -1 else -1
    if start != -1 and end != -1:
        return start, end + len(MARK_END)

    own = _rule_lines(rules)
    rows = soul.splitlines(keepends=True)
    offset = 0
    opening = None
    for row in rows:
        if opening is None and row.strip() == HEADING:
            opening = offset
        elif opening is not None and row.startswith("## ") and row.strip() not in own:
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


def _foreign(inside: str, known: set[str]) -> list[str]:
    """Le righe di *inside* che la proiezione non ha scritto, nel loro ordine.

    Le vuote restano solo come separatori fra due righe salvate, e mai più di
    una di fila: servono a tenere i paragrafi di Dream distinti, non a portarsi
    dietro gli a capo del blocco.
    """
    kept: list[str] = []
    for row in inside.splitlines():
        text = row.strip()
        if not text:
            if kept and kept[-1] != "":
                kept.append("")
            continue
        if text in known:
            continue
        kept.append(row.rstrip())
    while kept and kept[-1] == "":
        kept.pop()
    return kept


def _project(soul: str, rules: str, previous: str | None) -> tuple[str, int]:
    """:func:`project` più il numero di righe estranee spostate fuori dal blocco."""
    text = soul or ""
    ends = find_block(text, "\n".join(filter(None, (rules, previous))))
    if not ends:
        if _blank(rules):
            return text, 0
        tail = text.rstrip()
        block = _block(rules)
        return ((tail + "\n\n" + block + "\n") if tail else block + "\n"), 0

    start, end = ends
    known = _rule_lines(rules, previous, MARK_START, MARK_END, HEADING)
    rescued = _foreign(text[start:end], known)
    saved = "\n".join(rescued)
    before, after = text[:start], text[end:]

    if _blank(rules):
        middle = (saved + "\n\n") if saved else ""
        fresh = before.rstrip() + "\n" + middle + after.lstrip("\n")
        return fresh.rstrip() + "\n", len([r for r in rescued if r])

    fresh = before + _block(rules)
    if saved:
        fresh += "\n\n" + saved
    if not after:
        fresh += "\n"
    elif not after.startswith("\n"):
        # Il ripiego sull'intestazione finisce all'inizio della sezione dopo:
        # senza un a capo il marcatore di chiusura le si incollerebbe davanti.
        fresh += "\n\n"
    return fresh + after, len([r for r in rescued if r])


def project(soul: str, rules: str, previous: str | None = None) -> str:
    """*soul* col blocco portato a *rules*.

    Regole vuote **tolgono** il blocco: chi svuota la casella non si aspetta di
    ritrovarsi un'intestazione con niente sotto. Il posto del blocco si
    conserva se c'era già — riscriverlo in fondo a ogni salvataggio lo
    sposterebbe sotto a quel che Dream ha aggiunto nel frattempo.

    *previous* sono le regole che il blocco conteneva prima di questo
    salvataggio: le loro righe si sostituiscono. Ogni altra riga trovata nel
    blocco non è dell'utente e non si butta — va subito dopo il blocco.
    """
    return _project(soul, rules, previous)[0]


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


def sync_soul(
    workspace: Path, soul_file: Path | None = None, previous: str | None = None
) -> bool:
    """Rifà la proiezione dentro ``SOUL.md``. Torna ``True`` se ha scritto.

    Chiamata dopo ogni passata di Dream. Non solleva mai: una proiezione che
    non si rifà è una regola che torna a mancare dal prompt fino alla prossima
    passata, e non vale il prezzo di far fallire il ciclo che l'ha chiamata.

    *previous* lo passa solo :func:`save_rules`: le regole che quel salvataggio
    sostituisce, v. :func:`project`.
    """
    soul = Path(soul_file) if soul_file else Path(workspace) / "SOUL.md"
    try:
        rules = read_rules(workspace)
        if _blank(rules) and not soul.exists():
            return False
        text = soul.read_text(encoding="utf-8")
        fresh, rescued = _project(text, rules, previous)
        if fresh == text:
            return False
        atomic_write(soul, fresh)
        if rescued:
            logger.warning(
                "SOUL.md: {} line(s) found inside the user-rules block were not the user's "
                "rules; kept right after the block",
                rescued,
            )
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

    Le regole di prima si leggono prima di sovrascriverle: sono le righe che la
    proiezione deve sostituire, e non salvare fuori dal blocco come se le
    avesse scritte Dream.
    """
    text = (rules or "").strip()
    previous = read_rules(workspace)
    write_rules(workspace, text)
    sync_soul(workspace, previous=previous)
    return text
