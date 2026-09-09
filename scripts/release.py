#!/usr/bin/env python3
# Host-only: build/maintenance script. Never imported by the Android runtime.
"""Prepara una release: allinea i file di versione e genera ``latest.json``.

La versione vive in più posti che vanno tenuti in pari a mano (pyproject, il
fallback hardcoded in ``jenny/__init__.py``, ``versionCode``/``versionName``
del build Gradle) e con il manifest dell'updater ne arriva un altro ancora.
Questo script è l'unico punto in cui quei valori si toccano insieme.

Lo script **non pubblica niente**: calcola, scrive i file locali e stampa i
comandi ``gh`` da eseguire. La pubblicazione resta un gesto deliberato.

Flusso tipico::

    # 1. alza la versione (rifiuta una versione che non sale)
    python3 scripts/release.py 0.7.0

    # 2. costruisci e firma l'APK con il keystore locale
    (cd android && ./gradlew app:assembleRelease)

    # 3. genera il manifest sull'APK appena firmato
    python3 scripts/release.py 0.7.0 --manifest-only \\
        --apk android/app/build/outputs/apk/release/app-release.apk \\
        --summary-it "..." --summary-en "..."

Solo stdlib: il progetto è stdlib-only per scelta (vedi FORK_BOUNDARY.md).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shlex
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent

#: Slug del repository GitHub su cui vivono le release (override con ``--repo``).
DEFAULT_REPO = "flagdizero/jenny-android-ai-agent"

#: Il client legge il manifest da ``releases/latest/download/latest.json``:
#: quell'URL funziona solo se l'asset si chiama esattamente così.
MANIFEST_NAME = "latest.json"

#: Versione dello schema del manifest. Va alzata solo su un cambio incompatibile.
MANIFEST_SCHEMA = 1

DEFAULT_OUT_DIR = REPO_ROOT / "dist" / "release"

# Una release pubblicata è sempre X.Y.Z: i suffissi di pre-release sono ammessi
# nell'albero di sviluppo ma non come versione da pubblicare.
RELEASE_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")

# Pattern ancorati sulle righe esatte da riscrivere. Ognuno deve matchare una
# volta sola: zero match vuol dire che il file è cambiato sotto i piedi, più di
# uno che l'ancora non è abbastanza specifica. In entrambi i casi si smette.
PYPROJECT_VERSION_RE = re.compile(r'^version = "(?P<value>[^"]+)"$', re.MULTILINE)
INIT_FALLBACK_RE = re.compile(r'_read_pyproject_version\(\) or "(?P<value>[^"]+)"')
GRADLE_CODE_RE = re.compile(r"^[ \t]*versionCode = (?P<value>\d+)$", re.MULTILINE)
GRADLE_NAME_RE = re.compile(r'^[ \t]*versionName = "(?P<value>[^"]+)"$', re.MULTILINE)


class ReleaseError(RuntimeError):
    """Errore previsto: si stampa il messaggio e si esce, senza traceback."""


@dataclass(frozen=True)
class VersionFiles:
    """I file che contengono una copia della versione."""

    pyproject: Path
    init_py: Path
    gradle: Path

    @classmethod
    def under(cls, root: Path) -> VersionFiles:
        """Risolve i tre percorsi a partire dalla radice del repo."""
        return cls(
            pyproject=root / "pyproject.toml",
            init_py=root / "jenny" / "__init__.py",
            gradle=root / "android" / "app" / "build.gradle.kts",
        )


@dataclass(frozen=True)
class CurrentState:
    """Stato di versione letto dal repo."""

    version: str
    version_code: int


@dataclass(frozen=True)
class FileEdit:
    """Una singola riscrittura pianificata, mostrabile anche in dry-run."""

    path: Path
    label: str
    old: str
    new: str
    content: str


# --------------------------------------------------------------------------
# Lettura dei file di versione
# --------------------------------------------------------------------------


def _read(path: Path) -> str:
    if not path.is_file():
        raise ReleaseError(f"missing file: {path}")
    return path.read_text(encoding="utf-8")


def _match_once(pattern: re.Pattern[str], text: str, path: Path, what: str) -> re.Match[str]:
    """Cerca ``pattern`` pretendendo esattamente un match, altrimenti alza."""
    matches = list(pattern.finditer(text))
    if len(matches) == 1:
        return matches[0]
    found = "no match" if not matches else f"{len(matches)} matches"
    raise ReleaseError(
        f"{path}: expected exactly one {what} line, found {found} "
        f"(pattern: {pattern.pattern!r}). Refusing to write; fix the file or the pattern."
    )


def _base_version(version: str) -> str:
    """Scarta l'eventuale suffisso di pre-release (``0.7.0-dev`` -> ``0.7.0``)."""
    return version.split("-", 1)[0]


