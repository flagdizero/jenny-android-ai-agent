#!/usr/bin/env python3
# Host-only: development tool. Never imported by the Android runtime.
"""Passo 0 di «fatti fermi»: i numeri che decidono il disegno, su una copia vera.

Gira sul Mac contro una **copia** della cartella ``files/`` del telefono (almeno
``workspace/USER.md``, ``workspace/memory/`` e, per i messaggi veri,
``db/index.sqlite``). Non scrive niente, né lì né altrove: stampa i numeri, e
con ``--samples DIR`` scrive in *DIR* i campioni da etichettare a mano. I dati
restano sul Mac di chi la lancia: niente di quel che legge va nel repo.

Cosa misura (le soglie stanno nel piano):

1. l'erosione delle voci vive: quanti nomi e numeri passati per una voce che
   esiste ancora non sono più in nessun file vivo;
2. dove si ritrovano quelli persi (archivio, diario, journal);
3. le riscritture: quante hanno solo tolto, quante hanno aggiunto;
4. il richiamo per nome simulato sui messaggi veri dell'utente: quanto spesso
   scatta, quanto porta, quanto costa;
5. un messaggio da rigiocare (``--replay``), con ``--restore ID`` per riportare
   un file vivo com'era prima che l'archivio *ID* fosse sostituito;
6. i fatti del diario che il profilo non ha mai tenuto.

Uso::

    python3 scripts/memory_facts_probe.py COPIA/files --samples /tmp/campioni
    python3 scripts/memory_facts_probe.py COPIA/files \\
        --replay "stasera sono da Oreste" --session project --restore <id>
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
import sys
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jenny.agent.anchors import Vocabulary, delta, extract, words  # noqa: E402
from jenny.agent.memory_archive import ArchivedEntry, list_archived  # noqa: E402
from jenny.agent.tools.memory_entries import parse_entries  # noqa: E402
from jenny.utils.wiki_paths import fold  # noqa: E402

LIVE_FILES = ("USER.md", "memory/MEMORY.md")
# I tetti di partenza del piano.
MAX_TRIGGERS = 2
MAX_LINES = 4
MAX_LINE_CHARS = 240
MAX_BLOCK_CHARS = 700
TRIGGER_MIN_CHARS = 4
_FACT_MARKS = ("[permanent]", "[durable]", "[correction]")


@dataclass
class Chain:
    versions: list[ArchivedEntry]  # dalla più recente
    head_id: str  # l'id dove la catena esce dall'archivio


def chains(entries: list[ArchivedEntry]) -> list[Chain]:
    by_id = {e.id: e for e in entries}

    def head(entry: ArchivedEntry) -> str:
        # Si seguono i legami finché restano in archivio; dove escono, il
        # successore (vivo, o sparito) è la testa. Una catena senza successore
        # finisce sulla sua ultima voce.
        seen = {entry.id}
        current = entry
        while current.superseded_by in by_id and current.superseded_by not in seen:
            current = by_id[current.superseded_by]
            seen.add(current.id)
        if current.superseded_by and current.superseded_by not in by_id:
            return current.superseded_by
        return current.id

    groups: dict[str, Chain] = {}
    for entry in entries:
        key = head(entry)
        groups.setdefault(key, Chain([], key)).versions.append(entry)
    return list(groups.values())


def read_live(workspace: Path) -> dict[str, str]:
    out = {}
    for rel in LIVE_FILES:
        try:
            out[rel] = (workspace / rel).read_text(encoding="utf-8")
        except OSError:
            out[rel] = ""
    return out


def restore(live: dict[str, str], archived: list[ArchivedEntry], target: str) -> None:
    """Rimette nel file vivo la voce archiviata *target* al posto di chi l'ha sostituita."""
    entry = next((e for e in archived if e.id == target), None)
    if entry is None or not entry.superseded_by:
        raise SystemExit(f"--restore {target}: non in archivio o senza successore")
    for rel, text in live.items():
        for live_entry in parse_entries(text):
            if live_entry.id == entry.superseded_by:
                lines = text.splitlines()
                lines[live_entry.start:live_entry.end] = [f"- {entry.text}"]
                live[rel] = "\n".join(lines) + "\n"
                return
    raise SystemExit(f"--restore {target}: il successore {entry.superseded_by} non è vivo")


