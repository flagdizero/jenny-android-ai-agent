"""Budget di dimensione per i file di memoria a lungo termine.

Logica pura: l'unico I/O è la lettura del file interrogato per misurarne la
dimensione. Nessuna dipendenza dalla config — le soglie arrivano come interi
dal chiamante — e nessuna dipendenza dal layer dei tool, così il modulo resta
importabile senza cicli: è ``jenny/agent/memory.py`` a importare questo, mai il
contrario.

Il budget conta **caratteri**, non byte e non token. Altrove nel repo si tronca
a token (``truncate_text_to_tokens`` per WIKI.md) ma quello è un taglio
invisibile al modello, un mestiere diverso: lì il numero serve a noi per non
sforare il contesto. Qui il numero viene mostrato *al modello* come contratto da
rispettare mentre scrive, e i caratteri sono l'unica unità che il modello sappia
contare da solo mentre compone il testo.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jenny.agent.memory import MemoryStore

# Callback invocata subito prima di una scrittura su un file di memoria:
# riceve il path già risolto dal tool e il testo finale che finirebbe su disco,
# ritorna ``None`` per lasciar passare o il messaggio di rifiuto da restituire
# al modello come risultato del tool.
WriteSizeGuard = Callable[[Path, str], str | None]

# Soglia oltre la quale il gauge invita a consolidare prima di aggiungere.
# Non è applicata da nessun controllo: è l'unico avviso che il modello riceve
# *prima* di sbattere contro il rifiuto, quando ha ancora spazio per manovrare.
_CONSOLIDATE_PCT = 80


@dataclass(frozen=True, slots=True)
class FileBudget:
    """Stato di riempimento di un singolo file di memoria.

    ``budget == 0`` significa *misurato ma non applicato*: il file compare nel
    gauge con i suoi caratteri e non viene mai rifiutato. È lo stato di default
    per SOUL.md e il modo per disattivare l'enforcement su un singolo file senza
    farlo sparire dal report.
    """

    label: str
    path: Path
    chars: int
    budget: int
    # Solo per SOUL.md: il blocco con le regole dell'utente non conta. Il budget
    # esiste per limitare ciò che scrive Dream, e quelle righe non sono sue —
    # le proietta l'app da ``.jenny/soul_rules.md`` (v. ``agent/soul_rules.py``).
    # Contate, un testo lungo dell'utente porterebbe il file oltre il tetto e
    # ogni scrittura di Dream verrebbe rifiutata.
    skip_user_rules: bool = False

    def measure(self, text: str) -> int:
        """Caratteri di *text* come li conta questo budget."""
        return measure_text(text, skip_user_rules=self.skip_user_rules)

    def measure_now(self) -> int:
        """Caratteri del file adesso, con la stessa regola di ``chars``."""
        return count_chars(self.path, skip_user_rules=self.skip_user_rules)

    @property
    def enforced(self) -> bool:
        return self.budget > 0

    @property
    def over(self) -> bool:
        return self.enforced and self.chars > self.budget

    @property
    def pct(self) -> int | None:
        """Percentuale di riempimento troncata, ``None`` se il budget non è applicato.

        ``None`` e non ``0``: senza budget la percentuale non esiste, e uno zero
        finirebbe renderizzato come "0%" — cioè un file vuoto, che è l'opposto
        di quel che il chiamante deve capire.
        """
        if not self.enforced:
            return None
        return self.chars * 100 // self.budget


def measure_text(text: str, *, skip_user_rules: bool = False) -> int:
    """Caratteri di *text*; con *skip_user_rules* senza il blocco dell'utente.

    Il blocco è quello che ``soul_rules.find_block`` riconosce: gli stessi
    estremi che la proiezione riscrive, così budget e proiezione non possono
    disaccordarsi su dove finisca la parte dell'utente.
    """
    if not skip_user_rules:
        return len(text)
    from jenny.agent.soul_rules import find_block

    ends = find_block(text)
    if not ends:
        return len(text)
    start, end = ends
    return len(text) - (end - start)


def count_chars(path: Path, *, skip_user_rules: bool = False) -> int:
    """Caratteri del file, ``0`` se manca o non è leggibile.

    ``errors="ignore"`` perché la misura non deve mai diventare il motivo per
    cui una scrittura fallisce: un byte malformato in MEMORY.md renderebbe
    altrimenti il guard un blocco permanente su un file che il modello non ha
    modo di riparare.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return 0
    return measure_text(text, skip_user_rules=skip_user_rules)


