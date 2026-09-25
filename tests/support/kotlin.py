"""Leggere il sorgente Kotlin dai test: senza commenti, per blocchi.

Il Kotlin non gira in CI, quindi alcune regole si fissano sul sorgente. Cercare
una sottostringa nel file intero però trova anche i commenti — e i commenti di
questo progetto *nominano* apposta il codice che spiegano: ``catch (e:
Exception)`` o ``FLAG_DEBUGGABLE`` scritti in una KDoc facevano passare un
contratto con il codice vero sparito.

- :func:`strip_comments`: il sorgente con ``//`` e ``/* */`` (annidati, come li
  ammette Kotlin) sostituiti da spazi, **stringhe escluse** — un
  ``"http://…"`` non è un commento. Le righe restano al loro posto.
- :func:`block_after`: il contenuto fra le graffe bilanciate che seguono un
  punto del sorgente, per cercare la *struttura* dentro un ramo preciso.
"""

from __future__ import annotations


def _blank(text: str) -> str:
    return "".join(ch if ch == "\n" else " " for ch in text)


def strip_comments(src: str) -> str:
    """*src* con i commenti ridotti a spazi (a capo conservati)."""
    out: list[str] = []
    i, n = 0, len(src)
    while i < n:
        if src.startswith('"""', i):
            end = src.find('"""', i + 3)
            end = n if end < 0 else end + 3
            while end < n and src[end] == '"':  # """…"""" : le virgolette in coda
                end += 1
            out.append(src[i:end])
            i = end
        elif src[i] in "\"'":
            quote, j = src[i], i + 1
            while j < n and src[j] != quote and src[j] != "\n":
                j += 2 if src[j] == "\\" else 1
            out.append(src[i : j + 1])
            i = j + 1
        elif src.startswith("//", i):
            end = src.find("\n", i)
            end = n if end < 0 else end
            out.append(_blank(src[i:end]))
            i = end
        elif src.startswith("/*", i):
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
        else:
            out.append(src[i])
            i += 1
    return "".join(out)


def block_after(code: str, start: int) -> str | None:
    """Il testo fra la prima ``{`` a partire da *start* e la sua ``}``.

    Da usare su codice già passato da :func:`strip_comments`: le graffe dentro
    le stringhe non si distinguono, e nei sorgenti che si guardano non ce ne
    sono di sbilanciate.
    """
    open_at = code.find("{", start)
    if open_at < 0:
        return None
    depth = 0
    for j in range(open_at, len(code)):
        if code[j] == "{":
            depth += 1
        elif code[j] == "}":
            depth -= 1
            if depth == 0:
                return code[open_at + 1 : j]
    return None
