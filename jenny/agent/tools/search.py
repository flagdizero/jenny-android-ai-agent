"""Search tools: file discovery and grep."""

from __future__ import annotations

import asyncio
import fnmatch
import os
import re
from contextlib import suppress
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, TypeVar

from jenny.agent.tools.filesystem import ListDirTool, _FsTool
from jenny.security.subagent_policy import check_read as check_subagent_read
from jenny.security.subagent_policy import is_secret_path

_DEFAULT_HEAD_LIMIT = 250
_DEFAULT_FILE_HEAD_LIMIT = 200
T = TypeVar("T")
_TYPE_GLOB_MAP = {
    "py": ("*.py", "*.pyi"),
    "python": ("*.py", "*.pyi"),
    "js": ("*.js", "*.jsx", "*.mjs", "*.cjs"),
    "ts": ("*.ts", "*.tsx", "*.mts", "*.cts"),
    "tsx": ("*.tsx",),
    "jsx": ("*.jsx",),
    "json": ("*.json",),
    "md": ("*.md", "*.mdx"),
    "markdown": ("*.md", "*.mdx"),
    "go": ("*.go",),
    "rs": ("*.rs",),
    "rust": ("*.rs",),
    "java": ("*.java",),
    "yaml": ("*.yaml", "*.yml"),
    "yml": ("*.yaml", "*.yml"),
    "toml": ("*.toml",),
    "sql": ("*.sql",),
    "html": ("*.html", "*.htm"),
    "css": ("*.css", "*.scss", "*.sass"),
}


def _normalize_pattern(pattern: str) -> str:
    return pattern.strip().replace("\\", "/")


def _match_glob(rel_path: str, name: str, pattern: str) -> bool:
    normalized = _normalize_pattern(pattern)
    if not normalized:
        return False
    if "/" in normalized or normalized.startswith("**"):
        return PurePosixPath(rel_path).match(normalized)
    return fnmatch.fnmatch(name, normalized)


def _is_binary(raw: bytes) -> bool:
    if b"\x00" in raw:
        return True
    sample = raw[:4096]
    if not sample:
        return False
    non_text = sum(byte < 9 or 13 < byte < 32 for byte in sample)
    return (non_text / len(sample)) > 0.2


def _paginate(items: list[T], limit: int | None, offset: int) -> tuple[list[T], bool]:
    if limit is None:
        return items[offset:], False
    sliced = items[offset : offset + limit]
    truncated = len(items) > offset + limit
    return sliced, truncated


def _pagination_note(limit: int | None, offset: int, truncated: bool) -> str | None:
    if truncated:
        if limit is None:
            return f"(pagination: offset={offset})"
        return f"(pagination: limit={limit}, offset={offset})"
    if offset > 0:
        return f"(pagination: offset={offset})"
    return None


def _matches_type(name: str, file_type: str | None) -> bool:
    if not file_type:
        return True
    lowered = file_type.strip().lower()
    if not lowered:
        return True
    patterns = _TYPE_GLOB_MAP.get(lowered, (f"*.{lowered}",))
    return any(fnmatch.fnmatch(name.lower(), pattern.lower()) for pattern in patterns)


def _matches_query(rel_path: str, query: str | None) -> bool:
    if not query:
        return True
    haystack = rel_path.lower()
    terms = [part for part in query.lower().split() if part]
    return all(term in haystack for term in terms)


