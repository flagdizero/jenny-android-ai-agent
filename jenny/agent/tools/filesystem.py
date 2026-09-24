"""File system tools: read, write, edit, list."""

import difflib
import mimetypes
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from jenny.agent.tools.base import Tool, tool_parameters
from jenny.agent.tools.file_state import FileStates, _hash_file, current_file_states
from jenny.agent.tools.filesystem_edit_match import (
    _best_window,
    _find_matches,
    _preserve_quote_style,
    _reindent_like_match,
)
from jenny.agent.tools.path_utils import resolve_workspace_path
from jenny.agent.tools.schema import (
    BooleanSchema,
    IntegerSchema,
    StringSchema,
    tool_parameters_schema,
)
from jenny.agent.wiki_provenance import wiki_page_provenance_guard
from jenny.config.tool_schemas import (
    FileToolsConfig,  # noqa: F401 — re-export (def in config.tool_schemas)
)
from jenny.security.workspace_access import current_tool_workspace, current_turn_is_readonly
from jenny.security.workspace_policy import ReadOnlyTurnError, _path_key, _safe_expanduser
from jenny.utils.helpers import build_image_content_blocks, detect_image_mime
from jenny.utils.path import atomic_write
from jenny.utils.wiki_paths import page_chars, wiki_page_rel

# Costruito una volta all'import: il gancio non ha stato e ricava tutto dal
# percorso che riceve, quindi una istanza per tool sarebbe una istanza per niente.
# ``wiki_provenance`` è una foglia — stdlib e ``wiki_paths``, che questo modulo
# importa già — proprio perché è qui che va montata (v. la sua docstring).
_WIKI_PROVENANCE_GUARD = wiki_page_provenance_guard()

# Gancio pre-scrittura: riceve il path risolto e il testo esatto che finirebbe su
# disco, ritorna ``None`` per lasciar passare o un messaggio di rifiuto da
# restituire al modello al posto della scrittura. Il tipo è dichiarato qui e non
# importato da chi impone il budget: i tool sui file sono usati da tutto l'agente
# e non devono dipendere dal modulo che ne limita uno solo.
WriteSizeGuard = Callable[[Path, str], str | None]


def _page_over_ceiling_note(rel: str, chars: int, ceiling: int) -> str:
    """La frase che dice che *rel* ha appena smesso di essere iniettabile. T9.12.

    **Dice quel che dice la regola SPLIT**, e con le sue parole
    (``templates/agent/gardener.md``, la clausola «A page that outgrows the budget
    is SPLIT»): la stessa conseguenza — saltata intera, in ogni conversazione del
    progetto — e lo stesso rimedio, tagliare lungo le cose di cui la pagina parla
    spostando le frasi **parola per parola**. Se le due divergono, il modello
    riceve un avviso a cui il prompt risponde con un'altra istruzione.

    Funzione con un nome, come :func:`context._pages_left_out_notice`: il testo si
    prova per quel che dice, e il test che lo prova non deve ricopiarlo.
    """
    return (
        f"Note: `{rel}` is now {chars:,} characters, past the {ceiling:,} a turn in this "
        "project can inject — this write is what took it over. From here it is skipped whole "
        "in every conversation in this project: it is on disk and nobody can read it. Split it "
        "along the things it talks about — each part becomes a page named after its own thing, "
        "carrying the sentences that were already about it, moved word for word — then add the "
        "new pages to the map."
    )