def history_rows(workspace: Path) -> list[dict]:
    memory = workspace / "memory"
    paths = sorted((memory / "history-archive").glob("*.jsonl")) + [memory / "history.jsonl"]
    rows = []
    for path in paths:
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue
        except OSError:
            continue
    return rows


def history_facts(rows: list[dict]) -> list[tuple[str, dict]]:
    """Le righe di fatto del diario: marcate stabili dal riassunto, o «dette»."""
    out = []
    for row in rows:
        content = row.get("content")
        if not isinstance(content, str) or row.get("untrusted"):
            continue
        if row.get("kind") == "said":
            out.append((content, row))
            continue
        if row.get("kind") or content.startswith("[RAW]"):
            continue
        for line in content.splitlines():
            stripped = line.strip().lstrip("-* ").strip()
            for mark in _FACT_MARKS:
                if stripped.startswith(mark):
                    out.append((stripped[len(mark):].strip(), row))
    return out


def journal_text(workspace: Path) -> str:
    return "\n".join(
        p.read_text(encoding="utf-8", errors="replace")
        for p in sorted((workspace / "wikis").glob("*/raw/journal/*.md"))
    )


class Catalogue:
    """Le catene dell'archivio, indicizzate per ancora."""

    def __init__(self, archived: list[ArchivedEntry], vocab: Vocabulary,
                 stop: frozenset[str]) -> None:
        self.vocab = vocab
        self.stop = stop
        self.chains = [c for c in chains(archived) if not c.versions[0].retracted]
        self.anchors: list[dict[str, frozenset[str]]] = []
        self.by_anchor: dict[str, list[int]] = {}
        for i, chain in enumerate(self.chains):
            per_version = {v.id: extract(v.text, vocab, extra_stop=stop) for v in chain.versions}
            self.anchors.append(per_version)
            for anchor in set().union(*per_version.values()):
                self.by_anchor.setdefault(anchor, []).append(i)
        n = len(self.chains)
        self.df_ceiling = max(8, int(n * 0.05))

    def triggers(self, message: str) -> list[str]:
        folded = words(message)
        # Un nome che è anche una parola comune («neve», «solo») fa scattare
        # solo se il messaggio lo scrive maiuscolo a metà frase.
        named = extract(message, extra_stop=self.stop)
        found = []
        for anchor in folded:
            if len(anchor) < TRIGGER_MIN_CHARS or anchor[:1].isdigit():
                continue
            hits = self.by_anchor.get(anchor)
            if not hits or len(hits) > self.df_ceiling or anchor in self.stop:
                continue
            if self.vocab.is_common_word(anchor) and anchor not in named:
                continue
            found.append(anchor)
        found.sort(key=lambda a: (len(self.by_anchor[a]), a))
        return found[:MAX_TRIGGERS]

    def lines(self, message: str, known: frozenset[str], sources: tuple[str, ...],
              already: frozenset[str] = frozenset()) -> list[str]:
        out: list[str] = []
        used = 0
        # Una riga entra solo se porta un'ancora che le righe già scelte non
        # portano: due formulazioni dello stesso fatto non servono.
        brought: set[str] = set()
        for anchor in self.triggers(message):
            for i in self.by_anchor[anchor]:
                chain = self.chains[i]
                best, best_new = None, frozenset()
                for version in chain.versions:
                    if version.source not in sources:
                        continue
                    new = self.anchors[i][version.id] - known
                    if anchor in self.anchors[i][version.id] and len(new) > len(best_new):
                        best, best_new = version, new
                if best is None or best_new <= brought:
                    continue
                text = " ".join(best.text.split())
                if len(text) > MAX_LINE_CHARS:
                    text = text[: MAX_LINE_CHARS - 1] + "…"
                line = f"- {best.demoted} · {best.source} · {text}"
                if line in already or used + len(line) > MAX_BLOCK_CHARS or len(out) >= MAX_LINES:
                    continue
                out.append(line)
                brought |= best_new
                used += len(line)
        return out