def _is_prerelease(version: str) -> bool:
    """True se la versione porta un suffisso di pre-release (``0.7.0-dev``)."""
    return _base_version(version) != version


def _version_tuple(version: str) -> tuple[int, ...]:
    """Converte ``X.Y.Z`` nella tupla di interi usata per il confronto."""
    parts = _base_version(version).split(".")
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        raise ReleaseError(f"malformed version {version!r}: expected X.Y.Z")
    return tuple(int(part) for part in parts)


def _goes_up(version: str, current: str) -> bool:
    """True se *version* è pubblicabile partendo da *current*.

    Il confronto è fra le tuple ``X.Y.Z``, con un'eccezione che è poi il flusso
    normale: l'albero di sviluppo sta a ``X.Y.Z-dev`` e la release che lo
    promuove è proprio ``X.Y.Z``. Numeri uguali salgono se quello corrente è una
    pre-release, non salgono se è già una versione pubblicata — la stessa regola
    che ``--manifest-only`` applica confrontando le versioni base, che prima
    divergeva da questa e faceva rifiutare la promozione.
    """
    target, base = _version_tuple(version), _version_tuple(current)
    if target > base:
        return True
    return target == base and _is_prerelease(current)


def read_current_state(files: VersionFiles) -> CurrentState:
    """Legge versione e ``versionCode`` correnti, pretendendo che siano allineati."""
    pyproject_version = _match_once(
        PYPROJECT_VERSION_RE, _read(files.pyproject), files.pyproject, "project version"
    ).group("value")
    init_version = _match_once(
        INIT_FALLBACK_RE, _read(files.init_py), files.init_py, "version fallback"
    ).group("value")

    gradle_text = _read(files.gradle)
    gradle_name = _match_once(GRADLE_NAME_RE, gradle_text, files.gradle, "versionName").group(
        "value"
    )
    gradle_code = int(
        _match_once(GRADLE_CODE_RE, gradle_text, files.gradle, "versionCode").group("value")
    )

    mismatches = {
        str(files.pyproject): pyproject_version,
        str(files.init_py): init_version,
        str(files.gradle): gradle_name,
    }
    if len({_base_version(value) for value in mismatches.values()}) != 1:
        detail = "\n".join(f"  {path}: {value}" for path, value in mismatches.items())
        raise ReleaseError(
            "the version files disagree; align them before releasing:\n" + detail
        )

    return CurrentState(version=pyproject_version, version_code=gradle_code)


# --------------------------------------------------------------------------
# Bump
# --------------------------------------------------------------------------


def _replace_value(
    pattern: re.Pattern[str],
    text: str,
    path: Path,
    what: str,
    new_value: str,
) -> tuple[str, str]:
    """Sostituisce il gruppo ``value`` dell'unico match, senza usare ``re.sub``.

    ``re.sub`` interpreterebbe le sequenze di escape nella stringa di
    rimpiazzo; qui si lavora sugli offset del match, quindi il nuovo valore
    entra nel file letteralmente.
    """
    match = _match_once(pattern, text, path, what)
    start, end = match.span("value")
    return text[:start] + new_value + text[end:], match.group("value")


