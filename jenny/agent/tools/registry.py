"""Tool registry for dynamic tool management."""

import json
from typing import TYPE_CHECKING, Any, Callable

from jenny.agent.tool_error_policy import TOOL_ERROR_RETRY_HINT
from jenny.agent.tools.base import Tool
from jenny.agent.tools.result import ToolResult

if TYPE_CHECKING:
    from jenny.agent.tools.file_state import FileStates
    from jenny.agent.tools.memory_entries import MemoryEntryTool


class ToolRegistry:
    """
    Registry for agent tools.

    Allows dynamic registration and execution of tools.
    """

    def __init__(self):
        self._tools: dict[str, Tool] = {}
        self._cached_definitions: list[dict[str, Any]] | None = None
        # Facoltativo: alcuni builder (es. ``MemoryStore.build_dream_tools``)
        # creano un ``FileStates`` per-run e lo condividono tra i tool del
        # registry; esporlo qui permette al chiamante di ispezionare l'attività
        # di scrittura del run senza cambiare la firma del builder.
        # NB: annotazione fra apici — su Python 3.11/3.12 (runtime Chaquopy)
        # l'annotazione di un attributo viene valutata a runtime e l'import è
        # solo TYPE_CHECKING.
        self.file_states: "FileStates | None" = None
        # Stessa forma e stessa ragione di ``file_states``: il registry di
        # Dream espone il tool per voci perché il chiamante possa chiedere
        # com'è andato il run in *voci* — quante entrate, quante sostituite,
        # quante già presenti — invece di ristimarlo dai file. Dichiarato qui
        # e non assegnato al volo dal builder: un attributo che esiste solo
        # dopo che qualcuno lo ha scritto è invisibile a chi legge la classe,
        # e il type checker lo segnalava.
        self.memory_entries: "MemoryEntryTool | None" = None
        # Contatore monotono delle tool call passate da questo registry.
        # Serve a un tool per sapere se *altri* tool hanno girato dopo la sua
        # ultima chiamata: e per-registry, non globale, perche l'informazione
        # utile riguarda l'agente che chiede (vedi la guardia anti-polling in
        # ``subagent_control.py``) e i subagent hanno registry propri.
        # Avanza in :meth:`prepare_call` e non in :meth:`execute`: il perche e
        # documentato la, ed e meta del motivo per cui quella guardia era morta.
        self.exec_seq: int = 0

    def register(self, tool: Tool) -> None:
        """Register a tool."""
        self._tools[tool.name] = tool
        self._cached_definitions = None

    def unregister(self, name: str) -> None:
        """Unregister a tool by name."""
        self._tools.pop(name, None)
        self._cached_definitions = None

    def get(self, name: str) -> Tool | None:
        """Get a tool by name."""
        return self._tools.get(name)

    @staticmethod
    def _lookup_key(name: str) -> str:
        """Normalize names for suggestions only; never for execution."""
        return "".join(ch.lower() for ch in name if ch.isalnum())

    def _suggest_name(self, name: str) -> str | None:
        key = self._lookup_key(str(name or ""))
        if not key:
            return None
        matches = [
            registered
            for registered in self._tools
            if self._lookup_key(registered) == key
        ]
        if len(matches) == 1:
            return matches[0]
        return None

    def has(self, name: str) -> bool:
        """Check if a tool is registered."""
        return name in self._tools

    @staticmethod
    def _schema_name(schema: dict[str, Any]) -> str:
        """Extract a normalized tool name from either OpenAI or flat schemas."""
        fn = schema.get("function")
        if isinstance(fn, dict):
            name = fn.get("name")
            if isinstance(name, str):
                return name
        name = schema.get("name")
        return name if isinstance(name, str) else ""

    def get_definitions(self) -> list[dict[str, Any]]:
        """Get tool definitions with stable ordering for cache-friendly prompts.

        Built-in tools are sorted alphabetically. The result is cached until the
        next register/unregister call.
        """
        if self._cached_definitions is not None:
            return self._cached_definitions

        definitions = [tool.to_schema() for tool in self._tools.values()]
        definitions.sort(key=self._schema_name)
        self._cached_definitions = definitions
        return self._cached_definitions

    def prepare_call(
        self,
        name: str,
        params: Any,
    ) -> tuple[Tool | None, Any, str | None]:
        """Resolve, cast, and validate one tool call.

        Qui avanza ``exec_seq``, e non in :meth:`execute`: il runner
        (``agent/tool_execution.py``) risolve il tool con ``prepare_call`` e poi
        invoca ``tool.execute(...)`` diretto, saltando ``execute()``. Contare la
        lasciava ``exec_seq`` a zero per sempre in produzione — meta della
        ragione per cui la guardia anti-polling di ``subagent_status`` viveva
        solo nei test. ``prepare_call`` e invece l'unico passaggio comune ai due
        path, esattamente una volta per tool call (anche quando la risoluzione
        fallisce: il tentativo conta comunque come "qualcosa e girato in mezzo").
        """
        self.exec_seq += 1
        tool = self._tools.get(name)
        if not tool:
            suggestion = self._suggest_name(str(name))
            hint = f" Did you mean '{suggestion}'? Tool names must match exactly." if suggestion else ""
            return None, params, (
                f"Error: Tool '{name}' not found.{hint} Available: {', '.join(self.tool_names)}"
            )

        params = self._coerce_params(tool, params)
        if not isinstance(params, dict):
            return tool, params, (
                f"Error: Tool '{name}' parameters must be a JSON object, got "
                f"{type(params).__name__}. Use named parameters like "
                'tool_name(param1="value1", param2="value2") matching the tool schema.'
            )

        cast_params = tool.cast_params(params)
        errors = tool.validate_params(cast_params)
        if errors:
            return tool, cast_params, (
                f"Error: Invalid parameters for tool '{name}': " + "; ".join(errors)
            )
        return tool, cast_params, None

    @classmethod
    def _coerce_argument_value(cls, value: Any) -> Any:
        if value is None:
            return {}
        if not isinstance(value, str):
            return value

        stripped = value.strip()
        if not stripped:
            return {}

        if not stripped.startswith(("{", "[")):
            return value

        try:
            parsed = json.loads(stripped)
        except Exception:
            return value

        return parsed

    @classmethod
    def _coerce_params(cls, tool: Tool, params: Any) -> Any:
        params = cls._coerce_argument_value(params)
        return cls._unwrap_arguments_payload(tool, params)

    @classmethod
    def _unwrap_arguments_payload(cls, tool: Tool, params: Any) -> Any:
        if not isinstance(params, dict) or set(params) != {"arguments"}:
            return params
        properties = (tool.parameters or {}).get("properties", {})
        if isinstance(properties, dict) and "arguments" in properties:
            return params
        return cls._coerce_argument_value(params.get("arguments"))

    async def execute(self, name: str, params: Any) -> Any:
        """Execute a tool by name with given parameters."""
        # ``exec_seq`` avanza dentro ``prepare_call`` (chiamata qui sotto): un
        # incremento anche qui conterebbe due volte la stessa tool call e
        # sfaserebbe chi misura "e girato qualcos'altro in mezzo?".
        hint = TOOL_ERROR_RETRY_HINT
        tool, params, error = self.prepare_call(name, params)
        if error:
            return error + hint

        try:
            assert tool is not None  # guarded by prepare_call()
            result = await tool.execute(**params)
            # Tool migrati: esito strutturato → decisione su .ok (niente più
            # mis-classificazione di output legittimi che iniziano per "Error").
            if isinstance(result, ToolResult):
                rendered = result.render()
                if not result.ok and isinstance(rendered, str):
                    return rendered + hint
                return rendered
            # Tool legacy: vecchia convenzione stringly-typed.
            if isinstance(result, str) and result.startswith("Error"):
                return result + hint
            return result
        except Exception as e:
            return f"Error executing {name}: {str(e)}" + hint

    @property
    def tool_names(self) -> list[str]:
        """Get list of registered tool names."""
        return list(self._tools.keys())

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools


