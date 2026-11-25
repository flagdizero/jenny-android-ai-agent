"""Test SSRF aggiuntivi per jenny.security.network, complementari a
``test_security_network.py`` (24 casi esistenti): qui casi NON coperti là —
IPv6 aggiuntivi (link-local, unique-local, forme mappate meno comuni),
notazioni IP alternative (comportamento reale, non presunto), schemi/URL
malformati, e la separazione esplicita tra le due policy (``validate_url_target``
strict vs ``validate_app_server_target`` permissiva-LAN).

Nessuna risoluzione DNS reale: ``socket.getaddrinfo`` è sempre mockato con IP
letterali forniti dal test (stesso pattern del file esistente).
"""

from __future__ import annotations

import socket
from unittest.mock import patch

import pytest

from jenny.security.network import (
    configure_ssrf_whitelist,
    validate_app_server_target,
    validate_ssh_target,
    validate_url_target,
)


def _fake_resolve(host: str, results: list[str]):
    """Getaddrinfo fittizio: mappa ``host`` sugli IP (v4 o v6) indicati."""

    def _resolver(hostname, port, family=0, type_=0):
        if hostname != host:
            raise socket.gaierror(f"cannot resolve {hostname}")
        entries = []
        for ip in results:
            if ":" in ip:
                entries.append((socket.AF_INET6, socket.SOCK_STREAM, 0, "", (ip, 0, 0, 0)))
            else:
                entries.append((socket.AF_INET, socket.SOCK_STREAM, 0, "", (ip, 0)))
        return entries

    return _resolver


# ---------------------------------------------------------------------------
# IPv6 — casi non coperti dalla suite esistente
# ---------------------------------------------------------------------------


def test_blocks_ipv6_link_local_fe80():
    """fe80::/10 (link-local v6) è nel blocklist strict e deve essere bloccato."""
    with patch("jenny.security.network.socket.getaddrinfo", _fake_resolve("evil.com", ["fe80::1"])):
        ok, err = validate_url_target("http://evil.com/")
        assert not ok
        assert "blocked" in err.lower()


def test_blocks_ipv6_unique_local_fc00():
    """fc00::/7 (unique local address, l'analogo IPv6 di RFC1918) è bloccato per url_target."""
    with patch("jenny.security.network.socket.getaddrinfo", _fake_resolve("evil.com", ["fc00::1"])):
        ok, _ = validate_url_target("http://evil.com/")
        assert not ok


def test_blocks_ipv6_unique_local_fd_prefix():
    """fd00::/8 è un sottoinsieme di fc00::/7 (bit locale settato): deve restare bloccato."""
    with patch("jenny.security.network.socket.getaddrinfo", _fake_resolve("evil.com", ["fd12:3456::1"])):
        ok, _ = validate_url_target("http://evil.com/")
        assert not ok


def test_blocks_ipv6_mapped_cgnat_without_whitelist():
    """::ffff:100.100.1.1 (CGNAT mappato IPv6) deve restare bloccato di default,
    senza whitelist attiva (la suite esistente copre solo il caso IPv4 puro e il
    caso mappato *con* whitelist)."""
    resolver = _fake_resolve("ts.local", ["::ffff:100.100.1.1"])
    with patch("jenny.security.network.socket.getaddrinfo", resolver):
        ok, _ = validate_url_target("http://ts.local/api")
        assert not ok


def test_allows_bracketed_ipv6_literal_public_address():
    """Un URL con literal IPv6 tra parentesi (``http://[::]:port/``) deve
    risolvere l'hostname correttamente e passare se l'indirizzo è pubblico."""
    resolver = _fake_resolve("2606:4700::1111", ["2606:4700::1111"])
    with patch("jenny.security.network.socket.getaddrinfo", resolver):
        ok, err = validate_url_target("http://[2606:4700::1111]/")
        assert ok, f"Should allow public IPv6 literal, got: {err}"


def test_blocks_bracketed_ipv6_loopback_literal():
    with patch("jenny.security.network.socket.getaddrinfo", _fake_resolve("::1", ["::1"])):
        ok, _ = validate_url_target("http://[::1]:8080/")
        assert not ok


