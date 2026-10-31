"""Workspace access scope and sandbox capability helpers."""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal

from loguru import logger

from jenny.config.runtime_env import (
    WORKSPACE_SANDBOX_ENFORCED_ENV,
    WORKSPACE_SANDBOX_ENFORCED_LEGACY_ENV,
    WORKSPACE_SANDBOX_PROVIDER_ENV,
)
from jenny.security.workspace_policy import _safe_expanduser
from jenny.session.keys import (
    PROJECT_SESSION_PREFIX,
    is_project_session_key,
    notebook_of_cron_session,
)

WorkspaceAccessMode = Literal["restricted", "full"]
WORKSPACE_SCOPE_METADATA_KEY = "workspace_scope"
# Sola lettura: **un dato a parte, e non un terzo valore di ``access_mode``.**
# I due assi sono indipendenti — un progetto in sola lettura e' *restricted E*
# non scrivibile, e un enum a tre valori non sa dirlo senza rendere ambiguo
# ``restrict_to_workspace``, che governa anche il confine di lettura.
#
# Arriva **nel messaggio** e non dalla chiave di sessione, al contrario della
# cartella (v. ``for_turn``): la cartella non deve poter divergere, la
# scrivibilita' deve essere quella che l'utente vedeva quando ha premuto invio.
WORKSPACE_READONLY_METADATA_KEY = "readonly"
_ACCESS_MODES = {"restricted", "full"}

_TRUE_VALUES = {"1", "true", "yes", "on", "enabled"}
_FALSE_VALUES = {"0", "false", "no", "off", "disabled", ""}
_PROVIDER_LABELS = {
    "none": "None",
    "unknown": "Unknown system sandbox",
    "android_app_sandbox": "Android App Sandbox",
    "macos_app_sandbox": "macOS App Sandbox",
}

_CURRENT_WORKSPACE_SCOPE: ContextVar["WorkspaceScope | None"] = ContextVar(
    "jenny_workspace_scope",
    default=None,
)


