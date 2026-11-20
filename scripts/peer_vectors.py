#!/usr/bin/env python3
"""Riscrive i vettori di prova del protocollo fra Jenny (``tests/peers/vectors/peer-v1.json``).

Da lanciare quando il protocollo cambia di proposito: ``test_vectors.py`` fallisce
finché il file non è di nuovo quello che il codice produce. Poi i vettori nuovi
vanno copiati nel plugin ``opencode-jenny``, che li usa nei suoi test.

    python scripts/peer_vectors.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests.peers.vectors import build  # noqa: E402

OUT = ROOT / "tests" / "peers" / "vectors" / "peer-v1.json"


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(build(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
