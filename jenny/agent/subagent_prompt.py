"""Il prompt di sistema di un aiutante e il suo tetto di iterazioni.

Cosa si dice a un aiutante prima che cominci, e per quanti giri può andare.

Un pezzo di :class:`jenny.agent.subagent.SubagentManager` (mixin): il testo dei
metodi è quello di prima, spostato qui per responsabilità.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from jenny.agent.agent_types import AgentType
from jenny.security.workspace_access import WorkspaceScope, current_workspace_scope
from jenny.utils.prompt_templates import render_template

if TYPE_CHECKING:
    from typing import Any


class PromptMixin:
    """Cosa si dice a un aiutante prima che cominci, e per quanti giri può andare.

    Mixin di ``SubagentManager``.
    """

    if TYPE_CHECKING:
        # Contratto host↔mixin (solo per il type-checker; nessun effetto a
        # runtime): gli attributi di ``SubagentManager`` e i metodi degli
        # altri pezzi che questi metodi usano.
        disabled_skills: Any
        max_iterations: Any
        workspace: Path


    def _type_max_iterations(self, agent_type: AgentType) -> int:
        """Tetto di iterazioni effettivo per un tipo.

        Il valore del tipo puo solo STRINGERE quello configurato, mai allargarlo:
        ``max_tool_iterations`` e una decisione dell'utente sul proprio device, e
        un default di tipo piu generoso la scavalcherebbe in silenzio.
        """
        if agent_type.max_iterations is None:
            return self.max_iterations
        return min(agent_type.max_iterations, self.max_iterations)

    def _run_max_iterations(self, agent_type: AgentType, steps: int | None) -> int:
        """I passi di un lavoro: quelli chiesti allo spawn, o il default del tipo.

        Mai oltre il tetto del tipo e quello configurato
        (:meth:`_type_max_iterations`): ``steps`` stringe, non allarga.
        """
        ceiling = self._type_max_iterations(agent_type)
        wanted = steps or agent_type.default_iterations
        return min(wanted, ceiling) if wanted else ceiling

    def _build_subagent_prompt(
        self,
        workspace: Path | None = None,
        agent_type: AgentType | None = None,
        *,
        workspace_scope: WorkspaceScope | None = None,
        tool_names: list[str] | None = None,
        max_iterations: int | None = None,
    ) -> str:
        """Build a focused system prompt for the subagent.

        *tool_names* sono gli strumenti del subagent: i template dicono solo
        cio' che vale per chi li ha (``has``), come nel prompt principale.

        Il prompt di ruolo del tipo viene *composto* dentro il template base, non
        duplicato: la parte condivisa (contenuto non fidato, workspace, skills)
        resta in un solo file.

        ``workspace_scope`` e' lo scope della **spec**, cioe' quello che il run
        legherà davvero (v. ``_run_subagent``). Va passato, non dedotto
        dall'ambiente: questo metodo viene chiamato FUORI dal blocco
        ``enter_workspace_scope``, e su ``restart`` / ``send`` /
        ``_resume_lineage`` l'ambiente e' il turno del chiamante, non la spec —
        cosi' rilanciare una spec scrivibile da dentro un turno in sola lettura
        dava un prompt che diceva "non puoi scrivere" a un subagent che poteva.
        """
        from jenny.agent.context import ContextBuilder
        from jenny.agent.skills import SkillsLoader
        from jenny.config.paths import get_output_path
        from jenny.utils.wiki_paths import is_wiki_root

        time_ctx = ContextBuilder._build_runtime_context(None, None)
        root = workspace or self.workspace
        # Lo scope che il run legherà. Vedi la docstring per il perche' il
        # fallback all'ambiente e' corretto e non un ripiego.
        bound_scope = (
            workspace_scope if workspace_scope is not None else current_workspace_scope()
        )
        skills_summary = SkillsLoader(
            root,
            disabled_skills=self.disabled_skills,
        ).build_skills_summary()
        return render_template(
            "agent/subagent_system.md",
            has=ContextBuilder._tool_predicate(tool_names),
            time_ctx=time_ctx,
            workspace=str(root),
            # Assoluto come ``workspace`` qui accanto: il subagent scrive con
            # ``write_file``, e un path relativo lo lascerebbe a indovinare
            # rispetto a cosa — la radice che il prompt gli vieta di sporcare.
            # ``create=False``: qui il prompt la nomina soltanto, la directory
            # la crea ``sync_workspace_templates`` una volta per avvio.
            output_dir=str(get_output_path(root)),
            # Un subagent lanciato dentro un progetto riceve la radice del
            # progetto, e il paragrafo qui sopra gli dava le regole del
            # workspace applicate a una cartella che non e' il workspace: e'
            # cosi' che il 21/08 il file di prova e' finito in
            # ``wikis/<nome>/output/``, cartella che nello scaffold non esiste
            # nemmeno. La domanda si fa sulla cartella perche' qui la chiave di
            # sessione non arriva; ``agent/project.md`` e' lo stesso file che
            # legge l'agente principale, incluso e non ricopiato.
            project=is_wiki_root(root),
            project_path=str(root.resolve()),
            # La pianta della cartella gli serve — e' dove scrive. La politica di
            # cattura no: non ha un utente che gli dica fatti stabili, e quel che
            # scoprisse va in ``raw/research/`` e in una pagina, non nel diario.
            capture=False,
            # Il paragrafo sulla ricerca durante un piano e' per chi lancia
            # aiutanti: un subagent non ne lancia (``has('spawn')`` e' falso), e
            # qui la chiave di sessione non arriva. Falso, e dichiarato: il
            # test «ogni variabile e' alimentata» lo pretende.
            plan_running=False,
            # **T3.9 — la mappa e le pagine, o niente sezione.** Fino a qui le
            # due sezioni di ``agent/project.md`` si rendevano vuote, e in
            # silenzio: Jinja valuta falso un ``{% if %}`` su una variabile
            # assente. Il costo non era teorico — il subagent e' l'attore che
            # scrive le pagine e cura la mappa, e lo faceva senza averle davanti.
            #
            # **I lettori sono quelli del prompt principale, non una copia.** Un
            # ``ContextBuilder`` costruito qui e' l'unica strada: questa classe
            # non ne riceve uno (``AgentLoop`` tiene il suo e non lo passa al
            # manager), e i due lettori sono metodi suoi. Costruirlo costa un
            # ``MemoryStore`` e uno ``SkillsLoader`` — nessuna lettura, un
            # ``mkdir(exist_ok=True)`` su ``memory/`` che esiste gia' — cioe'
            # meno di quel che costa lo ``SkillsLoader`` che questo metodo
            # istanzia qualche riga piu' su. La radice passata e' quella
            # dell'installazione, come in produzione: la cartella del progetto e'
            # l'**argomento** dei lettori, non lo stato del builder.
            #
            # Senza guardia su ``project``: fuori da una wiki i due lettori non
            # trovano ne' ``wiki/index.md`` ne' pagine e tornano vuoti, e il
            # template la sezione non la rende comunque. Una guardia in piu' qui
            # sarebbe un secondo posto in cui sbagliare la stessa domanda.
            **ContextBuilder(self.workspace)._project_block_vars(root),
            # **L'esecuzione eredita lo scope, la conoscenza no.** Visto sul
            # telefono il 22/08: in sola lettura l'agente principale sapeva di
            # non poter scrivere, ha delegato, e il subagent ha pianificato e
            # scritto sei file — tutti rifiutati dal cancello. Il confine ha
            # tenuto, il lavoro e' stato buttato. Lo stesso file che legge
            # l'agente principale, incluso e non ricopiato.
            #
            # Lo scope della spec vince sull'ambiente perche' e' quello che il
            # run lega (``enter_workspace_scope(spec.workspace_scope)``). Il
            # fallback all'ambiente non e' un ripiego: con ``workspace_scope``
            # a ``None`` quel ``with`` e' un no-op, quindi lo scope legato *e'*
            # l'ambiente — e allora e' l'ambiente a dover parlare.
            readonly=not (bound_scope is None or bound_scope.writable),
            # Il budget di chiamate del tipo, quello che il run usera davvero: un
            # aiutante che non sa di averne uno si ferma a meta di una lettura.
            max_iterations=max_iterations or (
                self._run_max_iterations(agent_type, None)
                if agent_type is not None else self.max_iterations
            ),
            skills_summary=skills_summary or "",
            role_section=self._render_role_section(agent_type, install_path=self.workspace),
        )

    @staticmethod
    def _render_role_section(agent_type: AgentType | None, *,
                             install_path: Path | None = None) -> str:
        """Rende il prompt di ruolo del tipo, o stringa vuota se non c'e.

        Un template mancante non deve uccidere il subagent: un workspace
        sincronizzato da una versione precedente puo non avere ancora il file, e
        girare senza la sezione di ruolo e infinitamente meglio che non girare.
        """
        if agent_type is None:
            return ""
        try:
            # ``install_path``: la radice dell'installazione, perche' un subagent
            # in un quaderno lavora nella cartella del progetto e un percorso
            # relativo (``skills/…``) non ci arriva.
            return render_template(agent_type.prompt_template, strip=True,
                                   install_path=str(install_path or ""))
        except Exception as e:  # noqa: BLE001 — vedi docstring
            logger.warning(
                "Agent type prompt {} unavailable ({}); running without role section",
                agent_type.prompt_template, e,
            )
            return ""
