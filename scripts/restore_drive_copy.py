#!/usr/bin/env python3
# Host-only: recovery tool. Never imported by the Android runtime.
"""Riapre la copia di Jenny su Google Drive **senza Jenny**, su un computer.

Per il giorno in cui non c'è un telefono su cui reinstallarla, o si vuole solo
rileggere un ricordo: scarichi la cartella «Jenny» dal tuo Drive (dal sito,
tasto destro → Scarica, poi estrai lo zip), e con la password della copia
questo script ricostruisce il workspace in una cartella normale — memoria,
quaderni, conversazioni, file — leggibile con qualunque editor.

Uso::

    # quali copie ci sono (la password la chiede a schermo)
    python3 scripts/restore_drive_copy.py ~/Downloads/Jenny --list

    # la più recente, in ./jenny-restored
    python3 scripts/restore_drive_copy.py ~/Downloads/Jenny --out ./jenny-restored

    # una precisa (l'id viene da --list)
    python3 scripts/restore_drive_copy.py ~/Downloads/Jenny --snapshot 2e828e6f… \\
        --out ./jenny-restored

La password si chiede a schermo, senza eco; per uno script la si passa da
``JENNY_BACKUP_PASSWORD``. Mai sulla riga di comando, che finisce nella
cronologia della shell.

Serve il pacchetto ``cryptography`` (``pip install cryptography``): sul
telefono la cifratura passa da Android, qui no. Il formato è quello di
``jenny_extensions/drive/cloud.py``; questo script è solo una sua porta d'uscita.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jenny_extensions.drive.cloud import (  # noqa: E402
    CloudBackupError,
    CloudRepository,
    NoSealedKeyError,
    RepoNotFoundError,
    WrongKeyError,
    WrongPasswordError,
    unseal_key,
)
from jenny_extensions.drive.cloud_sink import LocalDirSink  # noqa: E402


def _when(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000).strftime("%d/%m/%Y %H:%M")


async def _main(args: argparse.Namespace) -> int:
    try:
        from jenny.snapshot.crypto_backends.dev import DevAesGcmBackend
    except ImportError:
        raise SystemExit("Serve il pacchetto «cryptography»: pip install cryptography") from None

    folder = Path(args.folder).expanduser()
    if not (folder / "repo.json").is_file():
        raise SystemExit(f"{folder} non sembra la cartella «Jenny» di Drive (manca repo.json).")
    password = os.environ.get("JENNY_BACKUP_PASSWORD") or getpass.getpass(
        "Password della copia: "
    )
    sink = LocalDirSink(folder)
    backend = DevAesGcmBackend()
    try:
        key = await unseal_key(sink, password, backend=backend)
    except WrongPasswordError:
        raise SystemExit("Questa password non apre la copia.") from None
    except NoSealedKeyError:
        raise SystemExit(
            "Questa copia non ha una password (manca key.jbk): sceglila sul telefono, "
            "da Backup, poi riscarica la cartella."
        ) from None

    with tempfile.TemporaryDirectory(prefix="jenny-restore-") as state:
        try:
            repo = await CloudRepository.open(sink, key, Path(state), backend=backend)
        except WrongKeyError:
            raise SystemExit("Questa password non apre la copia.") from None
        except RepoNotFoundError:
            raise SystemExit("Nella cartella non c'è una copia di Jenny.") from None

        snapshots = await repo.list_snapshots()
        if not snapshots:
            raise SystemExit("La copia non contiene ancora nessuno snapshot.")
        if args.list:
            for i, snap in enumerate(snapshots):
                mark = "  ← la più recente" if i == 0 else ""
                print(f"{_when(snap.created_at_ms)}  {snap.id}{mark}")
            return 0

        chosen = snapshots[0]
        if args.snapshot:
            matches = [s for s in snapshots if s.id.startswith(args.snapshot.lower())]
            if len(matches) != 1:
                raise SystemExit("Nessuna copia (o più d'una) con quell'id: vedi --list.")
            chosen = matches[0]

        out = Path(args.out).expanduser()
        if out.exists() and any(out.iterdir()):
            raise SystemExit(f"{out} esiste e non è vuota: scegline un'altra.")

        def progress(done: int, total: int) -> None:
            print(f"\r  pacchi {done}/{total}", end="", flush=True)

        print(f"Ripristino la copia del {_when(chosen.created_at_ms)} in {out} …")
        try:
            manifest = await repo.restore(chosen, out, progress=progress)
        except CloudBackupError as exc:
            raise SystemExit(f"\nLa copia è danneggiata: {exc}") from None
        print(f"\nFatto: {manifest.file_count} file.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Ricostruisce il workspace di Jenny dalla sua copia su Google Drive."
    )
    parser.add_argument("folder", help="la cartella «Jenny» scaricata da Drive ed estratta")
    parser.add_argument("--list", action="store_true", help="elenca le copie e basta")
    parser.add_argument("--snapshot", help="l'id (o il suo inizio) della copia da ripristinare")
    parser.add_argument("--out", default="jenny-restored", help="dove ricostruire il workspace")
    return asyncio.run(_main(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
