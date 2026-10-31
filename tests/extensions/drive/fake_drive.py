"""Un Google Drive finto per ``httpx.MockTransport``: solo quel che ``DriveSink`` usa.

Ricerca con ``q`` (nome, tipo, genitore, cestino), cartelle, multipart,
caricamento ripartibile a pezzi con ``Content-Range``, download, cancellazione,
token. I guasti si iniettano con :meth:`FakeDrive.fail_next`.
"""

from __future__ import annotations

import itertools
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlparse

import httpx

FOLDER_MIME = "application/vnd.google-apps.folder"


@dataclass
class FakeFile:
    id: str
    name: str
    parents: list[str]
    mime: str
    content: bytes = b""
    created: int = 0


@dataclass
class _Session:
    meta: dict
    size: int
    existing: str | None
    received: bytearray = field(default_factory=bytearray)
    done: str | None = None


Failure = Callable[[httpx.Request], "httpx.Response | Exception | None"]


class FakeDrive:
    def __init__(self) -> None:
        self.files: dict[str, FakeFile] = {}
        self.sessions: dict[str, _Session] = {}
        self.valid_tokens = {"tok-1"}
        self.requests: list[httpx.Request] = []
        self._ids = itertools.count(1)
        self._failures: list[Failure] = []

    # -- comodità per i test ------------------------------------------------------

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def add(self, name: str, parents: list[str], mime: str = "application/octet-stream",
            content: bytes = b"") -> FakeFile:
        fid = f"id{next(self._ids)}"
        self.files[fid] = FakeFile(fid, name, parents, mime, content, created=len(self.files))
        return self.files[fid]

    def find(self, name: str) -> list[FakeFile]:
        return [f for f in self.files.values() if f.name == name]

    def fail_next(self, failure: Failure) -> None:
        """*failure(request)* torna una risposta/eccezione per quella richiesta, o None."""
        self._failures.append(failure)

    # -- dispatcher -----------------------------------------------------------------

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        for failure in list(self._failures):
            outcome = failure(request)
            if outcome is not None:
                self._failures.remove(failure)
                if isinstance(outcome, Exception):
                    raise outcome
                return outcome
        return self._handle_unfaulted(request)

    def _handle_unfaulted(self, request: httpx.Request) -> httpx.Response:
        """Come il Drive vero, senza guasti: anche per un guasto che «arriva dopo»."""
        auth = request.headers.get("Authorization", "")
        if auth.removeprefix("Bearer ") not in self.valid_tokens:
            return httpx.Response(401, json={"error": {"code": 401}})

        url = urlparse(str(request.url))
        params = {k: v[0] for k, v in parse_qs(url.query).items()}
        path = url.path
        if "upload_id" in params and request.method == "PUT":
            return self._session_put(params["upload_id"], request)
        if path.startswith("/upload/drive/v3/files"):
            existing = path.rsplit("/", 1)[1] if path.count("/") > 4 else None
            if params.get("uploadType") == "multipart":
                return self._multipart(request, existing)
            if params.get("uploadType") == "resumable":
                return self._start_session(request, existing)
        if path == "/drive/v3/files" and request.method == "GET":
            return self._list(params)
        if path == "/drive/v3/files" and request.method == "POST":
            meta = json.loads(request.content)
            f = self.add(meta["name"], meta.get("parents", ["root"]), meta.get("mimeType", ""))
            return httpx.Response(200, json={"id": f.id})
        match = re.fullmatch(r"/drive/v3/files/([^/]+)", path)
        if match:
            f = self.files.get(match.group(1))
            if f is None:
                return httpx.Response(404, json={"error": {"code": 404}})
            if request.method == "DELETE":
                del self.files[f.id]
                return httpx.Response(204)
            if request.method == "GET" and params.get("alt") == "media":
                return httpx.Response(200, content=f.content)
        return httpx.Response(400, text=f"fake drive: unhandled {request.method} {path}")

    # -- endpoint -----------------------------------------------------------------------

    def _list(self, params: dict[str, str]) -> httpx.Response:
        q = params["q"]
        name = re.search(r"name = '((?:[^'\\]|\\.)*)'", q)
        parent = re.search(r"'((?:[^'\\]|\\.)*)' in parents", q)
        mime_eq = re.search(r"mimeType = '([^']*)'", q)
        mime_ne = re.search(r"mimeType != '([^']*)'", q)

        def unescape(s: str) -> str:
            return re.sub(r"\\(.)", r"\1", s)

        out = []
        for f in sorted(self.files.values(), key=lambda f: f.created):
            if name and f.name != unescape(name.group(1)):
                continue
            if parent and unescape(parent.group(1)) not in f.parents:
                continue
            if mime_eq and f.mime != mime_eq.group(1):
                continue
            if mime_ne and f.mime == mime_ne.group(1):
                continue
            out.append({"id": f.id, "name": f.name, "createdTime": str(f.created)})
        return httpx.Response(200, json={"files": out})

    def _multipart(self, request: httpx.Request, existing: str | None) -> httpx.Response:
        boundary = request.headers["Content-Type"].split("boundary=")[1]
        parts = request.content.split(f"--{boundary}".encode())
        meta = json.loads(parts[1].split(b"\r\n\r\n", 1)[1].rstrip(b"\r\n"))
        data = parts[2].split(b"\r\n\r\n", 1)[1][: -len(b"\r\n")]
        if existing is not None:
            self.files[existing].content = data
            return httpx.Response(200, json={"id": existing})
        f = self.add(meta["name"], meta["parents"], content=data)
        return httpx.Response(200, json={"id": f.id})

    def _start_session(self, request: httpx.Request, existing: str | None) -> httpx.Response:
        upload_id = f"up{next(self._ids)}"
        self.sessions[upload_id] = _Session(
            json.loads(request.content), int(request.headers["X-Upload-Content-Length"]), existing
        )
        location = (
            "https://www.googleapis.com/upload/drive/v3/files"
            f"?uploadType=resumable&upload_id={upload_id}"
        )
        return httpx.Response(200, headers={"Location": location})

    def _session_put(self, upload_id: str, request: httpx.Request) -> httpx.Response:
        session = self.sessions.get(upload_id)
        if session is None:
            return httpx.Response(404)
        if session.done is not None:
            # Come Drive: una sessione finita risponde ancora col file.
            return httpx.Response(200, json={"id": session.done})
        content_range = request.headers["Content-Range"]
        star = re.fullmatch(r"bytes \*/(\d+)", content_range)
        if star is None:
            m = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", content_range)
            assert m, content_range
            start, end = int(m.group(1)), int(m.group(2))
            assert start == len(session.received), (start, len(session.received))
            assert end - start + 1 == len(request.content)
            session.received.extend(request.content)
        if len(session.received) == session.size:
            if session.existing is not None:
                self.files[session.existing].content = bytes(session.received)
                fid = session.existing
            else:
                fid = self.add(
                    session.meta["name"], session.meta["parents"], content=bytes(session.received)
                ).id
            session.done = fid
            return httpx.Response(200, json={"id": fid})
        headers = {"Range": f"bytes=0-{len(session.received) - 1}"} if session.received else {}
        return httpx.Response(308, headers=headers)
