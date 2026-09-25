"""Inseguire una wiki che ha cambiato nome, portandole dietro la sua chat.

Passo **7** di ``roadmap/progetti-passi.md``, strada **B**.

L'indirizzo di una conversazione di progetto resta il **nome della cartella**, e
non diventa un id: la cartella continua a dedursi dalla chiave, cosi' come
deciso il 21/08, e i file di sessione continuano a chiamarsi
``project_patreon.jsonl`` invece di ``project_3f9a2c1b7e04.jsonl`` — che su
questo progetto non e' estetica, e' lo strumento con cui si guarda cosa e'
successo davvero.

L'id serve a una cosa sola: **ritrovare** una chat rimasta orfana. Ogni sessione
di progetto registra l'id della wiki a cui appartiene; quando la cartella non c'e'
piu' si cerca fra le wiki quella con quell'id, e se si trova le tracce della chat
prendono il nome nuovo. Cosi' la riparazione gira **solo quando qualcosa e' gia'
andato storto**, invece di mettersi sul percorso di ogni turno.

**L'elenco delle tracce non e' piu' qui**: vive in
:mod:`jenny.session.project_traces`, da quando a chiederlo non e' piu' solo il
rinomino ma anche la cancellazione di un progetto.

**Le tracce sono tre, non quattro.** La quarta —
``<progetto>/.jenny/tool-results/project_<nome>/`` — vive *dentro* la cartella
della wiki, quindi con il rinomino si e' già spostata da sé; il suo nome resta
quello vecchio, ma ``_cleanup_tool_result_buckets`` rimuove i bucket che non
appartengono alla sessione corrente, quindi si ripulisce da sola al primo turno.

**Se una destinazione e' occupata non si sposta niente.** Ci si arriva scambiando
due nomi (A→B, B→A): due sessioni vorrebbero lo stesso file. Meglio due chat da
rinominare a mano che due storie mescolate — la lezione del passo 6.

**Il controllo delle destinazioni e' atomico, la sequenza di rename no.** Fra il
primo e l'ultimo ``rename`` c'e' una finestra — microsecondi, ma su Android il
processo puo' morire in qualunque momento, e un ``OSError`` a meta' non e' un
caso di laboratorio. Lo stato che ne uscirebbe e' il peggiore possibile: la
sessione sotto un nome e la trascrizione sotto un altro, cioe' lo schermo di una
chat vuota con la memoria di un'altra, e *nessuno* che se ne accorga dopo — il
rifiuto del passo 6 scatta solo quando la cartella **manca**, e dopo un rinomino
la cartella c'e'.

Quindi prima del primo ``rename`` si scrive un **giornale**
(``sessions/.project-rename-pending.json``) con i due nomi, e si rimuove dopo
l'ultimo. Uno stato a meta' non e' piu' silenzioso: il giornale rimasto lo dice, e
:func:`repair_pending_project_renames` lo finisce.

**E non si insegue dentro un nome che nessuno puo' riaprire.** La cartella la
rinomina l'utente **fuori** da Jenny, quindi il nome nuovo non e' passato da
nessun controllo: ``wikis/Ricerca ETF`` non supera
:func:`~jenny.session.keys.is_valid_project_name`, e una chat spostata su
``project:Ricerca ETF`` non la apre ne' il canale (``session_key_for_channel``
la rifiuta) ne' il chip (non la elenca). Sarebbe uno spostamento riuscito verso
il nulla — peggio del non spostare, perche' sotto il nome vecchio la chat c'e'
ancora. Il rifiuto sta **prima** del giornale: se aprisse una voce,
:func:`repair_pending_project_renames` lo porterebbe a termine al prossimo
avvio. La riparazione invece non applica il controllo, ed e' voluto: una voce
aperta descrive uno spostamento **gia' cominciato**, cioe' tracce in due posti,
e lasciarle divise per sempre e' il male peggiore.

Perche' il giornale e non una directory di staging con scambio finale: lo staging
non elimina la finestra, la sposta — la seconda fase e' comunque N ``rename`` —
e per renderla recuperabile servirebbe *comunque* un marcatore che dica che la
staging esiste. Il giornale e' quel marcatore senza il doppio spostamento.
"""

from __future__ import annotations

import json
from pathlib import Path

from loguru import logger

from jenny.session.keys import PROJECT_SESSION_PREFIX, is_valid_project_name
from jenny.session.project_traces import project_trace_paths
from jenny.utils.path import atomic_write