class ToolRegistryView(ToolRegistry):
    """Il registro di un turno: quello del loop, meno cio' che qui non serve.

    **Vivo, non una copia.** Un'app creata a meta' turno registra le sue azioni
    sul registro del loop, e devono comparire subito anche qui; il registro del
    loop e' condiviso fra sessioni concorrenti, quindi questa vista non lo
    modifica mai (niente ``unregister``): filtra quel che c'e', a ogni lettura.

    Nato il 07/10/2026: in un quaderno 23 strumenti su 42 erano azioni di app
    posate altrove, che scrivevano e a runtime rispondevano «Not from here»;
    ``recall_history``, che nei quaderni rifiuta; ``nothing_to_report``, che
    serve solo ai giri pianificati. Un modello piccolo li vedeva tutti.

    ``exec_seq`` avanza anche sul registro del loop: la guardia anti-polling di
    ``subagent_status`` legge quello.
    """

    def __init__(self, base: ToolRegistry, hidden: "Callable[[str, Tool], bool]") -> None:
        super().__init__()
        self.view_of = base
        self._hidden = hidden
        self.file_states = base.file_states
        self.memory_entries = base.memory_entries

    @property  # type: ignore[override]
    def _tools(self) -> dict[str, Tool]:
        base = self.__dict__.get("view_of")
        if base is None:
            return {}
        return {n: t for n, t in base._tools.items() if not self._hidden(n, t)}

    @_tools.setter
    def _tools(self, _value: dict[str, Tool]) -> None:
        # ``ToolRegistry.__init__`` assegna un dict vuoto: qui non serve.
        pass

    def register(self, tool: Tool) -> None:
        self.view_of.register(tool)

    def unregister(self, name: str) -> None:
        self.view_of.unregister(name)

    def get_definitions(self) -> list[dict[str, Any]]:
        visible = set(self._tools)
        return [d for d in self.view_of.get_definitions() if self._schema_name(d) in visible]

    def prepare_call(self, name: str, params: Any) -> tuple[Tool | None, Any, str | None]:
        # Uno strumento visibile si risolve sul registro del loop, come sempre:
        # stessi controlli, e ``exec_seq`` avanza li'. Uno nascosto qui non
        # esiste, con i suggerimenti presi fra i visibili.
        if not self._is_hidden(name):
            return self.view_of.prepare_call(name, params)
        self.view_of.exec_seq += 1
        return super().prepare_call(name, params)

    async def execute(self, name: str, params: dict[str, Any]) -> Any:
        if not self._is_hidden(name):
            return await self.view_of.execute(name, params)
        return await super().execute(name, params)

    def _is_hidden(self, name: str) -> bool:
        tool = self.view_of.get(name)
        return tool is not None and self._hidden(name, tool)
