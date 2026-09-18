"""Allegati in ingresso da Telegram: quale file si sceglie, e cosa ne esce.

Due piani, tenuti separati di proposito. ``pick_file`` è una funzione pura su un
dizionario e si prova senza rete e senza canale; l'ingest è il canale che
scarica, salva in ``uploads/`` e pubblica (o risponde e tace). Il secondo piano
non ripete le scelte del primo: verifica che il file finisca dove deve e che
ogni uscita di errore parli.
"""

from __future__ import annotations

import asyncio
import pathlib
import re
import tempfile
from typing import Any

import pytest

from jenny.bus.queue import MessageBus
from jenny.channels.telegram import TelegramChannel
from jenny.channels.telegram_api import TelegramAPIError
from jenny.channels.telegram_media import (
    FILE_MAX_BYTES,
    IMAGE_MAX_BYTES,
    has_unsupported_attachment,
    pick_file,
)
from jenny.config.paths import get_uploads_dir, set_workspace_dir
from jenny.config.schema import TelegramConfig
from jenny.runtime.context import get_runtime_context
from jenny.utils.media_decode import FileSizeExceeded

# PNG minimo: magic byte veri, così ``is_image_file`` lo riconosce anche senza
# fidarsi dell'estensione.
PNG = bytes.fromhex("89504e470d0a1a0a") + b"\x00" * 32


# --- pick_file: la scelta ------------------------------------------------------


def test_photo_takes_the_largest_size() -> None:
    # Telegram ordina le taglie dalla più piccola: la prima è la miniatura da
    # ~90px. Prenderla non darebbe nessun errore, solo un francobollo.
    picked = pick_file({
        "photo": [
            {"file_id": "thumb", "file_size": 900},
            {"file_id": "medium", "file_size": 9_000},
            {"file_id": "full", "file_size": 90_000},
        ]
    })
    assert picked is not None
    assert picked.file_id == "full"
    assert picked.kind == "image"
    assert picked.size == 90_000


def test_document_keeps_its_filename() -> None:
    picked = pick_file({
        "document": {
            "file_id": "d1",
            "file_name": "relazione 2026.pdf",
            "mime_type": "application/pdf",
        }
    })
    assert picked is not None
    assert picked.filename == "relazione 2026.pdf"
    assert picked.kind == "document"


def test_document_without_filename_still_has_one() -> None:
    picked = pick_file({"document": {"file_id": "d1", "mime_type": "application/zip"}})
    assert picked is not None
    assert picked.filename == "document"


def test_image_sent_as_document_is_still_an_image() -> None:
    # È il modo in cui si manda una foto non compressa: trattarla da documento
    # le toglierebbe la vista, che è l'unica ragione per mandarla così.
    picked = pick_file({
        "document": {"file_id": "d1", "file_name": "scan.png", "mime_type": "image/png"}
    })
    assert picked is not None
    assert picked.kind == "image"
    assert picked.max_bytes == IMAGE_MAX_BYTES


def test_voice_is_audio_with_a_name() -> None:
    picked = pick_file({"voice": {"file_id": "v1", "duration": 4}})
    assert picked is not None
    assert (picked.kind, picked.filename) == ("audio", "voice.ogg")
    assert picked.max_bytes == FILE_MAX_BYTES


def test_static_sticker_is_an_image() -> None:
    picked = pick_file({"sticker": {"file_id": "s1"}})
    assert picked is not None
    assert (picked.kind, picked.filename) == ("image", "sticker.webp")


def test_animated_sticker_is_not_a_file_but_is_flagged() -> None:
    # Un lottie/webm non è un'immagine che il modello possa guardare. Scartarlo
    # senza segnalarlo lo renderebbe indistinguibile da un messaggio vuoto, e
    # il bot tacerebbe.
    message = {"sticker": {"file_id": "s1", "is_animated": True}}
    assert pick_file(message) is None
    assert has_unsupported_attachment(message) is True


def test_plain_text_has_no_attachment() -> None:
    assert pick_file({"text": "ciao"}) is None
    assert has_unsupported_attachment({"text": "ciao"}) is False


@pytest.mark.parametrize("key", ["contact", "poll", "dice"])
def test_unsupported_kinds_are_flagged(key: str) -> None:
    assert pick_file({key: {}}) is None
    assert has_unsupported_attachment({key: {}}) is True


# --- l'ingest: il canale -------------------------------------------------------


