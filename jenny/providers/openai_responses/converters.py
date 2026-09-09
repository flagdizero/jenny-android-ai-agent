"""Convert Chat Completions messages/tools to Responses API format."""

from __future__ import annotations

import json
from typing import Any

from jenny.providers.base import tool_arguments_json_for_replay


def convert_messages(messages: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    """Convert Chat Completions messages to Responses API input items.

    Returns ``(system_prompt, input_items)`` where *system_prompt* is extracted
    from any ``system`` role message and *input_items* is the Responses API
    ``input`` array.
    """
    system_prompt = ""
    input_items: list[dict[str, Any]] = []
    used_item_ids: set[str] = set()

    for idx, msg in enumerate(messages):
        role = msg.get("role")
        content = msg.get("content")

        if role == "system":
            system_prompt = content if isinstance(content, str) else ""
            continue

        if role == "user":
            input_items.append(convert_user_message(content))
            continue

        if role == "assistant":
            if isinstance(content, str) and content:
                message_id = _unique_item_id(f"msg_{idx}", used_item_ids)
                input_items.append({
                    "type": "message", "role": "assistant",
                    "content": [{"type": "output_text", "text": content}],
                    "status": "completed", "id": message_id,
                })
            for tool_call in msg.get("tool_calls", []) or []:
                fn = tool_call.get("function") or {}
                call_id, item_id = split_tool_call_id(tool_call.get("id"))
                response_item_id = _unique_item_id(item_id or f"fc_{idx}", used_item_ids)
                input_items.append({
                    "type": "function_call",
                    "id": response_item_id,
                    "call_id": call_id or f"call_{idx}",
                    "name": fn.get("name"),
                    "arguments": tool_arguments_json_for_replay(fn.get("arguments")),
                })
            continue

        if role == "tool":
            call_id, _ = split_tool_call_id(msg.get("tool_call_id"))
            input_items.append({
                "type": "function_call_output",
                "call_id": call_id,
                "output": convert_tool_output(content),
            })

    return system_prompt, input_items


def convert_user_message(content: Any) -> dict[str, Any]:
    """Convert a user message's content to Responses API format.

    Handles plain strings, ``text`` blocks -> ``input_text``, and
    ``image_url`` blocks -> ``input_image``.
    """
    if isinstance(content, str):
        return {"role": "user", "content": [{"type": "input_text", "text": content}]}
    if isinstance(content, list):
        converted: list[dict[str, Any]] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            part = _input_part(item)
            if part is not None:
                converted.append(part)
        if converted:
            return {"role": "user", "content": converted}
    return {"role": "user", "content": [{"type": "input_text", "text": ""}]}


def convert_tool_output(content: Any) -> str | list[dict[str, Any]]:
    """Rende il contenuto di un messaggio ``tool`` come ``output``.

    ``function_call_output.output`` accetta **o** una stringa **o** una lista
    di input part (``input_text`` / ``input_image`` / ``input_file``): la
    stessa forma del contenuto di un messaggio utente. Serializzare a JSON una
    lista di blocchi spenderebbe il file intero come testo e lascerebbe al
    modello l'impalcatura invece dell'immagine — ``read_file`` su un'immagine
    ritorna proprio quello (``utils.helpers.build_image_content_blocks``: un
    blocco ``image_url`` con un data URI, più un blocco ``text``).

    È lo stesso difetto chiuso sul ramo Anthropic in
    ``anthropic_conversion._tool_result_block``, e qui era più insidioso: lì
    l'API rifiutava i blocchi non convertiti e la rete di
    ``base.py`` lasciava un log, mentre ``json.dumps`` riesce sempre.

    La conversione scatta **solo** quando la lista porta un blocco immagine:
    per tutto il resto resta il JSON di prima, che è il comportamento su cui
    poggiano i tool che ritornano dati strutturati.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list) and _has_image_block(content):
        parts = _tool_output_parts(content)
        if parts:
            return parts
        # Ci si arriva solo con blocchi immagine privi di URL: non c'è nulla da
        # mostrare e, per definizione, nessun base64 da versare nel prompt.
    return json.dumps(content, ensure_ascii=False)


def convert_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert OpenAI function-calling tool schema to Responses API flat format."""
    converted: list[dict[str, Any]] = []
    for tool in tools:
        fn = (tool.get("function") or {}) if tool.get("type") == "function" else tool
        name = fn.get("name")
        if not name:
            continue
        params = fn.get("parameters") or {}
        converted.append({
            "type": "function",
            "name": name,
            "description": fn.get("description") or "",
            "parameters": params if isinstance(params, dict) else {},
        })
    return converted


def _input_part(block: dict[str, Any]) -> dict[str, Any] | None:
    """Traduce un blocco di contenuto in una input part della Responses API.

    Ritorna ``None`` quando il blocco non è rappresentabile — tipo ignoto, o
    un ``image_url`` senza URL. Chi chiama decide se saltarlo (contenuto
    utente) o degradarlo a testo (output di un tool, dove perdere un pezzo in
    silenzio nasconde metà del risultato).
    """
    kind = block.get("type")
    if kind == "text":
        return {"type": "input_text", "text": block.get("text", "")}
    if kind == "image_url":
        url = (block.get("image_url") or {}).get("url")
        if url:
            return {"type": "input_image", "image_url": url, "detail": "auto"}
    return None


def _has_image_block(content: list[Any]) -> bool:
    """True se la lista porta almeno un blocco immagine."""
    return any(
        isinstance(item, dict) and item.get("type") == "image_url" for item in content
    )


def _tool_output_parts(content: list[Any]) -> list[dict[str, Any]]:
    """Converte i blocchi di un tool result in input part.

    A differenza del contenuto utente, qui un blocco non rappresentabile non
    si salta: diventa testo. Un tool result è una risposta a una domanda del
    modello, e un pezzo sparito in silenzio è peggio di un pezzo grezzo. Le
    sole eccezioni sono i blocchi immagine inutilizzabili (nessun URL), che
    non hanno niente da dire.
    """
    parts: list[dict[str, Any]] = []
    for item in content:
        if isinstance(item, dict):
            part = _input_part(item)
            if part is not None:
                parts.append(part)
                continue
            if item.get("type") == "image_url":
                continue
            text = json.dumps(item, ensure_ascii=False)
        else:
            text = str(item)
        parts.append({"type": "input_text", "text": text})
    return parts


def _unique_item_id(item_id: str, used: set[str]) -> str:
    """Return a Responses input item id that is unique within one request."""
    if item_id not in used:
        used.add(item_id)
        return item_id

    suffix = 2
    while f"{item_id}_{suffix}" in used:
        suffix += 1
    unique = f"{item_id}_{suffix}"
    used.add(unique)
    return unique


def split_tool_call_id(tool_call_id: Any) -> tuple[str, str | None]:
    """Split a compound ``call_id|item_id`` string.

    Returns ``(call_id, item_id)`` where *item_id* may be ``None``.
    """
    if isinstance(tool_call_id, str) and tool_call_id:
        if "|" in tool_call_id:
            call_id, item_id = tool_call_id.split("|", 1)
            return call_id, item_id or None
        return tool_call_id, None
    return "call_0", None
