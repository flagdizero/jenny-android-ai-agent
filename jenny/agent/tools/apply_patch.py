"""Apply file edits by providing structured edit instructions."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jenny.agent.tools.base import tool_parameters
from jenny.agent.tools.filesystem import _FsTool
from jenny.agent.tools.schema import (
    ArraySchema,
    BooleanSchema,
    ObjectSchema,
    StringSchema,
    tool_parameters_schema,
)
from jenny.utils.file_edit_events import line_diff_stats


@dataclass(slots=True)
class _PatchSummary:
    action: str
    path: str
    added: int = 0
    deleted: int = 0


class _PatchError(ValueError):
    pass


def _validate_patch_path(path: str) -> str:
    normalized = path.strip()
    if not normalized:
        raise _PatchError("patch path cannot be empty")
    if "\0" in normalized:
        raise _PatchError(f"patch path contains a null byte: {path!r}")
    return normalized


def _append_text(content: str, addition: str) -> str:
    """Append text without merging it into an unterminated final line."""
    base = content.replace("\r\n", "\n")
    extra = addition.replace("\r\n", "\n")
    if base and extra and not base.endswith("\n") and not extra.startswith("\n"):
        base += "\n"
    combined = base + extra
    if combined and not combined.endswith("\n"):
        combined += "\n"
    return combined


def _format_summary(summary: _PatchSummary) -> str:
    stats = ""
    if summary.added or summary.deleted:
        stats = f" (+{summary.added}/-{summary.deleted})"
    return f"- {summary.action} {summary.path}{stats}"


@tool_parameters(
    tool_parameters_schema(
        edits=ArraySchema(
            items=ObjectSchema(
                path=StringSchema(
                    "Path to the file to edit. Relative paths resolve against the "
                    "workspace; absolute paths and '..' obey the workspace access policy."
                ),
                action=StringSchema(
                    "Operation type: replace or add.",
                    enum=["replace", "add"],
                ),
                old_text=StringSchema(
                    "Exact text to search for in the file. Required for replace.",
                    nullable=True,
                ),
                new_text=StringSchema(
                    "Text to replace with or append. Required for replace and add.",
                    nullable=True,
                ),
                required=["path", "action"],
            ),
            description="List of edits to apply. Each edit specifies a file and the change to make.",
            min_items=1,
            max_items=20,
        ),
        dry_run=BooleanSchema(
            description="Validate and summarize the patch without writing files.",
            default=False,
        ),
        required=["edits"],
    )
)
class ApplyPatchTool(_FsTool):
    """Apply file edits by providing structured edit instructions."""
    _scopes = {"core", "subagent"}

    @property
    def name(self) -> str:
        return "apply_patch"

    @property
    def description(self) -> str:
        return (
            "Default tool for code edits. Supports multi-file changes in a single call. "
            "Provide a list of structured edits, each specifying a file path, action "
            "(replace/add), and the exact text to change. "
            "Paths are resolved by the current workspace access policy. "
            "Set dry_run=true to validate and preview without writing files. "
            "Use edit_file only for small exact replacements on a single file."
        )

    async def execute(
        self,
        edits: list[dict] | None = None,
        dry_run: bool = False,
        **kwargs: Any,
    ) -> str:
        try:
            if not edits:
                raise _PatchError("must provide edits")

            writes: dict[Path, str] = {}
            summaries: list[_PatchSummary] = []

            for edit in edits:
                if not isinstance(edit, dict):
                    raise _PatchError("each edit must be an object")
                raw_path = edit.get("path")
                if not isinstance(raw_path, str):
                    raise _PatchError("path required for edit")
                path = _validate_patch_path(raw_path)
                action = edit.get("action")
                if not isinstance(action, str):
                    raise _PatchError(f"action required for edit: {path}")
                source = self._resolve_write(path)

                if action == "add":
                    new_text = edit.get("new_text")
                    if new_text is None:
                        raise _PatchError(f"new_text required for add: {path}")

                    pending = writes.get(source)
                    if pending is not None:
                        content = pending
                        exists = True
                    elif source.exists():
                        raw = source.read_bytes()
                        try:
                            content = raw.decode("utf-8")
                        except UnicodeDecodeError:
                            raise _PatchError(f"file is not UTF-8 text: {path}")
                        exists = True
                    else:
                        content = ""
                        exists = False

                    if exists:
                        uses_crlf = "\r\n" in content
                        new_norm = _append_text(content, new_text)
                        if uses_crlf:
                            new_norm = new_norm.replace("\n", "\r\n")
                        writes[source] = new_norm
                        added, deleted = line_diff_stats(content, new_norm)
                        action_name = "update"
                    else:
                        new_norm = new_text.replace("\r\n", "\n")
                        if new_norm and not new_norm.endswith("\n"):
                            new_norm += "\n"
                        writes[source] = new_norm
                        # Lo stesso conto della riga che la WebUI mostra durante la
                        # modifica: ``line_diff_stats`` conta le righe con la stessa
                        # regola per un file nuovo e per uno che c'era gia'.
                        added, deleted = line_diff_stats("", new_norm)
                        action_name = "add"

                    summaries.append(
                        _PatchSummary(
                            action=action_name, path=path, added=added, deleted=deleted
                        )
                    )

                elif action == "replace":
                    old_text = edit.get("old_text") or ""
                    if not old_text:
                        raise _PatchError(f"old_text required for replace: {path}")
                    new_text = edit.get("new_text")
                    if new_text is None:
                        raise _PatchError(f"new_text required for replace: {path}")

                    pending = writes.get(source)
                    if pending is not None:
                        content = pending
                    elif source.exists():
                        raw = source.read_bytes()
                        try:
                            content = raw.decode("utf-8")
                        except UnicodeDecodeError:
                            raise _PatchError(f"file is not UTF-8 text: {path}")
                    else:
                        raise _PatchError(f"file to update does not exist: {path}")

                    if pending is None and not source.is_file():
                        raise _PatchError(f"path to update is not a file: {path}")

                    uses_crlf = "\r\n" in content
                    norm_content = content.replace("\r\n", "\n")
                    norm_old = old_text.replace("\r\n", "\n")

                    pos = norm_content.find(norm_old)
                    if pos < 0:
                        raise _PatchError(f"old_text not found in {path}")
                    if norm_content.find(norm_old, pos + 1) >= 0:
                        raise _PatchError(f"old_text appears multiple times in {path}")

                    new_norm = (
                        norm_content[:pos]
                        + new_text.replace("\r\n", "\n")
                        + norm_content[pos + len(norm_old) :]
                    )
                    if new_norm and not new_norm.endswith("\n"):
                        new_norm += "\n"
                    if uses_crlf:
                        new_norm = new_norm.replace("\n", "\r\n")

                    writes[source] = new_norm
                    added, deleted = line_diff_stats(content, new_norm)
                    summaries.append(
                        _PatchSummary(
                            action="update", path=path, added=added, deleted=deleted
                        )
                    )

                else:
                    raise _PatchError(f"unknown action: {action}")

            if dry_run:
                # Il dry-run non passa dal gancio: non scrive niente, quindi non
                # c'è niente da rifiutare, e rifiutarlo nasconderebbe proprio il
                # riepilogo che serve a capire di quanto si sta sforando.
                return "Patch dry-run succeeded:\n" + "\n".join(
                    _format_summary(summary) for summary in summaries
                )

            # Tutto-o-niente: il gancio si pronuncia su *tutti* i file prima che
            # parta la prima scrittura. Il rollback qui sotto esiste perché una
            # patch applicata a metà è peggio di una rifiutata, e un rifiuto a
            # metà ciclo la produrrebbe senza nemmeno un'eccezione a innescarlo.
            notes: list[str] = []
            for target, pending_text in writes.items():
                refusal = self._check_write_size(target, pending_text)
                if refusal is not None:
                    return refusal
                # Nello stesso ciclo del gancio, cioe' **prima della prima
                # scrittura**: l'avviso confronta col "prima" letto da disco, e
                # una patch che tocca due volte lo stesso file lo tocca una volta
                # sola qui (``writes`` e' per percorso). Se un file piu' avanti
                # viene rifiutato non si scrive niente e l'avviso non parte, che
                # e' giusto: non e' successo niente da annunciare. T9.12.
                note = self._wiki_page_ceiling_note(target, pending_text)
                if note is not None:
                    notes.append(note)

            backups: dict[Path, bytes | None] = {}
            for path in writes:
                backups[path] = path.read_bytes() if path.exists() else None

            try:
                for path, content in writes.items():
                    # Dentro il ciclo di scrittura e non insieme al controllo del
                    # budget qui sopra: quello si pronuncia su tutti i file prima
                    # che parta la prima scrittura, e degradare li' vorrebbe dire
                    # archiviare le voci di una patch poi rifiutata. Se il
                    # rollback scatta dopo resta una voce archiviata che non se
                    # n'e' andata: dei due versi, quello riparabile.
                    self._archive_departing(path, content)
                    # ``_commit_write`` fa la mkdir e sceglie se la scrittura va
                    # in posto o via ``atomic_write`` (v. il suo docstring); il
                    # testo qui è già byte-per-byte quello che deve finire su
                    # disco, come con il vecchio ``newline=""``.
                    self._commit_write(path, content)
            except Exception:
                for path, data in backups.items():
                    if data is None:
                        if path.exists():
                            path.unlink()
                    else:
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_bytes(data)
                raise

            for path in writes:
                self._file_states.record_write(path)
            applied = "Patch applied:\n" + "\n".join(
                _format_summary(summary) for summary in summaries
            )
            # Il riepilogo di questo tool e' un **delta di righe**, mai una
            # dimensione: da solo non dice a cosa la patch ha portato il file.
            return applied if not notes else applied + "\n" + "\n".join(notes)
        except PermissionError as exc:
            return f"Error: {exc}"
        except _PatchError as exc:
            return f"Error applying patch: {exc}"
        except Exception as exc:
            return f"Error applying patch: {exc}"


# Registrazione esplicita dei tool di questo modulo (Fase 5.3): il
# ToolLoader legge questa lista invece della reflection dir(). Un nuovo
# tool va aggiunto qui esplicitamente.
TOOLS = [ApplyPatchTool]