class WorkspaceScopeError(ValueError):
    """Raised when a requested WebUI workspace scope is invalid."""

    status = 400

    def __init__(self, message: str, *, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


@dataclass(frozen=True)
class WorkspaceSandboxStatus:
    """Resolved workspace sandbox state for runtime display and tooling."""

    restrict_to_workspace: bool
    workspace_root: str
    level: str
    enforced: bool
    provider: str
    provider_label: str
    summary: str

    def as_dict(self) -> dict[str, object]:
        return {
            "restrict_to_workspace": self.restrict_to_workspace,
            "workspace_root": self.workspace_root,
            "level": self.level,
            "enforced": self.enforced,
            "provider": self.provider,
            "provider_label": self.provider_label,
            "summary": self.summary,
        }


@dataclass(frozen=True)
class WorkspaceScope:
    """Effective project root and access mode for one agent turn."""

    project_path: Path
    access_mode: WorkspaceAccessMode
    restrict_to_workspace: bool
    sandbox_status: WorkspaceSandboxStatus
    # Se questo turno puo' cambiare qualcosa. ``False`` = sola lettura, cioe'
    # "non cambia niente sul telefono": file, download, storage delle app,
    # memoria, cron, installazione dell'app. Restano possibili l'invio di
    # messaggi e ``ssh_exec``, che sono un altro asse (deciso il 22/08).
    #
    # **Il default e' scrivibile, e deve restarlo.** Il flag arriva da un
    # messaggio dell'utente; le sessioni interne (cron, Dream, heartbeat) non ne
    # hanno uno, e un default a ``False`` le spegnerebbe tutte in silenzio.
    writable: bool = True

    @property
    def project_name(self) -> str:
        return self.project_path.name or str(self.project_path)

    def without_write_access(self) -> "WorkspaceScope":
        """Lo stesso scope, senza il permesso di scrivere.

        Il nome evita ``read_only``, che su ``Tool`` esiste gia' e vuol dire
        un'altra cosa — "privo di effetti collaterali, parallelizzabile" — e il
        cui default e' ``False`` per ogni tool che nessuno ha classificato.
        Quel flag **non e' l'inventario** delle scritture: usarlo per decidere
        chi gira in sola lettura spegnerebbe decine di letture innocue, che e'
        l'errore "filtra la cassetta dei tool" travestito.
        """
        return self if not self.writable else replace(self, writable=False)

    def write_root(self) -> Path:
        """**L'unica risposta a «dove posso scrivere» per questo turno.**

        Passo T4.4: il confine di scrittura era ricalcolato in sei posti — i
        tool file, i builtin di ``python_exec``, i wrapper di ``os``/``io`` dello
        stesso modulo, ``download`` e ``journal`` — e i sei non erano d'accordo.
        Nessuno dei sei aveva torto da solo; l'insieme sì, perché "che cosa puo'
        cambiare questo turno" e' UNA domanda e sei risposte non possono che
        divergere. Da qui questo metodo, e la regola: chi deve scrivere lo chiede
        qui, e non ricava una radice da se'.

        Torna sempre una cartella, mai ``None``: la radice del turno esiste anche
        quando non c'e' nessun confine da far rispettare. E' un dato diverso da
        "la restrizione e' attiva" (``restrict_to_workspace``) e da "questo turno
        puo' scrivere" (``writable``), e mescolarli e' esattamente come sono nate
        le sei versioni. Chi ha bisogno del **confine** — cioe' della radice piu'
        ``None`` quando non ce n'e' uno — chiede a
        :meth:`ToolWorkspace.write_root`, che e' derivata da questa.

        In scrittura e' la cartella del progetto legato; **in lettura non e'
        questa** (v. ``FileSystemTools._read_allowed_root``): il confine e'
        asimmetrico di proposito.
        """
        return self.project_path

    def metadata(self) -> dict[str, str]:
        return {
            "project_path": str(self.project_path),
            "access_mode": self.access_mode,
        }

    def payload(self) -> dict[str, Any]:
        return {
            **self.metadata(),
            "project_name": self.project_name,
            "restrict_to_workspace": self.restrict_to_workspace,
            "writable": self.writable,
            "sandbox_status": self.sandbox_status.as_dict(),
        }


@dataclass(frozen=True)
class ToolWorkspace:
    """Workspace policy resolved for a tool call."""

    project_path: Path | None
    restrict_to_workspace: bool
    scope: WorkspaceScope | None = None
    # v. ``WorkspaceScope.writable``. **Non si esprime con ``write_root()``**:
    # li' ``None`` significa gia' "nessuna restrizione", quindi una radice nulla
    # per dire "sola lettura" aprirebbe tutto invece di chiudere tutto.
    writable: bool = True

    def write_root(self) -> Path | None:
        """Il **confine** entro cui una scrittura di questo turno deve restare.

        La forma "confine" di :meth:`WorkspaceScope.write_root`: la stessa
        radice, piu' ``None`` per dire "nessun confine da far rispettare" — che
        e' cio' che ``restrict_to_workspace`` spento significa, e che i cancelli
        di percorso traducono in ``UNRESTRICTED``. Un turno in sola lettura
        **non** si esprime qui: v. il commento su ``writable`` qui sopra.

        Quando c'e' uno scope legato la risposta e' la sua, non una seconda copia
        calcolata qui: e' il punto del passo T4.4. Le due sono comunque lo stesso
        valore, perche' ``current_tool_workspace`` — l'unico costruttore di
        questa classe — prende ``project_path`` proprio dallo scope.
        """
        if not self.restrict_to_workspace or self.project_path is None:
            return None
        return self.scope.write_root() if self.scope is not None else self.project_path

    @property
    def allowed_root(self) -> Path | None:
        """Alias storico di :meth:`write_root`, **non** una seconda risposta.

        Resta perche' ``agent/tools/message.py`` lo legge per risolvere gli
        allegati in uscita, che e' una domanda di lettura e non di scrittura:
        spostarlo vuol dire decidere quale confine gli spetta, ed e' lavoro a
        parte. Ogni sito di **scrittura** e' stato portato su ``write_root()``;
        ``tests/security/test_one_write_root.py`` impedisce che ne ricompaia uno.
        """
        return self.write_root()


@dataclass(frozen=True)
class WorkspaceScopeResolver:
    """Resolve the effective workspace scope at an agent turn boundary."""

    default_workspace: str | Path
    default_restrict_to_workspace: bool
    scoped_channel: str = "websocket"
    # Sottocartella che ospita i progetti, relativa al workspace. Un progetto
    # *e'* una wiki: non esiste una
    # ``projects/`` separata. Configurabile perche' lo e' ``config.wiki.wikis_dir``,
    # e chi costruisce il resolver la passa da li'.
    projects_subdir: str = "wikis"

    @property
    def sandbox_status(self) -> WorkspaceSandboxStatus:
        return self.default().sandbox_status

    def default(self) -> WorkspaceScope:
        return default_workspace_scope(
            self.default_workspace,
            self.default_restrict_to_workspace,
        )

    def for_message(
        self,
        msg: Any,
        session_metadata: Any,
    ) -> WorkspaceScope:
        return self.for_turn(
            channel=getattr(msg, "channel", None),
            message_metadata=getattr(msg, "metadata", None),
            session_metadata=session_metadata,
            session_key=getattr(msg, "session_key", None),
        )

    def for_turn(
        self,
        *,
        channel: str | None,
        message_metadata: Any,
        session_metadata: Any,
        session_key: str | None = None,
    ) -> WorkspaceScope:
        """Lo scope di questo turno.

        **Per una sessione-progetto la cartella si ricava dalla chiave**, e non
        dai metadati: ``project:ricette`` -> ``<workspace>/wikis/ricette``. Cosi'
        la sessione e la sua cartella non possono divergere — non c'e' un secondo
        dato da tenere allineato, e nessun client puo' chiedere una cartella
        diversa da quella che il suo nome dichiara. I metadati restano la strada
        per tutto il resto (uno scope scelto a mano, i test).
        """
        if channel != self.scoped_channel:
            return self.default()
        notebook = notebook_of_cron_session(session_key) if session_key else None
        if session_key and is_project_session_key(session_key):
            scope = self.for_project(session_key)
        elif notebook is not None:
            # Il turno di un cron del quaderno: lo stesso recinto della sua chat.
            scope = self.for_project(f"{PROJECT_SESSION_PREFIX}{notebook}")
        else:
            scope = resolve_effective_workspace_scope(
                message_metadata=message_metadata,
                session_metadata=session_metadata,
                default_workspace=self.default_workspace,
                default_restrict_to_workspace=self.default_restrict_to_workspace,
            )
        # La sola lettura si applica **dopo**, a qualunque scope sia uscito.
        # Dentro un progetto il ramo sopra torna prima di guardare i metadati —
        # e' la decisione del 21/08, la cartella si deduce dalla chiave — quindi
        # un flag letto solo la' verrebbe ignorato proprio nei progetti, cioe'
        # dove serve di piu'.
        return scope.without_write_access() if readonly_from_metadata(message_metadata) else scope

    def for_project(self, session_key: str) -> WorkspaceScope:
        """Lo scope di una sessione-progetto, dedotto dalla sua chiave.

        Sempre ``restricted``: la scrittura di un progetto sta nella sua
        cartella, e non c'e' un modo di chiedere il contrario. (La lettura resta
        aperta sul workspace — il confine e' asimmetrico, v.
        ``FileSystemTools._read_allowed_root``.)

        **Una cartella che non esiste non fa ricadere sulla radice personale.**
        Lo scope viene costruito lo stesso e punta al posto che manca: le
        scritture falliscono tutte, il che e' scomodo ma onesto, mentre il
        fallback silenzioso metterebbe il lavoro di un progetto nel workspace
        personale. Trasformarlo in un rifiuto detto a voce e' il passo 6.
        """
        name = session_key[len(PROJECT_SESSION_PREFIX):]
        root = _safe_expanduser(self.default_workspace).resolve(strict=False)
        project = (root / self.projects_subdir / name).resolve(strict=False)
        # Il nome arriva da una session key, che a sua volta arriva da un client:
        # un ``..`` non deve poter far uscire lo scope dalla cartella dei progetti.
        projects_root = (root / self.projects_subdir).resolve(strict=False)
        if project == projects_root or projects_root not in project.parents:
            logger.warning(
                "project session {} resolves outside {}; falling back to the default scope",
                session_key,
                projects_root,
            )
            return self.default()
        if not project.is_dir():
            logger.warning(
                "project session {} points at a missing folder ({}); writes will fail",
                session_key,
                project,
            )
        return build_workspace_scope(project, "restricted")


def readonly_from_metadata(message_metadata: Any) -> bool:
    """Se questo messaggio chiede la sola lettura.

    Solo ``True`` accende: un valore assente, non booleano o mal formato lascia
    il turno scrivibile. E' il verso giusto per un flag che arriva da un client
    — sbagliarlo in chiusura spegnerebbe le scritture senza che nessuno l'abbia
    chiesto, e il caso da difendere e' l'opposto (l'utente lo ha chiesto e il
    turno scrive comunque), che qui non puo' capitare perche' il client lo manda
    a ogni messaggio.
    """
    if not isinstance(message_metadata, dict):
        return False
    return message_metadata.get(WORKSPACE_READONLY_METADATA_KEY) is True


# Il rifiuto che un TOOL restituisce al modello quando il turno e' in sola
# lettura. Testo unico per i cinque che scrivono fuori dai cancelli, perche' e'
# la stessa regola e cinque parafrasi la farebbero sembrare cinque regole.
# I cancelli di percorso sollevano invece ``ReadOnlyTurnError``: la' l'errore
# deve assomigliare a un errore di filesystem, qui a una risposta.
READONLY_TOOL_REFUSAL = (
    "Not now: this conversation is read-only, so nothing on the device can be changed "
    "(files, downloads, app data, memory, scheduled jobs, app updates). Do not look for "
    "another tool or another path — there isn't one. Describe what you would have done, "
    "and tell the user they can turn writing back on with the switch next to the chip "
    "above the composer if they want it applied."
)


def current_turn_is_readonly() -> bool:
    """Se il turno in corso e' in sola lettura.

    Per i tool che scrivono **fuori** dai tre cancelli del confine di scrittura
    (``download``, lo storage delle app, la memoria, ``cron``, l'aggiornamento
    dell'app): quelli hanno una destinazione fissa e non passano da
    ``resolve_allowed_path``, quindi il cancello non li vede e devono chiedere.
    L'inventario di chi deve chiamarla sta in
    ``tests/security/test_readonly_write_surfaces.py``.
    """
    scope = current_workspace_scope()
    return scope is not None and not scope.writable


# I quaderni tenuti da un piano a lista partito (``jenny/plan/driver.py``):
# ``{cartella risolta: nome}``. Il driver si registra (``GatewayContainer``) e
# risponde dal suo indice in memoria, così il cancello costa un dizionario per
# scrittura e nessun giro sul disco. Senza driver (i test, l'agente non ancora
# nato) nessun quaderno è tenuto. Un gancio e non un import del driver, come
# ``goal_state.set_plan_probe``: la sicurezza non dipende dal pacchetto dei piani.
_held_notebook_probe: Callable[[], Mapping[Path, str]] | None = None


def set_held_notebook_probe(probe: Callable[[], Mapping[Path, str]] | None) -> None:
    global _held_notebook_probe
    _held_notebook_probe = probe


def refuse_held_notebook_write(path: str | Path) -> None:
    """Rifiuta una scrittura dentro un quaderno tenuto da un piano, se non è del piano.

    Scrive in un quaderno tenuto solo un turno il cui scope **è** quel
    quaderno: la sua conversazione, cioè i passi del piano, e gli aiutanti che
    i passi lanciano (ereditano lo scope del turno). La chat personale, i suoi
    aiutanti e ogni altra sessione che arriverebbe lì attraverso la radice del
    workspace ricevono :class:`HeldNotebookWriteError`. I turni della
    conversazione del quaderno che non sono passi — una pausa, un cron — sono
    già in sola lettura (``loop_plan.py``), quindi non passano di qui.
    """
    probe = _held_notebook_probe
    if probe is None:
        return
    try:
        held = probe()
    except Exception:  # noqa: BLE001 — un indice rotto non deve rompere ogni scrittura
        logger.opt(exception=True).warning("held-notebook probe failed")
        return
    if not held:
        return
    target = Path(os.path.abspath(path))
    for root, name in held.items():
        if target != root and root not in target.parents:
            continue
        scope = current_workspace_scope()
        if scope is not None and scope.project_path == root:
            return
        from jenny.security.workspace_policy import HeldNotebookWriteError

        raise HeldNotebookWriteError(name, f"refused write to {path}")


def workspace_sandbox_status(
    *,
    restrict_to_workspace: bool,
    workspace: str | Path,
    environ: dict[str, str] | None = None,
) -> WorkspaceSandboxStatus:
    """Return how workspace restriction is enforced in the current host."""

    workspace_root = str(_safe_expanduser(workspace).resolve(strict=False))
    provider = _env_system_provider(environ)
    if not restrict_to_workspace:
        return WorkspaceSandboxStatus(
            restrict_to_workspace=False,
            workspace_root=workspace_root,
            level="off",
            enforced=False,
            provider="none",
            provider_label=_provider_label("none"),
            summary="Workspace restriction is disabled.",
        )

    if provider:
        label = _provider_label(provider)
        return WorkspaceSandboxStatus(
            restrict_to_workspace=True,
            workspace_root=workspace_root,
            level="system",
            enforced=True,
            provider=provider,
            provider_label=label,
            summary=f"Workspace restriction is system-enforced by {label}.",
        )

    return WorkspaceSandboxStatus(
        restrict_to_workspace=True,
        workspace_root=workspace_root,
        level="application",
        enforced=False,
        provider="none",
        provider_label=_provider_label("none"),
        summary="Workspace restriction uses jenny application-level guards.",
    )


def default_access_mode(restrict_to_workspace: bool) -> WorkspaceAccessMode:
    return "restricted" if restrict_to_workspace else "full"


def build_workspace_scope(
    project_path: str | Path,
    access_mode: str,
) -> WorkspaceScope:
    mode = _normalize_access_mode(access_mode)
    root = _safe_expanduser(project_path).resolve(strict=False)
    restrict = mode == "restricted"
    return WorkspaceScope(
        project_path=root,
        access_mode=mode,
        restrict_to_workspace=restrict,
        sandbox_status=workspace_sandbox_status(
            restrict_to_workspace=restrict,
            workspace=root,
        ),
    )


def default_workspace_scope(
    workspace: str | Path,
    restrict_to_workspace: bool,
) -> WorkspaceScope:
    return build_workspace_scope(
        workspace,
        default_access_mode(restrict_to_workspace),
    )


def validate_workspace_scope_payload(
    raw: Any,
    *,
    default_workspace: str | Path,
    default_restrict_to_workspace: bool,
) -> WorkspaceScope:
    """Validate a client-requested workspace scope."""
    if raw is None:
        return default_workspace_scope(
            default_workspace,
            default_restrict_to_workspace,
        )
    if not isinstance(raw, dict):
        raise WorkspaceScopeError("workspace_scope must be an object")

    raw_path = raw.get("project_path") or raw.get("path")
    if raw_path is None or raw_path == "":
        raw_path = str(_safe_expanduser(default_workspace).resolve(strict=False))
    if not isinstance(raw_path, str):
        raise WorkspaceScopeError("project_path must be a string")
    if "\0" in raw_path:
        raise WorkspaceScopeError("project_path contains invalid characters")

    project = _safe_expanduser(raw_path)
    if not project.is_absolute():
        raise WorkspaceScopeError("project_path must be absolute")
    project = project.resolve(strict=False)
    if not project.is_dir():
        raise WorkspaceScopeError("project_path must be an existing directory")

    raw_mode = raw.get("access_mode")
    if raw_mode is None:
        raw_mode = default_access_mode(default_restrict_to_workspace)
    if not isinstance(raw_mode, str):
        raise WorkspaceScopeError("access_mode must be a string")
    return build_workspace_scope(project, raw_mode)


def workspace_scope_from_metadata(
    metadata: Any,
    *,
    default_workspace: str | Path,
    default_restrict_to_workspace: bool,
) -> WorkspaceScope:
    """Resolve persisted metadata, falling back safely for old or stale sessions."""
    if not isinstance(metadata, dict):
        return default_workspace_scope(
            default_workspace,
            default_restrict_to_workspace,
        )
    try:
        return validate_workspace_scope_payload(
            metadata.get(WORKSPACE_SCOPE_METADATA_KEY),
            default_workspace=default_workspace,
            default_restrict_to_workspace=default_restrict_to_workspace,
        )
    except WorkspaceScopeError:
        return default_workspace_scope(
            default_workspace,
            default_restrict_to_workspace,
        )


def resolve_effective_workspace_scope(
    *,
    message_metadata: Any,
    session_metadata: Any,
    default_workspace: str | Path,
    default_restrict_to_workspace: bool,
) -> WorkspaceScope:
    if isinstance(message_metadata, dict) and WORKSPACE_SCOPE_METADATA_KEY in message_metadata:
        return workspace_scope_from_metadata(
            message_metadata,
            default_workspace=default_workspace,
            default_restrict_to_workspace=default_restrict_to_workspace,
        )
    return workspace_scope_from_metadata(
        session_metadata,
        default_workspace=default_workspace,
        default_restrict_to_workspace=default_restrict_to_workspace,
    )


def bind_workspace_scope(scope: WorkspaceScope) -> Token[WorkspaceScope | None]:
    return _CURRENT_WORKSPACE_SCOPE.set(scope)


def reset_workspace_scope(token: Token[WorkspaceScope | None]) -> None:
    _CURRENT_WORKSPACE_SCOPE.reset(token)


@contextmanager
def enter_workspace_scope(scope: "WorkspaceScope | None") -> "Iterator[None]":
    """Bind *scope* per la durata del blocco (no-op se ``None``).

    Sostituisce il dance manuale ``bind_workspace_scope``/``reset_workspace_scope``
    con un unico costrutto strutturale, così il reset non può essere dimenticato.
    Con il gate fail-closed (`resolve_allowed_path`), uno scope non impostato fa
    comunque fallire in sicurezza le operazioni su path (deny), non aprirle."""
    if scope is None:
        yield
        return
    token = bind_workspace_scope(scope)
    try:
        yield
    finally:
        reset_workspace_scope(token)


def current_workspace_scope() -> WorkspaceScope | None:
    return _CURRENT_WORKSPACE_SCOPE.get()


def current_tool_workspace(
    default_workspace: str | Path | None,
    *,
    restrict_to_workspace: bool = False,
) -> ToolWorkspace:
    """Return the workspace/access policy for the current tool call."""

    scope = current_workspace_scope()
    project_path = (
        scope.project_path
        if scope is not None
        else _safe_expanduser(default_workspace) if default_workspace is not None else None
    )
    restrict = (
        scope.restrict_to_workspace
        if scope is not None
        else bool(restrict_to_workspace)
    )
    return ToolWorkspace(
        project_path=project_path,
        restrict_to_workspace=restrict,
        scope=scope,
        writable=scope.writable if scope is not None else True,
    )


def _env_system_provider(environ: dict[str, str] | None = None) -> str | None:
    env = environ if environ is not None else os.environ
    explicit_provider = env.get(WORKSPACE_SANDBOX_PROVIDER_ENV)
    enforced = env.get(WORKSPACE_SANDBOX_ENFORCED_ENV)
    compatibility = env.get(WORKSPACE_SANDBOX_ENFORCED_LEGACY_ENV)

    marker = enforced if enforced is not None else compatibility
    if marker is None:
        return None

    normalized_marker = marker.strip().lower()
    if normalized_marker in _FALSE_VALUES:
        return None
    if normalized_marker in _TRUE_VALUES:
        return _normalize_provider(explicit_provider)
    return _normalize_provider(marker)


def _normalize_provider(value: str | None) -> str:
    if not value:
        return "unknown"
    normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
    return normalized or "unknown"


def _provider_label(provider: str) -> str:
    if provider in _PROVIDER_LABELS:
        return _PROVIDER_LABELS[provider]
    return provider.replace("_", " ").title()


def _normalize_access_mode(value: str) -> WorkspaceAccessMode:
    mode = value.strip().lower().replace("_", "-")
    if mode == "restrict":
        mode = "restricted"
    if mode == "full-access":
        mode = "full"
    if mode not in _ACCESS_MODES:
        raise WorkspaceScopeError("access_mode must be restricted or full")
    return mode  # type: ignore[return-value]