# ---------------------------------------------------------------------------
# validate_app_server_target — IPv6 ULA è nella policy LAN-permissiva
# ---------------------------------------------------------------------------


def test_app_server_allows_ipv6_unique_local():
    """A differenza di validate_url_target, la policy app-server (LAN
    dichiarata dall'utente) ammette esplicitamente l'IPv6 ULA (fc00::/7):
    non è nel blocklist di ``_APP_SERVER_BLOCKED_NETWORKS``."""
    with patch("jenny.security.network.socket.getaddrinfo", _fake_resolve("srv.lan", ["fc00::1"])):
        ok, err = validate_app_server_target("http://srv.lan/x")
        assert ok, f"App policy should allow IPv6 ULA, got: {err}"


def test_app_server_still_blocks_ipv6_link_local():
    """fe80::/10 resta bloccato anche per la policy app-server (è nel blocklist ridotto)."""
    with patch("jenny.security.network.socket.getaddrinfo", _fake_resolve("srv.lan", ["fe80::1"])):
        ok, _ = validate_app_server_target("http://srv.lan/x")
        assert not ok


def test_app_server_allows_ipv6_mapped_rfc1918():
    """::ffff:192.168.1.50 (RFC1918 mappato IPv6) deve essere ammesso dalla
    policy app-server, esattamente come la sua forma IPv4 pura."""
    resolver = _fake_resolve("srv.lan", ["::ffff:192.168.1.50"])
    with patch("jenny.security.network.socket.getaddrinfo", resolver):
        ok, err = validate_app_server_target("http://srv.lan/x")
        assert ok, f"App policy should allow mapped RFC1918, got: {err}"


# ---------------------------------------------------------------------------
# Notazioni IP alternative — comportamento REALE verificato, non presunto
# ---------------------------------------------------------------------------
#
# Il modulo non fa mai matching testuale sull'hostname: valida solo gli
# indirizzi IP *risolti* da socket.getaddrinfo. Un letterale decimale/ottale/
# esadecimale nell'URL (es. "http://2130706433/") arriva a getaddrinfo così
# com'è; è compito del resolver di sistema (non di questo modulo) decidere se
# e come interpretarlo. I test seguenti simulano un resolver che, come fanno
# molte libc, interpreta la notazione decimale e restituisce l'IP canonico:
# in quel caso il blocco funziona comunque, perché si basa sull'IP restituito.


def test_decimal_notation_hostname_blocked_via_resolved_canonical_ip():
    """Se il resolver di sistema interpreta "2130706433" (forma decimale di
    127.0.0.1) e restituisce l'IP canonico, il blocco scatta regolarmente:
    la protezione è basata sull'IP risolto, non sulla stringa dell'hostname."""
    resolver = _fake_resolve("2130706433", ["127.0.0.1"])
    with patch("jenny.security.network.socket.getaddrinfo", resolver):
        ok, err = validate_url_target("http://2130706433/admin")
        assert not ok, f"Should block decimal-notation loopback bypass, got: {err}"


def test_malformed_resolved_address_string_is_silently_skipped():
    """COMPORTAMENTO SOSPETTO (documentato, non corretto qui): se
    ``getaddrinfo`` restituisse una entry il cui indirizzo non è parsabile da
    ``ipaddress.ip_address`` (es. una stringa letterale non normalizzata), il
    codice la scarta silenziosamente (``except ValueError: continue``, vedi
    ``_validate_target``). Se quella è l'unica entry risolta, la lista degli
    indirizzi validati resta vuota e la funzione ritorna ``ok=True`` per
    default, senza aver realmente controllato nessun indirizzo. In pratica un
    vero resolver di sistema non restituisce mai stringhe non canoniche in
    ``getaddrinfo()``, quindi non è sfruttabile con DNS reale — ma il
    contratto interno non fallisce "chiuso" in questo caso limite."""

    def resolver(hostname, port, family=0, type_=0):
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("2130706433", 0))]

    with patch("jenny.security.network.socket.getaddrinfo", resolver):
        ok, _ = validate_url_target("http://weird.example/")
        assert ok  # comportamento reale attuale: nessun indirizzo valido -> non bloccato


