#!/usr/bin/env python3
# Host-only: diagnostic script. Never imported by the Android runtime.
"""Ricostruisce la richiesta vera che Jenny manderebbe al modello, e la misura.

Il turno gira con il vero ``AgentLoop.from_config`` sopra una **copia** di un
workspace (per esempio quella del telefono, tirata giù con ``su`` e ``tar``),
con un provider finto che registra la richiesta invece di mandarla. Così si
misura il prompt che il modello riceve davvero — prompt di sistema blocco per
blocco, schemi dei tool, cronologia divisa in testo, ragionamenti, argomenti e
risultati — senza spendere un token e senza toccare il telefono.

Nato il 07/10/2026: sul Titan i turni duravano 4–14 minuti, il log diceva
«50.135 token» e il provider ne contava 165.309. Questa sonda è il modo di dire
«prima X, dopo Y» per ogni correzione al contesto.

**La copia contiene dati personali**: tienila fuori dal repo (la scratchpad va
bene) e non incollare il JSON salvato con ``--save`` in un issue.

Uso::

    python scripts/prompt_probe.py --workspace /percorso/copia \\
        --chat project:etf-finance --message "ciao" [--save richiesta.json]

Il turno di prova **scrive nella copia** (la sessione si allunga di un turno):
lavora su una copia usa e getta.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Caratteri per token misurati sul Titan con deepseek-v4.1-flash: 557 k
# caratteri di richiesta contro 150 k token riportati dal provider.
CHARS_PER_TOKEN_MEASURED = 3.7


class _RecordingProvider:
    """Un provider che non chiama nessuno: tiene la richiesta e risponde «ok»."""

    def __init__(self, model: str) -> None:
        self.model = model
        self.generation = SimpleNamespace(max_tokens=8192, temperature=0.1, reasoning_effort=None)
        self.requests: list[dict[str, Any]] = []

    def get_default_model(self) -> str:
        return self.model

    async def chat_with_retry(self, *_args: Any, **kwargs: Any) -> Any:
        from jenny.providers.base import LLMResponse

        self.requests.append(json.loads(json.dumps(kwargs, default=str)))
        return LLMResponse(content="ok")

    chat_stream_with_retry = chat_with_retry


def _size(value: Any) -> int:
    return len(value) if isinstance(value, str) else len(json.dumps(value, ensure_ascii=False))


def _system_blocks(text: str) -> list[tuple[str, int]]:
    """I blocchi del prompt di sistema: separati da ``---``, col primo titolo."""
    out = []
    for block in text.split("\n\n---\n\n"):
        head = next((ln for ln in block.splitlines() if re.match(r"^#{1,3} |^\[", ln)), "")
        out.append((head[:70] or block.strip().splitlines()[0][:70], len(block)))
    return out


def measure(request: dict[str, Any]) -> dict[str, Any]:
    """Le parti della richiesta, in caratteri."""
    messages = request.get("messages") or []
    tools = request.get("tools") or []
    system = "".join(m.get("content") or "" for m in messages if m.get("role") == "system")
    history = [m for m in messages if m.get("role") != "system"]
    parts = {
        "system": len(system),
        "tools": _size(tools),
        "reasoning": sum(_size(m.get("reasoning_content") or "") for m in history),
        "text": sum(_size(m.get("content") or "") for m in history if m.get("role") != "tool"),
        "tool_args": sum(len(t["function"]["arguments"]) for m in history
                         for t in m.get("tool_calls") or []),
        "tool_results": sum(_size(m.get("content") or "") for m in history
                            if m.get("role") == "tool"),
    }
    total = sum(parts.values())
    return {"parts": parts, "total_chars": total, "messages": len(messages),
            "tool_count": len(tools),
            "real_tokens_estimate": round(total / CHARS_PER_TOKEN_MEASURED),
            "system_blocks": _system_blocks(system)}


async def probe(workspace: Path, chat: str, message: str) -> dict[str, Any]:
    from jenny.agent.loop import AgentLoop
    from jenny.bus.events import InboundMessage
    from jenny.bus.queue import MessageBus
    from jenny.config.loader import load_config
    from jenny.runtime.context import get_runtime_context

    ctx = get_runtime_context()
    ctx.workspace_dir = workspace
    ctx.config_path = workspace / "config.json"
    config = load_config(workspace / "config.json")
    provider = _RecordingProvider(config.agents.defaults.model)
    loop = AgentLoop.from_config(config, MessageBus(), provider=provider)
    from jenny.utils.helpers import estimate_prompt_tokens_chain

    await loop._process_message(InboundMessage(
        channel="websocket", sender_id="probe", chat_id=chat, content=message,
        metadata={"webui": True}))
    if not provider.requests:
        raise SystemExit("the turn did not reach the provider")
    first = provider.requests[0]
    report = measure(first)
    report["jenny_estimate"] = estimate_prompt_tokens_chain(
        provider, provider.model, first.get("messages") or [], first.get("tools"))[0]
    report["request"] = first
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--chat", default="default",
                        help="chat id of the websocket channel, e.g. project:etf-finance")
    parser.add_argument("--message", default="ciao")
    parser.add_argument("--save", type=Path, help="write the recorded request here")
    args = parser.parse_args()

    report = asyncio.run(probe(args.workspace.resolve(), args.chat, args.message))
    request = report.pop("request")
    if args.save:
        args.save.write_text(json.dumps(request, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"messages {report['messages']} · tools {report['tool_count']} · "
          f"{report['total_chars']:,} chars ≈ {report['real_tokens_estimate']:,} real tokens · "
          f"Jenny's estimate {report['jenny_estimate']:,}")
    for name, size in report["parts"].items():
        share = 100 * size / max(1, report["total_chars"])
        print(f"  {name:13s} {size:9,d}  {share:5.1f}%")
    print("system prompt blocks:")
    for head, size in report["system_blocks"]:
        print(f"  {size:7,d}  {head}")


if __name__ == "__main__":
    main()