class _SearchTool(_FsTool):
    _IGNORE_DIRS = set(ListDirTool._IGNORE_DIRS)

    def _display_path(self, target: Path, root: Path) -> str:
        workspace = self._display_workspace()
        if workspace:
            with suppress(ValueError):
                return target.relative_to(workspace).as_posix()
            # Fuori dalla base dei percorsi relativi (un progetto che cerca in
            # `skills/`): un percorso relativo alla radice della *ricerca*
            # sarebbe risolto da `read_file` dentro il progetto, cioè «File not
            # found» per un percorso appena restituito. Assoluto, invece,
            # si apre con lo stesso controllo di lettura.
            return target.as_posix()
        return target.relative_to(root).as_posix()

    def _query_path(self, target: Path, display_path: str, rel_path: str) -> str:
        """Il testo su cui si misura il filtro ``query`` di un risultato.

        Di norma quello mostrato. Ma fuori dalla base dei percorsi relativi il
        risultato si mostra assoluto (v. ``_display_path``), e un filtro misurato
        sul percorso assoluto faceva passare ogni file per una parola che sta
        sopra l'installazione — il nome della sua cartella, ``tmp``, ``data``.
        Lì si misura sul percorso dentro il workspace, che è quello che un
        risultato interno mostrerebbe; e, se il file sta fuori anche da lui, su
        quello dentro la radice della ricerca.
        """
        if not Path(display_path).is_absolute():
            return display_path
        if self._workspace is not None:
            with suppress(ValueError):
                return target.relative_to(Path(self._workspace)).as_posix()
        return rel_path

    def _link_escapes(self, candidate: Path) -> bool:
        """Un file-symlink il cui bersaglio ``read_file`` rifiuterebbe.

        ``os.walk`` non scende nei link a cartelle, ma i link a file li elenca
        fra i file: senza questo controllo ``grep`` ne apriva il bersaglio fuori
        dal confine e ne stampava il contenuto. Stesso giudice di ``read_file``
        (``_resolve_read``), così i due tool non possono dire cose diverse.
        """
        if not candidate.is_symlink():
            return False
        try:
            self._resolve_read(str(candidate))
        except (OSError, ValueError, RuntimeError):
            return True
        return False

    def _iter_files(self, root: Path) -> Iterable[Path]:
        # Dentro un subagent i file segreti dell'installazione non compaiono
        # nemmeno: ``grep apiKey .`` dalla radice stampava ``config.json``.
        # Fuori da un subagent ``is_secret_path`` e' sempre False.
        if root.is_file():
            if not is_secret_path(root):
                yield root
            return

        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames if d not in self._IGNORE_DIRS)
            current = Path(dirpath)
            for filename in sorted(filenames):
                candidate = current / filename
                if self._link_escapes(candidate) or is_secret_path(candidate):
                    continue
                yield candidate


