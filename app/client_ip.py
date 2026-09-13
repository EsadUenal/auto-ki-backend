"""
Client-IP hinter einem Reverse-Proxy (Security Block 2, P1-7).

PROBLEM
-------
`slowapi.util.get_remote_address` liefert `request.client.host` — die Adresse
der direkten Gegenstelle. Steht ein Proxy davor (Railway Edge), ist das dessen
Adresse: alle Nutzer landen auf EINEM Rate-Limit-Schluessel, ein Einzelner kann
Login-, Chat- und AutoFinder-Limits fuer alle verbrauchen, und die anonyme
AutoFinder-Demo gaebe es weltweit einmal pro Tag.

WARUM NICHT EINFACH DEN HEADER NEHMEN
-------------------------------------
`X-Forwarded-For` ist ein beliebig setzbarer Request-Header. Wer die App direkt
erreicht, haette mit einer frei erfundenen IP je Anfrage gar kein Limit mehr.
Ein `--forwarded-allow-ips="*"` waere genau dieser Fehler.

REGEL HIER
----------
1. Ohne Konfiguration (`AUTO_KI_TRUSTED_PROXY_HOPS=0`) bleibt es beim bisherigen
   Verhalten: nur die direkte Gegenstelle zaehlt. Nichts ist faelschbar.
2. Mit Hops > 0 werden Header NUR ausgewertet, wenn die direkte Gegenstelle in
   einem vertrauenswuerdigen Netz liegt (`AUTO_KI_TRUSTED_PROXY_NETS`).
3. Bei `x-forwarded-for` zaehlt der Eintrag `hops` von RECHTS — der wurde vom
   naechstgelegenen vertrauenswuerdigen Proxy angehaengt. Alles weiter links
   kann vom Client stammen und wird ignoriert. Ein Header mit zu wenigen
   Eintraegen oder unsinnigem Inhalt faellt auf die Gegenstelle zurueck.
4. Andere Header (z.B. `x-real-ip`) werden als EIN Wert gelesen.

Jeder Fehlerfall endet bei der direkten Gegenstelle — nie bei einem vom Client
bestimmten Wert.
"""
from __future__ import annotations

import ipaddress
import logging

from starlette.requests import Request

from app.config import CLIENT_IP_HEADER, TRUSTED_PROXY_HOPS, TRUSTED_PROXY_NETS

log = logging.getLogger(__name__)

UNBEKANNT = "unbekannt"


def _netze() -> list[ipaddress._BaseNetwork]:
    netze = []
    for eintrag in TRUSTED_PROXY_NETS:
        try:
            netze.append(ipaddress.ip_network(eintrag, strict=False))
        except ValueError:
            log.warning("AUTO_KI_TRUSTED_PROXY_NETS: %r ist kein gueltiges Netz — ignoriert", eintrag)
    return netze


_TRUSTED = _netze()


def _gueltige_ip(wert: str) -> str | None:
    try:
        return str(ipaddress.ip_address(wert.strip()))
    except ValueError:
        return None


def ist_vertrauenswuerdiger_proxy(adresse: str | None) -> bool:
    """True, wenn die direkte Gegenstelle aus einem konfigurierten Proxy-Netz spricht."""
    if not adresse:
        return False
    try:
        ip = ipaddress.ip_address(adresse.strip())
    except ValueError:
        return False
    return any(ip in netz for netz in _TRUSTED)


def klient_ip(request: Request) -> str:
    """Beste belegbare Client-Adresse. Fallback ist immer die direkte Gegenstelle."""
    gegenstelle = getattr(getattr(request, "client", None), "host", None) or UNBEKANNT
    if TRUSTED_PROXY_HOPS <= 0:
        return gegenstelle
    if not ist_vertrauenswuerdiger_proxy(gegenstelle):
        # Direkter Zugriff (oder unbekanntes Netz): Header sind hier nichts wert.
        return gegenstelle
    roh = request.headers.get(CLIENT_IP_HEADER)
    if not roh:
        return gegenstelle
    if CLIENT_IP_HEADER == "x-forwarded-for":
        eintraege = [t for t in (teil.strip() for teil in roh.split(",")) if t]
        if len(eintraege) < TRUSTED_PROXY_HOPS:
            # Weniger Eintraege als vertrauenswuerdige Hops: die Kette passt nicht
            # zur Konfiguration — nichts davon ist belegbar.
            return gegenstelle
        kandidat = eintraege[-TRUSTED_PROXY_HOPS]
    else:
        kandidat = roh.split(",")[0]
    return _gueltige_ip(kandidat) or gegenstelle


def limit_schluessel(request: Request) -> str:
    """key_func fuer slowapi — eine Zeile, damit alle Limiter dieselbe Quelle nutzen."""
    return klient_ip(request)
