"""Un giro di uno scenario sull'app vera, e la suite intorno.

L'ordine di un giro, e perche':

1. sessione nuova (``/new``) — una sessione che ha visto un comportamento lo
   imita, quindi ogni giro parte da zero. ``/new`` archivia in sottofondo, con una
   chiamata al modello, la conversazione buttata: si aspetta che la voce arrivi
   in ``history.jsonl`` **prima** di scrivere i file di prova, o l'archivio
   scriverebbe sopra;
2. i file di base rimessi com'erano a inizio suite, poi quelli dello scenario;
3. i turni, la lettura della sessione e dei file;
4. la pulizia di quel che il giro ha acceso: subagent, job di cron, quaderno.

La suite fotografa il workspace a inizio e lo rimette a fine, anche dopo un
errore o un Ctrl-C, riavviando l'app: e' il riavvio che ricarica le sessioni e
riestrae i template modificati dalla taratura.
"""

from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass, field
from typing import Any

from .device import PERSONAL_CHAT, Device, DeviceError
from .model import Check, Scenario, make_nonce, observation_from_rows
from .scenarios import CROSS_CUTTING

#: Riscritti com'erano prima di ogni giro: i giri non si passano niente.
RESET_FILES = ("memory/MEMORY.md", "USER.md", "HEARTBEAT.md", "memory/history.jsonl",
               "memory/.cursor", "memory/.dream_cursor")

_FILLER_FACTS = (
    "The user's sister Ilaria lives in Trieste and teaches chemistry.",
    "The user prefers trains to planes for trips under five hours.",
    "The user's car is a grey hatchback bought in 2021.",
    "The user plays five-a-side football on Wednesday evenings.",
    "The user keeps a vegetable plot with tomatoes, basil and courgettes.",
    "The user's favourite author is Italo Calvino.",
    "The user drinks green tea in the afternoon, never coffee after 4pm.",
    "The user is saving for a trip to Japan next spring.",
    "The user's landlord is slow to answer about repairs.",
    "The user is learning to play the ukulele.",
)


@dataclass
class RunResult:
    scenario: str
    model: str
    calibrated: bool
    checks: list[Check]
    replies: list[str]
    tool_calls: list[dict[str, Any]]
    nonces: dict[str, str]
    cost: dict[str, int] = field(default_factory=dict)
    seconds: float = 0.0
    error: str = ""


