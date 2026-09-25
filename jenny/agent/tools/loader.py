"""Tool discovery and registration."""
from __future__ import annotations

import contextlib
import importlib
from dataclasses import dataclass
from typing import Any

from loguru import logger

from jenny.agent.tools.base import Tool
from jenny.agent.tools.registry import ToolRegistry


class ToolLoadError(RuntimeError):
    """Errore fatale di caricamento tool: aborta lo startup del gateway.

    Riservato agli errori di programmazione, deterministici e riproducibili
    a ogni boot: modulo senza ``TOOLS``, voce di ``TOOLS`` che non è un Tool,
    collisione di nome. Non viene mai catturato dal loader.
    """


@dataclass(frozen=True)
class ToolLoadFailure:
    """Fallimento tollerato di un singolo tool (``enabled()``/``create()``).

    A differenza di :class:`ToolLoadError` non aborta il boot: il tool viene
    escluso da questo run e il fallimento resta ispezionabile via
    ``ToolLoader.failures``.
    """

    tool: str
    stage: str  # "enabled" | "create"
    error: BaseException


def declared_tool_name(tool_cls: type[Tool]) -> str | None:
    """Nome di un tool ricavato dalla sola classe, senza costruirla.

    Serve al filtro ``allow`` di :meth:`ToolLoader.load`, che deve conoscere i
    nomi *prima* di ``enabled()``/``create()``: un tool disabilitato
    dall'ambiente ha comunque un nome, e senza questo una voce di allowlist
    legittima sembrerebbe un typo (vedi ``_validate_allow``).

    Alcune classi dichiarano ``name`` come attributo, altre come ``@property``:
    per le seconde si legge la property su un'istanza non inizializzata, che è
    sufficiente perché il valore è una costante. Se anche questo non basta si
    ritorna ``None``: il chiamante degrada, non solleva.
    """
    attr = getattr(tool_cls, "name", None)
    if isinstance(attr, str):
        return attr
    try:
        value = tool_cls.__new__(tool_cls).name  # type: ignore[call-arg]
    except Exception:
        return None
    return value if isinstance(value, str) else None


_HARDCODED_TOOL_MODULES = [
    "filesystem",
    "python_exec",
    "android_web",
    "browser",
    "download",
    "location",
    "long_task",
    "spawn",
    "subagent_control",
    "cron",
    "journal",
    "self",
    "memory_recall",
    "search",
    "message",
    "nothing_to_report",
    "apply_patch",
    "exec_session",
    "introspect",
    "diagnostics",
    "ui_view",
    "ssh",
    "app_update",
]


