"""alambic_core.security.url_guard — garde-fou anti-SSRF pour appels HTTP sortants.

Porté de FlowerScan (fcl_url_guard). Tout appel vers une URL configurée par
l'utilisateur (export web service, enrichissement) doit passer par validate_url.

Deux niveaux cumulatifs :
  1. ALLOWLIST de domaines (protection principale) : seuls les hôtes explicitement
     autorisés au niveau compte (account.enrich_allowed_domains) sont acceptés.
     Liste vide ⇒ rien n'est autorisé (fail-closed).
  2. BLOCAGE des cibles internes (défense en profondeur) : même si un domaine
     autorisé résout vers une IP privée / loopback / link-local
     (169.254.169.254 = métadonnées cloud !), l'appel est refusé.

Stdlib uniquement (ipaddress + socket + urllib).
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse


class UrlGuardError(Exception):
    """URL refusée par le garde-fou (SSRF, schéma, hôte non autorisé...)."""


# Schémas autorisés : HTTP(S) uniquement. Bloque file://, ftp://, gopher://...
_ALLOWED_SCHEMES = {"http", "https"}


def parse_allowed_domains(raw: str | None) -> list[str]:
    """Parse account.enrich_allowed_domains (chaîne) en liste de domaines.

    Accepte les séparateurs courants : virgule, point-virgule, espace, retour ligne.
    """
    if not raw:
        return []
    out = []
    for chunk in raw.replace(";", ",").replace("\n", ",").replace(" ", ",").split(","):
        d = chunk.strip().lower().rstrip(".")
        if d:
            out.append(d)
    return out


def _host_resolves_to_blocked_ip(host: str) -> bool:
    """True si le host résout vers au moins une IP interne (refus par prudence)."""
    try:
        infos = socket.getaddrinfo(host, None)
    except Exception:  # noqa: BLE001
        return True  # résolution impossible → on refuse

    for info in infos:
        ip_str = info[4][0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            return True
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local  # 169.254.0.0/16 (métadonnées cloud)
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            return True
    return False


def _domain_allowed(host: str, allowed_domains) -> bool:
    """host autorisé s'il est égal à un domaine autorisé ou sous-domaine de celui-ci.

    Insensible à la casse. allowed_domains vide ⇒ rien n'est autorisé (fail-closed).
    """
    if not allowed_domains:
        return False
    h = (host or "").strip().lower().rstrip(".")
    for d in allowed_domains:
        dd = (d or "").strip().lower().rstrip(".")
        if not dd:
            continue
        if h == dd or h.endswith("." + dd):
            return True
    return False


def validate_url(url: str, allowed_domains=None, *, block_internal: bool = True) -> str:
    """Valide une URL avant appel HTTP. Retourne l'URL si OK, lève UrlGuardError sinon.

    - allowed_domains : allowlist (liste). None/vide ⇒ aucun hôte autorisé.
    - block_internal  : si True (défaut), refuse les hôtes résolvant vers une IP
                        interne (anti-SSRF). À ne désactiver qu'en test maîtrisé.
    """
    if not url or not isinstance(url, str):
        raise UrlGuardError("URL vide ou invalide")

    parsed = urlparse(url.strip())
    if parsed.scheme.lower() not in _ALLOWED_SCHEMES:
        raise UrlGuardError(f"Schéma non autorisé : {parsed.scheme!r} (http/https uniquement)")

    host = parsed.hostname
    if not host:
        raise UrlGuardError("Hôte absent dans l'URL")

    if not _domain_allowed(host, allowed_domains):
        raise UrlGuardError(f"Hôte non autorisé : {host!r} (absent de l'allowlist)")

    if block_internal and _host_resolves_to_blocked_ip(host):
        raise UrlGuardError(f"Hôte {host!r} résout vers une adresse interne (refusé)")

    return url