# ---------------------------------------------------------------------------
# Schemi e URL malformati
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "javascript:alert(1)",
        "data:text/plain,hello",
        "ws://evil.com/socket",
        "//evil.com/path",
    ],
)
def test_rejects_non_http_schemes_and_scheme_relative_urls(url):
    ok, _ = validate_url_target(url)
    assert not ok


def test_app_server_rejects_non_http_scheme_too():
    ok, _ = validate_app_server_target("file:///etc/passwd")
    assert not ok


def test_rejects_hostname_that_fails_to_resolve():
    def resolver(hostname, port, family=0, type_=0):
        raise socket.gaierror("nodename nor servname provided")

    with patch("jenny.security.network.socket.getaddrinfo", resolver):
        ok, err = validate_url_target("http://this-does-not-exist.invalid/")
        assert not ok
        assert "cannot resolve" in err.lower()


def test_port_is_not_part_of_the_security_boundary():
    """Le porte non sono validate: solo scheme e IP risolto contano. Una
    porta insolita su un host pubblico non deve essere bloccata."""
    with patch("jenny.security.network.socket.getaddrinfo", _fake_resolve("example.com", ["93.184.216.34"])):
        ok, err = validate_url_target("http://example.com:65000/")
        assert ok, f"Port should not affect the SSRF decision, got: {err}"


# ---------------------------------------------------------------------------
# Separazione esplicita delle due policy — stesso IP, decisioni diverse
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ip,url_target_ok,app_server_ok,label",
    [
        ("127.0.0.1", False, False, "loopback: entrambe bloccano"),
        ("169.254.169.254", False, False, "metadata: entrambe bloccano"),
        ("100.100.1.1", False, True,
         "CGNAT/Tailscale: url_target blocca (indirizzo scelto dal modello), "
         "app_server ammette (baseUrl dichiarato nel manifest)"),
        ("10.0.0.5", False, True, "RFC1918: url_target blocca, app_server ammette"),
        ("192.168.50.1", False, True, "RFC1918: url_target blocca, app_server ammette"),
        ("172.20.0.9", False, True, "RFC1918: url_target blocca, app_server ammette"),
    ],
)
def test_policy_divergence_table(ip, url_target_ok, app_server_ok, label):
    """Tabella esplicita di divergenza tra le due policy per lo stesso IP: per
    design devono restare separate, non appiattite."""
    with patch("jenny.security.network.socket.getaddrinfo", _fake_resolve("host.example", [ip])):
        ok_url, _ = validate_url_target("http://host.example/x")
        ok_app, _ = validate_app_server_target("http://host.example/x")
        assert ok_url is url_target_ok, label
        assert ok_app is app_server_ok, label


@pytest.mark.parametrize(
    "ip,ssh_ok,label",
    [
        ("100.100.6.6", True, "CGNAT: e l'indirizzo che assegna Tailscale"),
        ("192.168.1.10", True, "RFC1918: il server di casa sulla LAN"),
        ("fd00::5", True, "IPv6 ULA: stesso caso della LAN"),
        ("93.184.216.34", True, "pubblico: un VPS qualunque"),
        ("127.0.0.1", False, "loopback: sarebbe il telefono stesso"),
        ("::1", False, "loopback v6: idem"),
        ("169.254.169.254", False, "metadata: mai un server dell'utente"),
        ("0.0.0.0", False, "0.0.0.0/8: non e una destinazione"),
    ],
)
def test_ssh_policy_table(ip, ssh_ok, label):
    """L'SSH ha una policy propria, piu larga delle altre due.

    Un host SSH lo scrive l'utente in Settings e la sua host key va accettata a
    mano: le due cose insieme reggono quel che altrove regge il blocco delle
    reti private. Quel che resta bloccato punta al telefono, non a un server.
    """
    with patch("jenny.security.network.socket.getaddrinfo", _fake_resolve("host.example", [ip])):
        ok, err = validate_ssh_target("host.example")
        assert ok is ssh_ok, f"{label} — {err}"