def user_messages(files: Path) -> list[tuple[str, str]]:
    path = files / "db" / "index.sqlite"
    if not path.exists():
        return []
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return conn.execute(
            "SELECT key, text FROM chat_texts WHERE role = 'user' ORDER BY key, turn, seq"
        ).fetchall()
    finally:
        conn.close()


def pct(a: int, b: int) -> str:
    return f"{a}/{b} ({100 * a / b:.0f}%)" if b else f"{a}/0"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("files", type=Path, help="copia della cartella files/ del telefono")
    ap.add_argument("--samples", type=Path, help="cartella dove scrivere i campioni da etichettare")
    ap.add_argument("--replay", help="un messaggio da rigiocare")
    ap.add_argument("--session", choices=("personal", "project"), default="personal")
    ap.add_argument("--restore", action="append", default=[],
                    help="id d'archivio da rimettere al posto del suo successore")
    args = ap.parse_args()

    workspace = args.files / "workspace"
    t0 = time.perf_counter()
    archived = list_archived(workspace / "memory")
    live = read_live(workspace)
    for target in args.restore:
        restore(live, archived, target)
    stop = frozenset(fold(p.name) for p in (workspace / "wikis").glob("*") if p.is_dir())
    rows = history_rows(workspace)
    history_texts = [r["content"] for r in rows if isinstance(r.get("content"), str)]
    # Il vocabolario legge anche il diario: più testo, meglio si distingue un
    # nome da una parola comune scritta maiuscola.
    vocab = Vocabulary.from_texts(
        [e.text for e in archived] + list(live.values()) + history_texts, extra_stop=stop,
    )
    catalogue = Catalogue(archived, vocab, stop)
    build_ms = (time.perf_counter() - t0) * 1000

    live_ids = {e.id for text in live.values() for e in parse_entries(text)}
    live_words = words("\n".join(live.values()))
    user_words = words(live["USER.md"])

    print(f"archivio: {len(archived)} formulazioni, {len(catalogue.chains)} catene; "
          f"voci vive: {len(live_ids)}")
    print(f"costruzione del catalogo: {build_ms:.0f} ms (soglia 300 ms)")
    print(f"tetto di frequenza per un'ancora che fa scattare: {catalogue.df_ceiling} catene")

    # 1. Erosione delle voci vive.
    passed: set[str] = set()
    lost: set[str] = set()
    for i, chain in enumerate(catalogue.chains):
        if chain.head_id not in live_ids:
            continue
        through = set().union(*catalogue.anchors[i].values())
        passed |= through
        lost |= {a for a in through if a not in live_words}
    print(f"\n[1] ancore passate per voci vive: {len(passed)}; non più in nessun file vivo: "
          f"{pct(len(lost), len(passed))}")

    # 2. Dove si ritrovano.
    history_words = words("\n".join(history_texts))
    journal_words = words(journal_text(workspace))
    print(f"[2] delle perse: nel diario {pct(len(lost & history_words), len(lost))}, "
          f"nei journal {pct(len(lost & journal_words), len(lost))}, "
          f"in nessuno dei due {pct(len(lost - history_words - journal_words), len(lost))}")

    # 3. Riscritture.
    by_id = {e.id: e for e in archived}
    live_by_id = {e.id: e.text for text in live.values() for e in parse_entries(text)}
    kinds: Counter[str] = Counter()
    samples: dict[str, list[str]] = {"eroded": [], "replaced": [], "reworded": []}
    for entry in archived:
        succ = entry.superseded_by
        new_text = by_id[succ].text if succ in by_id else live_by_id.get(succ)
        if not succ or new_text is None:
            continue
        dropped, added = delta(entry.text, new_text, vocab, extra_stop=stop)
        kind = "replaced" if added else "eroded" if dropped else "reworded"
        kinds[kind] += 1
        samples[kind].append(
            f"OLD {entry.text}\nNEW {new_text}\n   dropped={sorted(dropped)} added={sorted(added)}\n"
        )
    total = sum(kinds.values())
    print(f"[3] riscritture con successore leggibile: {total}: "
          + ", ".join(f"{k} {pct(v, total)}" for k, v in kinds.most_common()))

    # 4. Il richiamo simulato sui messaggi veri.
    messages = user_messages(args.files)
    fired = delivered = 0
    sizes: list[int] = []
    per_key: dict[str, list[int]] = {}
    trigger_counts: Counter[str] = Counter()
    fired_samples: list[str] = []
    recent: dict[str, list[list[str]]] = {}
    for key, text in messages:
        project = ":project:" in key
        known = (user_words if project else live_words) | words(text)
        sources = ("USER.md",) if project else LIVE_FILES
        triggers = catalogue.triggers(text)
        # Le righe già mandate negli ultimi 40 messaggi di quella chat sono in
        # storia: non si rimandano (``diary._in_history``).
        window = recent.setdefault(key, [])
        already = frozenset(line for sent in window[-40:] for line in sent)
        lines = catalogue.lines(text, known, sources, already) if triggers else []
        window.append(lines)
        if triggers:
            fired += 1
            trigger_counts.update(triggers)
        size = sum(len(line) + 1 for line in lines)
        per_key.setdefault(key, []).append(size)
        if lines:
            delivered += 1
            sizes.append(size)
            fired_samples.append(f"[{key}] triggers={triggers}\nMSG {text[:300]}\n"
                                 + "\n".join(lines) + "\n")
    windows = [sum(v[i:i + 40]) for v in per_key.values() for i in range(max(1, len(v) - 39))]
    print(f"\n[4] messaggi utente: {len(messages)}; scatta su {pct(fired, len(messages))} "
          f"(soglia 5–25%); porta righe su {pct(delivered, len(messages))}")
    print(f"    turni che scattano e portano un dettaglio nuovo: {pct(delivered, fired)} "
          "(soglia ≥ 50%)")
    if sizes:
        q = statistics.quantiles(sizes, n=20) if len(sizes) > 1 else [sizes[0]] * 19
        print(f"    caratteri per turno che porta: p50 {statistics.median(sizes):.0f}, "
              f"p95 {q[18]:.0f} (soglia 700); peggiori 40 messaggi di fila: "
              f"{max(windows) if windows else 0} (soglia 6.000)")
    print(f"    ancore che scattano più spesso: {trigger_counts.most_common(15)}")

    # 6. Fatti del diario mai tenuti dal profilo.
    archive_words = words("\n".join(e.text for e in archived))
    novel = []
    for text, row in history_facts(rows):
        anchors = extract(text, vocab, extra_stop=stop)
        if anchors and not anchors & (live_words | archive_words):
            novel.append(f"[{row.get('session_key', '?')} {row.get('timestamp', '')}] {text}")
    print(f"\n[6] fatti del diario con ancore che né i file vivi né l'archivio hanno: {len(novel)}")

    if args.replay:
        project = args.session == "project"
        known = (words(live["USER.md"]) if project else live_words) | words(args.replay)
        sources = ("USER.md",) if project else LIVE_FILES
        print(f"\n[5] replay ({args.session}): triggers={catalogue.triggers(args.replay)}")
        for line in catalogue.lines(args.replay, known, sources):
            print(f"    {line}")

    if args.samples:
        args.samples.mkdir(parents=True, exist_ok=True)
        for kind, items in samples.items():
            (args.samples / f"rewrites-{kind}.txt").write_text("\n".join(items[:40]), "utf-8")
        (args.samples / "fired.txt").write_text("\n".join(fired_samples), "utf-8")
        (args.samples / "history-novel.txt").write_text("\n".join(novel), "utf-8")
        print(f"\ncampioni in {args.samples}")


if __name__ == "__main__":
    main()
