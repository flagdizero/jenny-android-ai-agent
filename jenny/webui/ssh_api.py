"""Impostazioni SSH per la WebUI: host registrati, chiave, impronta dell'host.

Questo modulo è la controparte *umana* di ``agent/tools/ssh_transport``: lì si
decide se una connessione è autorizzata, qui si raccolgono le due decisioni che
solo una persona può prendere — quali host esistono e quale host key è quella
giusta. Nessuna delle due è delegabile all'agente, ed è il motivo per cui
vivono in Settings e non in un tool.

Tre regole valgono per tutto il file:

* **la chiave privata non esce mai di qui.** Il payload di lettura dichiara
  solo se esiste (``has_key``); la pubblica invece sì, perché è fatta apposta
  per essere incollata in ``authorized_keys``;
* **la password non esce mai di qui**, e nemmeno offuscata. Al posto suo il
  payload porta ``has_password``, un booleano: una password che torna al client
  finisce nella cronologia della WebView e in qualunque log che tocchi quel
  corpo, e a differenza di una chiave non si può revocare senza cambiarla anche
  per l'umano che entra a mano su quella macchina. Il parametro di scrittura si
  chiama ``password`` di proposito: è uno dei marcatori di
  ``http_utils.redact_query_secrets``, quindi il suo valore risulta già mascherato
  in ogni riga di log che stampi il path della richiesta;
* **ogni scrittura della config passa da** :func:`jenny.config.store.mutate`, e
  l'I/O lento (DNS, connessione di probe, generazione della chiave) sta
  **prima** di entrarci: il lock resta preso per tutta la durata del callback.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import re
import time
from pathlib import Path
from typing import Any

from jenny.agent.tools.ssh_backends.base import SshError, SshHostKeyError
from jenny.agent.tools.ssh_transport import (
    forget_host,
    get_ssh_backend,
    is_host_pinned,
    pinned_host_key,
    record_host_key,
    ssh_key_path,
)
from jenny.channels.http_utils import QueryParams, parse_flag, query_first
from jenny.config import store
from jenny.config.loader import load_config
from jenny.config.schema import Config
from jenny.config.tool_schemas import SshHostConfig
from jenny.security.network import validate_ssh_target
from jenny.utils.path import atomic_write
from jenny.webui.settings_api import WebUISettingsError

# L'alias è l'identità dell'host *e* il nome del file di chiave
# (``ssh_transport.ssh_key_path`` lo filtra a caratteri sicuri). Accettando qui
# solo ciò che quel filtro lascia passare intatto, due alias diversi non possono
# collassare sullo stesso file: "prod/../nas" e "prodnas" avrebbero la stessa
# chiave privata, e cancellare il primo scollegherebbe il secondo.
_ALIAS_RE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9_-]{0,31}\Z")

# Impronte viste da un probe e non ancora accettate, per alias:
# ``alias -> (riga known_hosts, impronta, timestamp)``.
#
# L'accettazione non ri-sonda: l'utente approva *l'impronta che ha letto*, e
# fra il probe e il tap potrebbe rispondere una macchina diversa. Tenere qui la
# riga è anche ciò che evita di farsela mandare dal client, dove sarebbe un
# known_hosts scrivibile dall'esterno.
_PENDING_PROBES: dict[str, tuple[str, str, float]] = {}

# Oltre questo, l'impronta in sospeso è considerata vecchia e va risondata: la
# schermata potrebbe essere rimasta aperta per ore.
_PROBE_TTL_S = 600.0


# -- helper di query ---------------------------------------------------------



def _required(query: QueryParams, key: str) -> str:
    value = (query_first(query, key) or "").strip()
    if not value:
        raise WebUISettingsError(f"{key} is required")
    return value


def _flag(query: QueryParams, key: str) -> bool:
    return parse_flag(query_first(query, key))


def _parse_alias(query: QueryParams) -> str:
    alias = _required(query, "alias")
    if not _ALIAS_RE.match(alias):
        raise WebUISettingsError(
            "alias must be 1-32 characters, letters, digits, '-' or '_' only"
        )
    return alias


_AUTH_MODES = ("key", "password")


def _parse_auth_or_keep(query: QueryParams) -> str | None:
    """Modo di autenticazione richiesto, o ``None`` per "lascia quello che c'è".

    Come per la porta, il default non si risolve qui: dipende dall'host già
    salvato, e quello si legge solo dentro il lock di ``mutate``.
    """
    value = (query_first(query, "auth") or "").strip().lower()
    if not value:
        return None
    if value not in _AUTH_MODES:
        raise WebUISettingsError("auth must be either 'key' or 'password'")
    return value


def _parse_password(query: QueryParams) -> str | None:
    """Password richiesta, o ``None`` per "tieni quella salvata".

    Non viene ripulita ai bordi: in una password gli spazi sono contenuto, non
    formattazione, e toglierli qui farebbe fallire l'autenticazione con un
    messaggio che parla di altro. Solo un campo interamente vuoto — o di soli
    spazi, che nessuno digita volendo — conta come "non compilato", perché è
    ciò che manda la UI quando l'utente modifica un host senza toccare la
    password: quella salvata non le è mai stata mostrata, quindi non può
    rimandarla indietro.
    """
    value = query_first(query, "password")
    if value is None or not value.strip():
        return None
    return value


def _parse_port_or_keep(value: str | None) -> int | None:
    """Porta richiesta, o ``None`` per "lascia quella che c'è".

    Il default non si risolve qui: dipende dall'host già salvato, e quello si
    legge solo dentro il lock di ``mutate``.
    """
    if value is None or not value.strip():
        return None
    try:
        port = int(value.strip())
    except ValueError:
        raise WebUISettingsError("port must be an integer") from None
    if not 1 <= port <= 65535:
        raise WebUISettingsError("port must be between 1 and 65535")
    return port


# -- percorsi ----------------------------------------------------------------


def _public_key_path(alias: str) -> Path:
    """Sidecar con la chiave pubblica.

    Il backend restituisce la pubblica **una sola volta**, alla generazione. Se
    non la salvassimo, un utente che chiude la schermata prima di incollarla sul
    server dovrebbe rigenerare la coppia — cioè invalidare la chiave che sta
    già installando. La pubblica non è un segreto: sta qui solo per poterla
    rileggere.
    """
    path = ssh_key_path(alias)
    return path.with_name(path.name + ".pub")


def _read_public_key(alias: str) -> str | None:
    path = _public_key_path(alias)
    try:
        return path.read_text("utf-8").strip() or None
    except OSError:
        return None


# -- impronte ----------------------------------------------------------------


def _fingerprint_from_known_hosts_line(line: str | None) -> str | None:
    """Impronta SHA256 di una riga ``known_hosts`` già registrata.

    Serve solo a *mostrare* all'utente l'impronta vecchia accanto a quella nuova
    quando una host key cambia: senza le due affiancate, "accetta" e "annulla"
    sono una scelta alla cieca. Formato identico a quello di OpenSSH e di
    ``probe_host_key``, così il confronto visivo è possibile.
    """
    if not line:
        return None
    parts = line.split()
    if len(parts) < 3:
        return None
    try:
        blob = base64.b64decode(parts[2], validate=True)
    except (ValueError, TypeError):
        return None
    digest = base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip("=")
    return f"SHA256:{digest}"


# -- payload -----------------------------------------------------------------


def _host_payload(host: SshHostConfig) -> dict[str, Any]:
    """Un host come lo vede la UI.

    Nessun campo deriva dalla chiave privata **né dalla password**: entrambe
    sono ridotte a un booleano. Per la password non c'è nemmeno la versione
    offuscata alla ``_mask_api_key`` di ``settings_api``: quel suggerimento
    esiste perché una API key la si riconosce dal prefisso, mentre di una
    password non c'è niente da riconoscere e quattro caratteri veri sarebbero
    quattro caratteri regalati.
    """
    return {
        "alias": host.alias,
        "host": host.host,
        "port": host.port,
        "username": host.username,
        "description": host.description,
        "job_log_dir": host.job_log_dir,
        "host_key_fingerprint": host.host_key_fingerprint,
        "auth": host.auth,
        # La privata resta un booleano: il suo contenuto non ha nessun motivo
        # di attraversare la WebView, e un campo che non c'è non si può perdere.
        "has_key": ssh_key_path(host.alias).exists(),
        # Stessa regola, stesso motivo: la UI deve solo poter dire "password
        # impostata" o "manca la password" per far vedere all'utente che l'host
        # non è ancora usabile.
        "has_password": bool(host.password),
        "public_key": _read_public_key(host.alias),
        "pinned": is_host_pinned(host.host, host.port),
    }


def _credentials_lost(host: SshHostConfig) -> bool:
    """L'host era stato verificato, e adesso non lo è più.

    Serve a distinguere "mai configurato" da "configurato e poi perso", che
    hanno lo stesso aspetto — nessun pin, nessuna chiave — ma vogliono due
    messaggi opposti. Il discriminante è dove vivono le due cose:
    ``host_key_fingerprint`` sta in ``config.json``, **dentro** il workspace, e
    quindi un ripristino lo riporta indietro; ``known_hosts`` e la chiave
    privata stanno **fuori** dal workspace (vedi ``config.paths.get_ssh_dir``)
    e nel backup non entrano affatto. Impronta salvata + nessun pin è quindi la
    firma esatta di un workspace ripristinato, ed è il momento in cui l'utente
    va avvisato: altrimenti lo scopre da un tool SSH che fallisce e sembra un
    guasto invece che una conseguenza voluta.
    """
    return bool(host.host_key_fingerprint) and not is_host_pinned(host.host, host.port)


def ssh_settings_payload(config: Any = None) -> dict[str, Any]:
    """Stato SSH per Settings.

    Legge ``config.tools.ssh.hosts`` invece di ``configured_hosts()``: quello
    nasconde tutto a SSH spento — giusto per i tool, sbagliato qui, dove
    l'utente deve vedere (e poter correggere) gli host anche a interruttore
    abbassato.
    """
    if config is None:
        config = load_config()
    ssh = config.tools.ssh
    return {
        "enabled": ssh.enable,
        "hosts": [_host_payload(h) for h in ssh.hosts],
        # Alias le cui credenziali erano state configurate e non ci sono più:
        # la UI ne fa un avviso unico che spiega il perché, invece di lasciare
        # l'utente davanti a dei badge "non verificato" senza causa.
        "credentials_lost": [h.alias for h in ssh.hosts if _credentials_lost(h)],
    }


def _find_host(config: Config, alias: str) -> SshHostConfig:
    for host in config.tools.ssh.hosts:
        if host.alias == alias:
            return host
    raise WebUISettingsError(f"unknown ssh host: {alias}", status=404)


# -- interruttore ------------------------------------------------------------


async def update_ssh_settings(query: QueryParams) -> dict[str, Any]:
    """Accende o spegne l'accesso SSH (``tools.ssh.enable``).

    Resta un gate distinto dall'elenco host: spegnere qui toglie la capacità
    all'agente senza cancellare nulla di ciò che l'utente ha configurato.
    """

    def _apply(config: Config) -> bool:
        enabled = query_first(query, "enabled")
        if enabled is None:
            return False
        value = _flag(query, "enabled")
        if config.tools.ssh.enable == value:
            return False
        config.tools.ssh.enable = value
        return True

    config = await store.mutate(_apply)
    return ssh_settings_payload(config)


# -- CRUD degli host ---------------------------------------------------------


async def save_ssh_host(query: QueryParams) -> dict[str, Any]:
    """Crea o aggiorna un host. L'alias è l'identità: non si rinomina.

    La policy di rete viene applicata **qui**, al salvataggio, e di nuovo alla
    connessione (``resolve_target``): questo controllo dice subito all'utente
    che l'indirizzo non è raggiungibile per policy, quello dopo copre il nome
    che comincia a risolvere a un indirizzo vietato più tardi.

    ``auth`` sceglie fra chiave e password; assente vuol dire "come prima", che
    per un host nuovo è ``key``. Un host a password senza password non viene
    salvato: risulterebbe configurato in Settings e fallirebbe solo al primo
    comando, cioè dentro un turno dell'agente, dove l'errore lo legge il modello
    e non l'utente che potrebbe correggerlo.
    """
    alias = _parse_alias(query)
    host = _required(query, "host")
    username = _required(query, "username")
    requested_port = _parse_port_or_keep(query_first(query, "port"))
    description = (query_first(query, "description") or "").strip()
    job_log_dir = (query_first(query, "job_log_dir") or "").strip()
    requested_auth = _parse_auth_or_keep(query)
    password = _parse_password(query)

    # DNS: I/O lento, quindi fuori dal lock di ``mutate`` per definizione.
    ok, error = await asyncio.to_thread(validate_ssh_target, host)
    if not ok:
        raise WebUISettingsError(f"host refused by the network policy: {error}")

    # Riempito *dentro* il lock con l'indirizzo che l'host aveva prima: leggerlo
    # da una config caricata qui fuori significherebbe decidere sul vecchio
    # stato se il bersaglio si è spostato.
    moved_from: list[tuple[str, int]] = []

    def _apply(config: Config) -> None:
        current = next((h for h in config.tools.ssh.hosts if h.alias == alias), None)
        if current is None:
            auth = requested_auth or "key"
            if auth == "password" and password is None:
                raise WebUISettingsError(
                    "a password is required to save a host with password authentication"
                )
            config.tools.ssh.hosts.append(
                SshHostConfig(
                    alias=alias,
                    host=host,
                    port=requested_port if requested_port is not None else 22,
                    username=username,
                    description=description,
                    job_log_dir=job_log_dir or "/tmp/jenny-jobs",
                    auth=auth,
                    # Su un host a chiave la password resta ``None``: una
                    # credenziale che nessuno legge non ha motivo di stare nel
                    # file, e nel file ci starebbe in chiaro.
                    password=password if auth == "password" else None,
                )
            )
            return
        port = requested_port if requested_port is not None else current.port
        # Cambiare host o porta sposta il bersaglio: l'impronta accettata per il
        # vecchio indirizzo non dice niente sul nuovo, e lasciarla in mostra
        # farebbe credere all'utente di aver già verificato una macchina che non
        # ha mai visto.
        if current.host != host or current.port != port:
            moved_from.append((current.host, current.port))
            current.host_key_fingerprint = None
        auth = requested_auth or current.auth
        if auth == "password":
            # Campo vuoto in modifica = "tieni quella salvata": la UI non l'ha
            # mai ricevuta, quindi non può rimandarla. Vuoto *e* senza niente
            # salvato è invece l'host mezzo configurato che questo endpoint
            # rifiuta di creare.
            if password is None and not current.password:
                raise WebUISettingsError(
                    "a password is required to save a host with password authentication"
                )
            if password is not None:
                current.password = password
        else:
            # Tornare alla chiave butta via la password. Tenerla "per comodità"
            # lascerebbe nel file una credenziale che niente usa più e che
            # l'utente crede di aver rimosso passando alla chiave.
            current.password = None
        current.auth = auth
        current.host = host
        current.port = port
        current.username = username
        current.description = description
        if job_log_dir:
            current.job_log_dir = job_log_dir

    saved = await store.mutate(_apply)

    for previous in moved_from:
        forget_host(*previous)
        _PENDING_PROBES.pop(alias, None)

    return ssh_settings_payload(saved)


async def delete_ssh_host(query: QueryParams) -> dict[str, Any]:
    """Rimuove un host **e tutto ciò che lo rendeva usabile**.

    Chiave privata, pubblica e riga ``known_hosts`` se ne vanno con lui: un
    alias ricreato più tardi con lo stesso nome ripartirebbe altrimenti da una
    chiave e da un'impronta che l'utente crede cancellate, cioè da un accesso
    che pensa di aver revocato.
    """
    alias = _parse_alias(query)
    removed: list[tuple[str, int]] = []

    def _apply(config: Config) -> None:
        # ``_find_host`` solleva a 404 se l'alias non esiste: sollevare dentro
        # ``mutate`` lascia il file intatto, quindi il caso "non esiste" non
        # ruota il backup della config per niente.
        host_cfg = _find_host(config, alias)
        removed.append((host_cfg.host, host_cfg.port))
        config.tools.ssh.hosts = [
            h for h in config.tools.ssh.hosts if h.alias != alias
        ]

    saved = await store.mutate(_apply)

    ssh_key_path(alias).unlink(missing_ok=True)
    _public_key_path(alias).unlink(missing_ok=True)
    for target in removed:
        forget_host(*target)
    _PENDING_PROBES.pop(alias, None)

    return ssh_settings_payload(saved)


# -- chiave ------------------------------------------------------------------


async def generate_ssh_key(query: QueryParams) -> dict[str, Any]:
    """Genera la coppia ed25519 dell'alias e restituisce **solo** la pubblica.

    Rigenerare su un alias che ha già una chiave revoca l'accesso già
    installato sul server, quindi richiede ``replace=1``: un tap sbagliato non
    deve poter scollegare una macchina che funzionava.
    """
    alias = _parse_alias(query)
    config = load_config()
    _find_host(config, alias)

    key_path = ssh_key_path(alias)
    if key_path.exists() and not _flag(query, "replace"):
        raise WebUISettingsError(
            f"a key already exists for {alias}; replacing it revokes the access "
            "already installed on the server",
            status=409,
        )

    backend = get_ssh_backend()
    try:
        public_key = await backend.generate_key_pair(key_path)
    except SshError as exc:
        raise WebUISettingsError(f"key generation failed: {exc}", status=502) from exc

    _write_public_key(alias, public_key)

    payload = ssh_settings_payload()
    payload["alias"] = alias
    payload["public_key"] = public_key
    return payload


def _write_public_key(alias: str, public_key: str) -> None:
    """Scrive il sidecar ``.pub``, atomicamente e con gli stessi permessi 0600.

    0600 anche se non è un segreto: sta nella stessa directory della privata, e
    una regola sola per quella directory è più facile da non sbagliare di due.
    """
    atomic_write(_public_key_path(alias), f"{public_key.strip()}\n", chmod=0o600)


# -- host key ----------------------------------------------------------------


async def probe_ssh_host_key(query: QueryParams) -> dict[str, Any]:
    """Legge l'impronta offerta dall'host, senza autenticarsi e senza accettarla.

    Il risultato non tocca ``known_hosts``: fin qui non è stata presa nessuna
    decisione, e ``changed`` dice alla UI se quella che sta per mostrare
    contraddice un'impronta già accettata.
    """
    alias = _parse_alias(query)
    config = load_config()
    host_cfg = _find_host(config, alias)

    ok, error = await asyncio.to_thread(validate_ssh_target, host_cfg.host)
    if not ok:
        raise WebUISettingsError(f"host refused by the network policy: {error}")

    backend = get_ssh_backend()
    try:
        line, fingerprint = await backend.probe_host_key(host_cfg.host, host_cfg.port)
    except SshError as exc:
        raise WebUISettingsError(f"could not reach {alias}: {exc}", status=502) from exc

    _PENDING_PROBES[alias] = (line, fingerprint, time.monotonic())

    pinned = pinned_host_key(host_cfg.host, host_cfg.port)
    already = pinned is not None and pinned.strip() == line.strip()
    payload = ssh_settings_payload(config)
    payload["probe"] = {
        "alias": alias,
        "host": host_cfg.host,
        "port": host_cfg.port,
        "fingerprint": fingerprint,
        "already_accepted": already,
        # Host key diversa da quella accettata: è un potenziale MITM, non un
        # aggiornamento, e la UI deve dirlo prima di offrire "sostituisci".
        "changed": pinned is not None and not already,
        "pinned_fingerprint": _fingerprint_from_known_hosts_line(pinned),
    }
    return payload


async def accept_ssh_host_key(query: QueryParams) -> dict[str, Any]:
    """Registra in ``known_hosts`` l'impronta che l'utente ha appena letto.

    ``fingerprint`` è obbligatoria e deve coincidere con quella del probe: è il
    modo per cui questa route accetta *ciò che è stato mostrato* e non ciò che
    l'host risponde adesso. ``replace=1`` è la seconda decisione esplicita,
    quella che serve solo quando l'host key è cambiata.
    """
    alias = _parse_alias(query)
    fingerprint = _required(query, "fingerprint")
    replace = _flag(query, "replace")

    config = load_config()
    _find_host(config, alias)

    pending = _PENDING_PROBES.get(alias)
    if pending is None or time.monotonic() - pending[2] > _PROBE_TTL_S:
        _PENDING_PROBES.pop(alias, None)
        raise WebUISettingsError(
            "no recent host key to accept for this host; probe it again",
            status=409,
        )
    line, probed_fingerprint, _ = pending
    if probed_fingerprint != fingerprint:
        _PENDING_PROBES.pop(alias, None)
        raise WebUISettingsError(
            "the fingerprint does not match the one just read from the host; "
            "probe it again and compare",
            status=409,
        )

    try:
        record_host_key(line, replace=replace)
    except SshHostKeyError as exc:
        # Volutamente non retriable in automatico: chi arriva qui deve
        # ripassare dal dialogo che mostra le due impronte affiancate.
        raise WebUISettingsError(str(exc), status=409) from exc
    except (SshError, ValueError) as exc:
        raise WebUISettingsError(f"could not record the host key: {exc}") from exc

    _PENDING_PROBES.pop(alias, None)

    def _apply(config: Config) -> bool:
        host_cfg = next(
            (h for h in config.tools.ssh.hosts if h.alias == alias), None
        )
        # Solo per display: l'enforcement è la riga in ``known_hosts``, già
        # scritta sopra. Se l'host è sparito nel frattempo non c'è niente da
        # aggiornare, e non è un errore da mostrare all'utente.
        if host_cfg is None or host_cfg.host_key_fingerprint == fingerprint:
            return False
        host_cfg.host_key_fingerprint = fingerprint
        return True

    saved = await store.mutate(_apply)
    payload = ssh_settings_payload(saved)
    payload["accepted"] = {"alias": alias, "fingerprint": fingerprint}
    return payload
