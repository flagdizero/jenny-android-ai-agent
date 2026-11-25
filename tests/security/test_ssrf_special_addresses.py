"""Indirizzi che nessuna delle tre policy SSRF deve lasciar passare.

``::`` (non specificato) su Linux raggiunge l'host stesso, come ``0.0.0.0``: con
``::1`` bloccato e ``::`` no, un servizio in ascolto sul telefono era
raggiungibile. ``::127.0.0.1`` è la forma IPv4-compatibile (deprecata) del
loopback, che ``_normalize_addr`` non riconosce perché non è una IPv4-mapped.
Multicast e broadcast non sono mai un server: né una pagina, né l'app server di
un utente, né un suo host SSH. Tutti letterali: nessuna risoluzione DNS vera.
"""

from __future__ import annotations

import pytest

from jenny.security.network import (
    validate_app_server_target,
    validate_ssh_target,
    validate_url_target,
)

_SPECIAL = [
    "::",
    "::127.0.0.1",
    "::10.0.0.1",
    "224.0.0.1",
    "239.255.255.250",
    "ff02::1",
    "255.255.255.255",
]


def _url(host: str) -> str:
    return f"http://[{host}]/" if ":" in host else f"http://{host}/"


@pytest.mark.parametrize("host", _SPECIAL)
def test_web_fetch_refuses_it(host: str) -> None:
    ok, err = validate_url_target(_url(host))
    assert not ok, f"{host} è passato per validate_url_target"
    assert "Blocked" in err, err


@pytest.mark.parametrize("host", [h for h in _SPECIAL if h != "::10.0.0.1"])
def test_an_app_server_refuses_it(host: str) -> None:
    ok, err = validate_app_server_target(_url(host))
    assert not ok, f"{host} è passato per validate_app_server_target"


@pytest.mark.parametrize("host", [h for h in _SPECIAL if h != "::10.0.0.1"])
def test_ssh_refuses_it(host: str) -> None:
    ok, err = validate_ssh_target(host)
    assert not ok, f"{host} è passato per validate_ssh_target"


def test_the_unspecified_address_is_the_phone_itself_for_ssh_even_whitelisted() -> None:
    """Come il loopback: il pavimento dell'SSH non cede alla whitelist globale."""
    from jenny.security import network

    network.configure_ssrf_whitelist(["::/0", "0.0.0.0/0"])
    try:
        assert not validate_ssh_target("::")[0]
        assert not validate_ssh_target("0.0.0.0")[0]
    finally:
        network.configure_ssrf_whitelist([])


@pytest.mark.parametrize("host", ["8.8.8.8", "2001:4860:4860::8888"])
def test_public_addresses_still_pass(host: str) -> None:
    assert validate_url_target(_url(host)) == (True, "")
    assert validate_ssh_target(host) == (True, "")


# Un IPv4 dentro un IPv6. NAT64 (64:ff9b::/96) e la forma tradotta SIIT
# (::ffff:0:0:0/96) sono lo stesso IPv4 scritto in un altro modo; 6to4
# (2002::/16) porta nel suo prefisso l'IPv4 del gateway del sito. Con il loopback,
# il link-local dei metadata o una LAN dentro, sono quegli indirizzi.
_EMBEDDED_PHONE = [
    "64:ff9b::7f00:1",      # NAT64 di 127.0.0.1
    "::ffff:0:7f00:1",      # SIIT di 127.0.0.1
    "2002:7f00:1::",        # 6to4 con gateway 127.0.0.1
]
_EMBEDDED_METADATA = [
    "64:ff9b::a9fe:a9fe",   # NAT64 di 169.254.169.254
    "2002:a9fe:a9fe::1",    # 6to4 con gateway 169.254.169.254
]
_EMBEDDED_LAN = [
    "64:ff9b::a00:1",       # NAT64 di 10.0.0.1
    "2002:c0a8:101::1",     # 6to4 con gateway 192.168.1.1
]
# Mai un server: il prefisso NAT64 locale (RFC 8215, lunghezza non fissa, quindi
# l'IPv4 dentro non si sa estrarre) e il site-local deprecato.
_NEVER = ["64:ff9b:1::a00:1", "64:ff9b:1::808:808"]


