"""I marcatori con cui un turno silenzioso dichiara l'esito di un controllo.

Quattro, e il primo è quello da cui è nato tutto: "non ho potuto controllare".
Tre parlano del controllo (non è avvenuto, è delegato, è andato bene) e il
quarto parla di noi: all'utente gliel'ho detto.

Perché il testo finale del turno e non un tool nuovo: su un controllo periodico
quel testo esiste già, è già scartato (il turno è SILENT per costruzione) e oggi
non significa niente. Dargli un significato non aggiunge superficie che il
modello debba imparare, non può raggiungere l'utente per sbaglio, e non costa
una chiamata LLM in più. Un tool dedicato sarebbe invece comparso nell'elenco di
OGNI turno, chat comprese: il registry di un turno cron è quello di default.

**Quel prezzo è stato poi pagato, per un caso solo, e vale sapere quale.**
``nothing_to_report`` (``agent/tools/nothing_to_report.py``) è un tool dedicato,
sta davvero nell'elenco di ogni turno, e il paragrafo qui sopra resta vero: il
suo schema pesa 757 caratteri — ~190 token su ogni richiesta, chat comprese, il
3,8% del payload dei tool. Ciò che è cambiato è la misura dall'altra parte. I
marcatori qui descrivono *che cosa* dichiarare, e per quello il testo finale
basta; non risolvono il
problema opposto, cioè che su un turno silenzioso **tacere non è un'azione**, e
un modello piccolo l'assenza di azione la codifica come ``message`` con dentro
una parola qualunque. Quattordici bolle di riempimento arrivate in chat fra il 21
agosto e il 3 settembre 2026, cinque dopo il guardiano in
``message.py::_unusable_silent_alert``. Il tool non sostituisce i marcatori: ne
scrive uno (``CHECK_OK <n>``, e solo se il numero è esplicito) come effetto di
un'azione che il modello può compiere.

Da non confondere col sentinella rifiutato in ``AgentLoop._process_message``: là
si decideva **se consegnare**, cioè un atto con un effetto sull'utente, e per
quello il tool ``message`` è e resta l'unica strada. Qui il modello non consegna
niente, dichiara un fatto su di sé.

Il modulo sta a parte — e non dentro ``bound_runner`` dove è nato con B8 —
perché ha due lettori: il run del monitor e il turno d'annuncio dell'aiutante a
cui il monitor ha passato il controllo (:mod:`jenny.cron.monitor_followup`). Un
parser solo, due chiamanti: la forma che il modello scrive resta una. Il numero
dopo il marcatore (``CHECK_FAILED 2: …``) veniva dall'heartbeat, che numerava i
suoi task; il parser lo legge ancora e nessuno lo guarda.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from loguru import logger

# La parola che apre la riga. Maiuscola e senza spazi: è un token, non prosa.
COULD_NOT_CHECK_MARKER = "CHECK_FAILED"

# Il secondo marcatore, e il motivo per cui ce n'è un secondo: l'agente
# principale gira in ``orchestrator_mode`` e non ha ``python_exec``, quindi un
# task che deve *fare* qualcosa viene delegato con ``spawn`` — che ritorna
# subito. Il turno del monitor finisce prima che il subagent abbia iniziato, e
# la regola "nessun marcatore = il controllo è avvenuto" leggerebbe quel
# silenzio come un successo. Con questa riga il turno dichiara invece "l'esito
# non lo so ancora", la sequenza di guasti non viene azzerata, e a scriverne il
# verdetto è il turno che riceve il risultato (:mod:`jenny.cron.monitor_followup`).
DELEGATED_MARKER = "CHECK_DELEGATED"

# Il verdetto positivo, e l'unico posto in cui questo modulo dice che qualcosa
# è andato *bene*. Serve dove il silenzio non è una prova: su un controllo
# delegato il turno d'annuncio che tace può aver visto il controllo riuscire
# oppure essersi dimenticato di dichiararne il guasto, e le due cose sono la
# stessa osservazione. Finché lo erano, l'unica scelta era fra due errori —
# riavvisare dello stesso guasto ogni due ore, o non riavvisare mai più.
#
# Con questa riga il recupero si dichiara invece di dedursi: la voce del task
# si chiude qui, ``escalated`` compreso, e un guasto nuovo mesi dopo torna ad
# avvisare. Se il modello la dimentica non si torna al ciclo di avvisi: si
# ricade nel caso "nessun verdetto", che tace (v. ``resolve_pending_delegations``).
#
# Non riguarda il monitor: là un controllo per turno e l'assenza di
# ``CHECK_FAILED`` è già una prova sufficiente. ``could_not_check_reason``
# legge solo i marcatori di guasto e non cambia comportamento.
OK_MARKER = "CHECK_OK"

# Il quarto, e il solo che non parli del controllo: parla di **noi**. Dice "di
# questo guasto all'utente ho appena parlato", ed è ciò che permette allo stato
# di registrare l'avviso invece di dedurlo.
#
# Perché serve. Il timbro ``escalated`` è il verbale di aver parlato, e finché
# lo si deduceva da ``TurnOutcome.spoke`` si deduceva dalla cosa sbagliata:
# ``spoke`` è vero per QUALUNQUE chiamata a ``message`` riuscita e non ha
# soggetto. Un monitor autorizzato a segnalare "una soglia superata" (v.
# ``agent/cron_monitor.md``) che poi non riesce a completare il controllo si
# timbrava così per un messaggio che del guasto non parlava, e da lì il prompt
# dice "non dirglielo di nuovo": misurato il 2026-08-17 sul dispatcher vero, 19
# run consecutivi con il controllo morto e zero avvisi. Sull'heartbeat lo stesso
# difetto era mediato da un'euristica (``sole_failure``, "se c'è un solo
# candidato quel messaggio era per lui"), che sbagliava dalla parte opposta ogni
# volta che i candidati erano due.
#
# Con questa riga il fatto è dichiarato: nessuna euristica, e il soggetto c'è.
# E vale anche per un avviso di **propria iniziativa**, che era il caso per cui
# ``sole_failure`` esisteva — un modello che decide di parlare lo dichiara, e non
# si finisce per riavvisare della stessa cosa due giri dopo.
#
# Se il modello la dimentica, ``escalated`` resta ``False`` e l'avviso si ripete
# al giro dopo: rumore recuperabile. È la direzione d'errore giusta — un guasto
# zittito per sempre non lo è — e la stessa scelta che fa ``OK_MARKER``.
WARNED_MARKER = "CHECK_WARNED"

# Quante esecuzioni consecutive senza controllo prima di avvisare l'utente.
# Tre, a mezz'ora di intervallo, fanno circa un'ora e mezza: abbastanza da non
# reagire a una rete che va e viene, poco abbastanza da non lasciare un
# controllo morto per una giornata. Il silenzio di un controllo sano resta
# gratis — qui non si muove niente finché il modello non dichiara di non aver
# potuto controllare.
ESCALATE_AFTER_FAILURES = 3

# Quante volte di fila si può *chiedere* l'avviso, se il turno non lo dichiara.
#
# Serve perché ``CHECK_WARNED`` sposta il timbro su una riga che il modello deve
# scrivere, e un modello che non la scrive mai non fa mai scattare
# ``already_warned``: la richiesta tornerebbe nel prompt a ogni run, per sempre,
# e con lei il messaggio all'utente ogni mezz'ora. La direzione d'errore scelta è
# il rumore, ma il rumore va comunque limitato — "per sempre" non è un costo
# accettabile in nessuna delle due direzioni.
#
# Tre, cioè la stessa soglia: si chiede ai run con sequenza 2, 3 e 4, e dal
# quinto in poi non si chiede più. Non resta scoperto niente, perché è
# esattamente lì che comincia ``jenny/cron/silence_watchdog.py``, che alza
# l'allarme a 6 (``ESCALATE_AFTER_FAILURES * 2``) senza passare dal modello. Le
# due finestre sono contigue di proposito: prima si chiede a chi sa scrivere una
# frase, poi si suona un allarme che non ha bisogno di lui.
ESCALATION_ASK_LIMIT = 3

# Il motivo finisce in ``CronJobState.last_error`` e nella run record: va tenuto
# corto, non è un log.
REASON_MAX_CHARS = 200

# Rumore markdown che un modello mette volentieri attorno a una riga
# "importante" — punto elenco compreso: un modello che scrive N marcatori in
# fondo alla risposta li mette volentieri in elenco.
_MARKER_DECORATION = "*_`#>-+ \t"

# Separatori ammessi fra il marcatore (o il riferimento) e il motivo.
_SEPARATORS = ": -–—"

# I quattro, insieme: nessuno è prefisso di un altro, quindi un unico
# ``startswith`` li riconosce tutti (v. :func:`is_only_markers`).
_ALL_MARKERS = (COULD_NOT_CHECK_MARKER, DELEGATED_MARKER, OK_MARKER, WARNED_MARKER)

# ``CHECK_FAILED 2: motivo`` — e le varianti che un modello scrive senza
# pensarci: ``#2``, ``[2]``, ``2)``, ``2.``, o il solo numero senza motivo.
_REF_RE = re.compile(r"^\[?\s*#?(\d{1,3})\s*[\])\.]?\s*[:\.\-–—]?\s*(.*)$")


@dataclass(frozen=True)
class CouldNotCheckMark:
    """Una riga di marcatore, letta.

    Attributes:
        ref: il riferimento al task, quando c'è (l'heartbeat numera i suoi
            task nel prompt). ``None`` su un monitor, che ha un controllo solo
            e quindi non ha niente da distinguere.
        reason: riga breve scritta dal modello su cosa lo ha bloccato. Può
            essere vuota: il marcatore senza motivo resta un marcatore.
    """

    ref: str | None
    reason: str


def parse_could_not_check_marks(final_text: str | None) -> list[CouldNotCheckMark]:
    """Legge TUTTE le righe di marcatore nel testo finale di un turno.

    Una lista e non un singolo esito perché un turno di heartbeat copre N task
    e può fallirne più d'uno: il monitor, che ne ha uno solo, guarda la prima e
    ignora il resto.
    """
    return _parse_marks(final_text, COULD_NOT_CHECK_MARKER)


def parse_delegated_marks(final_text: str | None) -> list[CouldNotCheckMark]:
    """Legge le righe ``CHECK_DELEGATED``: task il cui esito non si sa ancora.

    Stessa forma, stesso parser: la sintassi che il modello deve ricordare
    resta una sola, cambia solo la parola iniziale. ``reason`` qui è ciò che è
    stato delegato, e serve solo al log — a contare è quale task.
    """
    return _parse_marks(final_text, DELEGATED_MARKER)


def parse_ok_marks(final_text: str | None) -> list[CouldNotCheckMark]:
    """Legge le righe ``CHECK_OK``: controlli delegati che sono andati a buon fine.

    Stessa forma e stesso parser degli altri due. ``reason`` qui è quasi sempre
    vuota — non c'è niente da spiegare di un controllo riuscito — e non viene
    letta da nessuno: a contare è quale task.
    """
    return _parse_marks(final_text, OK_MARKER)


def parse_warned_marks(final_text: str | None) -> list[CouldNotCheckMark]:
    """Legge le righe ``CHECK_WARNED``: guasti di cui il turno ha avvisato l'utente.

    Stessa forma e stesso parser degli altri tre. ``reason`` non viene letta da
    nessuno — a contare è quale task — e sul monitor, che ha un controllo solo,
    conta la sola presenza di una riga.
    """
    return _parse_marks(final_text, WARNED_MARKER)


def is_only_markers(text: str | None) -> bool:
    """Vero se *text* non contiene niente oltre a righe di marcatore.

    Serve fuori da qui — al tool ``message``, che deve poter riconoscere un
    marcatore *prima* di consegnarlo all'utente come se fosse un avviso — e sta
    qui perché qui vive la grammatica: le quattro parole e il rumore markdown
    che il modello ci mette attorno. Un testo vuoto non è "solo marcatori": è
    vuoto, e chi chiama lo distingue prima.
    """
    lines = [
        stripped
        for stripped in (
            line.strip().lstrip(_MARKER_DECORATION) for line in (text or "").splitlines()
        )
        if stripped
    ]
    return bool(lines) and all(line.startswith(_ALL_MARKERS) for line in lines)


def _is_specimen(head: str) -> bool:
    """La riga è il *modello* della riga, non una riga scritta.

    Misurato sul Titan 2 il 2026-09-03 alle 11:25, e non è un caso di scuola: il
    modello ha scritto la sua riga vera e poi ha rigurgitato **l'intero
    preambolo** in coda alla risposta — 4.883 caratteri, con dentro
    ``CHECK_FAILED <task number>: <one short line naming what stopped you>`` e
    ``CHECK_WARNED <task number>``. Il parser li ha letti come dichiarazioni, e
    un controllo perfettamente sano è finito registrato come guasto **e** come
    già segnalato all'utente (``escalated``), senza che nessun avviso sia mai
    partito: esattamente lo stato "controllo morto in silenzio" che tutto questo
    modulo esiste per impedire.

    Il rigurgito del template è una patologia nota di questo modello — c'è già
    ``strip_think`` a toglierlo da ciò che raggiunge l'**utente** (v. il commit
    del 2026-08-27), e non c'era niente a toglierlo da ciò che raggiunge il
    *parser*. Qui la difesa è precisa e non euristica: un segnaposto comincia per
    ``<``, e una riga scritta da qualcuno non lo fa mai.

    La direzione d'errore è quella giusta anche nel caso limite in cui un motivo
    vero cominciasse per ``<``. Il generale "nel dubbio è ``CHECK_FAILED``" vale
    fra due letture di un'osservazione ambigua; qui l'osservazione non è ambigua,
    ed è il ramo opposto a costare di più: un guasto inventato si porta dietro un
    ``CHECK_WARNED`` inventato, che zittisce gli avvisi veri di quel controllo
    finché qualcuno non se ne accorge. Un guasto vero perso, invece, si ripresenta
    al ciclo dopo.
    """
    return head.startswith("<")


def _parse_marks(final_text: str | None, marker: str) -> list[CouldNotCheckMark]:
    """Corpo condiviso dai quattro marcatori. Nessuno è prefisso di un altro."""
    marks: list[CouldNotCheckMark] = []
    for line in (final_text or "").splitlines():
        stripped = line.strip().lstrip(_MARKER_DECORATION)
        if not stripped.startswith(marker):
            continue
        rest = stripped[len(marker):].rstrip(_MARKER_DECORATION)
        head = rest.lstrip()
        if _is_specimen(head.lstrip(_SEPARATORS).lstrip()) or _is_specimen(head):
            logger.debug("Marker specimen echoed back, not a declaration: {!r}", stripped[:80])
            continue
        if head[:1] and head[0] in _SEPARATORS:
            # Forma B8: ``CHECK_FAILED: motivo``. Il motivo può contenere due
            # punti a sua volta, quindi qui non si va a cercare un separatore —
            # si toglie quello iniziale e basta.
            marks.append(
                CouldNotCheckMark(None, rest.lstrip(_SEPARATORS).strip()[:REASON_MAX_CHARS])
            )
            continue
        match = _REF_RE.match(head)
        if match is not None:
            marks.append(
                CouldNotCheckMark(match.group(1), match.group(2).strip()[:REASON_MAX_CHARS])
            )
            continue
        marks.append(CouldNotCheckMark(None, head.strip()[:REASON_MAX_CHARS]))
    return marks


def could_not_check_reason(final_text: str | None) -> str | None:
    """Legge il marcatore nel testo finale di un monitor.

    Returns:
        ``None`` se il marcatore non c'è, cioè il controllo è avvenuto.
        Altrimenti il motivo dichiarato dal modello, che può essere la stringa
        vuota se non ne ha scritto uno: chi chiama deve distinguere con
        ``is not None``, non sulla verità della stringa.
    """
    marks = parse_could_not_check_marks(final_text)
    return marks[0].reason if marks else None
