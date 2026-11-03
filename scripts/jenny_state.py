#!/usr/bin/env python3
# Host-only: development tool. Never imported by the Android runtime.
"""Lo stato di Jenny sull'emulatore, letto e scritto dal Mac.

Lo stato vive in SQLite sotto ``files/db/`` (``jenny/db``): i job, gli aiutanti,
le conversazioni. Questo script ne prende una copia con ``run-as`` (solo una
build debug; su ``jenny_s22`` non c'è ``su``), la apre col sqlite3 di Python e,
se gli si chiede di scrivere, la rimanda indietro **ad app ferma**: il gateway
tiene i suoi database aperti, e un file cambiato sotto i suoi piedi verrebbe
sovrascritto o rotto. Non tocca mai ``config.json``: la chiave del provider
resta sul telefono.

Uso::

    # una lettura (l'app può restare accesa)
    python3 scripts/jenny_state.py sql "SELECT name, status FROM migration_runs"

    # una scrittura: ferma l'app, cambia, rimanda il file
    python3 scripts/jenny_state.py sql --write "UPDATE jobs SET ..."

    # solo la copia, per guardarla con calma
    python3 scripts/jenny_state.py pull ./state.sqlite
    python3 scripts/jenny_state.py push ./state.sqlite

``--db undo`` lavora sul diario dell'annulla invece che sullo stato. Il
dispositivo è quello di ``ANDROID_SERIAL``, che deve essere l'emulatore
``jenny_s22``.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

PACKAGE = "com.flagdizero.jenny"
DB_DIR = "files/db"
AVD = "jenny_s22"
_SIDECARS = ("-wal", "-shm")


def _adb(*args: str, stdin: bytes | None = None, check: bool = True) -> bytes:
    done = subprocess.run(["adb", *args], input=stdin, capture_output=True, timeout=120)
    if check and done.returncode != 0:
        raise SystemExit(f"adb {' '.join(args)}: {done.stderr.decode(errors='replace').strip()}")
    return done.stdout


def _check_device() -> None:
    if not os.environ.get("ANDROID_SERIAL"):
        raise SystemExit("Imposta ANDROID_SERIAL sull'emulatore (es. emulator-5554).")
    name = _adb("emu", "avd", "name").decode(errors="replace").splitlines()
    if not name or name[0].strip() != AVD:
        raise SystemExit(f"{os.environ['ANDROID_SERIAL']} non è {AVD}: niente da fare.")


def _run_as(command: str) -> str:
    """*command* come utente dell'app: una stringa sola per ``adb shell``, che
    altrimenti riunisce gli argomenti con spazi e perde le virgolette."""
    quoted = command.replace("'", "'\\''")
    return f"run-as {PACKAGE} sh -c '{quoted}'"


def _remote(db: str) -> str:
    return f"{DB_DIR}/{db}.sqlite"


def pull(db: str, dest: Path) -> Path:
    """Il database e il suo ``-wal`` in *dest*: senza il wal mancano le ultime righe."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    for suffix in ("", "-wal"):
        data = _adb("exec-out", _run_as(f"cat {_remote(db)}{suffix} 2>/dev/null"), check=False)
        target = Path(f"{dest}{suffix}")
        target.unlink(missing_ok=True)
        if data or not suffix:
            target.write_bytes(data)
    Path(f"{dest}-shm").unlink(missing_ok=True)
    if dest.stat().st_size == 0:
        raise SystemExit(f"{_remote(db)} non c'è sul telefono.")
    # Il wal dentro il file: da qui in poi è un database solo.
    conn = sqlite3.connect(dest)
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.execute("PRAGMA journal_mode=DELETE")
    finally:
        conn.close()
    return dest


def push(db: str, source: Path) -> None:
    """Rimanda *source* al posto del database, ad app ferma."""
    _adb("shell", "am", "force-stop", PACKAGE)
    remote = _remote(db)
    _adb("shell", _run_as(f"rm -f {remote}-wal {remote}-shm && cat > {remote}"),
         stdin=source.read_bytes())
    print(f"{remote} rimandato; l'app è ferma, riaprila per vederlo.")


def sql(db: str, statement: str, *, write: bool) -> None:
    if write:
        # Prima di copiare: quel che l'app scrivesse fra la copia e il ritorno
        # andrebbe perso.
        _adb("shell", "am", "force-stop", PACKAGE)
    with tempfile.TemporaryDirectory(prefix="jenny-state-") as tmp:
        local = pull(db, Path(tmp) / f"{db}.sqlite")
        conn = sqlite3.connect(local)
        try:
            cursor = conn.execute(statement)
            rows = cursor.fetchall()
            if cursor.description:
                print("\t".join(c[0] for c in cursor.description))
            for row in rows:
                print("\t".join("" if v is None else str(v) for v in row))
            if write:
                conn.commit()
            elif conn.in_transaction:
                conn.rollback()
        finally:
            conn.close()
        if write:
            push(db, local)


def main() -> int:
    parser = argparse.ArgumentParser(description="Lo stato di Jenny sull'emulatore.")
    parser.add_argument("--db", default="state", choices=("state", "undo"))
    sub = parser.add_subparsers(dest="command", required=True)
    p_sql = sub.add_parser("sql", help="una query (con --write la rimanda)")
    p_sql.add_argument("statement")
    p_sql.add_argument("--write", action="store_true")
    sub.add_parser("pull", help="una copia sul Mac").add_argument("dest")
    sub.add_parser("push", help="rimanda una copia, ad app ferma").add_argument("source")
    args = parser.parse_args()
    _check_device()
    if args.command == "sql":
        sql(args.db, args.statement, write=args.write)
    elif args.command == "pull":
        print(pull(args.db, Path(args.dest)))
    else:
        push(args.db, Path(args.source))
    return 0


if __name__ == "__main__":
    sys.exit(main())
