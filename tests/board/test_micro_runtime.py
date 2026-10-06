"""Il motore delle micro app: contratto, approvazione per hash, guasti, letture."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from jenny.board import micro as mc

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)

SPEC = {
    "title": "Acerello",
    "card": "acero",
    "every": 30,
    "fields": {
        "moisture": {"label": "Umidità", "unit": "%", "type": "number", "min": 0, "max": 100},
        "online": {"label": "Sensore", "type": "bool"},
    },
    "primary": "moisture",
    "alarm": {"field": "moisture", "op": "<", "value": 20},
}


def _notebook(tmp_path: Path) -> tuple[Path, Path]:
    ws = tmp_path / "workspace"
    root = ws / "wikis" / "piante"
    (root / "wiki").mkdir(parents=True)
    (root / "board").mkdir()
    return ws, root


def _live(ws: Path, root: Path, code: str, spec: dict | None = None) -> mc.Micro:
    m = mc.write_micro(root, "m-acerello", spec or SPEC, code, draft=False)
    mc.write_approval(ws, root, m)
    return m


GOOD = "def run(ctx):\n    return {'moisture': 38, 'online': True, '_source': 'hub, alle 14:01'}\n"


async def test_a_good_run_writes_a_reading_and_the_view_reads_it(tmp_path) -> None:
    ws, root = _notebook(tmp_path)
    _live(ws, root, GOOD)
    state = await mc.run_micro(ws, root, "m-acerello", now=NOW)
    assert state["status"] == "ok"
    view = mc.micro_view(ws, root, "m-acerello", now=NOW)
    assert (view["state"], view["value"], view["source"]) == ("ok", 38, "hub, alle 14:01")
    assert view["extras"][0]["field"] == "online" and view["extras"][0]["value"] is True
    # Non tocca di nuovo prima di trenta minuti.
    assert await mc.run_micro(ws, root, "m-acerello", now=NOW + timedelta(minutes=10)) is None


async def test_the_runtime_computes_the_alarm_not_the_code(tmp_path) -> None:
    ws, root = _notebook(tmp_path)
    _live(ws, root, "def run(ctx):\n    return {'moisture': 12, 'online': True}\n")
    await mc.run_micro(ws, root, "m-acerello", now=NOW)
    assert mc.micro_view(ws, root, "m-acerello", now=NOW)["state"] == "alarm"


@pytest.mark.parametrize("ret, why", [
    ("{'moisture': None, 'online': True}", "did not return"),
    ("{'moisture': 140, 'online': True}", "outside its range"),
    ("{'moisture': '38', 'online': True}", "must be a number"),
    ("{'moisture': 38}", "did not return `online`"),
    ("{'moisture': 38, 'online': True, 'extra': 1}", "undeclared"),
    ("[38]", "must return a dict"),
])
async def test_a_wrong_output_is_an_error_not_a_value(tmp_path, ret, why) -> None:
    ws, root = _notebook(tmp_path)
    _live(ws, root, f"def run(ctx):\n    return {ret}\n")
    state = await mc.run_micro(ws, root, "m-acerello", now=NOW)
    assert state["status"] == "error" and why in state["lastError"]
    assert mc.read_readings(mc.micro_dir(root, "m-acerello")) == []


async def test_three_code_errors_in_a_row_break_it_and_back_off(tmp_path) -> None:
    ws, root = _notebook(tmp_path)
    _live(ws, root, "def run(ctx):\n    raise KeyError('soil')\n")
    t = NOW
    for _ in range(3):
        state = await mc.run_micro(ws, root, "m-acerello", now=t, force=True)
        t += timedelta(hours=1)
    assert state["status"] == "broken" and "KeyError" in state["lastError"]
    spec = mc.load_micro(root, "m-acerello").spec
    due = mc.next_run_at(spec, state)
    assert due - mc.model.parse_iso(state["lastRunAt"]) == timedelta(minutes=30 * 8)


async def test_the_network_down_is_not_a_broken_micro_app(tmp_path) -> None:
    ws, root = _notebook(tmp_path)
    code = "def run(ctx):\n    raise ConnectionError('hub down')\n"
    _live(ws, root, code)
    for i in range(4):
        state = await mc.run_micro(ws, root, "m-acerello", now=NOW + timedelta(hours=i))
    assert state["status"] == "unreachable" and int(state.get("failures") or 0) == 0


async def test_code_changed_by_hand_does_not_run(tmp_path) -> None:
    ws, root = _notebook(tmp_path)
    m = _live(ws, root, GOOD)
    (m.dir / "run.py").write_text(GOOD.replace("38", "99"), encoding="utf-8")
    assert await mc.run_micro(ws, root, "m-acerello", now=NOW) is None
    assert mc.micro_view(ws, root, "m-acerello", now=NOW)["state"] == "unapproved"
    # L'approvazione sta fuori dal workspace.
    assert not mc.approvals_path(ws).is_relative_to(ws)


async def test_the_code_reads_but_cannot_write(tmp_path) -> None:
    ws, root = _notebook(tmp_path)
    (root / "wiki" / "acero.md").write_text("# acero\n\n38\n", encoding="utf-8")
    code = (
        "def run(ctx):\n"
        f"    text = open({str(root / 'wiki' / 'acero.md')!r}).read()\n"
        f"    open({str(root / 'wiki' / 'x.md')!r}, 'w').write('x')\n"
        "    return {'moisture': 1, 'online': True}\n"
    )
    _live(ws, root, code)
    state = await mc.run_micro(ws, root, "m-acerello", now=NOW)
    assert state["status"] == "error" and "read-only" in state["lastError"]
    assert not (root / "wiki" / "x.md").exists()


async def test_a_skill_module_is_importable_from_uses(tmp_path) -> None:
    ws, root = _notebook(tmp_path)
    scripts = ws / "skills" / "waterbot" / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "probe_fake.py").write_text(
        "def moisture(name):\n    return 41\n", encoding="utf-8")
    code = ("from probe_fake import moisture\n"
            "def run(ctx):\n    return {'moisture': moisture('Acerello'), 'online': True}\n")
    _live(ws, root, code, {**SPEC, "uses": ["waterbot"]})
    state = await mc.run_micro(ws, root, "m-acerello", now=NOW)
    assert state["status"] == "ok", state
    assert mc.micro_view(ws, root, "m-acerello", now=NOW)["value"] == 41


async def test_http_goes_only_to_declared_hosts(tmp_path) -> None:
    ws, root = _notebook(tmp_path)
    code = ("def run(ctx):\n    ctx.http.get_json('https://evil.example.com/x')\n"
            "    return {'moisture': 1, 'online': True}\n")
    _live(ws, root, code, {**SPEC, "net": ["hub.example.com"]})
    state = await mc.run_micro(ws, root, "m-acerello", now=NOW)
    assert state["status"] == "error" and "not in this micro app's net list" in state["lastError"]


def test_the_contract_ignores_the_code_and_the_labels() -> None:
    a = mc.normalize_spec(SPEC, micro_id="m-a")
    b = mc.normalize_spec({**SPEC, "title": "Altro", "every": 60,
                           "fields": {**SPEC["fields"],
                                      "online": {"label": "Online?", "type": "bool"}}},
                          micro_id="m-a")
    assert mc.contract_of(a) == mc.contract_of(b)
    c = mc.normalize_spec({**SPEC, "net": ["hub.example.com"]}, micro_id="m-a")
    assert mc.contract_of(a) != mc.contract_of(c)


def test_spec_rules() -> None:
    assert mc.normalize_spec({**SPEC, "every": 1}, micro_id="m")["every"] == 15
    with pytest.raises(mc.MicroError):
        mc.normalize_spec({**SPEC, "fields": {}}, micro_id="m")
    with pytest.raises(mc.MicroError):
        mc.normalize_spec({**SPEC, "uses": ["a", "b"]}, micro_id="m")


def test_readings_are_compacted(tmp_path) -> None:
    d = tmp_path / "m"
    for i in range(400):
        mc.append_reading(d, {"moisture": i}, now=NOW - timedelta(hours=400 - i))
    kept = mc.read_readings(d)
    assert len(kept) <= mc._COMPACT_AFTER
    assert kept[-1]["moisture"] == 399
    # Prima delle ultime letture ne resta al piu' una al giorno.
    compacted = kept[:-(len(kept) - mc.KEEP_DAYS - mc.KEEP_RECENT)] if len(kept) > 79 else kept
    days = [r["ts"][:10] for r in compacted[:10]]
    assert len(days) == len(set(days))


ROWS_SPEC = {
    "title": "Annunci", "view": "list",
    "fields": {"annunci": {"label": "Annunci nuovi", "type": "rows", "columns": {
        "titolo": {"label": "Titolo"}, "prezzo": {"label": "Prezzo", "type": "number", "unit": " €"},
    }}},
}


async def test_a_list_keeps_its_rows_and_counts_them(tmp_path) -> None:
    ws, root = _notebook(tmp_path)
    code = ("def run(ctx):\n    return {'annunci': [\n"
            "        {'titolo': 'Bilocale via Roma', 'prezzo': 180000},\n"
            "        {'titolo': 'Trilocale Pigneto', 'prezzo': 260000}]}\n")
    m = _live(ws, root, code, ROWS_SPEC)
    assert m.spec["primary"] == "annunci" and m.spec["view"] == "list"
    await mc.run_micro(ws, root, "m-acerello", now=NOW)
    view = mc.micro_view(ws, root, "m-acerello", now=NOW)
    assert view["view"] == "list" and view["value"] == 2
    assert view["rows"][0] == {"titolo": "Bilocale via Roma", "prezzo": 180000}
    assert [c["field"] for c in view["columns"]] == ["titolo", "prezzo"]
    # Nello storico resta il conteggio, non le righe: e' su quello che vivono le attese.
    assert mc.read_readings(m.dir)[-1]["annunci"] == 2
    assert mc.micro_field_value(root, "m-acerello", "annunci", now=NOW) == (2, True)


@pytest.mark.parametrize("ret, why", [
    ("{'annunci': 'tre'}", "must be a list of rows"),
    ("{'annunci': [{'titolo': 'x'}]}", "missing `prezzo`"),
    ("{'annunci': [{'titolo': 'x', 'prezzo': '1'}]}", "`prezzo` must be a number"),
    ("{'annunci': [{'titolo': 'x', 'prezzo': 1, 'url': 'y'}]}", "undeclared columns"),
])
async def test_wrong_rows_are_an_error(tmp_path, ret, why) -> None:
    ws, root = _notebook(tmp_path)
    _live(ws, root, f"def run(ctx):\n    return {ret}\n", ROWS_SPEC)
    state = await mc.run_micro(ws, root, "m-acerello", now=NOW)
    assert state["status"] == "error" and why in state["lastError"]


def test_shapes_have_their_rules() -> None:
    with pytest.raises(mc.MicroError, match="needs a field of type rows"):
        mc.normalize_spec({**SPEC, "view": "table"}, micro_id="m")
    with pytest.raises(mc.MicroError, match="with a max"):
        mc.normalize_spec({**SPEC, "view": "progress", "fields": {
            "parole": {"label": "Parole", "type": "number"}}}, micro_id="m")
    ok = mc.normalize_spec({**SPEC, "view": "progress", "fields": {
        "parole": {"label": "Parole", "type": "number", "max": 80000}}}, micro_id="m")
    assert ok["view"] == "progress"
    # Cambiare forma cambia quello che l'utente vede: e' un altro contratto.
    chips = mc.normalize_spec({**SPEC, "view": "chips"}, micro_id="m")
    assert mc.contract_of(chips) != mc.contract_of(mc.normalize_spec(SPEC, micro_id="m"))


@pytest.mark.parametrize("line", [
    "H = {'CF-Access-Client-Secret': 'cfast_LvZajClsscVTjVjMZN1HjY'}",
    "TOKEN = 'ghp_aaaaaaaaaaaaaaaaaaaaaaaaaaaa'",
    "h = {'Authorization': 'Bearer abcdefghijklmnopqrstuvwx'}",
    "K = 'QWxhZGRpbjpvcGVuIHNlc2FtZQQWxhZGRpbjpvcGVuIHNlc2FtZQ'",
])
def test_a_credential_in_run_py_is_refused(tmp_path, line) -> None:
    _ws, root = _notebook(tmp_path)
    with pytest.raises(mc.MicroError, match="credential"):
        mc.write_micro(root, "m-x", SPEC, f"{line}\n" + GOOD, draft=True)
    assert mc.redact(line).count("<redacted>") >= 1


def test_ordinary_code_is_not_a_credential() -> None:
    code = ("HUB = 'https://imh.example.com/v1/streams/waterbot/devices'\n"
            "DEVICE = 'wb-70123c'\n" + GOOD)
    assert mc.find_secrets(code) == []


def test_the_newest_reading_wins_across_time_zones(tmp_path) -> None:
    """L'orologio scrive con l'ora locale: come testo ``13:30+02:00`` batte ``12:00+00:00``."""
    ws, root = _notebook(tmp_path)
    m = _live(ws, root, GOOD)
    rome = timezone(timedelta(hours=2))
    first = mc.append_reading(m.dir, {"moisture": 40, "online": True},
                              now=datetime(2026, 10, 5, 13, 7, tzinfo=rome))
    assert first["ts"] == "2026-10-05T11:07:00+00:00"
    # Una riga scritta prima della correzione, ancora col fuso locale.
    with (m.dir / mc.READINGS_FILE).open("a", encoding="utf-8") as fh:
        fh.write('{"id": "x", "ts": "2026-10-05T13:30:00+02:00", "moisture": 35}\n')
    mc.append_reading(m.dir, {"moisture": 31, "online": True}, now=NOW)
    assert mc.micro_field_value(root, "m-acerello", "moisture", now=NOW) == (31, True)
