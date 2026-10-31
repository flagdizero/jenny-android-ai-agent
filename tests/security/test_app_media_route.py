"""Le immagini di un'app per il blocco Foto: solo loro, solo col segreto della WebUI.

``data/`` di un'app non si serve mai; da qui in poi si serve una sola cosa:
i file d'immagine diretti di ``data/media/``, alla casa, che ne fa un ``blob:``.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from support.gateway_http import AUTH_SECRET, make_handler, make_request

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32


def _workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    app = ws / "apps" / "diario"
    (app / "data" / "media").mkdir(parents=True, exist_ok=True)
    (app / "app.json").write_text(json.dumps({"name": "Diario", "description": "x"}))
    (app / "data" / "media" / "acero.png").write_bytes(PNG)
    (app / "data" / "media" / "note.txt").write_text("segreto")
    (app / "data" / "notes.jsonl").write_text('{"id": "x", "testo": "segreta"}\n')
    (app / "data" / "outside.png").write_bytes(PNG)
    return ws


async def _get(tmp_path: Path, path: str, token: str | None = AUTH_SECRET):
    handler = make_handler(tmp_path / "skills")
    ws = _workspace(tmp_path)
    with patch.object(handler, "_get_workspace_root", return_value=ws):
        return await handler.apps_routes.dispatch(make_request(path, token), path)


async def test_an_image_is_served_with_its_type(tmp_path: Path) -> None:
    response = await _get(tmp_path, "/api/webui/apps/diario/media/acero.png")
    assert response.status_code == 200 and response.body == PNG
    headers = dict(response.headers.raw_items())
    assert headers["Content-Type"] == "image/png"
    assert headers["X-Content-Type-Options"] == "nosniff"


async def test_only_with_the_webui_secret(tmp_path: Path) -> None:
    response = await _get(tmp_path, "/api/webui/apps/diario/media/acero.png", token=None)
    assert response.status_code == 401


async def test_nothing_else_of_data_is_reachable(tmp_path: Path) -> None:
    for path, status in [
        ("/api/webui/apps/diario/media/note.txt", 400),          # non e' un'immagine
        ("/api/webui/apps/diario/media/..%2Foutside.png", 400),  # niente cartelle
        ("/api/webui/apps/diario/media/..%2Fnotes.jsonl", 400),
        ("/api/webui/apps/diario/media/.hidden.png", 400),       # niente punti in testa
        ("/api/webui/apps/diario/media/missing.png", 404),
        ("/api/webui/apps/..%2F..%2Fsecret/media/acero.png", 400),
    ]:
        response = await _get(tmp_path, path)
        assert response.status_code == status, path


async def test_a_huge_file_is_refused(tmp_path: Path) -> None:
    from jenny.apps.slip_view import MAX_MEDIA_BYTES

    handler = make_handler(tmp_path / "skills")
    ws = _workspace(tmp_path)
    big = ws / "apps" / "diario" / "data" / "media" / "big.jpg"
    with big.open("wb") as fh:
        fh.truncate(MAX_MEDIA_BYTES + 1)
    path = "/api/webui/apps/diario/media/big.jpg"
    with patch.object(handler, "_get_workspace_root", return_value=ws):
        response = await handler.apps_routes.dispatch(make_request(path, AUTH_SECRET), path)
    assert response.status_code == 413


def test_the_photos_block_lists_the_newest_images(tmp_path: Path) -> None:
    import os

    from jenny.apps.manifest import load_app
    from jenny.apps.slip_view import slip_view

    ws = _workspace(tmp_path)
    app_dir = ws / "apps" / "diario"
    (app_dir / "app.json").write_text(json.dumps({
        "name": "Diario", "description": "x",
        "actions": [{"name": "add", "description": "Add", "kind": "storage", "op": "append",
                     "collection": "notes"}],
        "summary": {"collection": "notes", "blocks": [{"kind": "photos", "max": 2}]}}))
    media = app_dir / "data" / "media"
    for n, name in enumerate(["a.jpg", "b.webp", "c.png"]):
        (media / name).write_bytes(PNG)
        os.utime(media / name, (1_700_000_000 + n, 1_700_000_000 + n))
    os.utime(media / "acero.png", (1_600_000_000, 1_600_000_000))
    app = load_app(app_dir)
    assert not app.broken, app.error
    photos = slip_view(ws, app, {})["blocks"][0]
    assert photos["names"] == ["c.png", "b.webp"] and photos["more"] == 2
