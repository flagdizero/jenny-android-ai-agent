"""I tetti degli allegati: il telefono e il gateway devono contare uguale.

Il client ha dei tetti per non far partire un messaggio che il gateway
rifiuterebbe. Sono utili solo finché sono gli stessi, e per mesi non lo sono
stati: `image-handler.js` aveva **due** secchi — immagini e tutto-il-resto, 4 e
4 — mentre `ws_parsing.py` ne ha **tre**, e i video li accetta a **uno** per
messaggio.

Quindi due video passavano il telefono e li rifiutava il gateway a messaggio già
partito: in officina si leggeva `Errore: image_rejected`, in casa non succedeva
niente del tutto. Il commento nel client diceva «cap per-tipo allineati al
server», ed era la cosa che nessuno aveva mai verificato.

Questo test lo verifica. Non è teorico: è la misura che avrebbe trovato quel
difetto senza che servisse inciamparci.
"""

from __future__ import annotations

import re
from pathlib import Path

from support.js_harness import requires_node, run_js

ROOT = Path(__file__).resolve().parents[2]
HANDLER_JS = (ROOT / "jenny" / "templates" / "ui" / "assets" / "shared"
              / "image-handler.js").read_text(encoding="utf-8")
WS_PARSING = (ROOT / "jenny" / "channels" / "ws_parsing.py").read_text(encoding="utf-8")


def _server_int(name: str) -> int:
    m = re.search(rf"^{name} = (.+)$", WS_PARSING, re.M)
    assert m, f"{name} non trovato in ws_parsing.py"
    return int(eval(m.group(1), {"__builtins__": {}}))  # noqa: S307 — solo numeri e `*`


def _server_mimes(name: str) -> set[str]:
    m = re.search(rf"^{name}: frozenset\[str\] = frozenset\(\{{(.*?)\}}\)", WS_PARSING, re.M | re.S)
    assert m, f"{name} non trovato"
    return set(re.findall(r'"([^"]+)"', m.group(1)))


def _client_caps() -> dict[str, dict[str, int | str]]:
    """I tre secchi come li dichiara `image-handler.js`."""
    block = re.search(r"this\._caps = \{(.*?)\n    \};", HANDLER_JS, re.S)
    assert block, "il client non dichiara più i suoi tetti in `_caps`"
    caps: dict[str, dict[str, int | str]] = {}
    for kind, body in re.findall(r"(\w+): \{([^}]*)\}", block.group(1)):
        entry: dict[str, int | str] = {}
        num = re.search(r"max: (\d+)", body)
        assert num, kind
        entry["max"] = int(num.group(1))
        size = re.search(r"maxBytes: ([\d *]+)", body)
        assert size, kind
        entry["maxBytes"] = int(eval(size.group(1), {"__builtins__": {}}))  # noqa: S307
        over = re.search(r"over: '([a-z_]+)'", body)
        assert over, kind
        entry["over"] = over.group(1)
        caps[kind] = entry
    return caps


def _client_mimes(field: str) -> set[str]:
    m = re.search(rf"this\.{field} = \[(.*?)\];", HANDLER_JS, re.S)
    assert m, f"{field} non trovato"
    return set(re.findall(r"'([^']+)'", m.group(1)))


def test_the_client_knows_the_same_three_kinds() -> None:
    """Due secchi contro tre è il difetto originale, non un dettaglio."""
    assert set(_client_caps()) == {"image", "video", "file"}


def test_how_many_of_each_fit_in_one_message() -> None:
    caps = _client_caps()
    assert caps["image"]["max"] == _server_int("_MAX_IMAGES_PER_MESSAGE")
    assert caps["video"]["max"] == _server_int("_MAX_VIDEOS_PER_MESSAGE")
    assert caps["file"]["max"] == _server_int("_MAX_FILES_PER_MESSAGE")


def test_how_big_each_one_may_be() -> None:
    caps = _client_caps()
    assert caps["image"]["maxBytes"] == _server_int("_MAX_IMAGE_BYTES")
    assert caps["video"]["maxBytes"] == _server_int("_MAX_VIDEO_BYTES")
    assert caps["file"]["maxBytes"] == _server_int("_MAX_FILE_BYTES")


def test_the_two_sides_sort_a_file_into_the_same_bucket() -> None:
    """Contare uguale non basta se non si è d'accordo su *cosa* si conta."""
    assert _client_mimes("_imageTypes") == _server_mimes("_IMAGE_MIME_ALLOWED")
    assert _client_mimes("_videoTypes") == _server_mimes("_VIDEO_MIME_ALLOWED")


def test_the_client_refuses_with_the_server_s_own_words() -> None:
    """Il codice che il client usa per un rifiuto locale è quello del gateway.

    Così la stessa cosa detta un istante prima si legge identica, e non esiste
    un secondo vocabolario da tenere allineato a mano.
    """
    caps = _client_caps()
    assert caps["image"]["over"] == "too_many_images"
    assert caps["video"]["over"] == "too_many_videos"
    assert caps["file"]["over"] == "too_many_files"
    server_codes = set(re.findall(r'return \[\], "([a-z_]+)"',
                                  (ROOT / "jenny" / "channels" / "websocket.py")
                                  .read_text(encoding="utf-8")))
    for kind, entry in caps.items():
        assert entry["over"] in server_codes, (kind, entry["over"])