class FakeAPI:
    """Doppio della Bot API con la metà download, che gli altri file non hanno."""

    def __init__(
        self,
        *,
        payload: bytes = PNG,
        file_size: int | None = None,
        get_file_error: Exception | None = None,
        download_error: Exception | None = None,
        no_file_path: bool = False,
    ) -> None:
        self.sent: list[str] = []
        self.actions: list[str] = []
        self.get_file_calls: list[str] = []
        self.downloads: list[str] = []
        self._payload = payload
        self._file_size = file_size if file_size is not None else len(payload)
        self._get_file_error = get_file_error
        self._download_error = download_error
        self._no_file_path = no_file_path

    async def send_message(self, chat_id: str, text: str, *, parse_mode: str | None = None):
        self.sent.append(text)
        return {"message_id": len(self.sent)}

    async def send_chat_action(self, chat_id: str, action: str = "typing"):
        self.actions.append(action)

    async def get_file(self, file_id: str) -> dict[str, Any]:
        self.get_file_calls.append(file_id)
        if self._get_file_error is not None:
            raise self._get_file_error
        if self._no_file_path:
            return {"file_size": self._file_size}
        return {"file_path": f"photos/{file_id}.jpg", "file_size": self._file_size}

    async def download_file(self, file_path: str, *, max_bytes: int) -> bytes:
        self.downloads.append(file_path)
        if self._download_error is not None:
            raise self._download_error
        return self._payload

    async def get_updates(self, offset, timeout_s):  # pragma: no cover - non usato
        return []

    async def close(self) -> None:
        pass


@pytest.fixture(autouse=True)
def _workspace():
    """``uploads/`` vive nel workspace: senza questo i test scriverebbero nel tuo."""
    previous = get_runtime_context().workspace_dir
    set_workspace_dir(pathlib.Path(tempfile.mkdtemp()))
    yield
    set_workspace_dir(previous if previous is not None else "")


def _channel(api: FakeAPI) -> tuple[TelegramChannel, MessageBus]:
    config = TelegramConfig(enabled=True, bot_token="TOKEN", paired_chat_id="42")
    bus = MessageBus()
    return TelegramChannel(config, bus, api=api, language="en"), bus


def _update(**extra: Any) -> dict[str, Any]:
    return {
        "update_id": 1,
        "message": {"chat": {"id": "42"}, "from": {"id": "42"}, **extra},
    }


def _photo(file_size: int = len(PNG)) -> list[dict[str, Any]]:
    return [{"file_id": "thumb", "file_size": 90}, {"file_id": "full", "file_size": file_size}]


async def test_photo_with_caption_keeps_both() -> None:
    # Il difetto che questo blocca: prima il ramo del testo usciva per primo e
    # leggeva solo ``text``, quindi una foto con didascalia perdeva la foto —
    # o, peggio, la didascalia.
    api = FakeAPI()
    ch, bus = _channel(api)
    await ch._handle_update(_update(photo=_photo(), caption="guarda qui"))
    msg = await asyncio.wait_for(bus.consume_inbound(), timeout=1)
    await ch._typing.stop()

    assert msg.content == "guarda qui"
    assert len(msg.media) == 1
    assert api.downloads == ["photos/full.jpg"]
    assert api.sent == []


async def test_saved_attachment_lands_in_uploads_with_the_shared_naming() -> None:
    api = FakeAPI()
    ch, bus = _channel(api)
    await ch._handle_update(_update(photo=_photo(), caption="x"))
    msg = await asyncio.wait_for(bus.consume_inbound(), timeout=1)
    await ch._typing.stop()

    saved = pathlib.Path(msg.media[0])
    assert saved.parent == get_uploads_dir()
    assert saved.read_bytes() == PNG
    # Stessa forma dei nomi degli allegati WebUI: prefisso uuid a 12 esadecimali,
    # nome originale sanitizzato, estensione dal MIME.
    assert re.fullmatch(r"[0-9a-f]{12}-photo\.jpg", saved.name)


async def test_photo_without_caption_is_the_image_alone() -> None:
    # Nessun marcatore per le immagini: la foto arriva come blocco vision, e
    # quel testo finirebbe nell'eco WebUI come se l'utente se lo fosse scritto
    # da solo. È la stessa forma di una foto allegata dalla WebUI senza scrivere
    # niente — il turno è fatto dall'immagine.
    api = FakeAPI()
    ch, bus = _channel(api)
    await ch._handle_update(_update(photo=_photo()))
    msg = await asyncio.wait_for(bus.consume_inbound(), timeout=1)
    await ch._typing.stop()

    assert msg.content == ""
    assert len(msg.media) == 1