def plan_bump(files: VersionFiles, version: str, version_code: int) -> list[FileEdit]:
    """Calcola le riscrittive senza toccare il disco."""
    edits: list[FileEdit] = []

    pyproject_text = _read(files.pyproject)
    new_text, old = _replace_value(
        PYPROJECT_VERSION_RE, pyproject_text, files.pyproject, "project version", version
    )
    edits.append(FileEdit(files.pyproject, "project.version", old, version, new_text))

    init_text = _read(files.init_py)
    new_text, old = _replace_value(
        INIT_FALLBACK_RE, init_text, files.init_py, "version fallback", version
    )
    edits.append(FileEdit(files.init_py, "hardcoded version fallback", old, version, new_text))

    # I due valori Gradle stanno nello stesso file: la seconda sostituzione
    # parte dal testo già prodotto dalla prima.
    gradle_text = _read(files.gradle)
    gradle_text, old_code = _replace_value(
        GRADLE_CODE_RE, gradle_text, files.gradle, "versionCode", str(version_code)
    )
    gradle_text, old_name = _replace_value(
        GRADLE_NAME_RE, gradle_text, files.gradle, "versionName", version
    )
    edits.append(FileEdit(files.gradle, "versionCode", old_code, str(version_code), gradle_text))
    edits.append(FileEdit(files.gradle, "versionName", old_name, version, gradle_text))

    return edits


def apply_edits(edits: list[FileEdit]) -> None:
    """Scrive le riscritture pianificate (una write per file, l'ultima vince)."""
    for path in dict.fromkeys(edit.path for edit in edits):
        last = [edit for edit in edits if edit.path == path][-1]
        path.write_text(last.content, encoding="utf-8")


# --------------------------------------------------------------------------
# APK e manifest
# --------------------------------------------------------------------------


def hash_apk(path: Path) -> tuple[str, int]:
    """Restituisce ``(sha256 esadecimale minuscolo, dimensione in byte)``."""
    if not path.is_file():
        raise ReleaseError(f"APK not found: {path}")
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    if size == 0:
        raise ReleaseError(f"APK is empty: {path}")
    return digest.hexdigest(), size


def apk_asset_name(version: str) -> str:
    """Nome con cui l'APK va allegato alla release."""
    return f"jenny-{version}.apk"


def build_manifest(
    *,
    version: str,
    version_code: int,
    sha256: str,
    size: int,
    summary_it: str,
    summary_en: str,
    min_supported_code: int,
    rollout: int,
    critical: bool,
    repo: str,
) -> dict[str, Any]:
    """Costruisce il dizionario del manifest nell'ordine dello schema."""
    tag = f"v{version}"
    base = f"https://github.com/{repo}/releases"
    return {
        "schema": MANIFEST_SCHEMA,
        "version_code": version_code,
        "version_name": version,
        "apk_url": f"{base}/download/{tag}/{apk_asset_name(version)}",
        "sha256": sha256,
        "size": size,
        "notes_url": f"{base}/tag/{tag}",
        "summary_it": summary_it,
        "summary_en": summary_en,
        "min_supported_code": min_supported_code,
        "rollout": rollout,
        "critical": critical,
    }


def resolve_min_supported_code(explicit: int | None, manifest_path: Path) -> int:
    """Default di ``min_supported_code``: quello della release precedente, o 0.

    "Release precedente" qui vuol dire il ``latest.json`` già presente nella
    cartella di output: è il manifest che questo script ha generato l'ultima
    volta. Se non c'è (o non si legge), si parte da 0 e si può forzare il
    valore con ``--min-supported-code``.
    """
    if explicit is not None:
        return explicit
    if not manifest_path.is_file():
        return 0
    try:
        previous = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    value = previous.get("min_supported_code", 0)
    return value if isinstance(value, int) and value >= 0 else 0


def resolve_manifest_path(out: Path) -> Path:
    """Normalizza ``--out`` a un percorso che finisce in ``latest.json``."""
    if out.suffix == ".json":
        if out.name != MANIFEST_NAME:
            raise ReleaseError(
                f"the manifest must be named {MANIFEST_NAME} (got {out.name}): the client "
                f"reads it from releases/latest/download/{MANIFEST_NAME}"
            )
        return out
    return out / MANIFEST_NAME


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------


def _print_header(title: str) -> None:
    print(f"\n== {title}")


