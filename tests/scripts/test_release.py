"""Test dello script editoriale di release (``scripts/release.py``).

Tutto gira su un finto albero di repo dentro ``tmp_path``: i file di versione
veri del repository non vengono mai toccati.

L'ultima sezione è di *round-trip*: il manifest che lo script produce viene dato
al validatore vero del client (``jenny/runtime/update_check.py``). Le due metà
del sistema di aggiornamento sono state scritte l'una contro la descrizione
dell'altra, e questo è il solo posto dove si incontrano prima di una release
pubblicata.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

from jenny.runtime import update_check

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "release.py"


def _load_release_module() -> ModuleType:
    """Carica lo script per path: ``scripts/`` non è un package importabile."""
    spec = importlib.util.spec_from_file_location("jenny_release_script", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


release = _load_release_module()


PYPROJECT = """\
[project]
name = "jenny"
version = "0.6.6"
description = "Jenny"

[tool.ruff]
line-length = 100
"""

INIT_PY = '''\
def _version() -> str:
    try:
        return metadata.version("jenny")
    except Exception:
        return _read_pyproject_version() or "0.6.6"
'''

GRADLE = """\
android {
    defaultConfig {
        applicationId = "com.flagdizero.jenny"
        minSdk = 26
        // versionCode must increase monotonically on every published build.
        versionCode = 8
        versionName = "0.6.6"
    }
}
"""


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """Finto albero di repo con i tre file di versione."""
    (tmp_path / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")
    (tmp_path / "jenny").mkdir()
    (tmp_path / "jenny" / "__init__.py").write_text(INIT_PY, encoding="utf-8")
    gradle_dir = tmp_path / "android" / "app"
    gradle_dir.mkdir(parents=True)
    (gradle_dir / "build.gradle.kts").write_text(GRADLE, encoding="utf-8")
    return tmp_path


@pytest.fixture
def apk(tmp_path: Path) -> Path:
    """APK finto: allo script servono solo i byte per hash e dimensione."""
    path = tmp_path / "app-release.apk"
    path.write_bytes(b"not really an apk" * 100)
    return path


def _run(repo: Path, *argv: str) -> int:
    return release.main([*argv, "--repo-root", str(repo)])


def _manifest_argv(apk: Path, out: Path, *extra: str) -> list[str]:
    """Gli argomenti minimi per far generare il manifest, più eventuali extra."""
    return ["--apk", str(apk), "--out", str(out), "--summary-it", "a", "--summary-en", "b", *extra]


def _files(repo: Path) -> dict[str, str]:
    return {
        "pyproject": (repo / "pyproject.toml").read_text(encoding="utf-8"),
        "init": (repo / "jenny" / "__init__.py").read_text(encoding="utf-8"),
        "gradle": (repo / "android" / "app" / "build.gradle.kts").read_text(encoding="utf-8"),
    }


# --------------------------------------------------------------------------
# Bump
# --------------------------------------------------------------------------


def test_bump_rewrites_every_version_file(repo: Path) -> None:
    assert _run(repo, "0.7.0") == 0

    content = _files(repo)
    assert 'version = "0.7.0"' in content["pyproject"]
    assert '_read_pyproject_version() or "0.7.0"' in content["init"]
    assert "versionCode = 9" in content["gradle"]
    assert 'versionName = "0.7.0"' in content["gradle"]


def test_bump_preserves_the_rest_of_each_file(repo: Path) -> None:
    assert _run(repo, "0.7.0") == 0

    content = _files(repo)
    assert 'name = "jenny"' in content["pyproject"]
    assert "line-length = 100" in content["pyproject"]
    assert "versionCode must increase monotonically" in content["gradle"]
    assert 'applicationId = "com.flagdizero.jenny"' in content["gradle"]


def test_version_code_increments_by_one(repo: Path) -> None:
    state = release.read_current_state(release.VersionFiles.under(repo))
    assert state.version_code == 8

    _run(repo, "0.7.0")

    after = release.read_current_state(release.VersionFiles.under(repo))
    assert after.version_code == 9
    assert after.version == "0.7.0"


@pytest.mark.parametrize("version", ["0.6.5", "0.6.6", "0.5.9"])
def test_rejects_a_version_that_does_not_go_up(repo: Path, version: str, capsys) -> None:
    before = _files(repo)

    assert _run(repo, version) == 1

    assert "does not go up" in capsys.readouterr().err
    assert _files(repo) == before


def test_rejects_a_pre_release_suffix(repo: Path, capsys) -> None:
    assert _run(repo, "0.7.0-dev") == 1
    assert "invalid release version" in capsys.readouterr().err


@pytest.fixture
def dev_repo(repo: Path) -> Path:
    """Lo stesso albero, ma in sviluppo su ``0.7.0-dev``."""
    for path, old in (
        (repo / "pyproject.toml", 'version = "0.6.6"'),
        (repo / "jenny" / "__init__.py", 'or "0.6.6"'),
        (repo / "android" / "app" / "build.gradle.kts", 'versionName = "0.6.6"'),
    ):
        text = path.read_text(encoding="utf-8")
        path.write_text(text.replace(old, old.replace("0.6.6", "0.7.0-dev")), "utf-8")
    return repo


def test_a_dev_version_can_be_promoted_to_its_release(dev_repo: Path) -> None:
    """Sviluppo su ``0.7.0-dev``, pubblicazione di ``0.7.0``: è il flusso normale.

    Il suffisso è ammesso nell'albero di lavoro (lo dice il docstring dello
    script), quindi la versione che lo promuove non può essere respinta come se
    fosse un errore di chi pubblica.
    """
    assert _run(dev_repo, "0.7.0") == 0

    content = _files(dev_repo)
    assert 'version = "0.7.0"' in content["pyproject"]
    assert "-dev" not in content["pyproject"]
    assert 'versionName = "0.7.0"' in content["gradle"]
    assert "versionCode = 9" in content["gradle"]


def test_the_promotion_is_accepted_by_both_branches(dev_repo: Path, apk: Path) -> None:
    """``--manifest-only`` accettava già la promozione: ora anche il bump.

    I due rami leggevano la stessa versione in due modi diversi — uno scartando
    il suffisso, l'altro no — e su un albero ``-dev`` si contraddicevano.
    """
    state = release.read_current_state(release.VersionFiles.under(dev_repo))
    assert state.version == "0.7.0-dev"

    assert release._goes_up("0.7.0", state.version) is True
    assert release._base_version(state.version) == "0.7.0"


@pytest.mark.parametrize("version", ["0.6.6", "0.6.5"])
def test_a_dev_tree_still_refuses_a_version_that_does_not_go_up(
    dev_repo: Path, version: str, capsys
) -> None:
    """Il suffisso ammorbidisce solo l'uguaglianza, non l'ordine."""
    before = _files(dev_repo)

    assert _run(dev_repo, version) == 1

    assert "does not go up" in capsys.readouterr().err
    assert _files(dev_repo) == before


def test_a_published_version_cannot_be_published_again(repo: Path, capsys) -> None:
    """Senza suffisso, la stessa versione resta rifiutata: si è già pubblicata."""
    assert _run(repo, "0.6.6") == 1
    assert "does not go up" in capsys.readouterr().err


# --------------------------------------------------------------------------
# Pattern che non matcha
# --------------------------------------------------------------------------


def test_missing_pattern_aborts_without_writing(repo: Path, capsys) -> None:
    """Un build.gradle.kts riscritto (qui: versionCode assente) ferma tutto."""
    gradle = repo / "android" / "app" / "build.gradle.kts"
    gradle.write_text(GRADLE.replace("versionCode = 8", "// versionCode moved away"), "utf-8")
    before = _files(repo)

    assert _run(repo, "0.7.0") == 1

    err = capsys.readouterr().err
    assert "versionCode" in err and "no match" in err
    assert _files(repo) == before


def test_duplicate_pattern_aborts_without_writing(repo: Path, capsys) -> None:
    """Due ``versionName`` (es. un flavor aggiunto) rendono l'ancora ambigua."""
    gradle = repo / "android" / "app" / "build.gradle.kts"
    gradle.write_text(
        GRADLE + '\n    productFlavors {\n        versionName = "0.6.6"\n    }\n', "utf-8"
    )
    before = _files(repo)

    assert _run(repo, "0.7.0") == 1

    err = capsys.readouterr().err
    assert "versionName" in err and "2 matches" in err
    assert _files(repo) == before


