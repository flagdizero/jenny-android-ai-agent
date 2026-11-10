"""La cassetta di un aiutante: quali tool ha, con quale configurazione.

I tool di un aiutante, dal suo tipo e dalla configurazione di adesso.

Un pezzo di :class:`jenny.agent.subagent.SubagentManager` (mixin): il testo dei
metodi è quello di prima, spostato qui per responsabilità.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

from jenny.agent.agent_types import AgentType
from jenny.agent.subagent_common import unavailable_tools
from jenny.agent.tools.context import ToolContext
from jenny.agent.tools.file_state import FileStates
from jenny.agent.tools.loader import ToolLoader, ToolLoadError, declared_tool_name
from jenny.agent.tools.registry import ToolRegistry
from jenny.config.schema import ToolsConfig
from jenny.runtime.context import get_android_context

if TYPE_CHECKING:
    from typing import Callable


def split_allow_by_scope(
    loader: ToolLoader, agent_type: AgentType
) -> dict[str, frozenset[str] | None]:
    """Ripartisce l'allowlist di un tipo fra i suoi ``scopes``, validandola una volta.

    ``ToolLoader.load`` valida l'``allow`` che riceve contro i soli tool dello
    scope che sta caricando, e una voce che li non trova nessuno e un
    ``ToolLoadError`` **fatale** che aborta lo startup del gateway. La guardia e
    giusta — un typo in un agent type deve esplodere, non far girare un subagent
    con meno tool del previsto — ma e per-scope, e un tipo multi-scope come
    ``sysadmin`` la farebbe scattare a vuoto: ``ssh_exec`` non esiste nello scope
    ``subagent``, e passargli l'allowlist intera impedirebbe il boot.

    Quindi l'allowlist si valida qui **una volta contro l'unione** degli scope
    del tipo, e a ogni ``load`` si passa solo la fetta di sua competenza. Il
    risultato e quello voluto in entrambi i versi: un nome che non esiste in
    NESSUNO degli scope resta un errore fatale, un nome che esiste in uno solo
    non lo e piu.

    ``tools=None`` (``operator``) non e un'allowlist vuota: significa "tutto lo
    scope", e resta ``None`` per ogni scope.
    """
    if agent_type.tools is None:
        return {scope: None for scope in agent_type.scopes}

    discovered = loader.discover()
    by_scope: dict[str, frozenset[str] | None] = {}
    known: set[str] = set()
    for scope in agent_type.scopes:
        names = {
            name
            for cls in discovered
            if scope in getattr(cls, "_scopes", {"core"}) and (name := declared_tool_name(cls))
        }
        known |= names
        by_scope[scope] = frozenset(agent_type.tools & names)

    unknown = sorted(set(agent_type.tools) - known)
    if unknown:
        raise ToolLoadError(
            f"Unknown tool name(s) in allow list for agent type '{agent_type.name}': "
            f"{', '.join(unknown)}. Known tools in scope(s) "
            f"{', '.join(agent_type.scopes)}: {', '.join(sorted(known))}."
        )
    return by_scope


def unavailable_by_scope(
    loader: ToolLoader, agent_type: AgentType, ctx: Any
) -> dict[str, tuple[str, ...]]:
    """Per ogni scope del tipo, i tool che l'allowlist chiede e ``enabled()`` nega.

    ``split_allow_by_scope`` valida l'allowlist contro i tool che esistono *come
    classe*; ``ToolLoader.load`` poi salta quelli spenti dalla config. Fra i due
    c'e un buco per cui un tipo puo perdere la propria ragione d'essere senza
    che nessuno lo dica: ``sysadmin`` partiva con i soli tool sul filesystem e
    rispondeva "il tool SSH non era disponibile", che si legge come una scusa
    inventata dal modello e invece era la verita.

    Qui non si istanzia niente: ``enabled()`` e un classmethod e legge solo la
    config, quindi il controllo costa quanto una lettura di attributi e si puo
    fare *prima* di avviare il subagent.

    ``tools=None`` (``operator``) significa "tutto lo scope": non c'e nessuna
    promessa esplicita da tradire, quindi lo scope risulta sempre vuoto qui.
    """
    return {
        scope: () if allow is None else unavailable_tools(loader, allow, ctx)
        for scope, allow in split_allow_by_scope(loader, agent_type).items()
    }


class ToolboxMixin:
    """I tool di un aiutante, dal suo tipo e dalla configurazione di adesso.

    Mixin di ``SubagentManager``.
    """

    if TYPE_CHECKING:
        # Contratto host↔mixin (solo per il type-checker; nessun effetto a
        # runtime): gli attributi di ``SubagentManager`` e i metodi degli
        # altri pezzi che questi metodi usano.
        _tools_config_provider: Callable[[], ToolsConfig] | None
        cron_service: Any | None
        timezone: str
        tools_config: Any
        workspace: Path


    def _live_tools_config(self) -> ToolsConfig:
        """La ``ToolsConfig`` corrente, non quella catturata all'avvio.

        Quale tool *esiste* lo decide ``enabled()``, che legge da qui; cosa fa
        una volta chiamato lo decide il suo corpo, che rilegge la config a ogni
        chiamata (``ssh_transport.resolve_target`` carica da disco). Leggere una
        copia vecchia solo nel primo dei due punti produceva un'asimmetria
        assurda: togliere un host aveva effetto subito, aggiungerne uno no —
        finche non si riavviava l'app.

        Il provider e iniettato invece che letto qui dentro perche questo modulo
        non deve sapere che esiste un file: i test passano una config e basta, e
        il percorso vero resta uno solo.
        """
        if self._tools_config_provider is None:
            return self.tools_config
        try:
            return self._tools_config_provider()
        except Exception as exc:
            # Config illeggibile o corrotta: si degrada alla copia nota. Perdere
            # il subagent sarebbe peggio, ma in silenzio sarebbe peggio ancora.
            logger.warning(
                "Could not read the current tools config, using the startup copy: {}", exc
            )
            return self.tools_config

    def _subagent_tools_config(self) -> ToolsConfig:
        """Build a ToolsConfig scoped for subagent use."""
        live = self._live_tools_config()
        return ToolsConfig(
            python_exec=live.python_exec,
            android_web=live.android_web,
            file=live.file,
            # Senza questo il tipo ``sysadmin`` non vedrebbe MAI i tool SSH:
            # ``enabled()`` legge il toggle e gli host da qui, e una ToolsConfig
            # ricostruita senza ``ssh`` porta i default (spento, zero host). Il
            # gate vero resta l'allowlist del tipo, non l'assenza della config.
            ssh=live.ssh,
            # ATTENZIONE: questo NON viene da ``live``. Sull'oggetto vivo
            # ``restrict_to_workspace`` e il valore *risolto* da ``AgentLoop``
            # all'avvio, non quello scritto nel file; ripescarlo dal disco
            # insieme al resto cancellerebbe la risoluzione, e con essa il
            # confine dello workspace.
            restrict_to_workspace=self.tools_config.restrict_to_workspace,
        )

    def _build_tools(
        self,
        workspace: Path | None = None,
        tools_config: ToolsConfig | None = None,
        agent_type: AgentType | None = None,
        origin_session_key: str | None = None,
        lineage_id: str | None = None,
    ) -> ToolRegistry:
        """Build an isolated subagent tool registry via ToolLoader.

        L'allowlist del tipo e il *secondo* filtro dopo lo scope: ``_scopes`` e
        per-classe e globale, quindi non sa distinguere un researcher da un
        coder (vedi ``agent_types.py``). ``agent_type=None`` o un tipo con
        ``tools=None`` lascia l'intero scope subagent, come prima dei tipi.

        Gli scope del tipo si caricano uno alla volta sullo *stesso* registry:
        un tipo come ``sysadmin`` prende ``subagent`` + ``remote``. La
        ripartizione dell'allowlist fra gli scope, e la sua validazione,
        stanno in :func:`split_allow_by_scope` — che va letta prima di
        toccare questo metodo.
        """
        root = self.workspace if workspace is None else workspace
        registry = ToolRegistry()
        cfg = tools_config if tools_config is not None else self._subagent_tools_config()
        ctx = self._tool_context(root, cfg, origin_session_key, lineage_id)
        loader = ToolLoader()
        if agent_type is None:
            loader.load(ctx, registry, scope="subagent", allow=None)
            return registry
        for scope, allow in split_allow_by_scope(loader, agent_type).items():
            loader.load(ctx, registry, scope=scope, allow=allow)
        self._log_unavailable(loader, agent_type, ctx)
        return registry

    def _tool_context(self, root: Path, cfg: ToolsConfig,
                      origin_session_key: str | None = None,
                      lineage_id: str | None = None) -> ToolContext:
        """Il ctx che i tool ricevono. Estratto perche lo usa anche il pre-volo."""
        return ToolContext(
            config=cfg,
            workspace=str(root.resolve()),
            file_states=FileStates(),
            android_context=get_android_context(),
            cron_service=self.cron_service,
            timezone=self.timezone,
            origin_session_key=origin_session_key,
            subagent_lineage_id=lineage_id,
        )

    @staticmethod
    def _log_unavailable(loader: ToolLoader, agent_type: AgentType, ctx: ToolContext) -> None:
        """Traccia i tool *chiesti per nome* e non caricati.

        Solo quelli: l'insieme dei tool spenti in generale e grande e noioso,
        quello dei tool che un tipo aveva dichiarato di volere e piccolo ed e
        sempre interessante. Era la riga di log che mancava quando ``sysadmin``
        e partito senza SSH.
        """
        for scope, missing in unavailable_by_scope(loader, agent_type, ctx).items():
            if missing:
                logger.info(
                    "Agent type '{}' declared {} tool(s) in scope '{}' that are "
                    "switched off and were not loaded: {}",
                    agent_type.name, len(missing), scope, ", ".join(missing),
                )