def verification_block(*, version: str, sha256: str, size: int) -> str:
    """Il blocco di verifica da pubblicare nel corpo della release.

    Il README dice "verify it against the hash published on the release page":
    quella frase è vera solo se il corpo della release porta davvero l'hash.
    La 0.3.0 lo faceva a mano e dalla 0.4.0 la pratica si è persa, quindi il
    blocco lo genera lo script — così non dipende da chi si ricorda di
    incollarlo. ``latest.json`` porta lo stesso digest, ma è un file che si
    scarica: chi verifica vuole leggerlo nella pagina.

    Dimensione in MiB con l'etichetta "MB", come nella 0.3.0: cambiarla ora
    farebbe sembrare cresciuto un APK che non è cresciuto.
    """
    apk = apk_asset_name(version)
    mib = size / (1024 * 1024)
    return (
        "## Verify what you downloaded\n"
        "\n"
        "```\n"
        f"sha256  {sha256}\n"
        f"size    {size} bytes ({mib:.1f} MB)\n"
        "```\n"
        "\n"
        "```bash\n"
        f"shasum -a 256 {apk}\n"
        "```\n"
        "\n"
        "The hash proves you got this file whole; the **signature** is what proves it came from\n"
        "this project, and Android checks it for you on an in-place update. To check it yourself:\n"
        "\n"
        "```bash\n"
        f"apksigner verify --print-certs {apk}\n"
        "```\n"
        "\n"
        "The certificate is the same one published with\n"
        "[0.3.0](https://github.com/flagdizero/jenny-android-ai-agent/releases/tag/v0.3.0) — if it\n"
        "ever differs, the build did not come from this project.\n"
    )


def print_publish_commands(
    *,
    version: str,
    repo: str,
    apk_path: Path,
    manifest_path: Path,
    summary_en: str,
    sha256: str,
    size: int,
) -> None:
    """Stampa i comandi ``gh``, che restano da eseguire a mano."""
    tag = f"v{version}"
    quoted_apk = shlex.quote(str(apk_path))
    quoted_manifest = shlex.quote(str(manifest_path))
    quoted_repo = shlex.quote(repo)
    block = verification_block(version=version, sha256=sha256, size=size)
    notes = f"{summary_en}\n\n{block}"

    _print_header("Publish (run these yourself — this script never publishes)")
    print(
        f"gh release create {tag} \\\n"
        f"    {quoted_apk} \\\n"
        f"    {quoted_manifest} \\\n"
        f"    --repo {quoted_repo} \\\n"
        f"    --title {shlex.quote(f'Jenny {version}')} \\\n"
        f"    --notes {shlex.quote(notes)}"
    )
    print("\n# Re-upload the manifest after editing it (rollout change, kill switch):")
    print(f"gh release upload {tag} {quoted_manifest} --repo {quoted_repo} --clobber")

    _print_header("Verification block (already inside --notes above)")
    print("# Paste this into the release body if you write it by hand in the GitHub UI.")
    print(block)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="release.py",
        description="Bump the version files and generate the latest.json update manifest.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "The script writes local files and prints the gh commands to run. "
            "It never publishes anything by itself."
        ),
    )
    parser.add_argument("version", help="new release version, X.Y.Z (e.g. 0.7.0)")
    parser.add_argument(
        "--apk",
        type=Path,
        help="path to the signed release APK; generates the manifest when given",
    )
    parser.add_argument(
        "--manifest-only",
        action="store_true",
        help="skip the version bump (the files must already be at VERSION)",
    )
    parser.add_argument("--summary-it", help="one-line Italian summary shown in the app")
    parser.add_argument("--summary-en", help="one-line English summary shown in the app")
    parser.add_argument(
        "--rollout",
        type=int,
        default=100,
        help="percentage of devices offered the update, 0-100 (default: 100)",
    )
    parser.add_argument(
        "--critical",
        action="store_true",
        help="mark the release as critical (the client insists instead of asking once)",
    )
    parser.add_argument(
        "--min-supported-code",
        type=int,
        help="oldest versionCode still allowed to update (default: previous manifest, or 0)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT_DIR,
        help=f"output directory, or a path ending in {MANIFEST_NAME} (default: {DEFAULT_OUT_DIR})",
    )
    parser.add_argument(
        "--repo",
        default=DEFAULT_REPO,
        help=f"GitHub repository hosting the release (default: {DEFAULT_REPO})",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=REPO_ROOT,
        help=argparse.SUPPRESS,  # usato dai test per lavorare su una copia
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print everything that would happen without writing a single file",
    )
    return parser


