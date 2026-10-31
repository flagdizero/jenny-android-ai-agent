"""L'app installata sull'emulatore, vista dal Mac: file, turni, impostazioni.

Tutto passa da ``adb`` (``run-as``, quindi solo una build debug) e dal gateway
dell'app su ``127.0.0.1:18790`` attraverso ``adb forward``. La chiave del
provider resta sul dispositivo: da ``config.json`` si legge solo il segreto della
WebUI, lo stesso che la WebUI usa per parlare al gateway.
"""

from __future__ import annotations

import asyncio
import io
import json
import subprocess
import tarfile
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from typing import Any

PACKAGE = "com.flagdizero.jenny"
WORKSPACE = "files/workspace"
GATEWAY = "http://127.0.0.1:18790"
PERSONAL_CHAT = "default"

#: Quel che uno scenario puo' cambiare, fotografato a inizio suite e rimesso a
#: fine suite. Le sessioni si rimettono a app ferma: il gateway le tiene in
#: memoria e un file riscritto sotto i suoi piedi verrebbe sovrascritto.
SNAPSHOT_PATHS = ("memory", "USER.md", "SOUL.md", "HEARTBEAT.md", "AGENTS.md", "skills", "cron",
                  "sessions", "wikis", ".jenny/staged")
SNAPSHOT_FILE = "files/behaviour_snapshot.tar"


class DeviceError(RuntimeError):
    pass


@dataclass
class TurnResult:
    """Un turno visto dal canale: il testo che l'utente avrebbe letto, e i tempi."""

    reply: str
    frames: list[dict[str, Any]] = field(default_factory=list)
    seconds: float = 0.0
    timed_out: bool = False