class FindFilesTool(_SearchTool):
    """Find files by path fragment, glob, or type."""
    _scopes = {"core", "subagent"}

    @property
    def name(self) -> str:
        return "find_files"

    @property
    def description(self) -> str:
        return (
            "Find files by path fragment, glob, or file type. "
            "Use this before read_file when you need to locate files, and "
            "use for workspace file discovery."
            "Returns workspace-relative paths and skips common dependency/build "
            "directories."
        )

    @property
    def read_only(self) -> bool:
        return True

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Directory or file to search in (default '.')",
                },
                "query": {
                    "type": "string",
                    "description": (
                        "Optional case-insensitive path fragment search. "
                        "Whitespace-separated terms must all be present."
                    ),
                },
                "glob": {
                    "type": "string",
                    "description": "Optional file filter, e.g. '*.py' or 'tests/**/test_*.py'",
                },
                "type": {
                    "type": "string",
                    "description": "Optional file type shorthand, e.g. 'py', 'ts', 'md', 'json'",
                },
                "include_dirs": {
                    "type": "boolean",
                    "description": "Include matching directories as well as files (default false)",
                },
                "sort": {
                    "type": "string",
                    "enum": ["path", "modified"],
                    "description": "Sort by path or most recently modified first (default path)",
                },
                "head_limit": {
                    "type": "integer",
                    "description": "Maximum number of paths to return (default 200, 0 for all, max 1000)",
                    "minimum": 0,
                    "maximum": 1000,
                },
                "offset": {
                    "type": "integer",
                    "description": "Skip the first N results before applying head_limit",
                    "minimum": 0,
                    "maximum": 100000,
                },
            },
        }

    def _iter_paths(self, root: Path, *, include_dirs: bool) -> Iterable[Path]:
        if root.is_file():
            yield root
            return
        if include_dirs:
            yield root
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames if d not in self._IGNORE_DIRS)
            current = Path(dirpath)
            if include_dirs and current != root:
                yield current
            for filename in sorted(filenames):
                candidate = current / filename
                if self._link_escapes(candidate) or is_secret_path(candidate):
                    continue
                yield candidate

    async def execute(
        self,
        path: str = ".",
        query: str | None = None,
        glob: str | None = None,
        type: str | None = None,
        include_dirs: bool = False,
        sort: str = "path",
        head_limit: int | None = None,
        offset: int = 0,
        **kwargs: Any,
    ) -> str:
        try:
            target = self._resolve(path or ".")
            if not target.exists():
                return f"Error: Path not found: {path}"
            if not (target.is_dir() or target.is_file()):
                return f"Error: Unsupported path: {path}"

            if sort not in {"path", "modified"}:
                return "Error: sort must be 'path' or 'modified'"

            limit = (
                _DEFAULT_FILE_HEAD_LIMIT
                if head_limit is None
                else None if head_limit == 0 else head_limit
            )
            root = target if target.is_dir() else target.parent
            matches: list[tuple[str, float]] = []

            for candidate in self._iter_paths(target, include_dirs=include_dirs):
                if candidate.is_dir() and not include_dirs:
                    continue
                rel_path = candidate.relative_to(root).as_posix()
                display_path = self._display_path(candidate, root)
                name = candidate.name

                if glob and not _match_glob(rel_path, name, glob):
                    continue
                if candidate.is_file() and not _matches_type(name, type):
                    continue
                if candidate.is_dir() and type:
                    continue
                if not _matches_query(self._query_path(candidate, display_path, rel_path), query):
                    continue
                try:
                    mtime = candidate.stat().st_mtime
                except OSError:
                    mtime = 0.0
                suffix = "/" if candidate.is_dir() else ""
                matches.append((display_path + suffix, mtime))

            if sort == "modified":
                matches.sort(key=lambda item: (-item[1], item[0]))
            else:
                matches.sort(key=lambda item: item[0])

            paths = [item[0] for item in matches]
            paged, truncated = _paginate(paths, limit, offset)
            if not paged:
                return "No files found"

            result = "\n".join(paged)
            note = _pagination_note(limit, offset, truncated)
            if note:
                result += "\n\n" + note
            return result
        except PermissionError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error finding files: {e}"


