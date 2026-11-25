"""«Rete di casa»: gli indirizzi privati che gli strumenti di Jenny raggiungono.

Gli strumenti che vanno in rete (``web_fetch``, il browser, ``python_exec``, il
proxy delle app) non raggiungono indirizzi privati: e' il blocco SSRF di
``security/network.py``. ``security.ssrfWhitelist`` ne esenta alcuni; fino al
25/11/2026 si scriveva solo a mano in ``config.json``, e i rifiuti dicevano di
fare cosi'. Questa e' la sua porta in officina.

**Un indirizzo alla volta, mai un intervallo.** L'elenco vale per tutti gli
strumenti insieme, e il codice lo tratta gia' come un rischio
(``network.py``): qui si accetta un solo indirizzo IP, privato o di Tailscale.
Niente nomi (l'elenco li scarterebbe in silenzio), niente loopback ne' indirizzi
del telefono stesso (aprirebbero l'API del gateway agli strumenti), niente
indirizzi pubblici (non sono bloccati: esentarli non serve). Un intervallo
scritto a mano in ``config.json`` si vede e si puo' togliere, non creare.

La scrittura passa da ``config/store.py::mutate``; poi l'elenco si riapplica al
processo, perche' il blocco lo legge da li' (``configure_ssrf_whitelist``).
"""

from __future__ import annotations

import ipaddress
from typing import Any

# Gli intervalli in cui un indirizzo «di casa» puo' stare: le reti private, e
# quella di Tailscale (CGNAT). IPv6: gli indirizzi locali unici.
_HOME_RANGES = tuple(ipaddress.ip_network(n) for n in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10", "fc00::/7",
))
MAX_ENTRIES = 32
MAX_NAME = 40


class HomeNetworkError(ValueError):
    """Un indirizzo che la rete di casa non accetta; ``code`` per il client."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def parse_home_address(raw: Any) -> str:
    """L'indirizzo normalizzato come voce dell'elenco (``192.168.1.20/32``).

    Solleva :class:`HomeNetworkError` con ``code`` ``not_an_address`` (un nome,
    un intervallo, testo), ``not_home`` (pubblico, loopback, del telefono).
    """
    text = str(raw or "").strip()
    if not text or "/" in text:
        raise HomeNetworkError("not_an_address", "one IP address, not a name or a range")
    try:
        addr = ipaddress.ip_address(text)
    except ValueError as exc:
        raise HomeNetworkError("not_an_address", "one IP address, not a name or a range") from exc
    if addr.is_loopback or addr.is_unspecified or addr.is_multicast or addr.is_link_local:
        raise HomeNetworkError("not_home", "not an address of another device at home")
    if not any(addr in net for net in _HOME_RANGES if net.version == addr.version):
        raise HomeNetworkError("not_home", "not an address of another device at home")
    return f"{addr}/{addr.max_prefixlen}"


def clean_name(raw: Any) -> str:
    name = " ".join(str(raw or "").split())
    return name[:MAX_NAME]


def home_network_payload(config: Any) -> dict[str, Any]:
    """Le voci per l'officina: indirizzo leggibile, nome, e se e' un intervallo."""
    names = dict(getattr(config.security, "ssrf_names", {}) or {})
    entries = []
    for cidr in list(config.security.ssrf_whitelist or []):
        shown, single, phone = cidr, False, False
        try:
            net = ipaddress.ip_network(cidr, strict=False)
            single = net.num_addresses == 1
            shown = str(net.network_address) if single else str(net)
            phone = _covers_the_phone(net)
        except ValueError:
            pass
        entries.append({"entry": cidr, "address": shown, "name": names.get(cidr, ""),
                        "single": single, "phone": phone})
    return {"entries": entries}


def _covers_the_phone(net: ipaddress.IPv4Network | ipaddress.IPv6Network) -> bool:
    """Una voce scritta a mano che tocca il telefono stesso.

    Su quella parte non vale: il pavimento di ``security/network.py`` viene
    prima della whitelist. L'officina lo dice accanto alla voce, invece di
    lasciar credere che l'abbia aperto.
    """
    from jenny.security.network import _THE_PHONE_ITSELF

    return any(net.version == floor.version and net.overlaps(floor)
               for floor in _THE_PHONE_ITSELF)


def add_entry(config: Any, entry: str, name: str) -> None:
    """Dentro ``mutate``: aggiunge *entry* (gia' normalizzata) col suo nome."""
    current = list(config.security.ssrf_whitelist or [])
    if entry not in current:
        if len(current) >= MAX_ENTRIES:
            raise HomeNetworkError("too_many", "too many addresses")
        current.append(entry)
    config.security.ssrf_whitelist = current
    names = dict(config.security.ssrf_names or {})
    if name:
        names[entry] = name
    else:
        names.pop(entry, None)
    config.security.ssrf_names = names


def remove_entry(config: Any, entry: str) -> bool:
    """Dentro ``mutate``: toglie *entry* (com'e' scritta nell'elenco) e il suo nome."""
    current = list(config.security.ssrf_whitelist or [])
    if entry not in current:
        return False
    config.security.ssrf_whitelist = [c for c in current if c != entry]
    names = dict(config.security.ssrf_names or {})
    names.pop(entry, None)
    config.security.ssrf_names = names
    return True
