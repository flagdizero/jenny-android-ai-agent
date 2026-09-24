"""Source introspection tool: read jenny's own source code.

Gives the agent first-class, read-only visibility into the framework's
source so it can debug its own tools without resorting to bytecode
introspection through python_exec. Deliberately implemented server-side
(outside the python_exec sandbox) and restricted to the ``jenny``
package only.
"""

from __future__ import annotations

import importlib
import inspect
from typing import Any

from jenny.agent.tools.base import Tool, tool_parameters
from jenny.agent.tools.schema import StringSchema, tool_parameters_schema

_MAX_SOURCE_CHARS = 50_000


def _resolve_target(target: str) -> Any:
    """Resolve a dotted target like ``jenny.agent.tools.android_web._get_bridge``.

    Imports the longest importable module prefix, then walks the remaining
    attributes (classes, functions, methods).
    """
    parts = target.split(".")
    obj = None
    for i in range(len(parts), 0, -1):
        module_name = ".".join(parts[:i])
        try:
            obj = importlib.import_module(module_name)
        except ImportError:
            continue
        for attr in parts[i:]:
            obj = getattr(obj, attr)
        return obj
    raise ImportError(f"Cannot import any module prefix of '{target}'")


@tool_parameters(
    tool_parameters_schema(
        target=StringSchema(
            "Dotted path to a jenny module, class, or function, e.g. "
            "'jenny.agent.tools.android_web' or "
            "'jenny.agent.loop.AgentLoop.run'"
        ),
        required=["target"],
    )
)
class GetSourceTool(Tool):
    """Return the source code of a jenny module, class, or function."""

    _scopes = {"core", "orchestrator", "subagent"}

    name = "get_source"
    description = (
        "Read the source code of jenny's own modules, classes, or functions "
        "by dotted path (e.g. 'jenny.agent.tools.android_web._looks_like_captcha'). "
        "Use this to understand or debug the framework's behavior — do not "
        "reconstruct logic from bytecode via python_exec."
    )

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        return ctx.config.introspect.enable

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        return cls()

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, target: str, **kwargs: Any) -> str:
        target = target.strip()
        if target != "jenny" and not target.startswith("jenny."):
            return "Error: get_source only exposes the jenny package (target must start with 'jenny.')."
        try:
            obj = _resolve_target(target)
        except (ImportError, AttributeError) as exc:
            return f"Error: cannot resolve '{target}': {exc}"
        try:
            source = inspect.getsource(obj)
            source_file = inspect.getsourcefile(obj) or "<unknown>"
        except (OSError, TypeError):
            # Packaged builds (.imy) have no .py next to the bytecode; fall
            # back to the extracted source tree, returning the whole module.
            fallback = _read_from_source_root(target, obj)
            if fallback is None:
                return (
                    f"Error: source not available for '{target}' on this platform "
                    "(packaged build without extracted .py sources)."
                )
            source_file, source = fallback
        if len(source) > _MAX_SOURCE_CHARS:
            source = source[:_MAX_SOURCE_CHARS] + "\n... [truncated]"
        return f"# {source_file}\n{source}"


def _read_from_source_root(target: str, obj: Any) -> tuple[str, str] | None:
    """Read the target's module .py file from the extracted source tree."""
    from jenny.utils.android_assets import get_package_source_root

    root = get_package_source_root()
    if root is None:
        return None
    module = inspect.getmodule(obj)
    module_name = module.__name__ if module is not None else target
    rel = module_name.removeprefix("jenny").lstrip(".").replace(".", "/")
    # Root may be the package dir itself (dev) or a dir containing jenny/
    # (extracted assets); try both layouts, package and plain module.
    candidates = []
    for base in (root, root / "jenny"):
        candidates.append(base / f"{rel}.py" if rel else base / "__init__.py")
        if rel:
            candidates.append(base / rel / "__init__.py")
    for path in candidates:
        if path.is_file():
            return str(path), path.read_text(encoding="utf-8")
    return None


# Registrazione esplicita dei tool di questo modulo (Fase 5.3): il
# ToolLoader legge questa lista invece della reflection dir(). Un nuovo
# tool va aggiunto qui esplicitamente.
TOOLS = [GetSourceTool]