class GrepTool(_SearchTool):
    """Search file contents using a regex-like pattern.

    Presente anche nello scope ``orchestrator``, ma li solo come *indice*: vedi
    :attr:`_index_only`.
    """
    _scopes = {"core", "subagent", "orchestrator"}

    _MAX_RESULT_CHARS = 128_000
    _MAX_FILE_BYTES = 2_000_000
    # Sopra questa soglia la lettura esce dal loop; sotto, il salto di thread
    # costerebbe più della lettura, e un grep attraversa migliaia di file.
    _OFF_LOOP_BYTES = 256 * 1024
    # Tetto sui risultati in modalita indice. Un elenco di percorsi e piccolo,
    # ma "piccolo per risultato" moltiplicato per un pattern sfortunato non lo e
    # piu, e nella conversazione dell'orchestratore ci resta per sempre.
    _INDEX_HEAD_LIMIT = 60

    def __init__(self, *args: Any, index_only: bool = False, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # Modalita indice: si puo sapere *dove* guardare, non leggere qui il
        # contenuto trovato. L'orchestratore senza ricerca finiva a sfogliare i
        # file a fette di duecento righe — venti turni per un grep — ma dargli
        # anche ``content`` rimetterebbe nella conversazione permanente proprio
        # l'output grosso che la modalita orchestratore esiste per evitare.
        # Trovato il file, si legge con ``read_file`` o si delega.
        self._index_only = index_only

    @classmethod
    def create(cls, ctx: Any) -> Any:
        tool = super().create(ctx)
        tool._index_only = bool(getattr(ctx, "orchestrator", False))
        return tool

    @property
    def name(self) -> str:
        return "grep"

    @property
    def description(self) -> str:
        if self._index_only:
            return (
                "Locate files whose contents match a regex pattern. "
                "Returns matching file paths (or per-file match counts) — never the "
                "matching lines themselves. Use it to find *where* something is, then "
                "read_file that path, or delegate the reading to a subagent. "
                "Skips binary and files >2 MB. Supports glob/type filtering."
            )
        return (
            "Search file contents with a regex pattern. "
            "Default output_mode is files_with_matches (file paths only); "
            "use content mode for matching lines with context. Prefer this "
            "use for workspace content searches."
            "Skips binary and files >2 MB. Supports glob/type filtering."
        )

    @property
    def read_only(self) -> bool:
        return True

    @property
    def parameters(self) -> dict[str, Any]:
        schema = self._full_parameters()
        if not self._index_only:
            return schema
        # Lo schema stesso dice cosa non c'e: un enum senza ``content`` e un
        # rifiuto che il modello legge prima di sbatterci, non dopo.
        properties = schema["properties"]
        for gone in ("context_before", "context_after", "max_matches"):
            properties.pop(gone, None)
        properties["output_mode"] = {
            "type": "string",
            "enum": ["files_with_matches", "count"],
            "description": (
                "files_with_matches: only matching file paths; "
                "count: matching line counts per file. Default: files_with_matches. "
                "Matching lines are not available here — read_file the path instead."
            ),
        }
        properties["head_limit"] = {
            **properties["head_limit"],
            "maximum": self._INDEX_HEAD_LIMIT,
            "description": (
                f"Maximum number of file entries to return (default and cap "
                f"{self._INDEX_HEAD_LIMIT}). Narrow the pattern or add a glob "
                "instead of raising it."
            ),
        }
        return schema

    def _full_parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Regex or plain text pattern to search for",
                    "minLength": 1,
                },
                "path": {
                    "type": "string",
                    "description": "File or directory to search in (default '.')",
                },
                "glob": {
                    "type": "string",
                    "description": "Optional file filter, e.g. '*.py' or 'tests/**/test_*.py'",
                },
                "type": {
                    "type": "string",
                    "description": "Optional file type shorthand, e.g. 'py', 'ts', 'md', 'json'",
                },
                "case_insensitive": {
                    "type": "boolean",
                    "description": "Case-insensitive search (default false)",
                },
                "fixed_strings": {
                    "type": "boolean",
                    "description": "Treat pattern as plain text instead of regex (default false)",
                },
                "output_mode": {
                    "type": "string",
                    "enum": ["content", "files_with_matches", "count"],
                    "description": (
                        "content: matching lines with optional context; "
                        "files_with_matches: only matching file paths; "
                        "count: matching line counts per file. "
                        "Default: files_with_matches"
                    ),
                },
                "context_before": {
                    "type": "integer",
                    "description": "Number of lines of context before each match",
                    "minimum": 0,
                    "maximum": 20,
                },
                "context_after": {
                    "type": "integer",
                    "description": "Number of lines of context after each match",
                    "minimum": 0,
                    "maximum": 20,
                },
                "max_matches": {
                    "type": "integer",
                    "description": (
                        "Legacy alias for head_limit in content mode"
                    ),
                    "minimum": 1,
                    "maximum": 1000,
                },
                "max_results": {
                    "type": "integer",
                    "description": (
                        "Legacy alias for head_limit in files_with_matches or count mode"
                    ),
                    "minimum": 1,
                    "maximum": 1000,
                },
                "head_limit": {
                    "type": "integer",
                    "description": (
                        "Maximum number of results to return. In content mode this limits "
                        "matching line blocks; in other modes it limits file entries. "
                        "Default 250"
                    ),
                    "minimum": 0,
                    "maximum": 1000,
                },
                "offset": {
                    "type": "integer",
                    "description": "Skip the first N results before applying head_limit",
                    "minimum": 0,
                    "maximum": 100000,
                },
            },
            "required": ["pattern"],
        }

    @staticmethod
    def _format_block(
        display_path: str,
        lines: list[str],
        match_line: int,
        before: int,
        after: int,
    ) -> str:
        start = max(1, match_line - before)
        end = min(len(lines), match_line + after)
        block = [f"{display_path}:{match_line}"]
        for line_no in range(start, end + 1):
            marker = ">" if line_no == match_line else " "
            block.append(f"{marker} {line_no}| {lines[line_no - 1]}")
        return "\n".join(block)

    async def execute(
        self,
        pattern: str,
        path: str = ".",
        glob: str | None = None,
        type: str | None = None,
        case_insensitive: bool = False,
        fixed_strings: bool = False,
        output_mode: str = "files_with_matches",
        context_before: int = 0,
        context_after: int = 0,
        max_matches: int | None = None,
        max_results: int | None = None,
        head_limit: int | None = None,
        offset: int = 0,
        **kwargs: Any,
    ) -> str:
        clamp_note = ""
        if self._index_only:
            # Lo schema gia esclude ``content``, ma un modello puo chiederlo
            # comunque: una skill glielo insegna, un esempio vecchio glielo
            # mostra. Restituire i percorsi con una riga di spiegazione costa un
            # turno in meno di un errore, e gli insegna la mossa giusta invece
            # di limitarsi a vietargli quella sbagliata.
            if output_mode == "content":
                output_mode = "files_with_matches"
                clamp_note = (
                    "Note: matching lines are not available here — these are the files "
                    "that match. Read one with read_file, or delegate to a subagent."
                )
            context_before = context_after = 0
            requested = head_limit if head_limit is not None else max_results
            head_limit = (
                self._INDEX_HEAD_LIMIT
                if requested in (None, 0)
                else min(requested, self._INDEX_HEAD_LIMIT)
            )

        try:
            target = self._resolve(path or ".")
            if not target.exists():
                return f"Error: Path not found: {path}"
            if not (target.is_dir() or target.is_file()):
                return f"Error: Unsupported path: {path}"

            flags = re.IGNORECASE if case_insensitive else 0
            try:
                needle = re.escape(pattern) if fixed_strings else pattern
                regex = re.compile(needle, flags)
            except re.error as e:
                return f"Error: invalid regex pattern: {e}"

            if head_limit is not None:
                limit = None if head_limit == 0 else head_limit
            elif output_mode == "content" and max_matches is not None:
                limit = max_matches
            elif output_mode != "content" and max_results is not None:
                limit = max_results
            else:
                limit = _DEFAULT_HEAD_LIMIT
            blocks: list[str] = []
            result_chars = 0
            seen_content_matches = 0
            truncated = False
            size_truncated = False
            skipped_binary = 0
            skipped_large = 0
            matching_files: list[str] = []
            counts: dict[str, int] = {}
            file_mtimes: dict[str, float] = {}
            root = target if target.is_dir() else target.parent

            for file_path in self._iter_files(target):
                rel_path = file_path.relative_to(root).as_posix()
                if glob and not _match_glob(rel_path, file_path.name, glob):
                    continue
                if not _matches_type(file_path.name, type):
                    continue

                # Il tetto si controlla con `stat`, PRIMA di leggere: prima
                # il file si leggeva per intero e solo dopo si guardava la
                # lunghezza, cioè 600 MB di RSS per saltare un video.
                try:
                    st = file_path.stat()
                except OSError:
                    skipped_binary += 1
                    continue
                if st.st_size > self._MAX_FILE_BYTES:
                    skipped_large += 1
                    continue
                if st.st_size > self._OFF_LOOP_BYTES:
                    raw = await asyncio.to_thread(file_path.read_bytes)
                else:
                    raw = file_path.read_bytes()
                if _is_binary(raw):
                    skipped_binary += 1
                    continue
                mtime = st.st_mtime
                try:
                    content = raw.decode("utf-8")
                except UnicodeDecodeError:
                    skipped_binary += 1
                    continue

                lines = content.splitlines()
                display_path = self._display_path(file_path, root)
                file_had_match = False
                for idx, line in enumerate(lines, start=1):
                    if not regex.search(line):
                        continue
                    file_had_match = True

                    if output_mode == "count":
                        counts[display_path] = counts.get(display_path, 0) + 1
                        continue
                    if output_mode == "files_with_matches":
                        if display_path not in matching_files:
                            matching_files.append(display_path)
                            file_mtimes[display_path] = mtime
                        break

                    seen_content_matches += 1
                    if seen_content_matches <= offset:
                        continue
                    if limit is not None and len(blocks) >= limit:
                        truncated = True
                        break
                    block = self._format_block(
                        display_path,
                        lines,
                        idx,
                        context_before,
                        context_after,
                    )
                    extra_sep = 2 if blocks else 0
                    if result_chars + extra_sep + len(block) > self._MAX_RESULT_CHARS:
                        size_truncated = True
                        break
                    blocks.append(block)
                    result_chars += extra_sep + len(block)
                    # Righe di questo file entrano nel contesto: dentro un
                    # subagent valgono come una lettura (marcano il lavoro). Solo
                    # qui, non sull'elenco dei nomi: un ``grep`` su tutto il
                    # workspace non deve contaminare per i file che non mostra.
                    check_subagent_read(file_path)
                if output_mode == "count" and file_had_match:
                    if display_path not in matching_files:
                        matching_files.append(display_path)
                        file_mtimes[display_path] = mtime
                if output_mode in {"count", "files_with_matches"} and file_had_match:
                    continue
                if truncated or size_truncated:
                    break

            if output_mode == "files_with_matches":
                if not matching_files:
                    result = f"No matches found for pattern '{pattern}' in {path}"
                else:
                    ordered_files = sorted(
                        matching_files,
                        key=lambda name: (-file_mtimes.get(name, 0.0), name),
                    )
                    paged, truncated = _paginate(ordered_files, limit, offset)
                    result = "\n".join(paged)
            elif output_mode == "count":
                if not counts:
                    result = f"No matches found for pattern '{pattern}' in {path}"
                else:
                    ordered_files = sorted(
                        matching_files,
                        key=lambda name: (-file_mtimes.get(name, 0.0), name),
                    )
                    ordered, truncated = _paginate(ordered_files, limit, offset)
                    lines = [f"{name}: {counts[name]}" for name in ordered]
                    result = "\n".join(lines)
            else:
                if not blocks:
                    result = f"No matches found for pattern '{pattern}' in {path}"
                else:
                    result = "\n\n".join(blocks)

            notes: list[str] = []
            if clamp_note:
                notes.append(clamp_note)
            if output_mode == "content" and truncated:
                notes.append(
                    f"(pagination: limit={limit}, offset={offset})"
                )
            elif output_mode == "content" and size_truncated:
                notes.append("(output truncated due to size)")
            elif truncated and output_mode in {"count", "files_with_matches"}:
                notes.append(
                    f"(pagination: limit={limit}, offset={offset})"
                )
            elif output_mode in {"count", "files_with_matches"} and offset > 0:
                notes.append(f"(pagination: offset={offset})")
            elif output_mode == "content" and offset > 0 and blocks:
                notes.append(f"(pagination: offset={offset})")
            if skipped_binary:
                notes.append(f"(skipped {skipped_binary} binary/unreadable files)")
            if skipped_large:
                notes.append(f"(skipped {skipped_large} large files)")
            if output_mode == "count" and counts:
                notes.append(
                    f"(total matches: {sum(counts.values())} in {len(counts)} files)"
                )
            if notes:
                result += "\n\n" + "\n".join(notes)
            return result
        except PermissionError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error searching files: {e}"


# Registrazione esplicita dei tool di questo modulo (Fase 5.3): il
# ToolLoader legge questa lista invece della reflection dir(). Un nuovo
# tool va aggiunto qui esplicitamente.
TOOLS = [FindFilesTool, GrepTool]
