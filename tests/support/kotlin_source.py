"""Lettura strutturale di un sorgente Kotlin, per i contratti che il Kotlin non esegue.

I test di ``tests/security`` fissano regole su file ``.kt`` che in CI non girano.
Cercare una sottostringa nel sorgente grezzo ha un difetto noto: la trova anche
in un commento o in una stringa, e il test resta verde dopo che il codice è
sparito. Qui il sorgente si riduce al **solo codice** — commenti e contenuto
delle stringhe sostituiti da spazi, a parità di lunghezza e di righe, così gli
indici restano quelli del file — e i blocchi si estraggono contando le graffe.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

APP_SRC = Path(__file__).resolve().parents[2] / "android/app/src"
"""I source set dell'app: ``main`` e quelli dei flavor (``play``, ``foss``)."""
ANDROID_MAIN = APP_SRC / "main"
ANDROID_SRC = ANDROID_MAIN / "java/com/flagdizero/jenny"
PLAY_SRC = APP_SRC / "play/java/com/flagdizero/jenny"
"""Il Kotlin che solo la build ``play`` contiene (la copia su Google Drive)."""


def all_kotlin() -> list[Path]:
    """Ogni ``.kt`` dell'app, di tutti i source set: anche il codice di un
    flavor finisce in un APK."""
    return sorted(APP_SRC.glob("*/java/com/flagdizero/jenny/**/*.kt"))

_CHAR_LITERAL = re.compile(r"'(\\.[^']*|[^'\\])'")


def _blank(text: str) -> str:
    """Spazi al posto di tutto tranne gli a capo: stessi indici, stesse righe."""
    return re.sub(r"[^\n]", " ", text)


def _string_end(src: str, i: int) -> int:
    """Indice subito dopo il letterale che si apre in *i* (virgoletta semplice o tripla).

    Segue i template ``${...}``, che possono contenere altre stringhe
    (``"${x ?: "?"}"``): senza, la virgoletta interna chiuderebbe la stringa.
    """
    n = len(src)
    raw = src.startswith('"""', i)
    j = i + (3 if raw else 1)
    while j < n:
        if raw and src.startswith('"""', j):
            j += 3
            while j < n and src[j] == '"':  # quattro virgolette: chiude sull'ultima
                j += 1
            return j
        if not raw and src[j] == '"':
            return j + 1
        if not raw and src[j] == "\\":
            j += 2
            continue
        if src.startswith("${", j):
            depth, j = 1, j + 2
            while j < n and depth:
                if src[j] == '"':
                    j = _string_end(src, j)
                    continue
                # Un carattere letterale dentro il template (``'"'``, ``'{'``):
                # la sua virgoletta aprirebbe una stringa che non c'e', la sua
                # graffa sposterebbe il conto.
                if src[j] == "'" and (m := _CHAR_LITERAL.match(src, j)):
                    j = m.end()
                    continue
                depth += {"{": 1, "}": -1}.get(src[j], 0)
                j += 1
            continue
        j += 1
    return n


def code_only(src: str) -> str:
    """Il sorgente senza commenti né contenuto delle stringhe.

    Le virgolette restano (``""`` al posto di ``"text"``), così un letterale
    si riconosce ancora come tale; i template ``${...}`` si svuotano con la
    stringa che li contiene: per questi contratti non contano.
    """
    return _scan(src, keep_strings=False)


def strip_comments(src: str) -> str:
    """Il sorgente senza commenti, con le stringhe intatte.

    Per i contratti che devono leggere un letterale (``"workshop.html"``, una
    chiave JSON, un nome di comando): il letterale deve stare nel codice, non
    in una riga commentata. Stessi indici e stesse righe del file.
    """
    return _scan(src, keep_strings=True)


def _scan(src: str, *, keep_strings: bool) -> str:
    out: list[str] = []
    i, n = 0, len(src)
    while i < n:
        if src.startswith("//", i):
            j = src.find("\n", i)
            j = n if j == -1 else j
            out.append(_blank(src[i:j]))
            i = j
        elif src.startswith("/*", i):
            # I commenti a blocco di Kotlin si annidano.
            depth, j = 1, i + 2
            while j < n and depth:
                if src.startswith("/*", j):
                    depth, j = depth + 1, j + 2
                elif src.startswith("*/", j):
                    depth, j = depth - 1, j + 2
                else:
                    j += 1
            out.append(_blank(src[i:j]))
            i = j
        elif src[i] == '"':
            q = 3 if src.startswith('"""', i) else 1
            j = _string_end(src, i)
            if keep_strings:
                out.append(src[i:j])
            else:
                out.append('"' * q + _blank(src[i + q : j - q]) + '"' * q)
            i = j
        elif src[i] == "'" and (m := _CHAR_LITERAL.match(src, i)):
            out.append(m.group(0) if keep_strings else "'" + _blank(m.group(1)) + "'")
            i = m.end()
        else:
            out.append(src[i])
            i += 1
    return "".join(out)


def comments_only(src: str) -> str:
    """Il rovescio di :func:`strip_comments`: solo i commenti, codice in bianco.

    Per i pochi test che verificano che un KDoc dica il vero: leggono i
    commenti apposta, e dichiarandolo non passano per una lettura grezza.
    """
    kept = strip_comments(src)
    return "".join(c if (c != k or c == "\n") else " " for c, k in zip(src, kept, strict=True))


def _read(path: Path | str) -> str:
    path = Path(path)
    if not path.suffix:
        path = ANDROID_SRC / f"{path.name}.kt"
    if not path.is_file():
        pytest.skip("sorgente Android non presente in questo checkout")
    return path.read_text(encoding="utf-8")


def read_code(name: Path | str) -> str:
    """Il solo codice di ``<name>.kt`` (o del percorso dato); salta il test se il
    checkout non ha Android."""
    return code_only(_read(name))


def read_source(name: Path | str) -> str:
    """``<name>.kt`` (o il percorso dato) senza commenti, stringhe comprese.

    È la lettura di default dei contratti Kotlin: nessun test deve leggere il
    ``.kt`` grezzo, perché un'asserzione soddisfatta da un commento resta verde
    dopo che il codice è stato commentato.
    """
    return strip_comments(_read(name))


def read_comments(name: Path | str) -> str:
    """Solo i commenti di ``<name>.kt`` (v. :func:`comments_only`)."""
    return comments_only(_read(name))


def block_at(code: str, start: int) -> str:
    """Il blocco ``{...}`` che si apre alla prima graffa da *start*, graffe comprese."""
    open_at = code.index("{", start)
    depth = 0
    for j in range(open_at, len(code)):
        if code[j] == "{":
            depth += 1
        elif code[j] == "}":
            depth -= 1
            if depth == 0:
                return code[open_at : j + 1]
    raise AssertionError("graffa non chiusa")


def block_after(code: str, pattern: str) -> str:
    """Il blocco che segue la prima corrispondenza della regex *pattern*."""
    m = re.search(pattern, code)
    assert m, f"non trovato nel codice: {pattern}"
    return block_at(code, m.end())


def function_body(code: str, name: str) -> str:
    """Il corpo della funzione *name* (la prima dichiarata con quel nome)."""
    return block_after(code, rf"\bfun\s+(?:<[^>]*>\s*)?{re.escape(name)}\s*\(")