# Il giornale degli spostamenti aperti. Vive accanto ai file di sessione perche'
# e' di quelli che parla, e il nome comincia con un punto per non finire fra le
# tracce che qualcuno elenca.
RENAME_JOURNAL_NAME = ".project-rename-pending.json"


def _can_be_opened(key: str) -> bool:
    """Una chat spostata su *key* la si potrebbe riaprire?

    Solo le chiavi di progetto hanno qualcosa da verificare, e la verifica e'
    quella di :func:`jenny.session.keys.is_valid_project_name`: la stessa che il
    canale applica in ingresso (``session_key_for_channel``) e che il chip usa
    per elencare. Qualunque altra forma di chiave passa: non e' questa la
    funzione che decide quali chiavi esistono.
    """
    if not key.startswith(PROJECT_SESSION_PREFIX):
        return True
    return is_valid_project_name(key[len(PROJECT_SESSION_PREFIX):])


def _journal_path(workspace: Path) -> Path:
    return workspace / "sessions" / RENAME_JOURNAL_NAME


def pending_project_renames(workspace: Path) -> list[tuple[str, str]]:
    """Gli spostamenti aperti: ``(chiave vecchia, chiave nuova)``, in ordine.

    Vuoto e' lo stato normale. Non vuoto vuol dire che un rinomino e' cominciato
    e non si sa se e' finito — che e' tutta la differenza fra un difetto
    riparabile e una chat vuota per sempre.
    """
    path = _journal_path(workspace)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, ValueError):
        # Un giornale illeggibile e' peggio di nessun giornale, perche' non si
        # puo' nemmeno riparare: lo si dice e si va avanti.
        logger.opt(exception=True).error("Giornale dei rinomini illeggibile: {}", path)
        return []
    if not isinstance(raw, list):
        return []
    return [
        (str(item["old"]), str(item["new"]))
        for item in raw
        if isinstance(item, dict) and item.get("old") and item.get("new")
    ]


def _write_journal(workspace: Path, records: list[tuple[str, str]]) -> None:
    """Sostituisce il giornale, e lo *rimuove* quando non resta niente."""
    path = _journal_path(workspace)
    if not records:
        path.unlink(missing_ok=True)
        return
    atomic_write(
        path,
        json.dumps([{"old": old, "new": new} for old, new in records], ensure_ascii=False),
    )


def _forget(workspace: Path, record: tuple[str, str]) -> None:
    """Chiude una voce rileggendo il giornale, non la copia che avevamo in mano."""
    remaining = [r for r in pending_project_renames(workspace) if r != record]
    _write_journal(workspace, remaining)


def _roll_back(moved: list[tuple[Path, Path]]) -> bool:
    """Rimette indietro quel che era gia' partito. ``False`` = non del tutto."""
    intact = True
    for src, dst in reversed(moved):
        try:
            dst.rename(src)
        except OSError:
            intact = False
            logger.opt(exception=True).error(
                "Ritorno indietro fallito: {} resta sotto il nome nuovo", dst.name
            )
    return intact


def _finish_move(workspace: Path, old_key: str, new_key: str) -> bool:
    """Porta al nome nuovo le tracce rimaste indietro. ``False`` = ne restano.

    Idempotente per costruzione: le coppie si ricalcolano dai due nomi, e quelle
    gia' arrivate non hanno piu' una sorgente da spostare. Una destinazione
    occupata *mentre* si ripara non si sovrascrive — la voce resta aperta e lo
    dice, che e' la stessa scelta del rifiuto sullo scambio di due nomi.
    """
    stuck: list[str] = []
    for src, dst in zip(
        project_trace_paths(workspace, old_key),
        project_trace_paths(workspace, new_key),
    ):
        if not src.exists():
            continue
        if dst.exists():
            stuck.append(src.name)
            continue
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            src.rename(dst)
        except OSError:
            logger.opt(exception=True).error(
                "Ripresa dello spostamento: {} non si muove", src.name
            )
            stuck.append(src.name)
    if stuck:
        logger.error(
            "Spostamento {} -> {} ancora incompleto: {}", old_key, new_key, ", ".join(stuck)
        )
        return False
    logger.info("Spostamento interrotto completato: {} -> {}", old_key, new_key)
    return True