async def test_voice_note_says_it_was_not_heard() -> None:
    # Senza questo marcatore Jenny risponde come se avesse ascoltato: il file
    # arriva come path e niente, nel turno, dice che nessuno l'ha trascritto.
    api = FakeAPI()
    ch, bus = _channel(api)
    await ch._handle_update(_update(voice={"file_id": "v1", "duration": 3}))
    msg = await asyncio.wait_for(bus.consume_inbound(), timeout=1)
    await ch._typing.stop()

    assert "cannot hear" in msg.content
    assert len(msg.media) == 1


async def test_voice_with_caption_keeps_the_warning_too() -> None:
    api = FakeAPI()
    ch, bus = _channel(api)
    await ch._handle_update(
        _update(voice={"file_id": "v1"}, caption="senti qua")
    )
    msg = await asyncio.wait_for(bus.consume_inbound(), timeout=1)
    await ch._typing.stop()

    assert msg.content.startswith("senti qua")
    assert "cannot hear" in msg.content


async def test_declared_size_over_cap_costs_nothing() -> None:
    # ``file_size`` arriva già nell'update: il rifiuto non deve spendere né una
    # chiamata a getFile né un byte di banda.
    api = FakeAPI()
    ch, bus = _channel(api)
    await ch._handle_update(_update(photo=_photo(file_size=IMAGE_MAX_BYTES + 1)))
    await ch._typing.stop()

    assert api.get_file_calls == []
    assert api.downloads == []
    assert len(api.sent) == 1
    assert "too big" in api.sent[0]
    assert bus.inbound.empty()


async def test_get_file_revealing_a_bigger_size_stops_before_the_download() -> None:
    api = FakeAPI(file_size=IMAGE_MAX_BYTES + 1)
    ch, bus = _channel(api)
    await ch._handle_update(_update(photo=[{"file_id": "full"}]))
    await ch._typing.stop()

    assert api.get_file_calls == ["full"]
    assert api.downloads == []
    assert "too big" in api.sent[0]
    assert bus.inbound.empty()


async def test_cap_hit_mid_download_is_a_service_reply() -> None:
    api = FakeAPI(download_error=FileSizeExceeded("too big"))
    ch, bus = _channel(api)
    await ch._handle_update(_update(photo=[{"file_id": "full"}]))
    await ch._typing.stop()

    assert "too big" in api.sent[0]
    assert bus.inbound.empty()


async def test_get_file_failure_is_never_silent() -> None:
    # Lo scarto qui è definitivo: l'offset è già avanzato, quindi Telegram non
    # ripropone l'update. Un silenzio sarebbe indistinguibile da un bot morto.
    api = FakeAPI(get_file_error=TelegramAPIError(400, "file is too big"))
    ch, bus = _channel(api)
    await ch._handle_update(_update(document={"file_id": "d1", "file_name": "x.pdf"}))
    await ch._typing.stop()

    assert len(api.sent) == 1
    assert "couldn't download" in api.sent[0]
    assert bus.inbound.empty()


async def test_get_file_without_a_path_is_a_failure_not_a_crash() -> None:
    api = FakeAPI(no_file_path=True)
    ch, bus = _channel(api)
    await ch._handle_update(_update(photo=[{"file_id": "full"}]))
    await ch._typing.stop()

    assert "couldn't download" in api.sent[0]
    assert api.downloads == []
    assert bus.inbound.empty()


async def test_a_failed_attachment_does_not_become_a_caption_only_turn() -> None:
    # Un turno che parla di una foto che non è arrivata è peggio di nessun turno.
    api = FakeAPI(get_file_error=TelegramAPIError(500, "boom"))
    ch, bus = _channel(api)
    await ch._handle_update(_update(photo=[{"file_id": "full"}], caption="guarda"))
    await ch._typing.stop()

    assert bus.inbound.empty()


async def test_upload_action_matches_what_is_being_fetched() -> None:
    api = FakeAPI()
    ch, bus = _channel(api)
    await ch._handle_update(_update(document={"file_id": "d1", "file_name": "x.pdf"}))
    await asyncio.wait_for(bus.consume_inbound(), timeout=1)
    await ch._typing.stop()

    # "sta scrivendo" durante un download sarebbe la cosa sbagliata detta bene.
    assert api.actions[0] == "upload_document"