class Device:
    def __init__(self, serial: str = "emulator-5554") -> None:
        self.serial = serial
        self._secret: str | None = None

    # -- adb ------------------------------------------------------------------

    def adb(self, *args: str, input_bytes: bytes | None = None, check: bool = True) -> str:
        proc = subprocess.run(["adb", "-s", self.serial, *args], input=input_bytes,
                              capture_output=True, check=False)
        if check and proc.returncode != 0:
            raise DeviceError(f"adb {' '.join(args)}: {proc.stderr.decode(errors='replace')}")
        return proc.stdout.decode("utf-8", errors="replace")

    def sh(self, command: str, *, input_bytes: bytes | None = None, check: bool = True) -> str:
        """Un comando come l'utente dell'app, con la cartella ``files/`` come base."""
        quoted = command.replace("'", "'\\''")
        return self.adb("shell", f"run-as {PACKAGE} sh -c '{quoted}'",
                        input_bytes=input_bytes, check=check)

    def read(self, rel: str) -> str | None:
        """Un file del workspace, o ``None`` se non c'e'."""
        path = f"{WORKSPACE}/{rel}"
        out = self.sh(f"if [ -f {path} ]; then cat {path}; else printf __MISSING__; fi",
                      check=False)
        return None if out == "__MISSING__" else out

    def write(self, rel: str, text: str) -> None:
        directory = rel.rsplit("/", 1)[0] if "/" in rel else ""
        if directory:
            self.sh(f"mkdir -p {WORKSPACE}/{directory}")
        self.sh(f"cat > {WORKSPACE}/{rel}", input_bytes=text.encode("utf-8"))

    def remove(self, rel: str) -> None:
        self.sh(f"rm -rf {WORKSPACE}/{rel}", check=False)

    def write_many(self, files: dict[str, str]) -> None:
        """Molti file in un colpo solo: un tar sullo stdin di ``run-as``.

        Un ``write`` per file costa un ``adb shell``; i ~450 file d'archivio di
        uno scenario di recupero, uno alla volta, erano minuti per giro.
        """
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tar:
            for rel, text in files.items():
                data = text.encode("utf-8")
                info = tarfile.TarInfo(rel)
                info.size = len(data)
                info.mode = 0o600
                info.mtime = int(time.time())
                tar.addfile(info, io.BytesIO(data))
        self.sh(f"cd {WORKSPACE} && tar -xf -", input_bytes=buf.getvalue())

    def remove_many(self, rels: list[str], *, chunk: int = 100) -> None:
        for i in range(0, len(rels), chunk):
            paths = " ".join(f"{WORKSPACE}/{rel}" for rel in rels[i:i + chunk])
            self.sh(f"rm -rf {paths}", check=False)

    def listdir(self, rel: str) -> list[str]:
        out = self.sh(f"ls -1 {WORKSPACE}/{rel} 2>/dev/null", check=False)
        return [line for line in out.splitlines() if line]

    def mtime_size(self, rel: str) -> tuple[str, str]:
        out = self.sh(f"stat -c '%Y %s' {WORKSPACE}/{rel} 2>/dev/null", check=False).split()
        return (out[0], out[1]) if len(out) == 2 else ("", "")

    # -- app ------------------------------------------------------------------

    def forward(self) -> None:
        self.adb("forward", "tcp:18790", "tcp:18790")

    def unforward(self) -> None:
        self.adb("forward", "--remove", "tcp:18790", check=False)

    def restart_app(self, *, wait: float = 90.0) -> None:
        self.adb("shell", "am", "force-stop", PACKAGE)
        self.adb("shell", "monkey", "-p", PACKAGE, "-c", "android.intent.category.LAUNCHER",
                 "1", check=False)
        self.forward()
        self._secret = None
        self.wait_ready(wait)

    def wait_ready(self, timeout: float = 90.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                self.http_get("/webui/bootstrap")
                return
            except (urllib.error.URLError, OSError, DeviceError, json.JSONDecodeError):
                time.sleep(2)
        raise DeviceError("the gateway did not come up")

    def secret(self) -> str:
        if self._secret is None:
            cfg = json.loads(self.read("config.json") or "{}")
            found = _find_key(cfg, ("tokenIssueSecret", "token_issue_secret"))
            if not found:
                raise DeviceError("no WebUI secret in config.json")
            self._secret = found
        return self._secret

    # -- snapshot -------------------------------------------------------------

    def snapshot(self) -> None:
        present = [p for p in SNAPSHOT_PATHS
                   if self.sh(f"test -e {WORKSPACE}/{p} && echo y", check=False).strip() == "y"]
        self.sh(f"cd {WORKSPACE} && tar cf ../../{SNAPSHOT_FILE} {' '.join(present)}")
        listing = self.sh(f"tar tf {SNAPSHOT_FILE} | head -1", check=False)
        if not listing.strip():
            raise DeviceError("the snapshot is empty")

    def has_snapshot(self) -> bool:
        return self.sh(f"test -s {SNAPSHOT_FILE} && echo y", check=False).strip() == "y"

    def restore(self) -> None:
        """Rimette la fotografia ad app ferma, poi la riavvia."""
        if not self.has_snapshot():
            raise DeviceError("no snapshot to restore")
        self.adb("shell", "am", "force-stop", PACKAGE)
        paths = " ".join(SNAPSHOT_PATHS)
        self.sh(f"cd {WORKSPACE} && rm -rf {paths} && tar xf ../../{SNAPSHOT_FILE}")
        self.sh(f"rm -f {SNAPSHOT_FILE}")
        self.restart_app()

    # -- job di sistema --------------------------------------------------------

    def shift_system_jobs(self, *, delay_ms: int | None = None,
                          only: tuple[str, ...] = ()) -> dict[str, int]:
        """Sposta la prossima esecuzione dei job di sistema (``payload.kind == system_event``).

        Non c'e' un «metti in pausa» per loro (la route risponde 409 ``protected``),
        quindi si usa la leva dello store: ``state.nextRunAtMs``. Con *delay_ms*
        ``None`` si porta al passato (esecuzione al prossimo risveglio del
        servizio, fino a 5 minuti). Va fatto ad app ferma: il servizio tiene lo
        store in memoria e lo riscrive. Ritorna l'ultimo ``lastRunAtMs`` di
        ciascuno, per riconoscere il giro dopo.
        """
        store = json.loads(self.read("cron/jobs.json") or "{}")
        now = int(time.time() * 1000)
        last: dict[str, int] = {}
        for job in store.get("jobs", []):
            if (job.get("payload") or {}).get("kind") != "system_event":
                continue
            if only and job.get("id") not in only:
                continue
            state = job.setdefault("state", {})
            last[str(job.get("id"))] = int(state.get("lastRunAtMs") or 0)
            state["nextRunAtMs"] = now - 1000 if delay_ms is None else now + delay_ms
        self.write("cron/jobs.json", json.dumps(store, ensure_ascii=False, indent=2))
        return last

    def job_last_run(self, job_id: str) -> int:
        store = json.loads(self.read("cron/jobs.json") or "{}")
        for job in store.get("jobs", []):
            if job.get("id") == job_id:
                return int((job.get("state") or {}).get("lastRunAtMs") or 0)
        return 0

    # -- gateway --------------------------------------------------------------

    def http_get(self, path: str, *, method: str = "GET") -> Any:
        req = urllib.request.Request(GATEWAY + path, method=method,
                                     headers={"Authorization": f"Bearer {self.secret()}"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = resp.read()
        return json.loads(body) if body else {}

    def set_model(self, model: str) -> None:
        out = self.http_get(f"/api/settings/update?model={model}")
        if out.get("model") != model and out.get("agent", {}).get("model") != model:
            raise DeviceError(f"model not set: {str(out)[:200]}")
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if self.http_get("/webui/bootstrap").get("model_name") == model:
                return
            time.sleep(1)
        raise DeviceError(f"the agent did not switch to {model}")

    def remove_cron_job(self, job_id: str) -> None:
        # Le azioni del gateway sono GET su un path d'azione, come le chiama la WebUI.
        self.http_get(f"/api/webui/cron/{job_id}/remove")

    def cancel_subagents(self) -> None:
        try:
            snap = self.http_get("/api/subagents")
        except (OSError, DeviceError, json.JSONDecodeError):
            return
        for item in _running_subagents(snap):
            try:
                self.http_get(f"/api/subagents/{item}/cancel")
            except (OSError, json.JSONDecodeError):
                continue

    def turn(self, content: str, *, chat_id: str = PERSONAL_CHAT,
             timeout: float = 420.0) -> TurnResult:
        return asyncio.run(self._turn(content, chat_id, timeout))

    def rpc(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return asyncio.run(self._rpc(method, params or {}))

    def _url(self) -> str:
        return f"ws://127.0.0.1:18790/?client_id=bh-{uuid.uuid4().hex[:6]}&token={self.secret()}"

    async def _turn(self, content: str, chat_id: str, timeout: float) -> TurnResult:
        import websockets

        start = time.monotonic()
        frames: list[dict[str, Any]] = []
        text: list[str] = []
        async with websockets.connect(self._url(), max_size=None) as ws:
            await ws.send(json.dumps({"type": "message", "content": content,
                                      "chat_id": chat_id, "webui": True}))
            deadline = start + timeout
            while time.monotonic() < deadline:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=deadline - time.monotonic())
                except asyncio.TimeoutError:
                    break
                frame = json.loads(raw)
                event = frame.get("event")
                if event in ("delta", "reasoning_delta"):
                    if event == "delta":
                        text.append(str(frame.get("text") or frame.get("delta") or ""))
                    continue
                frames.append(frame)
                if event == "message" and frame.get("text"):
                    text.append("\n" + str(frame["text"]))
                if event == "turn_end":
                    return TurnResult("".join(text).strip(), frames, time.monotonic() - start)
        return TurnResult("".join(text).strip(), frames, time.monotonic() - start, True)

    async def _rpc(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        import websockets

        async with websockets.connect(self._url(), max_size=None) as ws:
            rid = uuid.uuid4().hex[:8]
            await ws.send(json.dumps({"type": "rpc", "id": rid, "method": method,
                                      "params": params}))
            while True:
                frame = json.loads(await asyncio.wait_for(ws.recv(), 120))
                if frame.get("event") == "rpc_result" and frame.get("id") == rid:
                    if frame.get("error"):
                        raise DeviceError(f"{method}: {frame['error']}")
                    return frame.get("result") or {}


def _find_key(obj: Any, names: tuple[str, ...]) -> str | None:
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key in names and isinstance(value, str):
                return value
            found = _find_key(value, names)
            if found:
                return found
    return None


def _running_subagents(snapshot: Any) -> list[str]:
    items = snapshot.get("running") if isinstance(snapshot, dict) else None
    if items is None and isinstance(snapshot, dict):
        items = snapshot.get("subagents") or snapshot.get("items") or []
    out = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        state = item.get("state") or item.get("status")
        if state not in ("completed", "done", "failed", "cancelled", "error"):
            ident = item.get("task_id") or item.get("id")
            if ident:
                out.append(str(ident))
    return out
