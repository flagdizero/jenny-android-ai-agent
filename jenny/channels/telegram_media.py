"""Scelta dell'allegato da un update Telegram, e i tetti che lo governano.

Modulo foglia di proposito: nessun import dal canale, nessuna rete, nessun
filesystem. Riceve il dizionario ``message`` così come arriva da ``getUpdates``
e dice *quale* file scaricare — o che non c'è niente da scaricare. Tutto il
resto (download, salvataggio, risposte di servizio) vive nel canale.

Una sola scelta per messaggio, ed è una scelta e non un compromesso: Telegram
manda un update **per ogni foto** anche quando l'utente ne spedisce cinque
insieme, quindi non esiste un "il messaggio ha tre allegati" da gestire. Un
album diventa cinque turni; aggregarli richiederebbe una finestra temporale su
``media_group_id`` e non è lavoro di questo modulo.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Tetto sul download di un'immagine destinata alla vista del modello.
#
# Protegge **l'ingresso**: banda, RAM del telefono e disco. Non è il tetto che
# protegge la *finestra di contesto*, che è una decisione diversa, vale su ogni
# porta da cui entra un'immagine (qui, gli allegati WebUI e `read_file`) e sta
# scritta in `roadmap/17-tetto-immagini-read-file.md`. Quando quel numero verrà
# scelto sarà più basso di questo, e questa costante dovrà seguirlo: è una riga.
#
# 5 MB perché una foto passata da Telegram è già ricompressa lato server e sta
# quasi sempre sotto il mega; il caso che consuma davvero questo tetto è la
# foto inviata **come documento**, cioè non compressa, ed è il caso in cui
# l'utente ha scelto apposta la qualità.
IMAGE_MAX_BYTES = 5 * 1024 * 1024

# Tetto sugli altri allegati. 20 MB non è una nostra preferenza: è il massimo
# che la Bot API lascia scaricare a un bot via ``getFile``. Alzarlo non
# servirebbe a niente, abbassarlo è una scelta che va motivata.
FILE_MAX_BYTES = 20 * 1024 * 1024


@dataclass(frozen=True)
class TelegramFile:
    """L'allegato scelto, già pronto per ``getFile``."""

    file_id: str
    filename: str
    kind: str  # "image" | "audio" | "video" | "document"
    mime: str = ""
    size: int | None = None

    @property
    def max_bytes(self) -> int:
        return IMAGE_MAX_BYTES if self.kind == "image" else FILE_MAX_BYTES


# Chiavi che portano un allegato ma che non sappiamo (ancora) trattare, e che
# quindi meritano una risposta di servizio invece del silenzio.
UNSUPPORTED_KEYS = ("contact", "poll", "dice", "game", "story")


def _as_dict(value: Any) -> dict[str, Any] | None:
    return value if isinstance(value, dict) else None


def _kind_for(mime: str, default: str) -> str:
    """Un documento è ciò che il suo MIME dice di essere, non la sua chiave.

    Una foto inviata *come file* arriva sotto ``document``: trattarla come un
    documento le toglierebbe la vista, che è l'unica ragione per cui qualcuno
    la manda in quel modo.
    """
    if mime.startswith("image/"):
        return "image"
    if mime.startswith("audio/"):
        return "audio"
    if mime.startswith("video/"):
        return "video"
    return default


def pick_file(message: dict[str, Any]) -> TelegramFile | None:
    """Ritorna l'allegato da scaricare, o ``None`` se non ce n'è uno gestibile."""
    photos = message.get("photo")
    if isinstance(photos, list) and photos:
        # Le taglie arrivano dalla più piccola alla più grande: la prima è la
        # miniatura da ~90px. Prendere quella darebbe al modello un francobollo
        # e nessun errore da nessuna parte.
        largest = photos[-1]
        if isinstance(largest, dict) and largest.get("file_id"):
            return TelegramFile(
                file_id=str(largest["file_id"]),
                filename="photo.jpg",
                kind="image",
                mime="image/jpeg",
                size=_int_or_none(largest.get("file_size")),
            )

    doc = _as_dict(message.get("document"))
    if doc and doc.get("file_id"):
        mime = str(doc.get("mime_type") or "")
        name = doc.get("file_name")
        return TelegramFile(
            file_id=str(doc["file_id"]),
            filename=str(name) if isinstance(name, str) and name.strip() else "document",
            kind=_kind_for(mime, "document"),
            mime=mime,
            size=_int_or_none(doc.get("file_size")),
        )

    sticker = _as_dict(message.get("sticker"))
    if sticker and sticker.get("file_id"):
        # Uno sticker animato è un lottie (.tgs) o un webm: non è un'immagine
        # che il modello possa guardare, e salvarlo come tale mentirebbe.
        if sticker.get("is_animated") or sticker.get("is_video"):
            return None
        return TelegramFile(
            file_id=str(sticker["file_id"]),
            filename="sticker.webp",
            kind="image",
            mime="image/webp",
            size=_int_or_none(sticker.get("file_size")),
        )

    for key, filename, kind, fallback_mime in (
        ("voice", "voice.ogg", "audio", "audio/ogg"),
        ("audio", "audio", "audio", ""),
        ("video", "video.mp4", "video", "video/mp4"),
        ("video_note", "video_note.mp4", "video", "video/mp4"),
        ("animation", "animation.mp4", "video", "video/mp4"),
    ):
        item = _as_dict(message.get(key))
        if not item or not item.get("file_id"):
            continue
        mime = str(item.get("mime_type") or fallback_mime)
        name = item.get("file_name")
        return TelegramFile(
            file_id=str(item["file_id"]),
            filename=str(name) if isinstance(name, str) and name.strip() else filename,
            kind=_kind_for(mime, kind),
            mime=mime,
            size=_int_or_none(item.get("file_size")),
        )

    return None


def has_unsupported_attachment(message: dict[str, Any]) -> bool:
    """Vero se il messaggio porta qualcosa che non sappiamo trattare.

    Include lo sticker animato, che ``pick_file`` scarta: senza questo, un
    lottie sarebbe indistinguibile da un messaggio vuoto e il bot tacerebbe.
    """
    if any(key in message for key in UNSUPPORTED_KEYS):
        return True
    sticker = _as_dict(message.get("sticker"))
    return bool(sticker and (sticker.get("is_animated") or sticker.get("is_video")))


def _int_or_none(value: Any) -> int | None:
    return value if isinstance(value, int) and value >= 0 else None