def repair_pending_project_renames(workspace: Path) -> list[tuple[str, str]]:
    """Finisce gli spostamenti che un'interruzione ha lasciato a meta'.

    Va chiamata **all'avvio**, prima che qualcuno apra una chat di progetto: il
    caso che il giornale copre e' il processo ucciso fra due ``rename``, e un
    processo ucciso riparte — quindi il boot e' esattamente il momento in cui lo
    stato a meta' esiste e nessuno lo sta ancora guardando.

    Costo quando non c'e' niente da fare: una ``read_text`` che solleva
    ``FileNotFoundError``.

    Ritorna le coppie completate, per poterle dire in un log o all'utente.
    """
    records = pending_project_renames(workspace)
    if not records:
        return []
    completed: list[tuple[str, str]] = []
    left: list[tuple[str, str]] = []
    for old_key, new_key in records:
        if _finish_move(workspace, old_key, new_key):
            completed.append((old_key, new_key))
        else:
            left.append((old_key, new_key))
    _write_journal(workspace, left)
    return completed


def follow_renamed_project(
    workspace: Path, old_key: str, new_key: str
) -> tuple[bool, str | None]:
    """Rinomina le tracce di *old_key* in quelle di *new_key*.

    Ritorna ``(spostato, motivo)``: ``motivo`` e' valorizzato solo quando **non**
    si e' spostato niente, e dice perche' in una forma che si possa mostrare.

    Tutto o niente, e quando non riesce a esserlo lo **scrive**: si controllano
    prima tutte le destinazioni, poi si apre una voce nel giornale, poi si
    sposta. Uno spostamento a metà lascerebbe la sessione sotto un nome e la sua
    trascrizione sotto un altro, che e' esattamente lo stato che il passo 1 ha
    faticato a evitare (lo schermo di una chat e la memoria di un'altra) — con la
    voce aperta quello stato e' visibile e finibile invece che definitivo.
    """
    if old_key == new_key:
        return False, None
    if not _can_be_opened(new_key):
        # **Prima del giornale**, quindi non resta niente da riparare e l'avvio
        # dopo non finisce uno spostamento che qui si e' rifiutato. E' l'ordine
        # che conta: un rifiuto che apre una voce sarebbe peggio del difetto,
        # perche' ``repair_pending_project_renames`` la porterebbe a termine.
        #
        # Il controllo sta *qui* e non solo dal chiamante perche' e' questa la
        # funzione che sposta: una destinazione che nessun canale sa aprire
        # (v. ``session_key_for_channel``) non e' una chat spostata, e' una chat
        # perduta con l'apparenza di un successo.
        logger.error(
            "Move refused: {!r} cannot be a conversation key",
            new_key[:120],
        )
        return False, "the new name cannot be the name of a conversation"
    # Una voce rimasta aperta va chiusa prima di aprirne un'altra: le tracce che
    # stiamo per cercare potrebbero essere per metà già sotto un altro nome.
    repair_pending_project_renames(workspace)
    pairs = [
        (src, dst)
        for src, dst in zip(
            project_trace_paths(workspace, old_key),
            project_trace_paths(workspace, new_key),
        )
        if src.exists()
    ]
    if not pairs:
        return False, "there was nothing recorded under the old name"
    occupied = [dst.name for _, dst in pairs if dst.exists()]
    if occupied:
        # Il caso dello scambio di due nomi. Non si sceglie: si dice.
        return False, (
            "the new name already has a conversation of its own "
            f"({', '.join(sorted(occupied))})"
        )
    record = (old_key, new_key)
    try:
        _write_journal(workspace, [*pending_project_renames(workspace), record])
    except OSError:
        # Senza giornale non si sposta: uno spostamento non tracciato e' proprio
        # il difetto che questo codice esiste per non avere.
        logger.opt(exception=True).error("Giornale dei rinomini non scrivibile, non sposto")
        return False, "I could not write down the move before starting it, so I did not start"
    moved: list[tuple[Path, Path]] = []
    for src, dst in pairs:
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            src.rename(dst)
        except OSError:
            logger.opt(exception=True).error(
                "Rinomino parziale delle tracce di {} verso {}: {} non spostato",
                old_key, new_key, src.name,
            )
            if _roll_back(moved):
                _forget(workspace, record)
                return False, "moving the conversation's files failed, so nothing was moved"
            # Ne' avanti ne' indietro: la voce resta aperta, ed e' quella che al
            # prossimo avvio finisce il lavoro.
            return False, (
                "moving the conversation's files stopped halfway; it is written down "
                "and I will finish it the next time I start"
            )
        moved.append((src, dst))
    _forget(workspace, record)
    logger.info("Chat di progetto seguita: {} -> {}", old_key, new_key)
    return True, None
