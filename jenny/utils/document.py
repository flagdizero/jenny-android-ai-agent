"""Document text extraction utilities for jenny."""

import mimetypes
from pathlib import Path

from loguru import logger

from jenny.utils.helpers import detect_image_mime

_MAX_TEXT_LENGTH = 200_000


def extract_text(path: Path) -> str | None:
    """Extract text from a file.

    Args:
        path: Path to the file.

    Returns:
        Extracted text as string, None for unsupported types,
        or error string for failures.
    """
    if not isinstance(path, Path):
        path = Path(path)

    if not path.exists():
        return f"[error: file not found: {path}]"

    ext = path.suffix.lower()

    if ext == ".pdf":
        return _extract_pdf(path)
    elif _is_text_extension(ext):
        return _extract_text_file(path)
    elif ext in {".png", ".jpg", ".jpeg", ".gif", ".webp"}:
        return f"[image: {path.name}]"
    else:
        return None


def _extract_pdf(path: Path) -> str:
    """Extract text from PDF using pypdf."""
    try:
        from pypdf import PdfReader
    except ImportError:
        return "[error: pypdf not installed]"
    try:
        reader = PdfReader(path)
        pages: list[str] = []
        for i, page in enumerate(reader.pages, 1):
            text = page.extract_text() or ""
            pages.append(f"--- Page {i} ---\n{text}")
        return _truncate("\n\n".join(pages), _MAX_TEXT_LENGTH)
    except Exception as e:
        logger.exception("Failed to extract PDF {}", path)
        return f"[error: failed to extract PDF: {e!s}]"


def _extract_text_file(path: Path) -> str:
    """Extract text from a plain text file."""
    try:
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            content = path.read_text(encoding="latin-1")
        return _truncate(content, _MAX_TEXT_LENGTH)
    except Exception as e:
        logger.exception("Failed to read text file {}", path)
        return f"[error: failed to read file: {e!s}]"


def _truncate(text: str, max_length: int) -> str:
    """Truncate text with a suffix indicating truncation."""
    if len(text) <= max_length:
        return text
    return text[:max_length] + f"... (truncated, {len(text)} chars total)"


def _is_text_extension(ext: str) -> bool:
    """Check if extension is a text format."""
    return ext in {
        ".txt",
        ".md",
        ".csv",
        ".json",
        ".xml",
        ".html",
        ".htm",
        ".log",
        ".yaml",
        ".yml",
        ".toml",
        ".ini",
        ".cfg",
    }


# ---------------------------------------------------------------------------
# High-level helper: split media into images + extracted document text
# ---------------------------------------------------------------------------

_MAX_EXTRACT_FILE_SIZE = 50 * 1024 * 1024  # 50 MB


def is_image_file(path: str) -> bool:
    """Check whether *path* looks like an image file.

    Uses magic-byte detection (reads first 16 bytes) with a ``mimetypes``
    extension-based fallback.
    """
    p = Path(path)
    mime: str | None = None
    if p.is_file():
        try:
            with p.open("rb") as f:
                mime = detect_image_mime(f.read(16))
        except OSError:
            mime = None
    if not mime:
        mime = mimetypes.guess_type(path)[0]
    return bool(mime and mime.startswith("image/"))


# Soglia oltre la quale un documento NON viene estratto inline ma solo
# referenziato per path (letto on-demand dagli strumenti dell'agente). Tiene il
# costo di contesto sotto controllo: i file testuali/PDF piccoli entrano nel
# turno (così l'agente li considera senza un round-trip di tool), quelli grandi
# o binari restano riferimenti leggeri.
_MAX_INLINE_EXTRACT_SIZE = 512 * 1024  # 512 KB


