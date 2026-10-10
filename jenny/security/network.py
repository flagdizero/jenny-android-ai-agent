"""Network security utilities — SSRF protection and internal URL detection."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from contextlib import suppress
from urllib.parse import urlparse

# Indirizzi che non sono mai un server, per nessuna delle tre policy:
# ``::`` non specificato (su Linux una connessione verso
# ``::`` arriva all'host stesso, come ``0.0.0.0``), ``::/96`` IPv4-compatibile
# (deprecato: ``::127.0.0.1`` è il loopback in una forma che ``_normalize_addr``
# non riconosce, perché non è una IPv4-mapped), multicast e broadcast.
#
# Stessa ragione per ``::ffff:0:0:0/96``, la forma «tradotta» di SIIT
# (``::ffff:0:a.b.c.d``, non una IPv4-mapped: fuori da un traduttore non va da
# nessuna parte), e per ``64:ff9b:1::/48``, il prefisso NAT64 di uso locale (RFC
# 8215): la lunghezza del prefisso lì non è fissa, quindi l'IPv4 che porta dentro
# non si sa estrarre, e un indirizzo così non è mai il server di nessuno.
_NEVER_A_SERVER = [
    ipaddress.ip_network("::/96"),             # :: e IPv4-compatibili (::1 compreso)
    ipaddress.ip_network("::ffff:0:0:0/96"),   # IPv4 tradotto (SIIT)
    ipaddress.ip_network("64:ff9b:1::/48"),    # NAT64 locale
    ipaddress.ip_network("224.0.0.0/4"),       # multicast v4
    ipaddress.ip_network("255.255.255.255/32"),  # broadcast
    ipaddress.ip_network("ff00::/8"),          # multicast v6
]

# NAT64 con il prefisso noto (RFC 6052): l'IPv4 negli ultimi 32 bit, e la
# connessione arriva a lui. Non si blocca il prefisso — su una rete mobile solo
# IPv6 il DNS64 dà un 64:ff9b:: anche ai siti pubblici — ma lo si legge come
# l'IPv4 che è (v. ``_normalize_addr``).
_NAT64_WELL_KNOWN = ipaddress.ip_network("64:ff9b::/96")
_SIIT_TRANSLATED = ipaddress.ip_network("::ffff:0:0:0/96")

_BLOCKED_NETWORKS = [
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),   # carrier-grade NAT
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),   # link-local / cloud metadata
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),          # unique local
    ipaddress.ip_network("fec0::/10"),         # site-local (deprecato), privato come l'ULA
    ipaddress.ip_network("fe80::/10"),         # link-local v6
    *_NEVER_A_SERVER,
]

# Blocklist for Jenny App `http` actions: app servers are user-declared LAN
# devices, so private ranges (RFC1918, IPv6 ULA) are allowed. Loopback stays
# blocked so an app manifest can't use the proxy as an authenticated bridge to
# the gateway's own API; link-local/metadata and 0.0.0.0/8 stay blocked because
# they are never a user's server.
#
# CGNAT (100.64.0.0/10) is ALLOWED here, same as in _SSH_BLOCKED_NETWORKS and
# for the same reason: it is the range Tailscale assigns its nodes, and reaching
# one's own server over Tailscale from a phone on 4G is the normal case, not an
# exotic one. It used to be blocked, with the escape hatch documented as
# "exemptable via the existing ssrf whitelist" — but that whitelist is *global*,
# so opening it to let one app talk to one's own server would have opened CGNAT
# to `web_fetch` and to every target the model picks. A narrow permission in the
# policy that needs it beats a wide one across all three.
#
# The stated justification for blocking it was that an app manifest must not use
# the proxy as a bridge to the gateway's own API — but that is the argument for
# blocking *loopback*, which still is: a CGNAT address does not reach the
# gateway. The two had been flattened into one sentence.
#
# What still stands guard: loopback and link-local blocked, redirects never
# followed (see apps/http.py), the target declared by the user in a manifest
# they can read, and only the app's own typed actions able to call it.
_APP_SERVER_BLOCKED_NETWORKS = [
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),   # link-local / cloud metadata
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fe80::/10"),         # link-local v6
    *_NEVER_A_SERVER,
]

# Blocklist for SSH targets. Come quella degli app server, ma senza CGNAT:
# 100.64.0.0/10 è la rete che Tailscale assegna ai propri nodi, ed è il modo
# normale di raggiungere il proprio server da un telefono in 4G. Bloccarla qui
# lasciava una sola via d'uscita — mettere 100.64.0.0/10 nella ssrf_whitelist —
# che però è globale: per aprire l'SSH verso Tailscale si sarebbe aperto il
# CGNAT anche a `web_fetch` e alle Jenny App, cioè proprio ai target che sceglie
# il modello. Meglio un permesso stretto qui che uno largo altrove.
#
# Quel che il CGNAT proteggeva resta coperto da vincoli più forti: un host SSH
# lo dichiara l'utente in Settings, e la sua host key va accettata a mano prima
# che parta una connessione. Loopback e link-local restano bloccati — quelli
# puntano al telefono stesso, non a un server dell'utente.
_SSH_BLOCKED_NETWORKS = [
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),   # link-local / cloud metadata
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fe80::/10"),         # link-local v6
    *_NEVER_A_SERVER,
]


_allowed_networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []


def configure_ssrf_whitelist(cidrs: list[str]) -> None:
    """Allow specific CIDR ranges to bypass SSRF blocking (e.g. Tailscale's 100.64.0.0/10)."""
    global _allowed_networks
    nets = []
    for cidr in cidrs:
        with suppress(ValueError):
            nets.append(ipaddress.ip_network(cidr, strict=False))
    _allowed_networks = nets


def _normalize_addr(
    addr: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    """Normalize IPv6-mapped IPv4 addresses to their IPv4 form.

    ``::ffff:127.0.0.1`` is semantically identical to ``127.0.0.1`` but
    Python's ipaddress treats it as an IPv6Address that matches neither
    ``127.0.0.0/8`` nor ``::1/128``.  Converting it to IPv4 ensures
    blocklist/allowlist checks work correctly.
    """
    if isinstance(addr, ipaddress.IPv6Address):
        if addr.ipv4_mapped is not None:
            return addr.ipv4_mapped
        # NAT64 con il prefisso noto, e la forma tradotta di SIIT, sono lo
        # stesso IPv4 in un'altra scrittura.
        if addr in _NAT64_WELL_KNOWN or addr in _SIIT_TRANSLATED:
            return ipaddress.IPv4Address(int(addr) & 0xFFFFFFFF)
    return addr


def _address_forms(
    addr: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    """L'indirizzo com'è, normalizzato, e l'IPv4 che un 6to4 porta nel prefisso.

    Un 6to4 (``2002:AABB:CCDD::/48``) non è una traduzione, è l'indirizzo di un
    sito il cui gateway è l'IPv4 ``AA.BB.CC.DD``: se quello è il loopback o il
    link-local dei metadata, l'indirizzo va trattato come loro. La forma com'è
    resta perché ``_NEVER_A_SERVER`` rifiuta anche prefissi che la
    normalizzazione trasforma (SIIT). Basta che una forma sia bloccata.
    """
    normalized = _normalize_addr(addr)
    forms = [addr] if normalized == addr else [addr, normalized]
    if isinstance(normalized, ipaddress.IPv6Address) and normalized.sixtofour is not None:
        forms.append(normalized.sixtofour)
    return forms


def _is_blocked(
    addr: ipaddress.IPv4Address | ipaddress.IPv6Address,
    blocked_networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network],
) -> bool:
    normalized = _normalize_addr(addr)
    if _allowed_networks and any(normalized in net for net in _allowed_networks):
        return False
    return any(form in net for form in _address_forms(addr) for net in blocked_networks)


def _validate_target(
    url: str,
    blocked_networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network],
    *,
    allow_loopback: bool = False,
) -> tuple[bool, str]:
    try:
        p = urlparse(url)
    except Exception as e:
        return False, str(e)

    if p.scheme not in ("http", "https"):
        return False, f"Only http/https allowed, got '{p.scheme or 'none'}'"
    if not p.netloc:
        return False, "Missing domain"

    hostname = p.hostname
    if not hostname:
        return False, "Missing hostname"

    try:
        infos = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
    except socket.gaierror:
        return False, f"Cannot resolve hostname: {hostname}"

    addrs: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    for info in infos:
        try:
            addr = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        addrs.append(addr)
    if allow_loopback and _is_allowed_loopback_target(hostname, addrs):
        return True, ""
    for addr in addrs:
        if _is_blocked(addr, blocked_networks):
            return False, f"Blocked: {hostname} resolves to private/internal address {addr}"

    return True, ""


def validate_url_target(url: str, *, allow_loopback: bool = False) -> tuple[bool, str]:
    """Validate a URL is safe to fetch: scheme, hostname, and resolved IPs.

    ``allow_loopback`` is intentionally narrow: it only permits literal
    loopback hosts (localhost, 127.0.0.0/8, ::1) when every resolved address is
    loopback. It does not allow RFC1918, link-local, metadata, or public DNS
    names that happen to resolve to loopback.

    Returns (ok, error_message).  When ok is True, error_message is empty.
    """
    return _validate_target(url, _BLOCKED_NETWORKS, allow_loopback=allow_loopback)


def validate_app_server_target(url: str) -> tuple[bool, str]:
    """Validate a Jenny App server URL (``server.baseUrl`` targets only).

    Unlike :func:`validate_url_target`, RFC1918, IPv6 ULA **and** CGNAT
    (Tailscale) are allowed — app servers are user-declared LAN or tailnet
    devices. Loopback, link-local metadata and 0.0.0.0/8 stay blocked (see
    ``_APP_SERVER_BLOCKED_NETWORKS`` for why CGNAT is permitted here rather
    than through the global ssrf whitelist).

    Returns (ok, error_message).  When ok is True, error_message is empty.
    """
    return _validate_target(url, _APP_SERVER_BLOCKED_NETWORKS)


def validate_ssh_target(host: str) -> tuple[bool, str]:
    """Validate an SSH target host (declared by the user, never model-supplied).

    RFC1918, IPv6 ULA and CGNAT are allowed: a home server reached over the LAN
    or over Tailscale is the main use case, and both are named by the user in
    Settings and host-key pinned before a single byte is sent. Loopback stays
    blocked — the agent must not be able to SSH into the phone itself, nor use
    the SSH tool as a bridge back to the gateway's own API — and so do
    link-local/metadata and 0.0.0.0/8 (see ``_SSH_BLOCKED_NETWORKS``).

    The loopback check is deliberately made **before** the blocklist, because
    the blocklist yields to ``security.ssrfWhitelist`` and this must not: a
    range opened for ``web_fetch`` would otherwise open the phone to SSH too.

    Unlike the URL validators this takes a bare hostname: SSH has no scheme to
    parse, so ``_validate_target`` (which requires http/https) does not apply.

    Called twice on purpose: once when the host is saved in Settings, and again
    at connection time so a name that later starts resolving to a blocked
    address (DNS rebinding) is caught.

    Returns (ok, error_message).  When ok is True, error_message is empty.
    """
    hostname = (host or "").strip().rstrip(".")
    if not hostname:
        return False, "Missing host"

    try:
        infos = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
    except socket.gaierror:
        return False, f"Cannot resolve hostname: {hostname}"

    addrs: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    for info in infos:
        try:
            addrs.append(ipaddress.ip_address(info[4][0]))
        except ValueError:
            continue
    if not addrs:
        return False, f"Cannot resolve hostname: {hostname}"

    for addr in addrs:
        # Il pavimento, controllato PRIMA della whitelist. ``_is_blocked``
        # consulta ``_allowed_networks`` per primo, quindi chi apre una fascia
        # per ``web_fetch`` la aprirebbe anche qui: bastava mettere in whitelist
        # un intervallo che copre 127.0.0.0/8 perche l'agente potesse aprire una
        # sessione SSH verso il telefono stesso, e con essa raggiungere l'API del
        # gateway dall'interno. Il loopback e la sola cosa che questa policy
        # promette senza condizioni: qui non si negozia.
        # Il non specificato (`0.0.0.0`, `::`) è il telefono tanto quanto il
        # loopback: una connessione verso di lui arriva all'host stesso.
        if any(form.is_loopback or form.is_unspecified for form in _address_forms(addr)):
            return False, f"Blocked: {hostname} resolves to the phone itself ({addr})"
        if _is_blocked(addr, _SSH_BLOCKED_NETWORKS):
            return False, f"Blocked: {hostname} resolves to {addr}"

    return True, ""


# ── Variante asincrona: il DNS non deve bloccare il loop ────────────────────
#
# Le tre policy risolvono il nome con ``socket.getaddrinfo``, che è bloccante:
# chiamate dal thread del loop, un DNS lento fermava tutto il gateway. Da un
# chiamante asincrono si usa la variante qui sotto, o ``asyncio.to_thread`` sul
# posto (``apps/http.py``, ``apps/proxy.py``). Chiama la funzione sincrona **per
# nome, al momento della chiamata**: chi la sostituisce nel modulo (i test lo
# fanno) sostituisce anche questa.


async def validate_url_target_async(url: str, *, allow_loopback: bool = False) -> tuple[bool, str]:
    """:func:`validate_url_target`, con la risoluzione fuori dal loop."""
    return await asyncio.to_thread(validate_url_target, url, allow_loopback=allow_loopback)


def _is_allowed_loopback_target(
    hostname: str,
    addrs: list[ipaddress.IPv4Address | ipaddress.IPv6Address],
) -> bool:
    if not addrs or not all(_normalize_addr(addr).is_loopback for addr in addrs):
        return False
    normalized = hostname.rstrip(".").lower()
    if normalized == "localhost":
        return True
    with suppress(ValueError):
        return ipaddress.ip_address(hostname).is_loopback
    return False
