# Third-Party Notices

Jenny redistributes the third-party components listed below. Each keeps its own
license; the AGPL-3.0 that covers Jenny itself does not apply to them. Full
license texts live next to the files they cover, at the paths given.

## Bundled WebUI assets

Vendored under `jenny/templates/ui/assets/vendor/` and shipped inside the APK.
Nothing is fetched from a CDN at runtime — the WebUI has no outbound asset
loads, which is why these are vendored in the first place.

| Component | Version | License | License text |
|---|---|---|---|
| [DOMPurify](https://github.com/cure53/DOMPurify) | 3.2.4 | Apache-2.0 **or** MPL-2.0 | `vendor/dompurify@3/LICENSE` |
| [marked](https://github.com/markedjs/marked) | 15.0.7 | MIT | `vendor/marked@15.0.7/LICENSE` |
| [highlight.js](https://github.com/highlightjs/highlight.js) | 11.11.1 | BSD-3-Clause | `vendor/highlight.js@11.11.1/LICENSE` |
| [D3](https://github.com/d3/d3) | 7.9.0 | ISC | `vendor/d3@7/LICENSE` |
| [CodeMirror](https://github.com/codemirror/codemirror5) | 5.65.16 | MIT | `vendor/codemirror@5.65.16/LICENSE` |
| [Tabler Icons](https://github.com/tabler/tabler-icons) (webfont) | 3.19.0 | MIT | `vendor/@tabler/icons-webfont@3.19.0/LICENSE` |

## Bundled fonts

Self-hosted under `jenny/templates/ui/assets/vendor/fonts/`. All are licensed
under the [SIL Open Font License 1.1](https://openfontlicense.org); the full
OFL text and the per-font copyright notices are in `vendor/fonts/LICENSE.txt`.

- **UI fonts** — Inter, Fira Code.
- **Per-theme display fonts** (`theme-fonts.css`) — Tenor Sans, Orbitron,
  Shippori Mincho, Baloo 2, Bangers, Comic Neue, Fredoka, Marcellus.

## Bundled Python packages

Chaquopy installs the wheels pinned in `requirements-android.lock.txt` into the
APK at build time: `httpx`, `websockets`, `loguru`, `croniter`, `json-repair`,
`jinja2`, `filelock`, `markdown`, `pyyaml`, `pypdf`, `typing-extensions`,
`tzdata`. All are pure-Python and resolve from public PyPI — none is local or
patched. Each is governed by its own license, available in the installed
wheel's metadata and in its upstream repository.

The APK also embeds a CPython 3.11 runtime and the Chaquopy support layer, both
supplied by the [Chaquopy](https://chaquo.com/chaquopy/) Gradle plugin.
[Chaquopy is MIT-licensed](https://chaquo.com/chaquopy/license/) — free and
open source since 12.0.1, with no remaining license restrictions — and CPython
is under the Python Software Foundation License. Both are compatible with this
project's AGPL-3.0 grant.

## Bundled Android libraries — SSH

The SSH client is native rather than Python. Both libraries below are plain
Java, resolve from Maven Central and are shipped inside the APK.

| Component | Version | License |
|---|---|---|
| [JSch (mwiede fork)](https://github.com/mwiede/jsch) | 2.28.6 | BSD-3-Clause, plus the bundled JZlib (BSD) and jBCrypt (ISC) notices carried in the jar |
| [Bouncy Castle](https://www.bouncycastle.org/) (`bcprov-jdk18on`) | 1.85 | Bouncy Castle Licence (MIT-style) |

Bouncy Castle is not optional. Android's own `BC` provider is a reduced build
without Ed25519, and X25519 only reached Conscrypt in Android 14 while this app
supports API 26 — so without it neither the modern key exchange every current
server negotiates nor ed25519 keys would work at all.

Both licenses are permissive and compatible with this project's AGPL-3.0 grant.

## Mascot artwork

The Jenny mascot artwork (`android/image_source/`, and the WebP poses derived
from it under `jenny/templates/ui/assets/`) is original work, copyright © 2026
Ludovico Ragno, and is **not** covered by the AGPL grant — see
[TRADEMARK.md](./TRADEMARK.md).

## Upstream project — nanobot (MIT License)

This project (Jenny) is derived from [nanobot](https://github.com/HKUDS/nanobot),
originally authored by Xubin Ren and the nanobot contributors and distributed
under the MIT License. The original license text is reproduced verbatim below,
as required by the MIT License's terms.

```
Copyright (c) 2025-present Xubin Ren and the nanobot contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