def test_version_files_out_of_sync_are_refused(repo: Path, capsys) -> None:
    init = repo / "jenny" / "__init__.py"
    init.write_text(INIT_PY.replace('or "0.6.6"', 'or "0.5.0"'), encoding="utf-8")
    before = _files(repo)

    assert _run(repo, "0.7.0") == 1

    assert "disagree" in capsys.readouterr().err
    assert _files(repo) == before


# --------------------------------------------------------------------------
# APK: hash e dimensione
# --------------------------------------------------------------------------


def test_hash_apk_matches_hashlib(apk: Path) -> None:
    payload = apk.read_bytes()
    sha256, size = release.hash_apk(apk)

    assert sha256 == hashlib.sha256(payload).hexdigest()
    assert size == len(payload)
    assert len(sha256) == 64
    assert sha256 == sha256.lower()


def test_hash_apk_rejects_a_missing_file(tmp_path: Path) -> None:
    with pytest.raises(release.ReleaseError, match="APK not found"):
        release.hash_apk(tmp_path / "nope.apk")


def test_hash_apk_rejects_an_empty_file(tmp_path: Path) -> None:
    empty = tmp_path / "empty.apk"
    empty.write_bytes(b"")
    with pytest.raises(release.ReleaseError, match="empty"):
        release.hash_apk(empty)


