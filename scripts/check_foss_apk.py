#!/usr/bin/env python3
"""Controlla che un APK ``foss`` non contenga niente delle estensioni.

La build ``foss`` (GitHub, F-Droid) promette di non contenere **nemmeno il
codice** della copia su Google Drive e di non avere nessuna libreria Google.
Gradle lo garantisce per costruzione (``android/app/build.gradle.kts``); questo
script lo verifica sull'APK vero, perché è l'APK che si pubblica.

Guarda tre posti:

- l'archivio Python di Chaquopy (``assets/chaquopy/app.imy``, uno zip dentro
  lo zip): nessun file sotto ``jenny_extensions/``;
- il dex: nessuna classe delle estensioni native né di Google Play Services
  (i descrittori delle classi sono stringhe nel dex);
- la radice dell'APK: nessun ``play-services-*.properties``, che le librerie
  Google si portano dietro anche quando R8 ne rinomina le classi.

Con ``--play`` fa il controllo opposto su un APK ``play``: le estensioni ci
sono. Serve a sapere che lo script guarda nei posti giusti.

Uso::

    python3 scripts/check_foss_apk.py android/app/build/outputs/apk/foss/release/app-foss-release.apk
    python3 scripts/check_foss_apk.py --play android/app/build/outputs/apk/play/debug/app-play-debug.apk
"""

from __future__ import annotations

import argparse
import io
import re
import sys
import zipfile
from pathlib import Path

EXTENSIONS_PACKAGE = "jenny_extensions/"
# Le classi native delle estensioni (``android/app/src/play/``) e la famiglia
# di Google Play Services.
DEX_MARKERS = (
    b"Lcom/flagdizero/jenny/DriveBridge;",
    b"Lcom/flagdizero/jenny/DriveExtension;",
    b"Lcom/google/android/gms/",
)
GOOGLE_PROPERTIES = re.compile(r"^(play-services|firebase)-[\w-]+\.properties$")


def inspect(apk: Path) -> dict[str, list[str]]:
    """Cosa c'è delle estensioni in *apk*, per posto."""
    found: dict[str, list[str]] = {"python": [], "dex": [], "properties": []}
    with zipfile.ZipFile(apk) as zf:
        names = zf.namelist()
        app_imy = zf.read("assets/chaquopy/app.imy")
        with zipfile.ZipFile(io.BytesIO(app_imy)) as inner:
            found["python"] = sorted(
                n for n in inner.namelist() if n.startswith(EXTENSIONS_PACKAGE)
            )
        found["python"] += sorted(
            n for n in names if n.startswith("assets/") and f"/{EXTENSIONS_PACKAGE}" in n
        )
        for name in names:
            if re.fullmatch(r"classes\d*\.dex", name):
                data = zf.read(name)
                found["dex"] += [
                    f"{name}: {marker.decode()}" for marker in DEX_MARKERS if marker in data
                ]
            elif GOOGLE_PROPERTIES.match(name):
                found["properties"].append(name)
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("apk", type=Path)
    parser.add_argument(
        "--play", action="store_true",
        help="controllo opposto: l'APK è play e le estensioni devono esserci",
    )
    args = parser.parse_args(argv)

    found = inspect(args.apk)
    if args.play:
        missing = [where for where in ("python", "dex") if not found[where]]
        if missing:
            print(f"{args.apk}: extensions missing from: {', '.join(missing)}", file=sys.stderr)
            return 1
        print(f"{args.apk}: play build, extensions present "
              f"({len(found['python'])} Python files, {len(found['dex'])} dex markers)")
        return 0

    leaks = [f"{where}: {item}" for where, items in found.items() for item in items]
    if leaks:
        print(f"{args.apk}: the foss build contains extension code:", file=sys.stderr)
        for leak in leaks:
            print(f"  {leak}", file=sys.stderr)
        return 1
    print(f"{args.apk}: foss build, no extension code and no Google library")
    return 0


if __name__ == "__main__":
    sys.exit(main())