def test_tailscale_allowed_where_the_user_names_the_target_never_where_the_model_does():
    """Il criterio che separa le tre policy sul CGNAT, con whitelist vuota.

    Non e' "SSH si, il resto no": e' **chi sceglie l'indirizzo**. Un host SSH lo
    digita l'utente in Settings; un `server.baseUrl` lo dichiara l'utente in un
    manifest che puo' leggere. In entrambi i casi il CGNAT e' il modo normale di
    raggiungere il proprio server da un telefono in 4G. In `web_fetch` invece
    l'indirizzo lo sceglie il modello, e li' resta bloccato.

    La whitelist globale resta vuota di proposito: e' esattamente la scorciatoia
    che avrebbe aperto il CGNAT a tutte e tre insieme.
    """
    configure_ssrf_whitelist([])
    with patch(
        "jenny.security.network.socket.getaddrinfo",
        _fake_resolve("ts.example", ["100.100.6.6"]),
    ):
        assert validate_ssh_target("ts.example")[0], "host digitato dall'utente"
        assert validate_app_server_target("http://ts.example/x")[0], "baseUrl dichiarato"
        assert not validate_url_target("http://ts.example/x")[0], "target scelto dal modello"


def test_ssrf_whitelist_applies_identically_to_both_policies():
    """Il whitelist globale (es. Tailscale CGNAT) si applica a entrambe le
    policy allo stesso modo: non è una feature di una sola delle due."""
    configure_ssrf_whitelist(["100.64.0.0/10"])
    try:
        with patch(
            "jenny.security.network.socket.getaddrinfo",
            _fake_resolve("ts.example", ["100.100.1.1"]),
        ):
            ok_url, _ = validate_url_target("http://ts.example/x")
            ok_app, _ = validate_app_server_target("http://ts.example/x")
            assert ok_url
            assert ok_app
    finally:
        configure_ssrf_whitelist([])


def test_app_server_policy_never_grants_loopback_even_with_lan_whitelist():
    """Whitelistare l'intero /8 di loopback non apre il telefono alle app.

    Fino al 25/11/2026 lo apriva: ``_is_blocked`` consultava la whitelist prima
    di ogni blocco, e questo test documentava il buco come «comportamento
    reale». Ora il pavimento (``_THE_PHONE_ITSELF``) viene prima della whitelist.
    """
    configure_ssrf_whitelist(["127.0.0.0/8"])
    try:
        with patch(
            "jenny.security.network.socket.getaddrinfo",
            _fake_resolve("self.example", ["127.0.0.1"]),
        ):
            ok, err = validate_app_server_target("http://self.example/x")
        assert not ok
        assert "127.0.0.1" in err
    finally:
        configure_ssrf_whitelist([])


def test_the_whitelist_cannot_open_the_phone_to_ssh():
    """Il pavimento del loopback non e negoziabile, nemmeno dalla whitelist.

    ``_is_blocked`` consulta ``_allowed_networks`` prima di ogni blocklist:
    chi apriva una fascia per ``web_fetch`` apriva anche l'SSH verso il telefono
    stesso, e con esso l'API del gateway raggiunta dall'interno. La docstring di
    ``validate_ssh_target`` prometteva il contrario.
    """
    configure_ssrf_whitelist(["127.0.0.0/8", "100.64.0.0/10"])
    try:
        with patch(
            "jenny.security.network.socket.getaddrinfo",
            _fake_resolve("me.example", ["127.0.0.1"]),
        ):
            ok, err = validate_ssh_target("me.example")
        assert not ok
        assert "the phone itself" in err

        # La stessa whitelist, nella stessa chiamata, continua a valere dove
        # deve: il pavimento riguarda il loopback, non la whitelist in se.
        with patch(
            "jenny.security.network.socket.getaddrinfo",
            _fake_resolve("ts.example", ["100.100.1.1"]),
        ):
            assert validate_url_target("http://ts.example/x")[0]
    finally:
        configure_ssrf_whitelist([])


def test_ipv6_loopback_is_covered_by_the_same_floor():
    configure_ssrf_whitelist(["::/0"])
    try:
        with patch(
            "jenny.security.network.socket.getaddrinfo",
            _fake_resolve("me6.example", ["::1"]),
        ):
            assert not validate_ssh_target("me6.example")[0]
    finally:
        configure_ssrf_whitelist([])