def _maybe_extract_inline(path_str: str, max_inline_size: int) -> str | None:
    """Estrae il testo di un allegato testo/PDF piccolo, altrimenti ``None``.

    Ritorna ``None`` (→ referenziare per path) per file inesistenti, tipi non
    estraibili (binari, archivi, …), file oltre soglia o estrazione fallita.
    """
    p = Path(path_str)
    if not p.is_file():
        return None
    ext = p.suffix.lower()
    if ext != ".pdf" and not _is_text_extension(ext):
        return None
    try:
        if p.stat().st_size > max_inline_size:
            return None
    except OSError:
        return None
    text = extract_text(p)
    if not text or text.startswith("[error:"):
        return None
    return f"[File: {p.name}]\n{text}"


def prepare_attachments(
    content: str,
    media: list[str],
    *,
    max_inline_size: int = _MAX_INLINE_EXTRACT_SIZE,
) -> tuple[str, list[str]]:
    """Separa immagini dagli altri allegati, estraendo inline i documenti brevi.

    - Immagini: path preservati per i blocchi vision a valle.
    - Documenti testo/PDF entro ``max_inline_size``: testo estratto e inlineato
      come ``[File: <name>]\\n<text>`` così l'agente lo considera nel turno.
    - Tutto il resto (binari, file oltre soglia, estrazione fallita): riferito
      come ``[Attachment: <path>]``, da leggere on-demand con gli strumenti.

    Compromesso rispetto all'estrazione totale: i documenti utili entrano nel
    contesto senza inlinare ogni volta blob enormi.
    """
    image_paths: list[str] = []
    inline_docs: list[str] = []
    refs: list[str] = []
    for path_str in media:
        if is_image_file(path_str):
            image_paths.append(path_str)
            continue
        extracted = _maybe_extract_inline(path_str, max_inline_size)
        if extracted is not None:
            inline_docs.append(extracted)
        else:
            refs.append(f"[Attachment: {path_str}]")

    suffix_blocks: list[str] = []
    if refs:
        suffix_blocks.append("\n".join(refs))
    if inline_docs:
        suffix_blocks.append("\n\n".join(inline_docs))
    if suffix_blocks:
        suffix = "\n\n".join(suffix_blocks)
        content = f"{content}\n\n{suffix}" if content else suffix
    return content, image_paths


def extract_documents(
    text: str,
    media_paths: list[str],
    *,
    max_file_size: int = _MAX_EXTRACT_FILE_SIZE,
) -> tuple[str, list[str]]:
    """Separate images from documents in *media_paths*.

    Documents (PDF, plain-text, …) have their text extracted and appended to
    *text*. Only image paths are kept in the returned list so downstream
    layers only need to handle vision blocks.

    Files larger than *max_file_size* bytes are skipped with a warning to
    avoid unbounded memory / CPU usage.
    """
    image_paths: list[str] = []
    doc_texts: list[str] = []
    refs: list[str] = []

    for path_str in media_paths:
        p = Path(path_str)
        if not p.is_file():
            continue

        try:
            size = p.stat().st_size
        except OSError:
            continue
        if size > max_file_size:
            logger.warning(
                "Referencing oversized file instead of extracting: {} ({:.1f} MB > {} MB limit)",
                p.name, size / (1024 * 1024), max_file_size // (1024 * 1024),
            )
            refs.append(f"[Attachment: {path_str}]")
            continue

        if is_image_file(path_str):
            image_paths.append(path_str)
        else:
            extracted = extract_text(p)
            if extracted and not extracted.startswith("[error:"):
                doc_texts.append(f"[File: {p.name}]\n{extracted}")
            else:
                # Binari/archivi/estrazione fallita: mai scartare in silenzio —
                # l'agente deve sapere che l'allegato esiste (riferimento path,
                # lettura on-demand), come fa prepare_attachments.
                refs.append(f"[Attachment: {path_str}]")

    suffix_blocks: list[str] = []
    if refs:
        suffix_blocks.append("\n".join(refs))
    if doc_texts:
        suffix_blocks.append("\n\n".join(doc_texts))
    if suffix_blocks:
        suffix = "\n\n".join(suffix_blocks)
        text = f"{text}\n\n{suffix}" if text else suffix

    return text, image_paths
