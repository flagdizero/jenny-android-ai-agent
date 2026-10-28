"""Il motore del lettore di un'app: uscita validata, guasti, rete, sandbox, letture.

Il motore e' quello delle micro app (``jenny/board/micro.py``), che il lettore
riusa; questi test lo provano dalla strada che gira davvero, ``run_reader``.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from jenny.apps import reader
from jenny.board import micro as mc
from jenny.board import model

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)

READER = {
    "every": 30,
    "fields": {
        "moisture": {"label": "Umidità", "unit": "%", "type": "number", "min": 0, "max": 100},
        "online": {"label": "Sensore", "type": "bool"},
    },
}
SUMMARY = {"primary": "moisture", "alarm": {"field": "moisture", "op": "<", "value": 20}}

GOOD = "def run(ctx):\n    return {'moisture': 38, 'online': True, '_source': 'hub, alle 14:01'}\n"


def _app(ws: Path, code: str, *, reader_spec: dict | None = None,
         summary: dict | None = None) -> Path:
    d = ws / "apps" / "acerello"
    d.mkdir(parents=True, exist_ok=True)
    (d / "app.json").write_text(json.dumps({
        "name": "Acerello", "description": "x",
        "reader": reader_spec or READER, "summary": summary or SUMMARY,
    }), encoding="utf-8")
    (d / "run.py").write_text(code, encoding="utf-8")
    return d


async def _run(ws: Path, *, now: datetime = NOW, force: bool = True) -> dict | None:
    return await reader.run_reader(ws, "acerello", now=now, force=force)


async def test_a_good_run_writes_a_reading_and_waits_its_turn(tmp_path) -> None:
    d = _app(tmp_path, GOOD)
    state = await _run(tmp_path, force=False)
    assert state["status"] == "ok"
    last = reader.read_readings(d)[-1]
    assert (last["moisture"], last["online"], last["_source"]) == (38, True, "hub, alle 14:01")
    # Non tocca di nuovo prima di trenta minuti.
    assert await _run(tmp_path, now=NOW + timedelta(minutes=10), force=False) is None


@pytest.mark.parametrize("ret, why", [
    ("{'moisture': None, 'online': True}", "did not return"),
    ("{'moisture': 140, 'online': True}", "outside its range"),
    ("{'moisture': '38', 'online': True}", "must be a number"),
    ("{'moisture': 38}", "did not return `online`"),
    ("{'moisture': 38, 'online': True, 'extra': 1}", "undeclared"),
    ("[38]", "must return one dict"),
])
async def test_a_wrong_output_is_an_error_not_a_value(tmp_path, ret, why) -> None:
    d = _app(tmp_path, f"def run(ctx):\n    return {ret}\n")
    state = await _run(tmp_path)
    assert state["status"] == "error" and why in state["lastError"], state
    assert reader.read_readings(d) == []


async def test_three_code_errors_in_a_row_break_it_and_back_off(tmp_path) -> None:
    _app(tmp_path, "def run(ctx):\n    raise KeyError('soil')\n")
    t = NOW
    for _ in range(3):
        state = await _run(tmp_path, now=t)
        t += timedelta(hours=1)
    assert state["status"] == "broken" and "KeyError" in state["lastError"]
    due = mc.next_run_at(READER, state)
    assert due - model.parse_iso(state["lastRunAt"]) == timedelta(minutes=30 * 8)


async def test_a_reader_error_shows_where_it_happened(tmp_path) -> None:
    """L'errore che arriva a Jenny e' quello vero, con la riga dell'app: non un
    finto SyntaxError del sandbox, non la sola cornice esterna."""
    _app(tmp_path, "def fetch():\n    raise RuntimeError('HTTP 429 from hub')\n"
                   "def run(ctx):\n    return fetch()\n")
    state = await _run(tmp_path)
    assert "RuntimeError: HTTP 429 from hub" in state["lastError"]
    assert "in fetch" in state["lastError"]
    assert "SyntaxError" not in state["lastError"]
    assert "During handling" not in state["lastError"]


async def test_the_network_down_is_not_a_broken_reader(tmp_path) -> None:
    _app(tmp_path, "def run(ctx):\n    raise ConnectionError('hub down')\n")
    for i in range(4):
        state = await _run(tmp_path, now=NOW + timedelta(hours=i))
    assert state["status"] == "unreachable" and int(state.get("failures") or 0) == 0


async def test_the_code_reads_but_cannot_write(tmp_path) -> None:
    page = tmp_path / "wikis" / "piante" / "wiki" / "acero.md"
    page.parent.mkdir(parents=True)
    page.write_text("# acero\n\n38\n", encoding="utf-8")
    other = page.with_name("x.md")
    _app(tmp_path, (
        "def run(ctx):\n"
        f"    text = open({str(page)!r}).read()\n"
        f"    open({str(other)!r}, 'w').write('x')\n"
        "    return {'moisture': 1, 'online': True}\n"
    ))
    state = await _run(tmp_path)
    assert state["status"] == "error" and "read-only" in state["lastError"], state
    assert not other.exists()


async def test_a_skill_module_is_importable_from_uses(tmp_path) -> None:
    scripts = tmp_path / "skills" / "waterbot" / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "probe_fake.py").write_text("def moisture(name):\n    return 41\n",
                                           encoding="utf-8")
    d = _app(tmp_path, "from probe_fake import moisture\n"
                       "def run(ctx):\n    return {'moisture': moisture('Acerello'), 'online': True}\n",
             reader_spec={**READER, "uses": "waterbot"})
    state = await _run(tmp_path)
    assert state["status"] == "ok", state
    assert reader.read_readings(d)[-1]["moisture"] == 41


async def test_http_goes_only_to_declared_hosts(tmp_path) -> None:
    _app(tmp_path, "def run(ctx):\n    ctx.http.get_json('https://evil.example.com/x')\n"
                   "    return {'moisture': 1, 'online': True}\n",
         reader_spec={**READER, "net": ["hub.example.com"]})
    state = await _run(tmp_path)
    assert state["status"] == "error" and "net list" in state["lastError"], state


ROWS_READER = {
    "every": 60,
    "fields": {"annunci": {"label": "Annunci nuovi", "type": "rows", "columns": {
        "titolo": {"label": "Titolo"}, "prezzo": {"label": "Prezzo", "type": "number", "unit": " €"},
    }}},
}
ROWS_SUMMARY = {"view": "list", "primary": "annunci"}


async def test_a_list_keeps_its_rows_and_counts_them(tmp_path) -> None:
    d = _app(tmp_path, "def run(ctx):\n    return {'annunci': [\n"
                       "        {'titolo': 'Bilocale via Roma', 'prezzo': 180000},\n"
                       "        {'titolo': 'Trilocale Pigneto', 'prezzo': 260000}]}\n",
             reader_spec=ROWS_READER, summary=ROWS_SUMMARY)
    state = await _run(tmp_path)
    assert state["status"] == "ok", state
    # Nello storico resta il conteggio, non le righe: e' su quello che vivono le attese.
    assert reader.read_readings(d)[-1]["annunci"] == 2
    latest = json.loads((reader.reader_dir(d) / reader.LATEST_FILE).read_text(encoding="utf-8"))
    assert latest["rows"]["annunci"][0] == {"titolo": "Bilocale via Roma", "prezzo": 180000}


@pytest.mark.parametrize("ret, why", [
    ("{'annunci': 'tre'}", "must be a list of rows"),
    ("{'annunci': [{'titolo': 'x'}]}", "missing `prezzo`"),
    ("{'annunci': [{'titolo': 'x', 'prezzo': '1'}]}", "`prezzo` must be a number"),
    ("{'annunci': [{'titolo': 'x', 'prezzo': 1, 'url': 'y'}]}", "undeclared columns"),
])
async def test_wrong_rows_are_an_error(tmp_path, ret, why) -> None:
    _app(tmp_path, f"def run(ctx):\n    return {ret}\n",
         reader_spec=ROWS_READER, summary=ROWS_SUMMARY)
    state = await _run(tmp_path)
    assert state["status"] == "error" and why in state["lastError"], state


# ── le regole pure ──────────────────────────────────────────────────────────

SPEC = {"title": "Acerello", **READER, **SUMMARY}


def test_spec_rules() -> None:
    assert mc.normalize_spec({**SPEC, "every": 1}, micro_id="m")["every"] == 15
    with pytest.raises(mc.MicroError):
        mc.normalize_spec({**SPEC, "fields": {}}, micro_id="m")
    with pytest.raises(mc.MicroError):
        mc.normalize_spec({**SPEC, "uses": ["a", "b"]}, micro_id="m")


def test_shapes_have_their_rules() -> None:
    with pytest.raises(mc.MicroError, match="needs a field of type rows"):
        mc.normalize_spec({**SPEC, "view": "table"}, micro_id="m")
    with pytest.raises(mc.MicroError, match="with a max"):
        mc.normalize_spec({**SPEC, "view": "progress", "fields": {
            "parole": {"label": "Parole", "type": "number"}}, "primary": "parole"},
            micro_id="m")
    ok = mc.normalize_spec({**SPEC, "view": "progress", "fields": {
        "parole": {"label": "Parole", "type": "number", "max": 80000}}, "primary": "parole",
        "alarm": None}, micro_id="m")
    assert ok["view"] == "progress"


def test_readings_are_compacted(tmp_path) -> None:
    n = mc._COMPACT_AFTER + 200
    for i in range(n):
        reader.append_readings(tmp_path, [{"moisture": i}], key=None,
                               now=NOW - timedelta(hours=n - i))
    kept = reader.read_readings(tmp_path)
    assert len(kept) <= mc._COMPACT_AFTER
    assert kept[-1]["moisture"] == n - 1
    # Prima delle ultime letture ne resta al piu' una al giorno.
    days = [r["ts"][:10] for r in kept[:10]]
    assert len(days) == len(set(days))


def test_a_year_of_daily_readings_survives_compaction(tmp_path) -> None:
    """Un andamento a 12 mesi nasce dalle letture del lettore stesso: fino al
    07/10/2026 se ne teneva una al giorno solo per 31 giorni."""
    n = 4 * 200  # ogni 6 ore per 200 giorni
    for i in range(n):
        reader.append_readings(tmp_path, [{"moisture": i % 100}], key=None,
                               now=NOW - timedelta(hours=6 * (n - i)))
    kept = reader.read_readings(tmp_path)
    oldest = datetime.fromisoformat(kept[0]["ts"])
    assert (NOW - oldest).days >= 195
    assert mc._COMPACT_AFTER > mc.KEEP_DAYS + mc.KEEP_RECENT


@pytest.mark.parametrize("line", [
    "H = {'CF-Access-Client-Secret': 'cfast_LvZajClsscVTjVjMZN1HjY'}",
    "TOKEN = 'ghp_aaaaaaaaaaaaaaaaaaaaaaaaaaaa'",
    "h = {'Authorization': 'Bearer abcdefghijklmnopqrstuvwx'}",
    "K = 'QWxhZGRpbjpvcGVuIHNlc2FtZQQWxhZGRpbjpvcGVuIHNlc2FtZQ'",
])
def test_a_credential_in_run_py_is_found_and_masked(line) -> None:
    assert mc.find_secrets(f"{line}\n" + GOOD)
    assert mc.redact(line).count("<redacted>") >= 1


def test_ordinary_code_is_not_a_credential() -> None:
    code = ("HUB = 'https://imh.example.com/v1/streams/waterbot/devices'\n"
            "DEVICE = 'wb-70123c'\n" + GOOD)
    assert mc.find_secrets(code) == []


def test_a_run_may_make_eight_requests(monkeypatch) -> None:
    """Quattro ETF, un prezzo ciascuno, con un ritentativo: con 3 non ci stavano."""
    import jenny.security.network as net

    monkeypatch.setattr(net, "validate_app_server_target", lambda url: (True, ""))
    http = mc.MicroHttp(["api.example.com"])
    for _ in range(mc.MAX_HTTP_REQUESTS):
        http._check("https://api.example.com/q")
    assert mc.MAX_HTTP_REQUESTS == 8
    with pytest.raises(PermissionError, match="at most 8 requests per run"):
        http._check("https://api.example.com/q")


def test_http_says_who_it_is_and_why_it_was_refused(monkeypatch) -> None:
    """Un 403 col suo motivo, e la richiesta col nome di Jenny.

    Wikimedia rifiuta ``python-httpx`` con «robot policy»: con solo «HTTP 403» il
    modello riprovava alla cieca.
    """
    import httpx

    import jenny.security.network as net

    sent = {}

    def handler(request: httpx.Request) -> httpx.Response:
        sent["ua"] = request.headers["user-agent"]
        return httpx.Response(403, text="Please respect our\n robot policy")

    def request(method, url, *, timeout, follow_redirects, **kw):
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            return client.request(method, url, **kw)

    monkeypatch.setattr(net, "validate_app_server_target", lambda url: (True, ""))
    monkeypatch.setattr(httpx, "request", request)
    with pytest.raises(RuntimeError) as e:
        mc.MicroHttp(["api.example.com"]).get_text("https://api.example.com/q")
    assert str(e.value) == "HTTP 403 from https://api.example.com/q: Please respect our robot policy"
    assert sent["ua"].startswith("Jenny/")


def test_a_user_agent_from_the_reader_replaces_jennys(monkeypatch) -> None:
    """Le maiuscole non contano: ``user-agent`` del lettore sostituisce, non affianca."""
    import httpx

    import jenny.security.network as net

    sent = {}

    def handler(request: httpx.Request) -> httpx.Response:
        sent["ua"] = request.headers.get_list("user-agent")
        return httpx.Response(200, text="ok")

    def request(method, url, *, timeout, follow_redirects, **kw):
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            return client.request(method, url, **kw)

    monkeypatch.setattr(net, "validate_app_server_target", lambda url: (True, ""))
    monkeypatch.setattr(httpx, "request", request)
    mc.MicroHttp(["api.example.com"]).get_text(
        "https://api.example.com/q", headers={"user-agent": "MyReader/1"},
    )
    assert sent["ua"] == ["MyReader/1"]