# --------------------------------------------------------------------------
# Manifest
# --------------------------------------------------------------------------


def test_manifest_matches_the_agreed_schema(repo: Path, apk: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    assert (
        _run(
            repo,
            "0.7.0",
            "--apk",
            str(apk),
            "--out",
            str(out),
            "--summary-it",
            "Aggiornamenti automatici.",
            "--summary-en",
            "Automatic updates.",
            "--repo",
            "flagdizero/jenny-android-ai-agent",
        )
        == 0
    )

    manifest_path = out / "latest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert list(manifest) == [
        "schema",
        "version_code",
        "version_name",
        "apk_url",
        "sha256",
        "size",
        "notes_url",
        "summary_it",
        "summary_en",
        "min_supported_code",
        "rollout",
        "critical",
    ]
    assert manifest["schema"] == 1
    assert manifest["version_code"] == 9
    assert manifest["version_name"] == "0.7.0"
    assert manifest["apk_url"] == (
        "https://github.com/flagdizero/jenny-android-ai-agent/releases/download/"
        "v0.7.0/jenny-0.7.0.apk"
    )
    assert manifest["notes_url"] == (
        "https://github.com/flagdizero/jenny-android-ai-agent/releases/tag/v0.7.0"
    )
    assert manifest["sha256"] == hashlib.sha256(apk.read_bytes()).hexdigest()
    assert manifest["size"] == apk.stat().st_size
    assert manifest["summary_it"] == "Aggiornamenti automatici."
    assert manifest["summary_en"] == "Automatic updates."
    assert manifest["min_supported_code"] == 0
    assert manifest["rollout"] == 100
    assert manifest["critical"] is False


def test_manifest_is_always_named_latest_json(repo: Path, apk: Path, tmp_path: Path) -> None:
    """L'URL stabile del client punta a ``latest.json``: altri nomi si rifiutano."""
    out = tmp_path / "out" / "jenny-update.json"

    assert _run(repo, "0.7.0", *_manifest_argv(apk, out)) == 1
    assert not out.exists()


