"""La guardia della sessione di navigazione non si fa pilotare dalla pagina.

``JennyBrowserGuard.blocked(host)`` è visibile a ogni frame che la sessione
apre, e il nome lo sceglie la pagina. Tre conseguenze che erano aperte:

- la cache dei verdetti era una ``ConcurrentHashMap`` senza tetto: nomi casuali
  in un ciclo la facevano crescere per tutta la sessione;
- ogni nome irrisolvibile, e ogni connessione rifiutata, scriveva un
  ``Log.w`` col nome dentro: un log che la pagina riempie a piacere;
- il DNS girava sul thread JavaBridge, e il costruttore di ``WebSocket`` è
  sincrono: un DNS lento congelava il JS della pagina per tutta la sua durata.

Ora: LRU con tetto, avvisi a intervallo, e il DNS della guardia su un pool
piccolo con un'attesa massima — oltre, «bloccato», come per un DNS che fallisce.
"""

from __future__ import annotations

import re
from pathlib import Path

from support.kotlin_source import block_after, function_body, read_code, read_source


def _code() -> str:
    return read_code("JennyBrowserBridge")


def test_the_verdict_cache_is_a_bounded_lru() -> None:
    code = _code()
    decl = code[code.index("private val hostVerdicts") :].split("\n\n", 1)[0]
    assert "LinkedHashMap<String, Boolean>(64, 0.75f, true)" in decl, "ordine d'accesso: LRU"
    assert "size > MAX_HOST_VERDICTS" in decl
    limit = re.search(r"const val MAX_HOST_VERDICTS = (\d+)", code)
    assert limit and 0 < int(limit.group(1)) <= 4096
    assert "ConcurrentHashMap" not in code


def test_every_access_to_the_cache_holds_its_monitor() -> None:
    """``LinkedHashMap`` in ordine d'accesso cambia anche leggendo: nessun
    accesso fuori da ``synchronized(hostVerdicts)``."""
    code = _code()
    decl_end = code.index("private val hostVerdicts")
    decl_end = code.index("\n\n", decl_end)
    for m in re.finditer(r"hostVerdicts\b", code[decl_end:]):
        at = decl_end + m.start()
        line = code[code.rindex("\n", 0, at) : code.index("\n", at)]
        assert "synchronized(hostVerdicts)" in line, line.strip()


def test_page_driven_warnings_are_throttled() -> None:
    code = _code()
    guard = function_body(code, "blocked")
    resolve = function_body(code, "isBlockedHost")
    intercept = function_body(code, "blockedResponse")
    for name, body in (("blocked", guard), ("isBlockedHost", resolve),
                       ("blockedResponse", intercept)):
        assert "Log.w(" not in body, f"{name}: un avviso per ogni nome scelto dalla pagina"
        assert "warnThrottled(" in body
    throttle = function_body(code, "warnThrottled")
    assert "WARN_INTERVAL_MS" in throttle and "compareAndSet(" in throttle


def test_the_warnings_do_not_carry_the_page_chosen_name() -> None:
    src = read_source("JennyBrowserBridge")
    for call in re.findall(r"warnThrottled\(([^\n]*)\)", src):
        assert not re.search(r"\$\{?(h|host|uri|url)\b", call), (
            f"il nome (o l'URL) della pagina finisce nel log: {call}"
        )


def test_the_javabridge_waits_a_bounded_time_for_dns() -> None:
    guard = function_body(_code(), "blocked")
    assert "cachedVerdict(h)" in guard, "il verdetto in cache risponde senza DNS"
    assert "guardDns.submit(" in guard
    assert ".get(GUARD_DNS_TIMEOUT_MS, TimeUnit.MILLISECONDS)" in guard
    # Il DNS è quello di sempre, sullo stesso verdetto e sulla stessa cache.
    assert "isBlockedHost(h)" in guard
    catch = block_after(guard, r"catch \(e: Exception\)")
    assert "true" in catch, "timeout o coda piena: nel dubbio si blocca"


def test_the_guard_pool_is_small_and_bounded() -> None:
    code = _code()
    pool = code[code.index("private val guardDns") :].split("\n\n", 1)[0]
    assert "ThreadPoolExecutor(" in pool
    assert "ArrayBlockingQueue(" in pool, "una coda senza tetto è un'altra cache senza tetto"
    assert "isDaemon = true" in pool


def test_the_security_model_puts_the_dns_cap_where_the_code_has_it() -> None:
    """Il tetto di 2 secondi c'e' solo sulla domanda della guardia lato pagina;
    una richiesta HTTP risolve con ``isBlockedHost`` diretto, senza tetto. Il
    modello di sicurezza lo diceva di ogni nome non in cache."""
    http = function_body(_code(), "blockedResponse")
    assert "isBlockedHost(host)" in http
    assert "GUARD_DNS_TIMEOUT_MS" not in http and "guardDns" not in http
    doc = (Path(__file__).resolve().parents[2] / "docs/internals/security-model.md").read_text(
        encoding="utf-8"
    )
    section = doc[doc.index("**The agent browser's WebView.**") :].split("\n- ", 1)[0]
    browser = " ".join(section.split())
    assert "On the page-side check a name that is not cached waits at most 2 seconds" in browser
    assert "an HTTP request waits for the system resolver" in browser