class _FsTool(Tool):
    """Shared base for filesystem tools — common init and path resolution."""

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        return ctx.config.file.enable

    @classmethod
    def disabled_reason(cls, ctx: Any) -> str | None:
        """Un solo interruttore spegne tutti i tool sui file, e si vede poco.

        Senza questa frase un subagent che vive di filesystem — ``writer``,
        ``coder``, ``analyst`` — partirebbe con zero tool e riporterebbe di aver
        fallito, che si legge come un problema del modello invece che di
        un'impostazione.
        """
        if not ctx.config.file.enable:
            # Non "Settings > ...": quel pannello non esiste. Questo
            # interruttore vive solo in config.json, e mandare l'utente a
            # cercare una schermata inventata e peggio che non dirgli niente.
            return "file tools are off (tools.file.enable in config.json)"
        return None

    def __init__(
        self,
        workspace: Path | None = None,
        allowed_dir: Path | None = None,
        extra_allowed_dirs: list[Path] | None = None,
        extra_read_allowed_dirs: list[Path] | None = None,
        extra_write_allowed_dirs: list[Path] | None = None,
        extra_write_allowed_files: list[Path] | None = None,
        file_states: FileStates | None = None,
        restrict_to_workspace: bool | None = None,
        write_files_only: bool = False,
        write_size_guard: WriteSizeGuard | None = None,
        entry_archiver: Callable[[Path, str], None] | None = None,
        read_media_dir: bool = True,
    ):
        self._workspace = workspace
        self._allowed_dir = allowed_dir
        # La cartella dei media (``.jenny/media``) fra le radici di **lettura**.
        # Acceso di default, ed e' il comportamento storico: per ogni tool
        # costruito da ``create()`` e' comunque ridondante — quella cartella sta
        # dentro la radice dell'installazione, che il lettore ha gia' — quindi
        # l'unico effetto vivo che ha e' allargare una cassetta costruita a mano
        # con una radice **piu' stretta**. Misurato (T9.10): quella cassetta e'
        # **una sola**, i tool di sola lettura del gardener, la cui radice e' un
        # progetto (``wikis/<nome>``) e non contiene i media — e li' l'allargamento
        # e' contro lo scopo dichiarato, quindi lo si spegne dal costruttore: v.
        # ``GardenerStore.build_tools``. Dream ha
        # ``allowed_dir=workspace`` e ``.jenny/media`` sta **dentro** quella
        # radice, quindi per lui il flag e' inerte e spegnerlo sarebbe un
        # placebo: farebbe *sembrare* imposto un confine che non cambia. L'unico
        # consumatore vivo del ramo acceso e'
        # ``test_filesystem_tools.py::…test_read_allowed_in_media_dir``, cioe' il
        # caso di merito di T4.13 (un subagent a cui si chiede di guardare
        # un'immagine).
        self._read_media_dir = read_media_dir
        # "Nessuna directory scrivibile, solo questi file esatti". Serve a un
        # runner isolato che riscrive pochi file noti (Dream → i suoi file di memoria):
        # senza questo, ``allowed_dir=None`` significa "eredita la radice dello
        # scope", cioè l'intero workspace scrivibile. Va usato insieme a
        # ``extra_write_allowed_files``; da solo nega qualunque scrittura.
        self._write_files_only = write_files_only
        # Legacy alias: extra_allowed_dirs is read-only. Write-capable tools
        # must opt in via extra_write_allowed_dirs.
        self._extra_read_allowed_dirs = [
            *(extra_allowed_dirs or []),
            *(extra_read_allowed_dirs or []),
        ]
        self._extra_write_allowed_dirs = list(extra_write_allowed_dirs or [])
        self._extra_write_allowed_files = list(extra_write_allowed_files or [])
        self._restrict_to_workspace = (
            bool(restrict_to_workspace)
            if restrict_to_workspace is not None
            else allowed_dir is not None
        )
        # Explicit state is used by isolated runners like Dream/subagents.
        # Main AgentLoop tools leave this unset and resolve state from the
        # current async task, which keeps shared tool instances session-safe.
        self._explicit_file_states = file_states
        self._fallback_file_states = FileStates()
        # Assente per default, ed è una decisione: i budget dei file di memoria
        # sono **consultivi** per l'agente principale e vincolanti solo per Dream,
        # che lo passa esplicitamente. Un rifiuto in mezzo a una conversazione
        # arriverebbe all'unico scrittore che ha l'utente lì davanti, e
        # scambierebbe un fallimento visibile con uno invisibile — il fatto appena
        # chiesto non salvato. Il prezzo è noto e sta in ``docs/using/memory.md``:
        # un turno di chat può lasciare il file saturo (misurato sul Titan 2:
        # 2.399 su 2.400) ed è Dream a non trovare più spazio.
        self._write_size_guard = write_size_guard
        # Gancio di degradazione, montato accanto al guard e per il motivo
        # opposto: il guard decide se una scrittura può avvenire, questo salva
        # ciò che quella scrittura sta per portare via. Assente di default —
        # riguarda i due file di memoria e li conosce solo chi lo costruisce
        # (``memory_entries.make_entry_archiver``).
        self._entry_archiver = entry_archiver

    @staticmethod
    def _installation_read_root(workspace: Path) -> Path | None:
        """La radice dell'installazione, quando ``workspace`` e' una sua sottocartella.

        Passo T4.5. L'allargamento delle letture di ``_read_allowed_root`` legge
        ``self._workspace``, e per l'agente principale quello *e'* la radice
        dell'installazione — ma non per un subagent: ``SubagentManager`` gli
        costruisce il ``ToolContext`` con ``workspace`` = la cartella del
        progetto, quindi l'allargamento era un no-op proprio dove serve. Sotto
        ``orchestratorMode`` i subagent sono gli **unici** attori con i tool di
        scrittura dentro un progetto, quindi la ragione dichiarata
        dell'allargamento — ``SkillsLoader`` che passa il percorso di
        ``SKILL.md`` perche' l'agente se lo legga — muore tutta li'.

        Torna ``None`` quando non c'e' niente da aggiungere: workspace non
        distinguibile dalla radice, radice non ancora configurata (i test che
        non montano un ``RuntimeContext``), oppure un workspace che sta **fuori**
        dall'installazione — quello e' un tool costruito per un altro posto, e
        regalargli la radice sarebbe un allargamento non chiesto.

        Solo lettura: il valore finisce in ``extra_read_allowed_dirs``, che
        ``_resolve_write`` non guarda mai (v.
        ``tests/agent/tools/test_subagent_project_reads.py``).
        """
        from jenny.runtime.context import get_runtime_context

        root = get_runtime_context().workspace_dir
        if root is None:
            return None
        try:
            root = _safe_expanduser(root).resolve(strict=False)
            current = _safe_expanduser(workspace).resolve(strict=False)
        except (OSError, RuntimeError, TypeError, ValueError):
            return None
        if current == root or root not in current.parents:
            return None
        return root

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        restrict = ctx.config.restrict_to_workspace
        allowed_dir = Path(ctx.workspace) if restrict else None
        extra_read = [Path(ctx.workspace) / "skills"]
        installation = cls._installation_read_root(Path(ctx.workspace))
        if installation is not None:
            extra_read.append(installation)
        if getattr(ctx.config.file, "expose_package_source", False):
            from jenny.utils.android_assets import get_package_source_root

            source_root = get_package_source_root()
            if source_root is not None:
                extra_read.append(source_root)
        return cls(
            workspace=Path(ctx.workspace),
            allowed_dir=allowed_dir,
            extra_read_allowed_dirs=extra_read,
            file_states=ctx.file_states,
            restrict_to_workspace=ctx.config.restrict_to_workspace,
        )

    @property
    def _file_states(self) -> FileStates:
        if self._explicit_file_states is not None:
            return self._explicit_file_states
        return current_file_states(self._fallback_file_states)

    def _effective_allowed_root(self, access_allowed_root: Path | None) -> Path | None:
        if self._allowed_dir is None or self._workspace is None:
            return access_allowed_root
        try:
            allowed_dir = _safe_expanduser(self._allowed_dir).resolve(strict=False)
            workspace = _safe_expanduser(self._workspace).resolve(strict=False)
        except (OSError, RuntimeError, TypeError, ValueError):
            return access_allowed_root if access_allowed_root is not None else self._allowed_dir
        if allowed_dir == workspace:
            return access_allowed_root
        return allowed_dir

    def _read_allowed_root(self, access: Any) -> Path | None:
        """Il confine di **lettura**: la radice dell'installazione, non quella dello scope.

        La prigione di una sessione-progetto e' sulla scrittura, non sulla
        lettura. In lettura non serve: fuori dalla cartella privata dell'app non
        si arriva comunque, perche' l'app non chiede il permesso di storage — il
        confine vero lo mette Android. Restringere anche le letture non
        aggiungeva sicurezza e toglieva a Jenny la possibilita' di leggere,
        dentro un progetto, la propria skill: ``SkillsLoader`` le passa il
        percorso di ``SKILL.md`` perche' se lo legga da se', e sotto uno scope
        stretto quel percorso veniva poi negato — il caricamento progressivo
        moriva dentro ogni progetto.

        Resta invece una restrizione **esplicita del costruttore** (Dream, un
        subagent con la sua directory): quella e' una scelta di chi ha costruito
        il tool, non dello scope, e non va allargata.

        Attenzione a ``self._workspace``: e' la radice dell'installazione solo
        per chi e' stato costruito con quella. Un subagent riceve la cartella del
        progetto, e per lui questo metodo torna il progetto — l'installazione gli
        arriva da ``_installation_read_root``, come radice di **sola** lettura in
        ``extra_read_allowed_dirs`` (passo T4.5). Il posto giusto e' quello e non
        qui: allargare la ``return`` qui sotto cambierebbe il confine anche per
        chi passa ``allowed_dir`` **uguale** al proprio ``workspace`` — Dream
        e i tool di sola lettura del gardener, che sono costruiti con
        ``workspace=allowed_dir=<progetto>`` proprio per non vedere il resto
        dell'installazione. Quei due cadono su questo stesso ramo: la loro
        directory e' una scelta del costruttore che qui non si distingue da uno
        scope, e allargarla e' l'unica cosa che il passo T4.5 non doveva fare.
        """
        if not access.restrict_to_workspace:
            return None
        explicit = self._effective_allowed_root(None)
        if explicit is not None:
            return explicit
        return self._workspace

    def _resolve_with_extra(
        self,
        path: str,
        extra_allowed_dirs: list[Path] | None,
        extra_allowed_files: list[Path] | None,
        *,
        include_media_dir: bool,
        for_write: bool,
    ) -> Path:
        access = current_tool_workspace(
            self._workspace,
            restrict_to_workspace=self._restrict_to_workspace,
        )
        # La base dei percorsi relativi resta ``project_path`` per entrambi: "il
        # file X" dentro un progetto vuol dire dentro quel progetto, che si stia
        # leggendo o scrivendo. A cambiare e' solo il *confine*.
        # ``write_root()`` e' l'unico posto in cui si decide dove si puo'
        # scrivere (v. ``WorkspaceScope.write_root``); qui sopra ci resta solo la
        # **restrizione del costruttore**, che stringe e non allarga.
        allowed_root = (
            self._effective_allowed_root(access.write_root())
            if for_write
            else self._read_allowed_root(access)
        )
        return resolve_workspace_path(
            path,
            access.project_path,
            allowed_root,
            extra_allowed_dirs,
            extra_allowed_files,
            include_media_dir=include_media_dir,
        )

    def _resolve_read(self, path: str) -> Path:
        return self._resolve_with_extra(
            path,
            self._extra_read_allowed_dirs,
            None,
            include_media_dir=self._read_media_dir,
            for_write=False,
        )

    def _resolve_write(self, path: str) -> Path:
        # Punto di raccolta unico per l'intento di scrittura di tutti i tool
        # write-capable (write_file / edit_file / apply_patch): contarlo qui,
        # prima della risoluzione (che può sollevare ``PermissionError`` o
        # ``WorkspaceBoundaryError``), cattura anche le scritture bloccate.
        # Dream lo usa per non avanzare il cursore quando ha provato a scrivere
        # ma è stato bloccato.
        self._file_states.record_write_attempt()
        # La sola lettura si controlla **qui**, cioe' nello stesso punto in cui
        # si conta l'intento di scrittura: e' l'imbuto di write_file, edit_file e
        # apply_patch, e un tentativo rifiutato resta un tentativo (Dream ci si
        # appoggia per non avanzare il cursore).
        if current_turn_is_readonly():
            raise ReadOnlyTurnError(f"refused write to {path}")
        if self._write_files_only:
            # Bypassa ``_effective_allowed_root``: passare ``allowed_dir=None``
            # più un'allowlist di file non vuota fa scattare la modalità
            # "solo questi file" di ``resolve_allowed_path`` (fail-closed se
            # l'allowlist è vuota).
            access = current_tool_workspace(
                self._workspace,
                restrict_to_workspace=self._restrict_to_workspace,
            )
            return resolve_workspace_path(
                path,
                access.project_path,
                None,
                None,
                self._extra_write_allowed_files,
                include_media_dir=False,
            )
        return self._resolve_with_extra(
            path,
            self._extra_write_allowed_dirs,
            self._extra_write_allowed_files,
            include_media_dir=False,
            for_write=True,
        )

    def _archive_departing(self, path: Path, text: str) -> None:
        """Salva le voci che questa scrittura sta per far sparire.

        Chiamata subito prima della scrittura vera, non insieme al guard: per
        ``apply_patch`` il guard si pronuncia su tutti i file *prima* che parta la
        prima scrittura, e archiviare lì significherebbe degradare voci di una
        patch poi rifiutata.
        """
        if self._entry_archiver is None:
            return
        self._entry_archiver(path, text)

    def _check_write_size(self, path: Path, text: str) -> str | None:
        """Chiede al gancio se questo esatto contenuto può andare su disco.

        Va chiamato con il testo *finale* (conversione CRLF inclusa): il gancio
        misura quello che al giro dopo rileggerà dal file, e un conteggio fatto
        sulla stringa pre-conversione non corrisponderebbe.

        Il rifiuto non passa da ``record_write``: la scrittura non è avvenuta,
        quindi resta un ``writes_attempted`` senza ``writes_ok`` — vedi il
        commento su ``FileStates.writes_refused_budget``.

        Il rifiuto viene registrato **con il testo**, non solo contato: se il
        modello obbedisce al messaggio e riscrive lo stesso file accorciato
        *portandosi dentro* ciò che stava aggiungendo, quella scrittura chiude il
        rifiuto e il run torna a poter commettere. Se invece pota e perde il
        contenuto nuovo, il rifiuto resta aperto — distinzione che il solo
        percorso non sa fare, perché il guard accetta sia una scrittura che
        rientra nel tetto sia una che rimpicciolisce (v.
        ``FileStates.record_write_refused``).

        **Due ganci, e il secondo non lo monta nessuno.** Il primo è quello
        iniettato dal chiamante (il tetto dei file di memoria, la cessione del
        passo del giardiniere, la sua guardia di provenienza): chi non ne passa
        nessuno non ne ha. Il secondo è
        :func:`~jenny.agent.wiki_provenance.wiki_page_provenance_guard`, che vale
        **sempre** e ricava il progetto dal percorso — perché il difetto del 26/08
        era esattamente che un gancio andava montato e la conversazione non lo
        montava: la passata con meno contesto era l'unica trattenuta. Fuori da una
        ``wiki/`` di progetto quel gancio torna ``None`` e non costa niente.

        Il gancio iniettato resta **primo**: quando è la cessione del passo, quel
        rifiuto è l'unica cosa vera da dire alla passata (v.
        ``gardener._compose_write_guards``), e un rifiuto di provenienza che
        arrivasse prima le racconterebbe un'altra storia. Il nome del metodo dice
        ancora «size» per la ragione già scritta in
        ``GardenerStore.build_tools``: lo slot è uno e il suo contratto è generico
        da un pezzo — «questo contenuto può andare su disco?».
        """
        refusal = None
        guard = self._write_size_guard
        if guard is not None:
            refusal = guard(path, text)
        if refusal is None:
            refusal = _WIKI_PROVENANCE_GUARD(path, text)
        if refusal is None:
            return None
        self._file_states.record_write_refused(path, text)
        return refusal

    def _wiki_page_ceiling_note(self, path: Path, text: str) -> str | None:
        """L'avviso se *questa* scrittura porta una pagina oltre il tetto. **T9.12.**

        Va chiamato **prima** della scrittura e col testo finale (conversione CRLF
        inclusa): il "prima" lo legge da disco, e dopo non c'e' piu'.

        Tre soglie di silenzio, e la seconda e' tutto il task.

        1. **Solo una pagina di un progetto** (:func:`wiki_page_rel`). Chi scrive
           non sa se sta scrivendo dentro un progetto o in ``memory/``, che ha un
           budget diverso e un guard suo (:data:`WriteSizeGuard`); la mappa ha un
           tetto diverso ancora. Il percorso e' l'unica cosa che lo dice.
        2. **Scatta sulla transizione, mai sullo stato.** Una pagina che era
           *gia'* oltre non produce niente. Misurato sulla copia in sola lettura
           delle otto wiki del dispositivo (274 pagine, 24/08): **25 sono oltre il
           tetto e 78 oltre i 4.000**, cioe' a un append modesto da esso. Un
           avviso sullo stato darebbe alla prima passata su quel corpo
           venticinque richiami e trasformerebbe una passata di cattura in una di
           potatura — che e' esattamente il rischio da evitare; sulla transizione
           ne da' **uno**, alla scrittura che l'ha causata, e mai piu'.
        3. **Il predicato e' esatto**, non una stima: il tetto e' quello della
           *sezione* pagine, quindi «questa pagina entrera'» dipende anche
           dall'ordine della mappa e dalle altre pagine — ma una pagina che da
           sola costa piu' del tetto non entra **in nessun ordine**. E' quel che
           il lint chiama ``PAGE_MAX_CHARS`` e quel che l'inventario della
           passata annota (``GardenerStore.build_inventory``, T3.14).

        Il tetto si legge da :func:`gardener.page_ceiling` e non da una terza
        copia del numero (T3.12): l'import e' dentro la funzione perche'
        ``agent/gardener.py`` — e ``agent/context.py`` che sta sotto — tirano
        dentro mezzo repo, e i tool sui file li importa tutto l'agente. La lettura
        dell'attributo a ogni chiamata e' anche quel che rende la condivisione
        **provabile** invece che dichiarata.

        **Costo**: nel caso normale un ``suffix``, il giro sui segmenti del
        percorso e una misura della stringa che si aveva gia' in mano. Il file si
        rilegge solo quando il testo nuovo e' oltre il tetto **ed** e' una pagina,
        cioe' quasi mai — e la' una lettura in piu' e' il prezzo di sapere se la
        pagina ci stava prima.
        """
        rel = wiki_page_rel(path)
        if rel is None:
            return None
        # **Sotto la porta del percorso**, non sopra: cosi' una scrittura che non
        # riguarda una pagina non carica niente, e chi non tocca wiki mai —
        # l'autoscrittura su un ``.py``, il diario, ``memory/`` — non paga
        # l'import nemmeno una volta.
        from jenny.agent.gardener import page_ceiling

        ceiling = page_ceiling()
        chars = page_chars(text)
        if chars <= ceiling:
            return None
        try:
            before = page_chars(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            # Assente o illeggibile: prima non c'era nessuna pagina che entrava,
            # quindi la transizione e' reale. Un file illeggibile l'iniettore lo
            # conta fra le rimaste fuori, non fra quelle oltre il tetto.
            before = 0
        if before > ceiling:
            return None
        return _page_over_ceiling_note(rel, chars, ceiling)

    def _is_exact_allowed_file(self, path: Path) -> bool:
        """``path`` è uno dei file esatti per cui questo tool è stato costruito.

        Cioè una voce di ``extra_write_allowed_files``, l'allowlist che un runner
        isolato passa quando il tool esiste *per* riscrivere quei file e nient'altro:
        memory/MEMORY.md, SOUL.md, USER.md per Dream.
        Confronto sulla forma risolta e con la stessa chiave di
        ``workspace_policy`` — la allowlist è già ciò che ha autorizzato questa
        scrittura, quindi le due forme coincidono per costruzione.
        """
        if not self._extra_write_allowed_files:
            return False
        key = _path_key(path)
        for allowed in self._extra_write_allowed_files:
            try:
                resolved = Path(allowed).resolve()
            except (OSError, RuntimeError, TypeError, ValueError):
                continue
            if _path_key(resolved) == key:
                return True
        return False

    def _commit_write(self, path: Path, text: str) -> None:
        """Imbuto unico di scrittura dei tool sui file, e sede della scelta atomica.

        La regola di ``.agent/gotchas.md`` vale per verso: i file **dell'utente**
        si scrivono in posto (rimpiazzare l'inode cambierebbe la semantica —
        permessi, hardlink), mentre lo stato che Jenny **rilegge da sé** passa da
        ``atomic_write``. Qui il discriminante è ``_is_exact_allowed_file``: un
        tool costruito con una allowlist di file esatti esiste solo per riscrivere
        stato di Jenny, e su Android un processo ucciso a metà di quella
        scrittura lascerebbe un file troncato che si legge come integro.

        Passa da qui **ogni** scrittura di ``write_file``/``edit_file``/
        ``apply_patch``: un nuovo punto di scrittura eredita la decisione invece
        di doversela ricordare. Byte e non testo perché ``apply_patch`` scrive con
        ``newline=""`` e la conversione CRLF è già stata fatta da chi chiama: il
        testo qui è già esattamente ciò che deve finire su disco.
        """
        data = text.encode("utf-8")
        if self._is_exact_allowed_file(path):
            atomic_write(path, data)
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def _resolve(self, path: str) -> Path:
        return self._resolve_read(path)

    def _display_workspace(self) -> Path | None:
        return current_tool_workspace(self._workspace).project_path


# ---------------------------------------------------------------------------
# read_file
# ---------------------------------------------------------------------------


_BLOCKED_DEVICE_PATHS = frozenset({
    "/dev/zero", "/dev/random", "/dev/urandom", "/dev/full",
    "/dev/stdin", "/dev/stdout", "/dev/stderr",
    "/dev/tty", "/dev/console",
    "/dev/fd/0", "/dev/fd/1", "/dev/fd/2",
})


def _is_blocked_device(path: str | Path) -> bool:
    """Check if path is a blocked device that could hang or produce infinite output."""
    import re
    raw = str(path)

    # Resolve symlinks to check the actual target
    try:
        resolved = str(Path(raw).resolve())
    except (OSError, ValueError):
        resolved = raw

    if raw in _BLOCKED_DEVICE_PATHS or resolved in _BLOCKED_DEVICE_PATHS:
        return True
    if re.match(r"/proc/\d+/fd/[012]$", raw) or re.match(r"/proc/self/fd/[012]$", raw):
        return True
    if re.match(r"/proc/\d+/fd/[012]$", resolved) or re.match(r"/proc/self/fd/[012]$", resolved):
        return True

    # Check if resolved path starts with /dev/ (covers symlinks to devices)
    # Note: /dev/ layout differs on Android but blocking the entire subtree is
    # correct for security regardless of platform.
    if resolved.startswith("/dev/"):
        return True
    return False


def _parse_page_range(pages: str, total: int) -> tuple[int, int]:
    """Parse a page range like '2-5' into 0-based (start, end) inclusive."""
    parts = pages.strip().split("-")
    if len(parts) == 1:
        p = int(parts[0])
        return max(0, p - 1), min(p - 1, total - 1)
    start = int(parts[0])
    end = int(parts[1])
    return max(0, start - 1), min(end - 1, total - 1)


@tool_parameters(
    tool_parameters_schema(
        path=StringSchema("The file path to read"),
        offset=IntegerSchema(
            1,
            description="Line number to start reading from (1-indexed, default 1)",
            minimum=1,
        ),
        limit=IntegerSchema(
            2000,
            description="Maximum number of lines to read (default 2000)",
            minimum=1,
        ),
        pages=StringSchema("Page range for PDF files, e.g. '1-5' (default: all, max 20 pages)"),
        force=BooleanSchema(
            description="Bypass same-file read deduplication and return content again.",
            default=False,
        ),
        required=["path"],
    )
)
class ReadFileTool(_FsTool):
    """Read file contents with optional line-based pagination."""
    _scopes = {"core", "orchestrator", "subagent"}

    _MAX_CHARS = 128_000
    _DEFAULT_LIMIT = 2000
    _MAX_PDF_PAGES = 20

    @property
    def name(self) -> str:
        return "read_file"

    @property
    def description(self) -> str:
        return (
            "Read a file (text, image, or PDF document). "
            "Text output format: LINE_NUM|CONTENT. "
            "Images return visual content for analysis. "
            "Supports PDF documents. "
            "Use find_files/list_dir first when the path is uncertain. "
            "Read the relevant range before editing so replacements or patches "
            "are based on current content. "
            "Use offset and limit for large text files. "
            "Use force=true to re-read content even if unchanged. "
            "Reads exceeding ~128K chars are truncated."
        )

    @property
    def read_only(self) -> bool:
        return True

    async def execute(
        self,
        path: str | None = None,
        offset: int = 1,
        limit: int | None = None,
        pages: str | None = None,
        force: bool = False,
        **kwargs: Any,
    ) -> Any:
        try:
            if not path:
                return "Error reading file: Unknown path"

            # Device path blacklist
            if _is_blocked_device(path):
                return f"Error: Reading {path} is blocked (device path that could hang or produce infinite output)."

            fp = self._resolve_read(path)
            if _is_blocked_device(fp):
                return f"Error: Reading {fp} is blocked (device path that could hang or produce infinite output)."
            if not fp.exists():
                return f"Error: File not found: {path}"
            if not fp.is_file():
                return f"Error: Not a file: {path}"

            # PDF support
            if fp.suffix.lower() == ".pdf":
                return self._read_pdf(fp, pages)

            raw = fp.read_bytes()
            if not raw:
                return f"(Empty file: {path})"

            mime = detect_image_mime(raw) or mimetypes.guess_type(path)[0]
            if mime and mime.startswith("image/"):
                return build_image_content_blocks(raw, mime, str(fp), f"(Image file: {path})")

            # Read dedup: same path + offset + limit + unchanged mtime → stub
            # Always check for external modifications before dedup
            entry = self._file_states.get(fp)
            try:
                current_mtime = os.path.getmtime(fp)
            except OSError:
                current_mtime = 0.0
            if (
                not force
                and entry
                and entry.can_dedup
                and entry.offset == offset
                and entry.limit == limit
            ):
                if current_mtime != entry.mtime:
                    # File was modified externally - force full read and mark as not dedupable
                    entry.can_dedup = False
                    # Continue to read full content (don't return dedup message)
                else:
                    # File unchanged - return dedup message
                    # But only if content is actually unchanged (not just mtime)
                    current_hash = _hash_file(str(fp))
                    if current_hash == entry.content_hash:
                        return (
                            f"[File unchanged since last read: {path} \u2014 if this "
                            "conversation no longer contains its content, read it again "
                            "with force=true]"
                        )
                    else:
                        # Content changed despite same mtime - force full read
                        entry.can_dedup = False
            else:
                # No previous state or marked as not dedupable - read full content
                # Force full read by setting can_dedup to False for this read
                if entry:
                    entry.can_dedup = False

            # Read the file content after dedup check
            raw = fp.read_bytes()
            try:
                text_content = raw.decode("utf-8")
            except UnicodeDecodeError:
                # Binary file - return error message
                mime = detect_image_mime(raw) or mimetypes.guess_type(path)[0]
                if mime and mime.startswith("image/"):
                    return build_image_content_blocks(raw, mime, str(fp), f"(Image file: {path})")
                return f"Error: Cannot read binary file {path} (MIME: {mime or 'unknown'}). Only UTF-8 text and images are supported."

            # Normalize CRLF -> LF before line-splitting. Primarily a Windows
            # concern (git checkouts with autocrlf, editors saving CRLF) but
            # applied on all platforms so downstream StrReplace/Grep behavior
            # is consistent regardless of where the file was written.
            text_content = text_content.replace("\r\n", "\n")

            all_lines = text_content.splitlines()
            total = len(all_lines)

            if offset < 1:
                offset = 1
            if offset > total:
                return f"Error: offset {offset} is beyond end of file ({total} lines)"

            start = offset - 1
            end = min(start + (limit or self._DEFAULT_LIMIT), total)
            numbered = [f"{start + i + 1}| {line}" for i, line in enumerate(all_lines[start:end])]
            result = "\n".join(numbered)

            if len(result) > self._MAX_CHARS:
                trimmed, chars = [], 0
                for line in numbered:
                    chars += len(line) + 1
                    if chars > self._MAX_CHARS:
                        break
                    trimmed.append(line)
                end = start + len(trimmed)
                result = "\n".join(trimmed)

            if end < total:
                result += f"\n\n(Showing lines {offset}-{end} of {total}. Use offset={end + 1} to continue.)"
            else:
                result += f"\n\n(End of file — {total} lines total)"
            self._file_states.record_read(fp, offset=offset, limit=limit)
            return result
        except PermissionError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error reading file: {e}"

    def _read_pdf(self, fp: Path, pages: str | None) -> str:
        from pypdf import PdfReader

        reader = PdfReader(str(fp))
        total_pages = len(reader.pages)
        if pages:
            try:
                start, end = _parse_page_range(pages, total_pages)
            except (ValueError, IndexError):
                return f"Error: Invalid page range '{pages}'. Use format like '1-5'."
            if start > end or start >= total_pages:
                return f"Error: Page range '{pages}' is out of bounds (document has {total_pages} pages)."
        else:
            start = 0
            end = min(total_pages - 1, self._MAX_PDF_PAGES - 1)

        if end - start + 1 > self._MAX_PDF_PAGES:
            end = start + self._MAX_PDF_PAGES - 1

        parts: list[str] = []
        for i in range(start, end + 1):
            text = (reader.pages[i].extract_text() or "").strip()
            if text:
                parts.append(f"--- Page {i + 1} ---\n{text}")

        if not parts:
            return f"(PDF has no extractable text: {fp})"

        result = "\n\n".join(parts)
        if end < total_pages - 1:
            result += (
                f"\n\n(Showing pages {start + 1}-{end + 1} of {total_pages}. "
                f"Use pages='{end + 2}-{min(end + 1 + self._MAX_PDF_PAGES, total_pages)}' "
                "to continue.)"
            )
        if len(result) > self._MAX_CHARS:
            result = result[:self._MAX_CHARS] + "\n\n(PDF text truncated at ~128K chars)"
        return result

# ---------------------------------------------------------------------------
# write_file
# ---------------------------------------------------------------------------


@tool_parameters(
    tool_parameters_schema(
        path=StringSchema("The file path to write to"),
        content=StringSchema("The content to write"),
        required=["path", "content"],
    )
)
class WriteFileTool(_FsTool):
    """Write content to a file."""
    _scopes = {"core", "subagent"}

    @property
    def name(self) -> str:
        return "write_file"

    @property
    def description(self) -> str:
        return (
            "Create a new file or intentionally replace an entire file with "
            "the provided content. Overwrites existing files and creates parent "
            "directories as needed. For code changes or partial edits, prefer "
            "apply_patch; use edit_file only for small exact replacements."
        )

    async def execute(self, path: str | None = None, content: str | None = None, **kwargs: Any) -> str:
        try:
            if not path:
                raise ValueError("Unknown path")
            if content is None:
                raise ValueError("Unknown content")
            fp = self._resolve_write(path)
            # Prima di ``_commit_write`` (che fa la mkdir): una scrittura
            # rifiutata non deve nemmeno lasciare in giro la directory che
            # avrebbe dovuto contenerla.
            refusal = self._check_write_size(fp, content)
            if refusal is not None:
                return refusal
            # Prima della scrittura: l'avviso confronta col "prima", che dopo non
            # esiste piu'. Vale anche qui e non solo per i due tool che lavorano
            # per aggiunta — il conteggio che questo tool ritorna dice *quanto*
            # ha scritto, non che quel quanto ha appena spento la pagina.
            note = self._wiki_page_ceiling_note(fp, content)
            self._archive_departing(fp, content)
            self._commit_write(fp, content)
            self._file_states.record_write(fp)
            msg = f"Successfully wrote {len(content)} characters to {fp}"
            return msg if note is None else f"{msg}\n{note}"
        except PermissionError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error writing file: {e}"


# ---------------------------------------------------------------------------
# edit_file
# ---------------------------------------------------------------------------





@tool_parameters(
    tool_parameters_schema(
        path=StringSchema("The file path to edit"),
        old_text=StringSchema("The text to find and replace"),
        new_text=StringSchema("The text to replace with"),
        replace_all=BooleanSchema(description="Replace all occurrences (default false)"),
        occurrence=IntegerSchema(
            1,
            description="Optional 1-based occurrence to replace when old_text appears multiple times.",
            minimum=1,
            nullable=True,
        ),
        line_hint=IntegerSchema(
            1,
            description="Optional 1-based line hint used to choose the nearest match.",
            minimum=1,
            nullable=True,
        ),
        expected_replacements=IntegerSchema(
            1,
            description="Optional guard for the number of replacements that must be made.",
            minimum=1,
            nullable=True,
        ),
        required=["path", "old_text", "new_text"],
    )
)
class EditFileTool(_FsTool):
    """Edit a file by replacing text with fallback matching."""
    _scopes = {"core", "subagent"}

    _MAX_EDIT_FILE_SIZE = 1024 * 1024 * 1024  # 1 GiB
    _MARKDOWN_EXTS = frozenset({".md", ".mdx", ".markdown"})

    @property
    def name(self) -> str:
        return "edit_file"

    @property
    def description(self) -> str:
        return (
            "Perform a small, exact replacement in one file by replacing "
            "old_text with new_text. Use this for narrow text substitutions "
            "with old_text copied from read_file. For multi-file, structural, "
            "or generated code edits, prefer apply_patch. If old_text matches "
            "multiple times, provide more context or set occurrence, line_hint, "
            "replace_all, and expected_replacements. Shows closest-match "
            "diagnostics on failure."
        )

    @staticmethod
    def _strip_trailing_ws(text: str) -> str:
        """Strip trailing whitespace from each line."""
        return "\n".join(line.rstrip() for line in text.split("\n"))

    async def execute(
        self, path: str | None = None, old_text: str | None = None,
        new_text: str | None = None,
        replace_all: bool = False, occurrence: int | None = None,
        line_hint: int | None = None, expected_replacements: int | None = None, **kwargs: Any,
    ) -> str:
        try:
            if not path:
                raise ValueError("Unknown path")
            if old_text is None:
                raise ValueError("Unknown old_text")
            if new_text is None:
                raise ValueError("Unknown new_text")
            if occurrence is not None and occurrence < 1:
                return "Error: occurrence must be >= 1."
            if line_hint is not None and line_hint < 1:
                return "Error: line_hint must be >= 1."
            if expected_replacements is not None and expected_replacements < 1:
                return "Error: expected_replacements must be >= 1."

            fp = self._resolve_write(path)

            # Create-file semantics: old_text='' + file doesn't exist → create
            if not fp.exists():
                if old_text == "":
                    refusal = self._check_write_size(fp, new_text)
                    if refusal is not None:
                        return refusal
                    note = self._wiki_page_ceiling_note(fp, new_text)
                    self._archive_departing(fp, new_text)
                    self._commit_write(fp, new_text)
                    self._file_states.record_write(fp)
                    msg = f"Successfully created {fp}"
                    return msg if note is None else f"{msg}\n{note}"
                return self._file_not_found_msg(path, fp)

            # File size protection
            try:
                fsize = fp.stat().st_size
            except OSError:
                fsize = 0
            if fsize > self._MAX_EDIT_FILE_SIZE:
                return f"Error: File too large to edit ({fsize / (1024**3):.1f} GiB). Maximum is 1 GiB."

            # Create-file: old_text='' but file exists and not empty → reject
            if old_text == "":
                raw = fp.read_bytes()
                content = raw.decode("utf-8")
                if content.strip():
                    return f"Error: Cannot create file — {path} already exists and is not empty."
                refusal = self._check_write_size(fp, new_text)
                if refusal is not None:
                    return refusal
                note = self._wiki_page_ceiling_note(fp, new_text)
                self._archive_departing(fp, new_text)
                self._commit_write(fp, new_text)
                self._file_states.record_write(fp)
                msg = f"Successfully edited {fp}"
                return msg if note is None else f"{msg}\n{note}"

            # Read-before-edit check
            warning = self._file_states.check_read(fp)

            raw = fp.read_bytes()
            uses_crlf = b"\r\n" in raw
            content = raw.decode("utf-8").replace("\r\n", "\n")
            norm_old = old_text.replace("\r\n", "\n")
            matches = _find_matches(content, norm_old)

            if not matches:
                return self._not_found_msg(old_text, content, path)
            count = len(matches)
            if replace_all and occurrence is not None:
                return "Error: occurrence cannot be used with replace_all=true."
            if replace_all and line_hint is not None:
                return "Error: line_hint cannot be used with replace_all=true."
            if occurrence is not None and line_hint is not None:
                return "Error: line_hint cannot be used with occurrence."
            if count > 1 and not replace_all:
                if occurrence is not None:
                    if occurrence > count:
                        return (
                            f"Error: occurrence {occurrence} is out of range; "
                            f"old_text appears {count} times."
                        )
                elif line_hint is not None:
                    nearest = min(matches, key=lambda match: abs(match.line - line_hint))
                    distance = abs(nearest.line - line_hint)
                    if sum(1 for match in matches if abs(match.line - line_hint) == distance) > 1:
                        return (
                            f"Error: line_hint {line_hint} is ambiguous; "
                            f"old_text appears {count} times."
                        )
                else:
                    line_numbers = [match.line for match in matches]
                    preview = ", ".join(f"line {n}" for n in line_numbers[:3])
                    if len(line_numbers) > 3:
                        preview += ", ..."
                    location_hint = f" at {preview}" if preview else ""
                    return (
                        f"Warning: old_text appears {count} times{location_hint}. "
                        "Provide more context, set occurrence to choose one match, "
                        "or set replace_all=true."
                    )
            elif occurrence is not None and occurrence > count:
                return (
                    f"Error: occurrence {occurrence} is out of range; "
                    f"old_text appears {count} time."
                )

            norm_new = new_text.replace("\r\n", "\n")

            # Trailing whitespace stripping (skip markdown to preserve double-space line breaks)
            if fp.suffix.lower() not in self._MARKDOWN_EXTS:
                norm_new = self._strip_trailing_ws(norm_new)

            if replace_all:
                selected = matches
            elif line_hint is not None:
                selected = [min(matches, key=lambda match: abs(match.line - line_hint))]
            else:
                selected = [matches[occurrence - 1 if occurrence else 0]]
            if expected_replacements is not None and len(selected) != expected_replacements:
                return (
                    f"Error: expected {expected_replacements} replacements but "
                    f"would make {len(selected)}."
                )
            new_content = content
            for match in reversed(selected):
                replacement = _preserve_quote_style(norm_old, match.text, norm_new)
                replacement = _reindent_like_match(norm_old, match.text, replacement)

                # Delete-line cleanup: when deleting text (new_text=''), consume trailing
                # newline to avoid leaving a blank line
                end = match.end
                if replacement == "" and not match.text.endswith("\n") and content[end:end + 1] == "\n":
                    end += 1

                new_content = new_content[: match.start] + replacement + new_content[end:]
            if uses_crlf:
                new_content = new_content.replace("\n", "\r\n")

            # Dopo la conversione CRLF: il gancio deve pesare la stringa che
            # finisce davvero su disco, non quella normalizzata a LF.
            refusal = self._check_write_size(fp, new_content)
            if refusal is not None:
                return refusal
            note = self._wiki_page_ceiling_note(fp, new_content)
            self._archive_departing(fp, new_content)
            self._commit_write(fp, new_content)
            self._file_states.record_write(fp)
            msg = f"Successfully edited {fp}"
            if warning:
                msg = f"{warning}\n{msg}"
            if note is not None:
                # Dopo l'esito e non prima: l'avviso parla di quel che il file *e'
                # diventato*, quindi si legge dopo aver saputo che la scrittura c'e'
                # stata. Il ``warning`` della lettura-prima-della-modifica sta
                # invece sopra, perche' parla di quel che la scrittura non sapeva.
                msg = f"{msg}\n{note}"
            return msg
        except PermissionError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error editing file: {e}"

    def _file_not_found_msg(self, path: str, fp: Path) -> str:
        """Build an error message with 'Did you mean ...?' suggestions."""
        parent = fp.parent
        suggestions: list[str] = []
        if parent.is_dir():
            siblings = [f.name for f in parent.iterdir() if f.is_file()]
            close = difflib.get_close_matches(fp.name, siblings, n=3, cutoff=0.6)
            suggestions = [str(parent / c) for c in close]
        parts = [f"Error: File not found: {path}"]
        if suggestions:
            parts.append("Did you mean: " + ", ".join(suggestions) + "?")
        return "\n".join(parts)

    @staticmethod
    def _not_found_msg(old_text: str, content: str, path: str) -> str:
        best_ratio, best_start, best_window_lines, hints = _best_window(old_text, content)
        if best_ratio > 0.5:
            diff = "\n".join(difflib.unified_diff(
                old_text.splitlines(keepends=True),
                best_window_lines,
                fromfile="old_text (provided)",
                tofile=f"{path} (actual, line {best_start + 1})",
                lineterm="",
            ))
            hint_text = ""
            if hints:
                hint_text = "\nPossible cause: " + ", ".join(hints) + "."
            return (
                f"Error: old_text not found in {path}."
                f"{hint_text}\nBest match ({best_ratio:.0%} similar) at line {best_start + 1}:\n{diff}"
            )

        if hints:
            return (
                f"Error: old_text not found in {path}. "
                f"Possible cause: {', '.join(hints)}. "
                "Copy the exact text from read_file and try again."
            )
        return f"Error: old_text not found in {path}. No similar text found. Verify the file content."


# ---------------------------------------------------------------------------
# list_dir
# ---------------------------------------------------------------------------

@tool_parameters(
    tool_parameters_schema(
        path=StringSchema("The directory path to list"),
        recursive=BooleanSchema(description="Recursively list all files (default false)"),
        max_entries=IntegerSchema(
            200,
            description="Maximum entries to return (default 200)",
            minimum=1,
        ),
        required=["path"],
    )
)
class ListDirTool(_FsTool):
    """List directory contents with optional recursion."""
    _scopes = {"core", "orchestrator", "subagent"}

    _DEFAULT_MAX = 200
    _IGNORE_DIRS = {
        ".git", "node_modules", "__pycache__", ".venv", "venv",
        "dist", "build", ".tox", ".mypy_cache", ".pytest_cache",
        ".ruff_cache", ".coverage", "htmlcov",
    }

    @property
    def name(self) -> str:
        return "list_dir"

    @property
    def description(self) -> str:
        return (
            "List the contents of a directory. "
            "Set recursive=true to explore nested structure. "
            "Common noise directories (.git, node_modules, __pycache__, etc.) are auto-ignored."
        )

    @property
    def read_only(self) -> bool:
        return True

    async def execute(
        self, path: str | None = None, recursive: bool = False,
        max_entries: int | None = None, **kwargs: Any,
    ) -> str:
        try:
            if path is None:
                raise ValueError("Unknown path")
            dp = self._resolve(path)
            if not dp.exists():
                return f"Error: Directory not found: {path}"
            if not dp.is_dir():
                return f"Error: Not a directory: {path}"

            cap = max_entries or self._DEFAULT_MAX
            items: list[str] = []
            total = 0

            if recursive:
                for item in sorted(dp.rglob("*")):
                    if any(p in self._IGNORE_DIRS for p in item.parts):
                        continue
                    total += 1
                    if len(items) < cap:
                        rel = item.relative_to(dp)
                        items.append(f"{rel}/" if item.is_dir() else str(rel))
            else:
                for item in sorted(dp.iterdir()):
                    if item.name in self._IGNORE_DIRS:
                        continue
                    total += 1
                    if len(items) < cap:
                        pfx = "📁 " if item.is_dir() else "📄 "
                        items.append(f"{pfx}{item.name}")

            if not items and total == 0:
                return f"Directory {path} is empty"

            result = "\n".join(items)
            if total > cap:
                result += f"\n\n(truncated, showing first {cap} of {total} entries)"
            return result
        except PermissionError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error listing directory: {e}"


# Registrazione esplicita dei tool di questo modulo (Fase 5.3): il
# ToolLoader legge questa lista invece della reflection dir(). Un nuovo
# tool va aggiunto qui esplicitamente.
TOOLS = [ReadFileTool, WriteFileTool, EditFileTool, ListDirTool]
