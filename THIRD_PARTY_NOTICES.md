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
| [KaTeX](https://github.com/KaTeX/KaTeX) | 0.16.10 | MIT | `vendor/katex@0.16.10/LICENSE` |
| [Mermaid](https://github.com/mermaid-js/mermaid) | 10.9.6 | MIT | `vendor/mermaid@10/LICENSE` |
| [D3](https://github.com/d3/d3) | 7.9.0 | ISC | `vendor/d3@7/LICENSE` |
| [CodeMirror](https://github.com/codemirror/codemirror5) | 5.65.16 | MIT | `vendor/codemirror@5.65.16/LICENSE` |
| [QR Code Generator](https://github.com/kazuhikoarase/qrcode-generator) | 1.4.4 | MIT | `vendor/qrcode-generator@1.4.4/LICENSE` |
| [Tabler Icons](https://github.com/tabler/tabler-icons) (webfont) | 3.19.0 | MIT | `vendor/@tabler/icons-webfont@3.19.0/LICENSE` |

The KaTeX distribution includes its own web fonts under
`katex@0.16.10/dist/fonts/`; they are covered by the KaTeX license above. Only
the `.woff2` faces are shipped: the stylesheet lists woff2 first and the Android
WebView supports it, so the `.ttf` and `.woff` copies were never fetched — 700 kB
of APK that nothing could open.

## Bundled fonts

Self-hosted under `jenny/templates/ui/assets/vendor/fonts/`. All are licensed
under the [SIL Open Font License 1.1](https://openfontlicense.org); the full
OFL text and the per-font copyright notices are in `vendor/fonts/LICENSE.txt`.

- **UI fonts** — Inter, Fira Code.
- **Per-theme display fonts** (`theme-fonts.css`) — Tenor Sans, Orbitron,
  Shippori Mincho, Baloo 2, Bangers, Comic Neue, Fredoka, Marcellus.

## Bundled Python packages

Chaquopy installs the wheels pinned in `requirements-android.lock.txt` into the
APK at build time. All resolve from public PyPI — none is local or patched. Each
is governed by its own license, available in the installed wheel's metadata and
in its upstream repository.

| Package | Version | License |
|---|---|---|
| `httpx` | 0.28.1 | BSD-3-Clause |
| `httpcore` | 1.0.9 | BSD-3-Clause |
| `h11` | 0.16.0 | MIT |
| `anyio` | 4.14.1 | MIT |
| `idna` | 3.18 | BSD-3-Clause |
| `certifi` | 2026.6.17 | MPL-2.0 |
| `websockets` | 16.0 | BSD-3-Clause |
| `loguru` | 0.7.3 | MIT |
| `json-repair` | 0.61.2 | MIT |
| `jinja2` | 3.1.6 | BSD-3-Clause |
| `MarkupSafe` | 3.0.3 | BSD-3-Clause |
| `markdown` | 3.10.2 | BSD-3-Clause |
| `pyyaml` | 6.0.3 | MIT |
| `pypdf` | 5.9.0 | BSD-3-Clause |
| `typing-extensions` | 4.16.0 | PSF-2.0 |
| `tzdata` | 2026.3 | Apache-2.0 |

`certifi` is the one weak-copyleft entry: it ships unmodified, and its CA bundle
is under the MPL-2.0, which applies per file and is compatible with this
project's AGPL-3.0 grant.

The APK also embeds a CPython 3.11 runtime and the Chaquopy support layer, both
supplied by the [Chaquopy](https://chaquo.com/chaquopy/) Gradle plugin.
[Chaquopy is MIT-licensed](https://chaquo.com/chaquopy/license/) — free and
open source since 12.0.1, with no remaining license restrictions — and CPython
is under the Python Software Foundation License. Both are compatible with this
project's AGPL-3.0 grant.

## Bundled Android libraries

Resolved from Google's Maven repository and Maven Central, and shipped inside
the APK (`android/app/build.gradle.kts`). Both flavors carry them unless the
table says otherwise.

| Component | Version | License | Flavor |
|---|---|---|---|
| AndroidX `core-ktx`, `appcompat`, `constraintlayout`, `browser`, `webkit`, `work-runtime-ktx` (with the AndroidX and Kotlin libraries they pull in) | 1.12.0, 1.6.1, 2.1.4, 1.7.0, 1.14.0, 2.9.1 | Apache-2.0 | both |
| [Markwon](https://github.com/noties/Markwon) `core`, `ext-strikethrough`, `ext-tables`, `ext-tasklist`, `linkify`, `html` | 4.6.2 | Apache-2.0 | both |
| [commonmark-java](https://github.com/commonmark/commonmark-java) (pulled in by Markwon, with its GFM strikethrough and tables extensions) | 0.13.0 | BSD-2-Clause | both |
| Google Play services `play-services-auth` (with the `play-services-*` libraries it pulls in) | 21.2.0 | Android Software Development Kit License (proprietary) | `play` only |

The `foss` build contains no Google library; `play-services-auth` is there only
for the encrypted copy on Google Drive.

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
