"""`shared/media.js`: il tipo di un allegato, uno per casa e officina.

Il tipo lo dice il server (`entry.kind`). Il ripiego per un messaggio che non
lo porta e' la regola del server (`transcript_markdown.media_kind_from_name`):
la casa ne aveva una sua, che contava anche `bmp` e `m4v`.
"""

from __future__ import annotations

import re

from support.js_harness import ASSETS, function, requires_node, run_js

from jenny.webui.transcript_markdown import (
    INLINE_MARKDOWN_IMAGE_EXTS,
    INLINE_MARKDOWN_VIDEO_EXTS,
)

MEDIA_JS = (ASSETS / "shared" / "media.js").read_text(encoding="utf-8")


def _js_set(name: str) -> set[str]:
    m = re.search(rf"const {name} = new Set\(\[([^\]]*)\]\);", MEDIA_JS)
    assert m, name
    return {f".{x}" for x in re.findall(r"'([a-z0-9]+)'", m.group(1))}


def test_the_fallback_is_the_servers_rule() -> None:
    assert _js_set("IMAGE_EXTS") == INLINE_MARKDOWN_IMAGE_EXTS
    assert _js_set("VIDEO_EXTS") == INLINE_MARKDOWN_VIDEO_EXTS


def test_neither_shell_keeps_its_own() -> None:
    for name in ("home-chat.js", "mobile-chat.js"):
        src = (ASSETS / name).read_text(encoding="utf-8")
        assert "_mediaKindFromName" not in src and "function mediaKind" not in src, name
        assert "_openMediaFile(entry) {" not in src, name


@requires_node
def test_media_kind() -> None:
    run_js(
        "import assert from 'node:assert/strict';\n"
        + MEDIA_JS[MEDIA_JS.index("const IMAGE_EXTS"):MEDIA_JS.index("/** `'image'`")]
        + function(MEDIA_JS, "mediaKind")
        + """
assert.equal(mediaKind({ kind: 'video', name: 'a.png' }), 'video', 'il server vince');
assert.equal(mediaKind({ name: 'Foto.JPG' }), 'image');
assert.equal(mediaKind({ name: 'clip.webm' }), 'video');
assert.equal(mediaKind({ name: 'voice.ogg', url: '/x/a.png' }), 'file', 'il nome prima dell\\u2019URL');
assert.equal(mediaKind({ url: '/api/media/abc/p.png?sig=1' }), 'image', 'senza nome, il percorso');
assert.equal(mediaKind({ name: 'old.bmp' }), 'file');
assert.equal(mediaKind({}), 'file');
"""
    )