def _canonical(path: Path) -> Path:
    """Forma canonica usata per confrontare i path nel guard.

    Su Android la dir dati è esposta come ``/data/user/0/<pkg>`` ma ``.resolve()``
    la canonicalizza in ``/data/data/<pkg>``: è lo stesso inciampo documentato in
    ``MemoryStore.build_dream_tools``, dove una allowlist tenuta in una forma e
    confrontata nell'altra non matchava mai. Qui il costo di sbagliare è peggiore
    che là: un guard che non matcha mai non fallisce rumorosamente, semplicemente
    lascia passare tutto — cioè non esiste. Entrambi i lati del confronto passano
    di qui, così le due forme coincidono per costruzione.

    Il fallback sul path grezzo copre i casi in cui ``resolve()`` alza OSError
    (loop di symlink, path troppo lungo): resta un confronto coerente, perché
    anche l'altro lato ricadrebbe sulla stessa forma.
    """
    try:
        return path.resolve()
    except OSError:
        return path


def budget_report(
    store: MemoryStore,
    *,
    memory_chars: int,
    user_chars: int,
    soul_chars: int,
) -> list[FileBudget]:
    """Misura i tre file di memoria e li accoppia ai rispettivi budget.

    Prende tre interi separati e non l'oggetto config di proposito: è ciò che
    tiene questo modulo indipendente dallo schema di configurazione, che vive e
    cambia altrove. Il chiamante estrae i numeri da dove preferisce; qui dentro
    non c'è nulla da riallineare quando quello schema si muove.

    L'ordine della lista è quello di rendering (MEMORY, USER, SOUL) ed è parte
    del contratto: ``render_gauge`` non riordina.
    """
    specs = (
        ("MEMORY.md", store.memory_file, memory_chars, False),
        ("USER.md", store.user_file, user_chars, False),
        ("SOUL.md", store.soul_file, soul_chars, True),
    )
    return [
        FileBudget(
            label=label,
            path=path,
            chars=count_chars(path, skip_user_rules=skip),
            budget=max(0, budget),
            skip_user_rules=skip,
        )
        for label, path, budget, skip in specs
    ]


