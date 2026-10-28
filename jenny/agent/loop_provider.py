"""Switch di provider/preset a runtime per ``AgentLoop``.

`ProviderPresetMixin` isola lo scambio di modello/provider e l'applicazione dei
preset (`model_presets`) per i turni futuri, senza disturbare un turno attivo.
Mixato in ``AgentLoop`` verbatim: `self` risolve via MRO. I processi provider
non vengono scambiati a runtime (richiede riavvio gateway).
"""

from __future__ import annotations

from collections.abc import Collection
from typing import TYPE_CHECKING, Any

from loguru import logger

if TYPE_CHECKING:
    from jenny.agent.memory import Consolidator
    from jenny.agent.runner import AgentRunner
    from jenny.agent.subagent import SubagentManager
    from jenny.bus.runtime_events import RuntimeEventPublisher
    from jenny.providers.base import LLMProvider


class ProviderPresetMixin:
    """Metodi di switch provider/preset (mixin di AgentLoop)."""

    if TYPE_CHECKING:
        # Contratto host↔mixin (solo per il type-checker; nessun effetto a
        # runtime). Attributi forniti da ``AgentLoop.__init__``.
        _active_preset: str | None
        consolidator: Consolidator
        context_window_tokens: int
        main_reasoning_effort: str | None
        max_iterations: int
        model: str
        model_presets: dict[str, Any]
        provider: LLMProvider
        runner: AgentRunner
        subagents: SubagentManager

        # Metodo dell'host (loop.py) invocato qui.
        def _runtime_events(self) -> RuntimeEventPublisher: ...

    def _sync_subagent_runtime_limits(self) -> None:
        """Keep subagent runtime limits aligned with mutable loop settings."""
        self.subagents.max_iterations = self.max_iterations

    def _apply_provider_switch(
        self,
        provider: LLMProvider,
        model: str,
        context_window_tokens: int,
        *,
        publish_update: bool = True,
        model_preset_name: str | None = None,
    ) -> None:
        """Swap model/provider for future turns without disturbing an active one."""
        old_model = self.model
        self.provider = provider
        self.model = model
        self.context_window_tokens = context_window_tokens
        self.runner.provider = provider
        self.subagents.set_provider(provider, model)
        self.consolidator.set_provider(provider, model, context_window_tokens)
        if publish_update:
            self._runtime_events().runtime_model_changed(
                self.model,
                model_preset_name if model_preset_name is not None else self._active_preset,
                provider=getattr(provider, "provider_name", None),
            )
        logger.info("Runtime model switched for next turn: {} -> {}", old_model, model)

    @property
    def model_preset(self) -> str | None:
        return self._active_preset

    @model_preset.setter
    def model_preset(self, name: str | None) -> None:
        if name is None:
            self._active_preset = None
            return
        self._apply_model_preset(str(name))

    async def set_model_preset(self, name: str) -> None:
        """Switch model settings to a named preset for future turns."""
        self._apply_model_preset(name)

    def _turn_reasoning_effort(self, tool_names: Collection[str]) -> str | None:
        """Il ragionamento di un turno di Jenny; ``None`` lascia quello del provider.

        Il suo vale solo dove delega: e' basso perche' il lavoro che vuole
        pensiero lo fa un aiutante. Senza ``spawn`` (core mode) il lavoro e' suo,
        e con ``plan_propose`` sta scrivendo un piano: in entrambi i casi decide
        il ragionamento di tutti. Lo stesso se quello di tutti e' spento
        («decide il provider»): ``low`` accenderebbe il thinking a chi l'aveva
        tolto (su Anthropic un budget e la temperatura a 1), e il default non si
        distingue da una scelta perche' e' gia' scritto nei ``config.json``.

        Un preset attivo che sceglie il suo ``reasoning_effort`` vince: e' una
        scelta del momento, fatta per tutti (``_apply_model_preset`` lo scrive
        sul provider), e un'impostazione di fondo non deve smentirla.
        """
        if "spawn" not in tool_names or "plan_propose" in tool_names:
            return None
        generation = getattr(self.provider, "generation", None)
        if getattr(generation, "reasoning_effort", None) is None:
            return None
        preset = self.model_presets.get(self._active_preset) if self._active_preset else None
        if preset is not None and self._preset_field(preset, "reasoning_effort") is not None:
            return None
        return self.main_reasoning_effort

    @staticmethod
    def _preset_field(preset: Any, key: str) -> Any:
        if isinstance(preset, dict):
            return preset.get(key)
        return getattr(preset, key, None)

    def _apply_model_preset(self, name: str, *, publish_update: bool = True) -> None:
        """Apply a named preset from ``model_presets`` to the running loop.

        Presets switch model, context window, and generation settings on the
        active provider; a preset's ``provider`` field only selects request
        routing when the active provider supports it (provider processes are
        not swapped at runtime — that requires a gateway restart).
        """
        preset = self.model_presets.get(name)
        if preset is None:
            available = ", ".join(sorted(self.model_presets)) or "(none configured)"
            raise KeyError(f"unknown model preset {name!r}; available: {available}")
        model = self._preset_field(preset, "model") or self.model
        context_window = (
            self._preset_field(preset, "context_window_tokens") or self.context_window_tokens
        )
        generation = getattr(self.provider, "generation", None)
        if generation is not None:
            from jenny.utils.helpers import output_tokens_for_window

            max_tokens = self._preset_field(preset, "max_tokens")
            if max_tokens:
                generation.max_tokens = int(max_tokens)
            # Un preset con una finestra piccola e il tetto di output del default
            # lascerebbero il prompt senza spazio.
            generation.max_tokens = output_tokens_for_window(
                int(generation.max_tokens), int(context_window or 0))
            temperature = self._preset_field(preset, "temperature")
            if temperature is not None:
                generation.temperature = float(temperature)
            generation.reasoning_effort = self._preset_field(preset, "reasoning_effort")
        self._active_preset = name
        self._apply_provider_switch(
            self.provider,
            model,
            context_window,
            publish_update=publish_update,
            model_preset_name=name,
        )