class ToolLoader:
    def __init__(self, package: Any = None, *, test_classes: list[type[Tool]] | None = None):
        if package is None:
            import jenny.agent.tools as _pkg
            package = _pkg
        self._package = package
        self._test_classes = test_classes
        self._discovered: list[type[Tool]] | None = None
        # Popolata da ``load()``: i tool esclusi da questo run perché
        # ``enabled()``/``create()`` hanno sollevato. Vedi ToolLoadFailure.
        self.failures: list[ToolLoadFailure] = []

    def discover(self) -> list[type[Tool]]:
        if self._test_classes is not None:
            return list(self._test_classes)
        if self._discovered is not None:
            return self._discovered

        seen: set[int] = set()
        results: list[type[Tool]] = []

        for name in _HARDCODED_TOOL_MODULES:
            module = importlib.import_module(f".{name}", self._package.__name__)
            # Registrazione esplicita (Fase 5.3): ogni modulo tool dichiara
            # ``TOOLS = [...]``. Niente più reflection dir() (implicita,
            # sensibile all'ordine). Un modulo senza ``TOOLS`` è un errore
            # rumoroso allo startup, non un tool che sparisce in silenzio.
            tools = getattr(module, "TOOLS", None)
            if tools is None:
                raise ToolLoadError(
                    f"Tool module '{name}' does not declare a TOOLS list "
                    f"(explicit registration required — see loader.py)."
                )
            for attr in tools:
                if not (isinstance(attr, type) and issubclass(attr, Tool) and attr is not Tool):
                    raise ToolLoadError(
                        f"TOOLS entry {attr!r} in module '{name}' is not a Tool subclass."
                    )
                if id(attr) in seen:
                    continue
                seen.add(id(attr))
                results.append(attr)

        results.sort(key=lambda cls: cls.__name__)
        self._discovered = results
        return results

    def load(
        self,
        ctx: Any,
        registry: ToolRegistry,
        *,
        scope: str = "core",
        allow: set[str] | frozenset[str] | None = None,
    ) -> list[str]:
        """Istanzia e registra i tool discovered nel registry.

        ``scope`` filtra per ``Tool._scopes``, che è per-classe e globale:
        distingue "core" da "subagent", non un researcher da un coder. ``allow``
        è il secondo filtro, per *nome* di tool, e serve proprio a quello (vedi
        ``jenny/agent/agent_types.py``). ``allow=None`` non filtra nulla ed è il
        comportamento storico.

        Due classi di errore, distinte esplicitamente:

        * **Fatale** (:class:`ToolLoadError`, propagato fuori da ``load()``):
          collisione di nome, voce di ``allow`` che non corrisponde a nessun
          tool noto, e — via :meth:`discover` — modulo senza ``TOOLS``
          o voce non-Tool. Sono bug deterministici del codice: si riproducono a
          ogni boot e vanno visti subito, non nascosti in un log.
        * **Tollerato** (:class:`ToolLoadFailure`): ``enabled()``/``create()``
          sollevano. Dipendono dall'ambiente runtime (config, servizi Android
          assenti) e il gateway resta l'unico modo che l'utente ha per
          rimediare: il singolo tool viene escluso, ma con log ERROR + entry in
          ``self.failures``, non in silenzio.
        """
        self.failures = []
        # Il registry in costruzione viene esposto sul ctx: un tool che deve
        # sapere se *altri* tool hanno girato (guardia anti-polling di
        # ``subagent_status``) non ha altro modo di osservarlo. ``ctx`` puo
        # essere None nei test: in quel caso non c'e nulla da valorizzare.
        if getattr(ctx, "registry", None) is None:
            with contextlib.suppress(AttributeError):
                ctx.registry = registry
        in_scope = [
            cls for cls in self.discover()
            if scope in getattr(cls, "_scopes", {"core"})
        ]
        if allow is not None:
            self._validate_allow(allow, in_scope, scope=scope)
        registered: list[str] = []
        for tool_cls in in_scope:
            cls_label = tool_cls.__name__
            if allow is not None and declared_tool_name(tool_cls) not in allow:
                continue

            # --- Parte tollerata: dipende dall'ambiente ---
            try:
                if not tool_cls.enabled(ctx):
                    continue
            except Exception as exc:
                self._record_failure(cls_label, "enabled", exc)
                continue
            try:
                tool = tool_cls.create(ctx)
            except Exception as exc:
                self._record_failure(cls_label, "create", exc)
                continue

            # Nome effettivo dell'istanza: normalmente identico a quello
            # dichiarato, ma un tool a nome dinamico non deve poter entrare in
            # un registry filtrato solo perché la risoluzione statica ha
            # sbagliato.
            if allow is not None and tool.name not in allow:
                continue

            # --- Parte fatale: errore di programmazione ---
            if registry.has(tool.name):
                # Fase 5.3: una collisione di nome è un errore (prima era un
                # warning che sovrascriveva silenziosamente il tool esistente).
                raise ToolLoadError(
                    f"Tool name collision: '{tool.name}' from {cls_label} "
                    f"conflicts with an already-registered tool."
                )
            registry.register(tool)
            registered.append(tool.name)

        if self.failures:
            logger.error(
                "Tools disabled for this run after load failures: {}",
                ", ".join(f"{f.tool} ({f.stage})" for f in self.failures),
            )
        return registered

    @staticmethod
    def _validate_allow(
        allow: set[str] | frozenset[str],
        in_scope: list[type[Tool]],
        *,
        scope: str,
    ) -> None:
        """Rifiuta le voci di ``allow`` che non corrispondono a nessun tool noto.

        Il confronto è contro i nomi *dichiarati* dalle classi in scope, non
        contro i tool effettivamente registrati: ``enabled()`` può dire no per
        ragioni d'ambiente (toggle di config, servizio Android assente) e un
        tool disabilitato dall'utente non deve trasformare un'allowlist corretta
        in un boot abortito. Al contrario un nome che non esiste *affatto* è un
        typo nella definizione di un agent type, cioè un bug deterministico: se
        passasse in silenzio, il subagent girerebbe con meno tool di quelli
        previsti e nessuno lo saprebbe.
        """
        known = {name for cls in in_scope if (name := declared_tool_name(cls))}
        unknown = sorted(set(allow) - known)
        if unknown:
            raise ToolLoadError(
                f"Unknown tool name(s) in allow list for scope '{scope}': "
                f"{', '.join(unknown)}. Known tools in this scope: "
                f"{', '.join(sorted(known))}."
            )

    def _record_failure(self, cls_label: str, stage: str, exc: BaseException) -> None:
        self.failures.append(ToolLoadFailure(tool=cls_label, stage=stage, error=exc))
        logger.opt(exception=exc).error(
            "Tool {} disabled: {}() raised", cls_label, stage
        )