def render_gauge(report: Sequence[FileBudget], *, for_review: bool = False) -> str:
    """Rendi il report come gauge testuale da iniettare nel prompt di Dream.

    Compatto per costruzione — una riga per file più una di istruzioni — perché
    finisce in *ogni* prompt di Dream, che gira ogni due ore: qualsiasi prosa in
    più qui la si paga a ogni run e per sempre.

    ``for_review`` cambia solo la riga di istruzioni, perché i due prompt che
    consumano questo gauge fanno mestieri opposti. Il run incrementale aggiunge,
    e la cosa utile da dirgli è quando smettere; il review pass non aggiunge
    niente per definizione, e "consolida prima di aggiungere" lì è un'istruzione
    che non descrive nessuna azione disponibile. Le misure sono le stesse: è il
    consiglio sopra a non poter essere lo stesso.

    Se **nessun** file del report è enforced — lo stato di ogni installazione coi
    default, dove i tre budget valgono 0 — la testa cambia di nuovo. Le due
    versioni sopra parlano di una soglia che non esiste: una promette un rifiuto
    che il guard non emetterà mai, l'altra nomina "già sotto il proprio budget"
    come predicato di stop, e il modello non può valutarlo su un file che un
    budget non ce l'ha. La testa resta comunque (le misure senza etichetta non si
    capiscono, e il prompt manuale la cerca), ma dice solo ciò che è vero:
    misurato, non applicato.
    """
    if not report:
        return ""
    if not any(item.enforced for item in report):
        tail = (
            "so let the criteria alone decide what goes." if for_review else "nothing is enforced."
        )
        head = (
            "Long-term memory budget (characters). "
            f"Sizes are measured only — no budget is set, {tail}"
        )
    elif for_review:
        # "Shrink what the criteria allow" e non "porta ogni file sotto il
        # budget". La seconda formulazione è un ordine senza eccezioni, e su
        # USER.md contraddice frontalmente le regole che il review pass applica:
        # quel file è fatto di attributi personali, che ``Never delete`` copre.
        # Un modello che riceve le due istruzioni insieme deve rompere il
        # pareggio da solo, e lo romperà in modo diverso a ogni run. Qui il
        # budget è il bersaglio; l'autorità sono i criteri.
        head = (
            "Long-term memory budget (characters). Shrink what the criteria allow; a "
            "file already under its budget does not need to shrink further."
        )
    else:
        head = (
            "Long-term memory budget (characters). "
            f"Past {_CONSOLIDATE_PCT}% consolidate before adding; over budget the write is "
            "refused, so free space in the same turn."
        )
    lines = [head]
    for item in report:
        if item.enforced:
            lines.append(
                f"{item.label} [{item.pct}% — {item.chars:,}/{item.budget:,} chars]"
            )
        else:
            lines.append(f"{item.label} [{item.chars:,} chars — no budget]")
    return "\n".join(lines)


def make_write_size_guard(report: Sequence[FileBudget]) -> WriteSizeGuard:
    """Costruisci il guard che rifiuta le scritture che sforano il budget.

    Il guard è invocato con ``(path_risolto, testo_finale)`` subito prima della
    scrittura e non tocca il filesystem: legge soltanto il file interrogato per
    conoscerne la dimensione attuale, e non scrive nulla in nessun caso. Un path
    che non è nel report passa sempre.

    La dimensione attuale viene riletta a ogni invocazione invece di riusare
    ``FileBudget.chars``: il report è costruito all'inizio del run e nello stesso
    run il modello può aver già potato il file: decidere sul valore fotografato
    allora significherebbe rifiutare una scrittura che sta rientrando.
    """
    budgets: dict[Path, FileBudget] = {
        _canonical(item.path): item for item in report
    }

    def guard(path: Path, new: str) -> str | None:
        item = budgets.get(_canonical(path))
        if item is None or not item.enforced:
            return None
        size = item.measure(new)
        if size <= item.budget:
            return None
        old_size = item.measure_now()
        # La clausola "non sta rimpicciolendo" non è un di più. Anche la potatura
        # passa da una scrittura: senza di essa un file già oltre budget non
        # potrebbe più essere accorciato — il primo tentativo di potarlo verrebbe
        # rifiutato perché il risultato è ancora sopra la soglia — e la feature si
        # autobloccherebbe esattamente sui file che deve sistemare, che è lo stato
        # di partenza sul device (MEMORY.md è già fuori misura oggi).
        if size < old_size:
            return None
        over = size - item.budget
        return (
            f"Write refused: {item.label} would become {size:,} chars, over its "
            f"{item.budget:,} char budget by {over:,}. Nothing was written; the file on disk "
            "is unchanged. Free space first, in this same turn: read the file, delete entries "
            "that are obsolete, superseded, or recoverable from the repo, then write the "
            "shortened text — a write that makes the file smaller is always accepted, even "
            "while it is still over budget. Adding a fact costs the same as pruning one: if "
            "the new fact is worth keeping, something already in the file is not."
        )

    return guard