class Runner:
    def __init__(self, device: Device, *, seed: int | None = None, log=print) -> None:
        self.device = device
        self.rng = random.Random(seed)
        self.log = log
        self.base: dict[str, str | None] = {}
        self.jobs_at_start: set[str] = set()
        self.active: Scenario | None = None
        self.originals: dict[str, str | None] = {}

    # -- suite ------------------------------------------------------------------

    def begin(self) -> None:
        dev = self.device
        dev.forward()
        dev.wait_ready()
        if dev.has_snapshot():
            raise DeviceError("a snapshot from an earlier run is still there: "
                              "run `behaviour_check.py --restore` first")
        dev.snapshot()
        # I job di sistema (Dream, giardiniere, heartbeat, promemoria) girerebbero
        # in mezzo agli scenari: un Dream a meta' giro riscrive MEMORY.md. Si
        # spostano di un giorno ad app ferma; la fotografia rimette lo store.
        dev.adb("shell", "am", "force-stop", "com.flagdizero.jenny")
        dev.shift_system_jobs(delay_ms=24 * 3600 * 1000)
        dev.restart_app()
        self.base = {path: dev.read(path) for path in RESET_FILES}
        self.jobs_at_start = self._job_ids()

    def end(self) -> None:
        try:
            self._stop_subagents()
        finally:
            self.device.restore()
            jobs = self._job_ids()
            if jobs != self.jobs_at_start:
                self.log(f"WARNING: cron jobs differ after restore: "
                         f"{sorted(jobs ^ self.jobs_at_start)}")
            self.device.unforward()

    # -- un giro -----------------------------------------------------------------

    def run(self, scenario: Scenario, model: str, *, calibrated: bool = False) -> RunResult:
        dev = self.device
        nonces = {key: make_nonce(self.rng) for key in scenario.nonces}
        start = time.monotonic()
        result = RunResult(scenario.name, model, calibrated, [], [], [], nonces)
        project = None
        created: list[str] = []
        jobs_before = self._job_ids()
        usage_before = self._usage()
        try:
            if scenario.chat == "personal":
                self._fresh_personal_session()
            self._reset_files()
            created = self._write_fixtures(scenario, nonces)
            if scenario.chat == "notebook":
                project = f"bh-{nonces[scenario.nonces[0]]}"
                dev.rpc("project.create", {"name": project, "seed": "Le piante del terrazzo."})
                chat_id = f"project:{project}"
                session_file = f"sessions/project_{project}.jsonl"
            else:
                chat_id = PERSONAL_CHAT
                session_file = "sessions/unified_default.jsonl"
            replies, timed_out = [], False
            last_turn_start = 0
            for index, turn in enumerate(scenario.turns):
                if index and scenario.wait_subagents:
                    self._wait_subagents_done(session_file)
                last_turn_start = len(_rows(dev.read(session_file) or ""))
                outcome = dev.turn(scenario.render(turn, nonces), chat_id=chat_id)
                replies.append(outcome.reply)
                timed_out = timed_out or outcome.timed_out
            if scenario.final_reply_after_subagents:
                self._wait_subagents_done(session_file)
                final = [r for r in _rows(dev.read(session_file) or "")
                         if r.get("role") == "assistant" and isinstance(r.get("content"), str)
                         and r["content"].strip()]
                if final:
                    replies.append(final[-1]["content"])
            rows = _rows(dev.read(session_file) or "")
            extra: dict[str, Any] = {
                "last_turn_calls": observation_from_rows(rows[last_turn_start:], []).tool_calls,
            }
            if scenario.archive_after:
                extra["archive_entry"] = self._archive_now()
            if scenario.dream_after:
                before_sessions = set(dev.listdir("sessions"))
                extra.update(self._dream_now())
                # Le chiamate di Dream, per capire un giro rosso senza rifarlo.
                new = sorted(f for f in set(dev.listdir("sessions")) - before_sessions
                             if f.startswith("dream"))
                dream_rows = [r for f in new for r in _rows(dev.read(f"sessions/{f}") or "")]
                rows = rows + dream_rows
                extra["dream_sessions"] = new
            if scenario.heartbeat_after:
                hb_rows, ran = self._heartbeat_now()
                extra["heartbeat_ran"] = ran
                rows = hb_rows
            files = {p: dev.read(scenario.render(p, nonces)) for p in scenario.read_after}
            obs = observation_from_rows(rows, replies, files=files, timed_out=timed_out)
            obs.extra = extra
            result.replies = replies
            result.tool_calls = [{"name": c.name, "args": c.args, "result": c.result[:300]}
                                 for c in obs.tool_calls]
            result.checks = [*scenario.check(obs, nonces),
                             *(check(obs) for check in CROSS_CUTTING)]
        except Exception as exc:  # noqa: BLE001 — un giro rotto si scrive nel report
            result.error = f"{type(exc).__name__}: {exc}"
        finally:
            self._cleanup(jobs_before, project, created)
            result.cost = _usage_delta(usage_before, self._usage())
            result.seconds = round(time.monotonic() - start, 1)
        return result

    # -- taratura ----------------------------------------------------------------

    def apply_calibration(self, scenario: Scenario) -> None:
        self.active = scenario
        for edit in scenario.calibration:
            text = self.device.read(edit.path)
            if text is None or edit.old not in text:
                raise DeviceError(f"calibration: {edit.path} does not contain the line to remove")
            self.originals.setdefault(edit.path, text)
            self.device.write(edit.path, text.replace(edit.old, edit.new))

    def undo_calibration(self) -> None:
        for path, text in self.originals.items():
            if text is not None:
                self.device.write(path, text)
        self.originals.clear()
        self.active = None

    # -- pezzi -------------------------------------------------------------------

    def _fresh_personal_session(self) -> None:
        dev = self.device
        before = _max_cursor(dev.read("memory/history.jsonl") or "")
        rows = _rows(dev.read("sessions/unified_default.jsonl") or "")
        dev.turn("/new")
        if any(r.get("role") in ("user", "assistant") and not r.get("_command") for r in rows):
            self._wait_for_entry(before)

    def _wait_for_entry(self, before: int, timeout: float = 120.0) -> str:
        """La voce che ``/new`` sta scrivendo in sottofondo, quando arriva."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            text = self.device.read("memory/history.jsonl") or ""
            if _max_cursor(text) > before:
                time.sleep(1.5)  # la scrittura del cursore segue di poco quella della voce
                lines = [json.loads(line) for line in text.splitlines() if line.strip()]
                return "\n".join(str(e.get("content")) for e in lines
                                 if int(e.get("cursor") or 0) > before)
            time.sleep(2)
        return ""

    def _archive_now(self) -> str:
        before = _max_cursor(self.device.read("memory/history.jsonl") or "")
        self.device.turn("/new")
        return self._wait_for_entry(before)

    def _dream_now(self, timeout: float = 420.0) -> dict[str, Any]:
        dev = self.device
        before = (dev.read("memory/.dream_cursor") or "0").strip() or "0"
        dev.turn("/dream", timeout=60)
        deadline = time.monotonic() + timeout
        after = before
        while time.monotonic() < deadline:
            after = (dev.read("memory/.dream_cursor") or "0").strip() or "0"
            if after != before:
                time.sleep(5)  # Dream scrive i file prima del cursore, ma il giro chiude dopo
                break
            time.sleep(5)
        return {"dream_cursor_before": before, "dream_cursor_after": after}

    def _heartbeat_now(self, timeout: float = 420.0) -> tuple[list[dict[str, Any]], bool]:
        """Un giro dell'heartbeat, forzato dallo store, e le sue righe di sessione.

        Lo store si tocca ad app ferma, e il riavvio riestrae i template: una
        taratura in corso si riapplica subito dopo.
        """
        dev = self.device
        started = time.strftime("%Y-%m-%dT%H:%M:%S")
        dev.adb("shell", "am", "force-stop", "com.flagdizero.jenny")
        before = dev.shift_system_jobs(only=("heartbeat",)).get("heartbeat", 0)
        dev.restart_app()
        self._reapply_calibration()
        deadline = time.monotonic() + timeout
        ran = False
        while time.monotonic() < deadline:
            if dev.job_last_run("heartbeat") > before:
                ran = True
                time.sleep(3)
                break
            time.sleep(5)
        # Rimesso lontano, o tornerebbe a girare fra mezz'ora in mezzo a un altro scenario.
        dev.adb("shell", "am", "force-stop", "com.flagdizero.jenny")
        rows = [r for r in _rows(dev.read("sessions/heartbeat.jsonl") or "")
                if str(r.get("timestamp") or "") >= started]
        dev.shift_system_jobs(delay_ms=24 * 3600 * 1000, only=("heartbeat",))
        dev.restart_app()
        self._reapply_calibration()
        return rows, ran

    def _reapply_calibration(self) -> None:
        for path, original in self.originals.items():
            current = self.device.read(path)
            if current == original and self.active is not None:
                for edit in self.active.calibration:
                    if edit.path == path:
                        current = (current or "").replace(edit.old, edit.new)
                self.device.write(path, current or "")

    def _reset_files(self) -> None:
        for path, text in self.base.items():
            if text is None:
                self.device.remove(path)
            else:
                self.device.write(path, text)

    def _write_fixtures(self, scenario: Scenario, nonces: dict[str, str]) -> list[str]:
        dev = self.device
        created = []
        for raw_path, raw_text in scenario.files.items():
            path = scenario.render(raw_path, nonces)
            text = scenario.render(raw_text, nonces).replace("{filler}", self._filler())
            if dev.read(path) is None:
                created.append(path)
            dev.write(path, text)
        # Sopra il pavimento che ``/new`` ha messo alla sessione (``_history_floor``)
        # e sopra il cursore del consolidatore: sotto, una voce resterebbe fuori
        # dal prompt (``# Recent History``) e lo scenario misurerebbe altro.
        cursor = max(_max_cursor(dev.read("memory/history.jsonl") or ""),
                     _int((dev.read("memory/.cursor") or "").strip()),
                     _history_floor(dev.read("sessions/unified_default.jsonl") or ""))
        last = cursor
        for raw_path, raw_text in scenario.append.items():
            path = scenario.render(raw_path, nonces)
            text = scenario.render(raw_text, nonces)
            for k in range(1, 10):
                if "{cursor+%d}" % k in text:
                    last = cursor + k
                text = text.replace('"{cursor+%d}"' % k, str(cursor + k))
            dev.write(path, (dev.read(path) or "") + text)
        if last != cursor:
            dev.write("memory/.cursor", str(last))
        return created

    def _filler(self) -> str:
        """Un MEMORY.md oltre il tetto di Dream (``memoryBudgetChars``, 3.000 di default)."""
        lines = [f"- {fact} (note {i})" for i in range(6) for fact in _FILLER_FACTS]
        return "\n".join(lines) + "\n"

    def _cleanup(self, jobs_before: set[str], project: str | None, created: list[str]) -> None:
        dev = self.device
        try:
            self._stop_subagents()
            for job in self._job_ids() - jobs_before:
                dev.remove_cron_job(job)
            if project:
                self._delete_project(project)
            for path in created:
                dev.remove(path)
        except Exception as exc:  # noqa: BLE001
            self.log(f"  cleanup: {exc}")

    def _wait_subagents_done(self, session_file: str, timeout: float = 300.0) -> None:
        """Fino a quando nessun subagent gira e la sessione resta ferma per 15 s.

        La sessione ferma dice che anche il turno d'annuncio, che parte da solo
        quando il subagent finisce, si e' chiuso.
        """
        from .device import _running_subagents

        deadline = time.monotonic() + timeout
        last, quiet_since = None, time.monotonic()
        while time.monotonic() < deadline:
            try:
                running = _running_subagents(self.device.http_get("/api/subagents"))
            except Exception:  # noqa: BLE001
                running = []
            current = self.device.mtime_size(session_file)
            if current != last:
                last, quiet_since = current, time.monotonic()
            if not running and time.monotonic() - quiet_since > 15:
                return
            time.sleep(3)
        self.log("  a subagent did not finish in time")

    def _stop_subagents(self, timeout: float = 90.0) -> None:
        """Ferma i subagent del giro e aspetta che siano fermi davvero.

        Uno ancora vivo scrive nel giro dopo: il suo annuncio arriva nella
        sessione nuova, e i suoi token finiscono nel costo di un altro scenario.
        """
        from .device import _running_subagents

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.device.cancel_subagents()
            try:
                running = _running_subagents(self.device.http_get("/api/subagents"))
            except Exception:  # noqa: BLE001
                return
            if not running:
                return
            time.sleep(3)
        self.log("  cleanup: a subagent is still running")

    def _delete_project(self, name: str, timeout: float = 90.0) -> None:
        deadline = time.monotonic() + timeout
        while True:
            try:
                self.device.rpc("project.delete", {"name": name})
                return
            except DeviceError as exc:
                if "conflict" not in str(exc) or time.monotonic() > deadline:
                    raise
                time.sleep(3)

    def _job_ids(self) -> set[str]:
        try:
            out = self.device.http_get("/api/webui/cron")
        except Exception:  # noqa: BLE001
            return set()
        jobs = out.get("jobs") if isinstance(out, dict) else out
        return {str(j.get("id")) for j in jobs or [] if isinstance(j, dict) and j.get("id")}

    def _usage(self) -> dict[str, int]:
        try:
            state = json.loads(self.device.read(".jenny/webui/token-usage.json") or "{}")
            day = state["days"][sorted(state["days"])[-1]]
        except (KeyError, IndexError, json.JSONDecodeError):
            return {}
        return {k: int(day.get(k) or 0) for k in ("prompt_tokens", "cached_tokens",
                                                   "completion_tokens", "requests")}


def _rows(text: str) -> list[dict[str, Any]]:
    out = []
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and row.get("_type") != "metadata":
            out.append(row)
    return out


def _max_cursor(text: str) -> int:
    best = 0
    for line in text.splitlines():
        try:
            best = max(best, int(json.loads(line).get("cursor") or 0))
        except (json.JSONDecodeError, ValueError, TypeError, AttributeError):
            continue
    return best


def _int(text: str) -> int:
    try:
        return int(text)
    except ValueError:
        return 0


def _history_floor(session_text: str) -> int:
    first = session_text.split("\n", 1)[0]
    try:
        meta = json.loads(first)
    except json.JSONDecodeError:
        return 0
    return _int(str((meta.get("metadata") or {}).get("_history_floor") or 0))


def _usage_delta(before: dict[str, int], after: dict[str, int]) -> dict[str, int]:
    return {k: after.get(k, 0) - before.get(k, 0) for k in after}


def summarize(results: list[RunResult]) -> dict[str, Any]:
    """Il tasso di ogni proprieta', per scenario, modello e taratura."""
    table: dict[str, dict[str, Any]] = {}
    for r in results:
        key = f"{r.scenario}|{r.model}|{'calibrated' if r.calibrated else 'normal'}"
        row = table.setdefault(key, {"runs": 0, "errors": 0, "checks": {}, "tokens": 0})
        row["runs"] += 1
        row["tokens"] += r.cost.get("prompt_tokens", 0) - r.cost.get("cached_tokens", 0)
        if r.error:
            row["errors"] += 1
            continue
        for c in r.checks:
            passed, total = row["checks"].get(c.name, (0, 0))
            row["checks"][c.name] = (passed + int(c.ok), total + 1)
    return table