def run(args: argparse.Namespace) -> int:
    version: str = args.version
    if not RELEASE_VERSION_RE.match(version):
        raise ReleaseError(
            f"invalid release version {version!r}: expected X.Y.Z with no pre-release suffix"
        )
    if not 0 <= args.rollout <= 100:
        raise ReleaseError(f"--rollout must be between 0 and 100 (got {args.rollout})")

    files = VersionFiles.under(args.repo_root)
    current = read_current_state(files)
    dry_run: bool = args.dry_run

    print(f"current: {current.version} (versionCode {current.version_code})")
    print(f"target:  {version}")
    if dry_run:
        print("mode:    dry run — nothing will be written")

    # -- fase 1: bump ------------------------------------------------------
    if args.manifest_only:
        if _base_version(current.version) != version:
            raise ReleaseError(
                f"--manifest-only expects the version files to be at {version}, "
                f"but they are at {current.version}. Run the bump first."
            )
        version_code = current.version_code
        _print_header("Version bump")
        print(f"skipped (--manifest-only); versionCode stays {version_code}")
    else:
        if not _goes_up(version, current.version):
            raise ReleaseError(
                f"version {version} does not go up from {current.version}: "
                "a release must increase the version"
            )
        version_code = current.version_code + 1
        edits = plan_bump(files, version, version_code)
        _print_header("Version bump")
        for edit in edits:
            marker = "would set" if dry_run else "set"
            print(f"{marker} {edit.label}: {edit.old} -> {edit.new}  ({edit.path})")
        if not dry_run:
            apply_edits(edits)

    # -- fase 2: manifest --------------------------------------------------
    if args.apk is None:
        _print_header("Next steps")
        print("1. Build and sign the APK:")
        print("     (cd android && ./gradlew app:assembleRelease)")
        print("2. Generate the manifest on the signed APK:")
        print(
            f"     python3 scripts/release.py {version} --manifest-only \\\n"
            "         --apk android/app/build/outputs/apk/release/app-release.apk \\\n"
            '         --summary-it "..." --summary-en "..."'
        )
        return 0

    if not args.summary_it or not args.summary_en:
        raise ReleaseError("--summary-it and --summary-en are required when --apk is given")

    apk_source: Path = args.apk
    sha256, size = hash_apk(apk_source)
    manifest_path = resolve_manifest_path(args.out)
    min_supported_code = resolve_min_supported_code(args.min_supported_code, manifest_path)
    if min_supported_code > version_code:
        raise ReleaseError(
            f"--min-supported-code {min_supported_code} is above the new versionCode "
            f"{version_code}: nobody would be allowed to update"
        )

    manifest = build_manifest(
        version=version,
        version_code=version_code,
        sha256=sha256,
        size=size,
        summary_it=args.summary_it,
        summary_en=args.summary_en,
        min_supported_code=min_supported_code,
        rollout=args.rollout,
        critical=args.critical,
        repo=args.repo,
    )

    # L'asset deve chiamarsi jenny-<version>.apk: GitHub usa il nome del file
    # caricato, non l'etichetta, quindi se serve si stage una copia rinominata.
    asset_name = apk_asset_name(version)
    staged_apk = apk_source
    if apk_source.name != asset_name:
        staged_apk = manifest_path.parent / asset_name

    _print_header("Artifacts")
    print(f"apk:      {apk_source}")
    print(f"sha256:   {sha256}")
    print(f"size:     {size} bytes")
    if staged_apk != apk_source:
        verb = "would stage" if dry_run else "staged"
        print(f"{verb} as: {staged_apk}  (asset name must be {asset_name})")
    verb = "would write" if dry_run else "wrote"
    print(f"{verb}:  {manifest_path}")

    if not dry_run:
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        if staged_apk != apk_source:
            shutil.copy2(apk_source, staged_apk)
        manifest_path.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )

    _print_header(MANIFEST_NAME)
    print(json.dumps(manifest, indent=2, ensure_ascii=False))

    print_publish_commands(
        version=version,
        repo=args.repo,
        apk_path=staged_apk,
        manifest_path=manifest_path,
        summary_en=args.summary_en,
        sha256=sha256,
        size=size,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return run(args)
    except ReleaseError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