def test_the_name_fallback_is_for_images_only_on_both_sides() -> None:
    """Il ripiego sull'estensione esiste per le catture senza MIME.

    Il gateway ce l'ha **solo** per le immagini. Darlo anche ai video da questa
    parte rifarebbe nascere la divergenza al contrario: un `.mp4` senza MIME
    sarebbe un video qui e un file generico di là.
    """
    assert "_name_looks_like_image(" in WS_PARSING
    assert "_name_looks_like_video" not in WS_PARSING
    kind_of = re.search(r"_kindOf\(file\) \{(.*?)\n  \}", HANDLER_JS, re.S)
    assert kind_of, "_kindOf non trovato"
    assert "_looksLikeImageName" in kind_of.group(1)
    assert "_looksLikeVideoName" not in HANDLER_JS


def test_nothing_is_dropped_without_saying_so() -> None:
    """Il `continue` muto era la seconda metà del difetto: sceglievi cinque
    foto, ne comparivano quattro, e nessuno diceva quale mancasse."""
    handle = re.search(r"_handleFiles\(fileList\) \{(.*?)\n  \}", HANDLER_JS, re.S)
    assert handle, "_handleFiles non trovato"
    body = handle.group(1)
    # Ogni `continue` è preceduto da una spiegazione.
    for chunk in body.split("continue;")[:-1]:
        assert "this.onReject?.(" in chunk.rsplit("if (", 1)[-1], (
            "un allegato viene scartato in silenzio"
        )


def test_both_shells_listen_to_the_refusal() -> None:
    assets = ROOT / "jenny" / "templates" / "ui" / "assets"
    casa = (assets / "casa-app.js").read_text(encoding="utf-8")
    officina = (assets / "mobile-chat.js").read_text(encoding="utf-8")
    assert "onReject = (reason) =>" in casa, "la casa non ascolta i rifiuti locali"
    assert "imageHandler.onReject = (reason) =>" in officina, "l'officina non li ascolta"


def test_the_officina_hook_comes_after_the_handler_exists() -> None:
    """Un aggancio scritto prima del `new ImageHandler()` è un TypeError al
    caricamento: la chat non parte affatto, e `node --check` non lo vede."""
    officina = (ROOT / "jenny" / "templates" / "ui" / "assets"
                / "mobile-chat.js").read_text(encoding="utf-8")
    born = officina.index("this.imageHandler = new ImageHandler();")
    hooked = officina.index("this.imageHandler.onReject")
    assert born < hooked, "onReject viene agganciato prima che il gestore esista"


# ── E che i tetti mordano davvero ────────────────────────────────────────────

HANDLER_PATH = ROOT / "jenny" / "templates" / "ui" / "assets" / "shared" / "image-handler.js"

_HARNESS = """
import assert from 'node:assert/strict';

/* `FileReader` non esiste in node: qui il contenuto non conta, conta quale file
   entra e quale no. */
globalThis.FileReader = class {
  readAsDataURL(file) { this.result = 'data:' + (file.type || '') + ';base64,AA=='; this.onload(); }
};
const { ImageHandler } = await import('__HANDLER_URL__');

const file = (name, type, size = 10) => ({ name, type, size });

function makeHandler() {
  const h = new ImageHandler();
  h.refused = [];
  h.onReject = (reason) => h.refused.push(reason);
  return h;
}
const kinds = (h) => h._items.map((it) => it.kind);
"""


def _run_js(script: str) -> None:
    run_js(_HARNESS.replace("__HANDLER_URL__", HANDLER_PATH.as_uri()) + "\n" + script)


dynamic = requires_node


@dynamic
def test_the_second_video_does_not_get_on_board() -> None:
    """Il caso misurato sul telefono: due video passavano di qui e li rifiutava
    il gateway a messaggio già partito."""
    _run_js("""
      const h = makeHandler();
      await h._handleFiles([file('uno.mp4', 'video/mp4'), file('due.mp4', 'video/mp4')]);
      assert.deepEqual(kinds(h), ['video'], 'il secondo video è entrato lo stesso');
      assert.deepEqual(h.refused, ['too_many_videos'], 'ed è entrato in silenzio');
    """)


@dynamic
def test_a_video_no_longer_counts_as_a_generic_file() -> None:
    """Prima un `.mp4` finiva nel secchio dei file: quattro ce ne stavano."""
    _run_js("""
      const h = makeHandler();
      await h._handleFiles([file('a.mp4', 'video/mp4'), file('b.pdf', 'application/pdf')]);
      assert.deepEqual(kinds(h), ['video', 'file']);
      assert.deepEqual(h.refused, []);
    """)


@dynamic
def test_the_buckets_do_not_steal_from_each_other() -> None:
    """Quattro immagini, un video e quattro file stanno tutti insieme."""
    _run_js("""
      const h = makeHandler();
      await h._handleFiles([
        file('1.png', 'image/png'), file('2.png', 'image/png'),
        file('3.png', 'image/png'), file('4.png', 'image/png'),
        file('v.mp4', 'video/mp4'),
        file('a.pdf', 'application/pdf'), file('b.pdf', 'application/pdf'),
        file('c.pdf', 'application/pdf'), file('d.pdf', 'application/pdf'),
      ]);
      assert.equal(h.count, 9);
      assert.deepEqual(h.refused, []);
    """)


@dynamic
def test_a_camera_capture_without_a_mime_is_still_a_photo() -> None:
    """Il ripiego sul nome: senza, uno scatto Android finirebbe fra i file."""
    _run_js("""
      const h = makeHandler();
      await h._handleFiles([file('IMG_0001.jpg', ''), file('x.mp4', '')]);
      assert.deepEqual(kinds(h), ['image', 'file'],
                       'il video senza MIME deve restare un file, come lo vede il server');
    """)


@dynamic
def test_too_big_is_said_and_not_swallowed() -> None:
    _run_js("""
      const h = makeHandler();
      await h._handleFiles([file('enorme.png', 'image/png', 9 * 1024 * 1024)]);
      assert.equal(h.count, 0);
      assert.deepEqual(h.refused, ['size']);
    """)
