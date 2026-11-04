"""``openFile``/``shareFile``/``saveToDownloads`` escono solo dal workspace.

I tre comandi nativi passano un file a un'altra app (viewer, share sheet) o lo
copiano nella cartella Download, visibile a tutte. Il recinto era l'intero
``filesDir`` e il FileProvider esponeva ``path="."``: dentro c'erano la chiave
privata SSH (``files/ssh/``, tenuta fuori dal workspace apposta), lo store degli
snapshot, lo staging dei backup, e nel workspace ``config.json`` con le chiavi
dei provider.

Ora il recinto è il workspace — dove stanno i file dell'esploratore e gli
allegati della chat (``uploads/``, ``.jenny/media/``, gli unici path che il JS
passa) — con ``config.json`` e i suoi compagni esclusi; e il FileProvider
espone solo ``workspace/`` e il temporaneo della fotocamera.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from support.kotlin_source import function_body, read_code, read_source

FILE_PATHS = (
    Path(__file__).resolve().parents[2] / "android/app/src/main/res/xml/file_paths.xml"
)


def _resolver() -> str:
    return function_body(read_source("MainActivity"), "resolveLocalFile")


def test_the_fence_is_the_workspace_not_files_dir() -> None:
    body = _resolver()
    assert 'File(filesDir, "workspace").canonicalFile' in body
    assert "filesDir.canonicalPath" not in body, "il recinto non è più tutto filesDir"
    code = function_body(read_code("MainActivity"), "resolveLocalFile")
    assert "canonical.path.startsWith(workspace.path + File.separator)" in code
    assert "raw.canonicalFile" in code, "il path canonico risolve anche i symlink"


def test_config_json_is_refused_inside_the_workspace() -> None:
    body = _resolver()
    assert "isWorkspaceSecret(canonical, workspace)" in body
    assert body.index("isWorkspaceSecret(") < body.index("return canonical")
    src = read_source("MainActivity")
    secret = src[src.index("private fun isWorkspaceSecret(") :].split("\n\n", 1)[0]
    assert "file.parentFile != workspace) return false" in secret
    assert 'name.startsWith("config.json")' in secret, "anche .bak e i temporanei"


def test_the_quarantined_copy_of_a_broken_config_is_refused_too() -> None:
    """Il loader mette da parte un ``config.json`` illeggibile come
    ``config.corrupt-<data>.json``, con le stesse chiavi: il nome non comincia
    con ``config.json``, e il recinto deve riconoscerlo lo stesso."""
    src = read_source("MainActivity")
    secret = src[src.index("private fun isWorkspaceSecret(") :].split("\n\n", 1)[0]
    prefixes = re.findall(r'name\.startsWith\("([^"]+)"\)', secret)
    # La quarantena della config è ``utils.path.quarantine``.
    path_module = (Path(__file__).resolve().parents[2] / "jenny/utils/path.py").read_text(
        encoding="utf-8"
    )
    assert '{path.stem}.corrupt-{stamp}{path.suffix}' in path_module, "il nome della quarantena e' cambiato"
    names = [
        "config.json", "config.json.bak", "config.json.0123abcd.tmp",
        "config.corrupt-20260927T010203Z.json", "config.json.corrupt-20260927T010203Z.bak",
    ]
    for name in names:
        assert any(name.startswith(p) for p in prefixes), (name, prefixes)
    for name in ("notes.md", "configurazione.md", "my-config.json"):
        assert not any(name.startswith(p) for p in prefixes), (name, prefixes)


@pytest.mark.parametrize("command", ["openFile", "shareFile", "saveToDownloads"])
def test_every_file_command_goes_through_the_fence(command: str) -> None:
    body = function_body(read_code("MainActivity"), command)
    assert "resolveLocalFile(path," in body
    assert "File(path)" not in body, "un File costruito a mano salta il recinto"


def test_the_file_provider_exposes_only_the_workspace_and_the_camera() -> None:
    if not FILE_PATHS.is_file():
        pytest.skip("sorgente Android non presente in questo checkout")
    roots = [(el.tag, el.get("path")) for el in ET.parse(FILE_PATHS).getroot()]
    assert sorted(roots) == [("cache-path", "camera/"), ("files-path", "workspace/")], roots


def test_nothing_else_asks_the_provider_for_a_file_outside_those_roots() -> None:
    """Due sole richieste di Uri: la fotocamera (cacheDir) e ``contentUriFor``,
    che riceve solo file già passati da ``resolveLocalFile``."""
    code = read_code("MainActivity")
    assert code.count("FileProvider.getUriForFile(") == 2
    for command in ("openFile", "shareFile"):
        body = function_body(code, command)
        assert body.index("resolveLocalFile(") < body.index("contentUriFor(")