def test_apk_is_staged_under_the_asset_name(repo: Path, apk: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    _run(repo, "0.7.0", *_manifest_argv(apk, out))

    staged = out / "jenny-0.7.0.apk"
    assert staged.is_file()
    assert staged.read_bytes() == apk.read_bytes()


def test_rollout_and_critical_flow_into_the_manifest(repo: Path, apk: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    _run(
        repo,
        "0.7.0",
        *_manifest_argv(apk, out, "--rollout", "10", "--critical", "--min-supported-code", "6"),
    )

    manifest = json.loads((out / "latest.json").read_text(encoding="utf-8"))
    assert manifest["rollout"] == 10
    assert manifest["critical"] is True
    assert manifest["min_supported_code"] == 6


def test_rollout_out_of_range_is_refused(repo: Path, apk: Path, tmp_path: Path, capsys) -> None:
    argv = _manifest_argv(apk, tmp_path / "out", "--rollout", "140")

    assert _run(repo, "0.7.0", *argv) == 1
    assert "--rollout must be between 0 and 100" in capsys.readouterr().err


def test_min_supported_code_defaults_to_the_previous_manifest(
    repo: Path, apk: Path, tmp_path: Path
) -> None:
    out = tmp_path / "out"
    out.mkdir()
    (out / "latest.json").write_text(json.dumps({"min_supported_code": 4}), encoding="utf-8")

    _run(repo, "0.7.0", *_manifest_argv(apk, out))

    manifest = json.loads((out / "latest.json").read_text(encoding="utf-8"))
    assert manifest["min_supported_code"] == 4


def test_summaries_are_required_with_an_apk(repo: Path, apk: Path, tmp_path: Path, capsys) -> None:
    assert _run(repo, "0.7.0", "--apk", str(apk), "--out", str(tmp_path / "out")) == 1
    assert "--summary-it and --summary-en are required" in capsys.readouterr().err


def test_manifest_only_reuses_the_current_version_code(
    repo: Path, apk: Path, tmp_path: Path
) -> None:
    """Secondo passaggio: bump già fatto, APK costruito, manifest da generare."""
    _run(repo, "0.7.0")
    before = _files(repo)
    out = tmp_path / "out"

    assert _run(repo, "0.7.0", "--manifest-only", *_manifest_argv(apk, out)) == 0

    assert _files(repo) == before  # nessun secondo bump
    manifest = json.loads((out / "latest.json").read_text(encoding="utf-8"))
    assert manifest["version_code"] == 9


def test_manifest_only_refuses_a_repo_at_another_version(
    repo: Path, apk: Path, tmp_path: Path, capsys
) -> None:
    argv = _manifest_argv(apk, tmp_path / "out")

    assert _run(repo, "0.7.0", "--manifest-only", *argv) == 1
    assert "Run the bump first" in capsys.readouterr().err


# --------------------------------------------------------------------------
# Dry run
# --------------------------------------------------------------------------


def test_dry_run_writes_nothing(repo: Path, apk: Path, tmp_path: Path, capsys) -> None:
    before = _files(repo)
    out = tmp_path / "out"

    assert _run(repo, "0.7.0", *_manifest_argv(apk, out, "--dry-run")) == 0

    assert _files(repo) == before
    assert not out.exists()

    stdout = capsys.readouterr().out
    assert "dry run" in stdout
    assert "would set versionCode: 8 -> 9" in stdout
    assert '"version_code": 9' in stdout


def test_dry_run_prints_the_publish_commands(repo: Path, apk: Path, tmp_path: Path, capsys) -> None:
    _run(repo, "0.7.0", *_manifest_argv(apk, tmp_path / "out", "--dry-run"))

    stdout = capsys.readouterr().out
    assert "gh release create v0.7.0" in stdout
    assert "jenny-0.7.0.apk" in stdout
    assert "latest.json" in stdout
    assert "--clobber" in stdout


def test_the_publish_notes_carry_the_hash(repo: Path, apk: Path, tmp_path: Path, capsys) -> None:
    """Il README promette l'hash *nella pagina della release*: qui si paga.

    La 0.3.0 pubblicava sha256, dimensione e comando di verifica nel corpo; dalla
    0.4.0 la pratica si è persa senza che nulla lo notasse, e l'istruzione del
    README è rimasta a puntare a qualcosa che non c'era più. Ora il blocco esce
    dallo script, quindi non dipende dalla memoria di chi pubblica.
    """
    expected = hashlib.sha256(apk.read_bytes()).hexdigest()

    _run(repo, "0.7.0", *_manifest_argv(apk, tmp_path / "out", "--dry-run"))

    stdout = capsys.readouterr().out
    assert expected in stdout.split("Publish (run these yourself")[1]
    assert f"{apk.stat().st_size} bytes" in stdout
    assert "shasum -a 256 jenny-0.7.0.apk" in stdout
    assert "apksigner verify --print-certs" in stdout


def test_bump_without_an_apk_explains_the_next_step(repo: Path, capsys) -> None:
    assert _run(repo, "0.7.0") == 0

    stdout = capsys.readouterr().out
    assert "assembleRelease" in stdout
    assert "--manifest-only" in stdout


# --------------------------------------------------------------------------
# Round-trip: quello che questo script scrive, il client lo accetta
# --------------------------------------------------------------------------
#
# I test qui sopra descrivono il manifest a parole ("deve avere questi campi").
# Quelli qui sotto lo danno in pasto al **vero** validatore del client
# (``jenny.runtime.update_check``), che è stato scritto contro la stessa
# specifica a parole ma da un'altra parte dell'albero. È l'unico punto in cui i
# due lati si toccano davvero, e senza questa giunzione una divergenza — un
# campo rinominato, uno ``schema`` alzato da un lato solo, un tipo che si
# restringe — non si vedrebbe fino alla prima release pubblicata sul serio:
# ``check_for_update`` scarta un manifest illeggibile *in silenzio*, per scelta,
# e il sintomo sarebbe "nessuno riceve l'aggiornamento" senza niente da leggere.


def _roundtrip(repo: Path, apk: Path, out: Path, *extra: str) -> dict:
    """Genera il manifest con lo script vero e lo rilegge dal disco."""
    assert _run(repo, "0.7.0", *_manifest_argv(apk, out, *extra)) == 0
    return json.loads((out / "latest.json").read_text(encoding="utf-8"))


def test_the_two_sides_agree_on_the_schema_number() -> None:
    """Lo ``schema`` è il campo che fa scartare tutto il resto.

    Il client rifiuta in blocco un manifest che dichiara uno schema più alto di
    quello che conosce. Se qualcuno alza la costante solo da un lato, ogni
    release successiva risulta illeggibile a ogni telefono già installato — e
    l'unico segnale è una riga di log su un dispositivo che nessuno guarda.
    """
    assert release.MANIFEST_SCHEMA == update_check.MANIFEST_SCHEMA


def test_the_default_manifest_url_points_at_the_default_repo() -> None:
    """L'URL che il client interroga deve stare sul repo su cui lo script pubblica."""
    assert release.DEFAULT_REPO in update_check.DEFAULT_MANIFEST_URL
    assert update_check.DEFAULT_MANIFEST_URL.endswith(f"/{release.MANIFEST_NAME}")


def test_a_generated_manifest_survives_validation(repo: Path, apk: Path, tmp_path: Path) -> None:
    """Il validatore accetta il manifest e non ne butta via nessun campo."""
    manifest = _roundtrip(repo, apk, tmp_path / "out")

    validated = update_check.validate_manifest(manifest)

    assert validated is not None, "the real validator rejected a manifest release.py wrote"
    # Il validatore tiene *solo* i campi noti: se lo script ne scrivesse uno che
    # il client non conosce, sparirebbe qui invece che al primo update vero.
    assert set(validated) == set(manifest)
    assert validated == manifest


def test_a_generated_manifest_becomes_an_installable_update(
    repo: Path, apk: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """E dai gate del client esce un ``UpdateInfo`` con i valori dello script."""
    manifest = _roundtrip(repo, apk, tmp_path / "out")
    # Il device finge di stare alla release precedente (versionCode 8 -> 9).
    monkeypatch.setattr(update_check, "installed_version_code", lambda: 8)

    validated = update_check.validate_manifest(manifest)
    assert validated is not None
    info = update_check._installable(validated, install="test-install-id", language="it")

    assert info is not None, "a fresh manifest was validated but not offered as an update"
    assert info.version_code == 9
    assert info.version_name == "0.7.0"
    assert info.sha256 == hashlib.sha256(apk.read_bytes()).hexdigest()
    assert info.size == apk.stat().st_size
    assert info.apk_url == manifest["apk_url"]
    assert info.notes_url == manifest["notes_url"]
    # La lingua sceglie il sommario: ``--summary-it`` è "a" in ``_manifest_argv``.
    assert info.summary == manifest["summary_it"]
    assert info.critical is False


def test_the_critical_flag_survives_the_round_trip(
    repo: Path, apk: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--critical`` deve arrivare fino all'``UpdateInfo``: salta l'ondata.

    Salta l'ondata, non lo stop: la percentuale qui è 1, non 0. Il caso dello
    zero è l'altra metà della regola, in
    ``test_the_kill_switch_beats_critical_too``.
    """
    manifest = _roundtrip(repo, apk, tmp_path / "out", "--critical", "--rollout", "1")
    monkeypatch.setattr(update_check, "installed_version_code", lambda: 8)

    validated = update_check.validate_manifest(manifest)
    assert validated is not None
    info = update_check._installable(validated, install="test-install-id", language="it")

    assert info is not None, "a critical update must ignore the rollout wave"
    assert info.critical is True


def test_min_supported_code_from_the_script_gates_the_client(
    repo: Path, apk: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """La soglia scritta dallo script è quella che il client fa valere."""
    manifest = _roundtrip(repo, apk, tmp_path / "out", "--min-supported-code", "8")
    validated = update_check.validate_manifest(manifest)
    assert validated is not None

    monkeypatch.setattr(update_check, "installed_version_code", lambda: 7)
    assert update_check._installable(validated, install="id", language="it") is None

    monkeypatch.setattr(update_check, "installed_version_code", lambda: 8)
    assert update_check._installable(validated, install="id", language="it") is not None


def test_rollout_zero_is_the_kill_switch_the_client_honours(
    repo: Path, apk: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--rollout 0`` su un manifest ripubblicato deve fermare tutti.

    È la manovra d'emergenza documentata in ``publish-a-release.md``: si
    ricarica il manifest con rollout a zero e la release smette di essere
    offerta. Vale solo se lo zero dello script è lo zero del gate del client.
    """
    manifest = _roundtrip(repo, apk, tmp_path / "out", "--rollout", "0")
    monkeypatch.setattr(update_check, "installed_version_code", lambda: 8)

    validated = update_check.validate_manifest(manifest)
    assert validated is not None
    assert validated["rollout"] == 0
    for install in ("a", "b", "c", "d", "e", "f", "g", "h"):
        assert update_check._installable(validated, install=install, language="it") is None


def test_the_kill_switch_beats_critical_too(
    repo: Path, apk: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """E vale anche sulla release che il freno serve di più a fermare.

    La procedura documentata per riparare una build rotta è pubblicare la fix
    con ``--critical``. Se quella fix è a sua volta rotta, l'unico modo di
    fermarla è ripubblicare il manifest a ``--rollout 0``: uno zero che
    ``critical`` scavalcasse sarebbe un freno che non esiste.
    """
    manifest = _roundtrip(repo, apk, tmp_path / "out", "--rollout", "0", "--critical")
    monkeypatch.setattr(update_check, "installed_version_code", lambda: 8)

    validated = update_check.validate_manifest(manifest)
    assert validated is not None
    assert (validated["rollout"], validated["critical"]) == (0, True)
    for install in ("a", "b", "c", "d", "e", "f", "g", "h"):
        assert update_check._installable(validated, install=install, language="it") is None