@pytest.mark.parametrize("host", _EMBEDDED_PHONE + _EMBEDDED_METADATA + _EMBEDDED_LAN + _NEVER
                         + ["fec0::1"])
def test_web_fetch_refuses_an_embedded_internal_ipv4(host: str) -> None:
    ok, err = validate_url_target(_url(host))
    assert not ok, f"{host} è passato per validate_url_target"
    assert "Blocked" in err, err


@pytest.mark.parametrize("host", _EMBEDDED_PHONE + _EMBEDDED_METADATA + _NEVER)
def test_app_servers_and_ssh_refuse_the_phone_and_metadata_embedded(host: str) -> None:
    assert not validate_app_server_target(_url(host))[0], host
    assert not validate_ssh_target(host)[0], host


@pytest.mark.parametrize("host", _EMBEDDED_PHONE)
def test_the_ssh_floor_sees_the_embedded_loopback_even_whitelisted(host: str) -> None:
    from jenny.security import network

    network.configure_ssrf_whitelist(["::/0", "0.0.0.0/0"])
    try:
        ok, err = validate_ssh_target(host)
    finally:
        network.configure_ssrf_whitelist([])
    assert not ok and "phone itself" in err, (host, err)


@pytest.mark.parametrize("host", _EMBEDDED_LAN)
def test_a_lan_behind_nat64_or_6to4_stays_reachable_for_ssh(host: str) -> None:
    """Le LAN restano permesse all'SSH e alle app, come in IPv4."""
    assert validate_ssh_target(host) == (True, "")
    assert validate_app_server_target(_url(host)) == (True, "")


@pytest.mark.parametrize("host", ["64:ff9b::808:808", "2002:808:808::1"])
def test_a_public_ipv4_behind_nat64_or_6to4_still_passes(host: str) -> None:
    """Su una rete mobile solo-IPv6 il DNS64 dà 64:ff9b:: anche ai siti pubblici."""
    assert validate_url_target(_url(host)) == (True, "")
    assert validate_ssh_target(host) == (True, "")


_THE_PHONE = [
    "127.0.0.1",
    "127.1.2.3",
    "0.0.0.0",
    "::1",
    "::",
    "::127.0.0.1",
    "::ffff:127.0.0.1",
    *_EMBEDDED_PHONE,
]


@pytest.mark.parametrize("host", _THE_PHONE)
def test_no_whitelist_opens_the_phone_to_any_policy(host: str) -> None:
    """Il pavimento vale per tutte e tre le policy e per i socket di ``python_exec``.

    Prima del 25/11/2026 lo aveva solo l'SSH: una voce scritta a mano che
    copriva il loopback apriva ``web_fetch``, ``http_get``, i socket grezzi e le
    Jenny App all'API del gateway dall'interno.
    """
    from jenny.security import network

    network.configure_ssrf_whitelist(["::/0", "0.0.0.0/0", "127.0.0.0/8", "::1/128"])
    try:
        assert not validate_url_target(_url(host))[0], host
        assert not validate_app_server_target(_url(host))[0], host
        assert not validate_ssh_target(host)[0], host
        assert network.is_blocked_ip(host), host
    finally:
        network.configure_ssrf_whitelist([])


def test_the_whitelist_still_opens_a_lan_range() -> None:
    """Il pavimento riguarda il telefono, non la whitelist in sé."""
    from jenny.security import network

    network.configure_ssrf_whitelist(["192.168.1.0/24"])
    try:
        assert validate_url_target("http://192.168.1.20/") == (True, "")
        assert not network.is_blocked_ip("192.168.1.20")
    finally:
        network.configure_ssrf_whitelist([])
    assert not validate_url_target("http://192.168.1.20/")[0]
