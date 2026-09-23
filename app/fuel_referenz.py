from __future__ import annotations

"""
Kraftstoff-Referenzpreis Deutschland — amtliche Wochenzahl statt Platzhalter.

WARUM
-----
Der Autokosten-Rechner trug bisher die Beispielwerte 1,75 €/l (Benzin) und
1,65 €/l (Diesel) als Platzhalter im Formular. Die sind seit Jahren überholt und
sahen im Ergebnis trotzdem wie echte Preise aus. Dieses Modul liefert stattdessen
den aktuellen nationalen Wochenwert für Deutschland aus einer amtlichen Quelle.

QUELLE
------
Europäische Kommission, **Weekly Oil Bulletin** — die Mitgliedstaaten melden
mittwochs, die Kommission veröffentlicht donnerstags. Öffentlich, kostenlos,
kein Key, KEIN KI-/Search-Provider.

  * Datei: `Weekly_Oil_Bulletin_Prices_History_maticni_4web.xlsx`
  * Blatt 1 "Prices with taxes" = Verbraucherpreise INKLUSIVE Steuern
  * Zeile 1 trägt maschinenlesbare Spaltenschlüssel
    (`DE_price_with_tax_euro95`, `DE_price_with_tax_diesel`) — genau daran wird
    die Spalte gesucht. Die Buchstabenposition (heute BC/BD) wird NICHT
    hartcodiert: sie verschiebt sich, sobald die Kommission ein Land ergänzt.
  * Zeile 3 trägt die Einheit ("1000 l") — sie wird geprüft, nicht angenommen.
  * Ab Zeile 4 die Daten, NEUESTE ZUERST; Spalte A ist ein Excel-Serial-Datum.
  * Preise stehen je 1000 l -> /1000 ergibt €/l.

Gelesen wird ausschließlich mit der Standardbibliothek (zipfile + ElementTree):
ein XLSX ist ein ZIP mit XML. Keine neue Abhängigkeit für eine Handvoll Zellen.

WAS DAS MODUL NICHT BEHAUPTET
-----------------------------
  * Es ist KEIN Livepreis und kein Tankstellenpreis, sondern ein nationaler
    Wochendurchschnitt. Der Wortlaut dazu steht in `HINWEIS_DE`.
  * "Euro-super 95" ist nicht identisch mit E10 — das Bulletin führt E5/95.
  * Ist der amtliche Datenstand älter als `FUEL_REFERENZ_MAX_ALTER_TAGE`, wird er
    NICHT mehr als aktuell ausgegeben (Status `veraltet`), sondern nur noch mit
    seinem Datum. Ein Monate alter Preis darf nie wie der heutige aussehen.
  * Für Strom gibt es bewusst KEINE Referenz: Heimladen, AC und DC/HPC liegen zu
    weit auseinander, ein einzelner "deutscher Ladepreis" wäre eine Erfindung.

AUSFALLVERHALTEN (der Rechner darf nie blockieren)
--------------------------------------------------
  1. frischer Wert im Prozess-Cache            -> sofort
  2. letzter guter Wert von der Platte          -> sofort, ggf. Status `veraltet`
  3. ausdrücklich konfigurierter Fallback       -> Status `fallback`
  4. sonst                                      -> Status `nicht_verfuegbar`,
     das Eingabefeld bleibt leer und editierbar
"""

import asyncio
import datetime as dt
import json
import logging
import zipfile
from dataclasses import dataclass, asdict
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree as ET

import httpx

from app.config import (
    DB_PATH,
    FUEL_REFERENZ_CACHE_STUNDEN,
    FUEL_REFERENZ_ERLAUBT,
    FUEL_REFERENZ_FALLBACK_BENZIN,
    FUEL_REFERENZ_FALLBACK_DIESEL,
    FUEL_REFERENZ_MAX_ALTER_TAGE,
    FUEL_REFERENZ_TIMEOUT_SECONDS,
    FUEL_REFERENZ_URL,
)

log = logging.getLogger(__name__)

STATUS_OK = "ok"
STATUS_VERALTET = "veraltet"
STATUS_FALLBACK = "fallback"
STATUS_NICHT_VERFUEGBAR = "nicht_verfuegbar"

QUELLE = "Europäische Kommission, Weekly Oil Bulletin"
LAND = "DE"
EINHEIT = "EUR/l"

HINWEIS_DE = ("Deutschland-Referenz (nationaler Wochenwert), kein Livepreis. Dein tatsächlicher "
              "Preis kann regional und je nach Tankstelle abweichen.")

# Spaltenschlüssel in Zeile 1 des Blattes "Prices with taxes".
_SCHLUESSEL = {
    "benzin": "DE_price_with_tax_euro95",
    "diesel": "DE_price_with_tax_diesel",
}
# Produktbezeichnung für die Anzeige — "Euro-super 95" ist NICHT E10.
_PRODUKT = {
    "benzin": "Euro-Super 95 (E5)",
    "diesel": "Dieselkraftstoff",
}

_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_EXCEL_EPOCHE = dt.date(1899, 12, 30)
# Der Download ist rund 4,5 MB. Deckel gegen eine unerwartet riesige Antwort.
_MAX_BYTES = 40 * 1024 * 1024


@dataclass(frozen=True)
class Kraftstoffreferenz:
    kraftstoff: str            # "benzin" | "diesel"
    produkt: str               # amtliche Produktbezeichnung
    preis: float | None        # EUR je Einheit; None wenn nicht verfügbar
    einheit: str               # "EUR/l"
    land: str                  # "DE"
    quelle: str
    quelle_datum: str | None   # ISO-Datum des amtlichen Erhebungsstands
    abgerufen_am: str | None   # ISO-Zeitstempel des Abrufs
    status: str
    hinweis: str


# ══ XLSX-Parsing (rein, ohne Netz — genau so auch testbar) ═══════════════════

def _zellwert(c: ET.Element, sst: list[str]) -> str:
    v = c.find(f"{_NS}v")
    if c.get("t") == "s" and v is not None and v.text is not None:
        try:
            i = int(v.text)
        except ValueError:
            return ""
        return sst[i] if 0 <= i < len(sst) else ""
    if c.get("t") == "inlineStr":
        return "".join(t.text or "" for t in c.iter(f"{_NS}t"))
    return v.text if v is not None and v.text is not None else ""


def _spalte(ref: str | None) -> str:
    return "".join(ch for ch in (ref or "") if ch.isalpha())


def parse_wob_deutschland(daten: bytes) -> dict[str, tuple[float, dt.date]]:
    """{"benzin": (EUR/l, Erhebungsdatum), "diesel": (...)} aus dem XLSX.

    Nimmt den NEUESTEN Datensatz, der für die jeweilige Spalte einen Zahlenwert
    trägt — je Kraftstoff getrennt, damit eine einzelne Lücke (das Bulletin lässt
    Felder gelegentlich leer) nicht beide Werte kostet. Wirft `ValueError`, wenn
    die Datei nicht die erwartete Struktur hat; der Aufrufer behandelt das wie
    einen Ausfall und liefert den letzten guten Wert.
    """
    try:
        z = zipfile.ZipFile(BytesIO(daten))
    except zipfile.BadZipFile as e:
        raise ValueError("Antwort ist keine XLSX-Datei") from e

    with z:
        namen = set(z.namelist())
        if "xl/worksheets/sheet1.xml" not in namen:
            raise ValueError("Blatt 1 fehlt")
        sst: list[str] = []
        if "xl/sharedStrings.xml" in namen:
            wurzel = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for si in wurzel.iter(f"{_NS}si"):
                sst.append("".join(t.text or "" for t in si.iter(f"{_NS}t")))
        blatt = ET.fromstring(z.read("xl/worksheets/sheet1.xml"))

    zeilen = list(blatt.iter(f"{_NS}row"))
    if len(zeilen) < 4:
        raise ValueError("zu wenige Zeilen")

    kopf = {_spalte(c.get("r")): (_zellwert(c, sst) or "").strip() for c in zeilen[0].iter(f"{_NS}c")}
    einheiten = {_spalte(c.get("r")): (_zellwert(c, sst) or "").strip() for c in zeilen[2].iter(f"{_NS}c")}

    spalte_je_kraftstoff: dict[str, str] = {}
    for kraftstoff, schluessel in _SCHLUESSEL.items():
        treffer = [sp for sp, name in kopf.items() if name == schluessel]
        if len(treffer) != 1:
            raise ValueError(f"Spalte {schluessel!r} nicht eindeutig gefunden ({len(treffer)})")
        sp = treffer[0]
        # Einheit prüfen statt annehmen: "1000 l" ist die Grundlage der /1000.
        if einheiten.get(sp, "").replace(" ", "").lower() != "1000l":
            raise ValueError(f"unerwartete Einheit für {schluessel!r}: {einheiten.get(sp)!r}")
        spalte_je_kraftstoff[kraftstoff] = sp

    ergebnis: dict[str, tuple[float, dt.date]] = {}
    for zeile in zeilen[3:]:
        if len(ergebnis) == len(spalte_je_kraftstoff):
            break
        zellen = {_spalte(c.get("r")): _zellwert(c, sst) for c in zeile.iter(f"{_NS}c")}
        try:
            datum = _EXCEL_EPOCHE + dt.timedelta(days=int(float(zellen.get("A", ""))))
        except (TypeError, ValueError):
            continue
        for kraftstoff, sp in spalte_je_kraftstoff.items():
            if kraftstoff in ergebnis:
                continue
            try:
                je_1000l = float(zellen.get(sp, ""))
            except (TypeError, ValueError):
                continue
            if je_1000l <= 0:
                continue
            preis = round(je_1000l / 1000, 3)
            # Plausibilitätsband: ein Kraftstoffpreis außerhalb 0,20–5,00 €/l ist
            # kein Preis, sondern ein Strukturfehler (verschobene Spalte o. ä.).
            if not (0.20 <= preis <= 5.00):
                raise ValueError(f"unplausibler Preis für {kraftstoff}: {preis}")
            ergebnis[kraftstoff] = (preis, datum)

    if not ergebnis:
        raise ValueError("kein verwertbarer Datensatz gefunden")
    return ergebnis


# ══ Cache (Prozess + Platte) ═════════════════════════════════════════════════

_cache: dict[str, object] = {}            # {"werte": {...}, "abgerufen_am": iso}
_sperre: asyncio.Lock | None = None


def _cache_datei() -> Path:
    """Neben der Live-DB — in Produktion das persistente Volume, lokal %LOCALAPPDATA%."""
    return Path(DB_PATH).parent / "fuel_referenz_cache.json"


def _lade_von_platte() -> dict | None:
    try:
        roh = _cache_datei().read_text(encoding="utf-8")
    except (OSError, ValueError):
        return None
    try:
        obj = json.loads(roh)
    except ValueError:
        return None
    if not isinstance(obj, dict) or not isinstance(obj.get("werte"), dict):
        return None
    return obj


def _schreibe_auf_platte(obj: dict) -> None:
    try:
        datei = _cache_datei()
        datei.parent.mkdir(parents=True, exist_ok=True)
        datei.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    except OSError as e:
        log.info("Kraftstoffreferenz: Cache konnte nicht geschrieben werden (%s)", type(e).__name__)


def _ist_frisch(abgerufen_am: str | None) -> bool:
    if not abgerufen_am:
        return False
    try:
        ts = dt.datetime.fromisoformat(abgerufen_am)
    except ValueError:
        return False
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=dt.timezone.utc)
    alter = dt.datetime.now(dt.timezone.utc) - ts
    return alter.total_seconds() < FUEL_REFERENZ_CACHE_STUNDEN * 3600


# ══ Abruf ════════════════════════════════════════════════════════════════════

async def _hole_datei() -> bytes:
    """Ein Versuch, kein Retry. Fehler propagieren an den Aufrufer."""
    timeout = httpx.Timeout(FUEL_REFERENZ_TIMEOUT_SECONDS)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        antwort = await client.get(FUEL_REFERENZ_URL)
        antwort.raise_for_status()
        inhalt = antwort.content
    if len(inhalt) > _MAX_BYTES:
        raise ValueError("Antwort unerwartet groß")
    return inhalt


async def _aktualisiere() -> dict | None:
    """Holt und parst die amtliche Datei. None bei jedem Ausfall (nie werfen)."""
    try:
        daten = await _hole_datei()
        werte = parse_wob_deutschland(daten)
    except Exception as e:  # noqa: BLE001 — bewusst breit
        # Bewusst JEDE Ausnahme: dieser Abruf darf den Rechner unter keinen
        # Umständen mit nach unten reißen. Ein unerwarteter Fehlertyp (TLS,
        # DNS, kaputtes Archiv, Parserfehler) ist genau derselbe Fall wie ein
        # HTTP-Fehler — kein Wert, weiter mit dem letzten guten.
        # Ohne URL/Klartext im Log: der Typ sagt schon alles Nötige.
        log.info("Kraftstoffreferenz nicht abrufbar (%s)", type(e).__name__)
        return None
    obj = {
        "werte": {k: {"preis": p, "quelle_datum": d.isoformat()} for k, (p, d) in werte.items()},
        "abgerufen_am": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
    }
    _schreibe_auf_platte(obj)
    log.info("Kraftstoffreferenz aktualisiert (Stand %s)",
             next(iter(obj["werte"].values()))["quelle_datum"])
    return obj


def _fallback_preis(kraftstoff: str) -> float | None:
    roh = FUEL_REFERENZ_FALLBACK_BENZIN if kraftstoff == "benzin" else FUEL_REFERENZ_FALLBACK_DIESEL
    if not roh:
        return None
    try:
        wert = float(roh.replace(",", "."))
    except ValueError:
        return None
    return wert if 0.20 <= wert <= 5.00 else None


def _leer(kraftstoff: str, status: str = STATUS_NICHT_VERFUEGBAR) -> Kraftstoffreferenz:
    return Kraftstoffreferenz(
        kraftstoff=kraftstoff, produkt=_PRODUKT.get(kraftstoff, kraftstoff), preis=None,
        einheit=EINHEIT, land=LAND, quelle=QUELLE, quelle_datum=None, abgerufen_am=None,
        status=status,
        hinweis="Aktuell keine amtliche Referenz verfügbar. Trage deinen eigenen Preis ein.",
    )


def _baue(kraftstoff: str, obj: dict) -> Kraftstoffreferenz:
    eintrag = (obj.get("werte") or {}).get(kraftstoff) or {}
    preis = eintrag.get("preis")
    quelle_datum = eintrag.get("quelle_datum")
    if not isinstance(preis, (int, float)) or not quelle_datum:
        fb = _fallback_preis(kraftstoff)
        if fb is None:
            return _leer(kraftstoff)
        return Kraftstoffreferenz(
            kraftstoff=kraftstoff, produkt=_PRODUKT.get(kraftstoff, kraftstoff), preis=fb,
            einheit=EINHEIT, land=LAND, quelle="ENFAL-Konfiguration (Ersatzwert)",
            quelle_datum=None, abgerufen_am=None, status=STATUS_FALLBACK,
            hinweis="Ersatzwert, kein amtlicher Stand. Bitte durch deinen eigenen Preis ersetzen.",
        )

    status = STATUS_OK
    try:
        alter = (dt.date.today() - dt.date.fromisoformat(quelle_datum)).days
        if alter > FUEL_REFERENZ_MAX_ALTER_TAGE:
            status = STATUS_VERALTET
    except ValueError:
        status = STATUS_VERALTET

    hinweis = HINWEIS_DE
    if status == STATUS_VERALTET:
        hinweis = (f"Letzter amtlicher Stand vom {quelle_datum}, aktuell nicht abrufbar. "
                   f"Bitte als älteren Wert behandeln und ggf. selbst anpassen.")
    return Kraftstoffreferenz(
        kraftstoff=kraftstoff, produkt=_PRODUKT.get(kraftstoff, kraftstoff),
        preis=float(preis), einheit=EINHEIT, land=LAND, quelle=QUELLE,
        quelle_datum=quelle_datum, abgerufen_am=obj.get("abgerufen_am"),
        status=status, hinweis=hinweis,
    )


async def hole_referenzen() -> list[Kraftstoffreferenz]:
    """Benzin- und Diesel-Referenz für Deutschland. Wirft nie."""
    global _cache, _sperre

    if not FUEL_REFERENZ_ERLAUBT:
        return [_leer(k) for k in _SCHLUESSEL]

    if _ist_frisch(_cache.get("abgerufen_am")):        # 1) Prozess-Cache
        return [_baue(k, _cache) for k in _SCHLUESSEL]

    if _sperre is None:
        _sperre = asyncio.Lock()
    async with _sperre:                                # Single-Flight
        if _ist_frisch(_cache.get("abgerufen_am")):
            return [_baue(k, _cache) for k in _SCHLUESSEL]

        von_platte = _lade_von_platte()                # 2) Platte
        if von_platte and _ist_frisch(von_platte.get("abgerufen_am")):
            _cache = von_platte
            return [_baue(k, _cache) for k in _SCHLUESSEL]

        neu = await _aktualisiere()                    # 3) amtliche Quelle
        if neu:
            _cache = neu
            return [_baue(k, _cache) for k in _SCHLUESSEL]

        if von_platte:                                 # 4) letzter guter Wert
            _cache = von_platte
            return [_baue(k, _cache) for k in _SCHLUESSEL]

    return [_baue(k, {"werte": {}}) for k in _SCHLUESSEL]   # 5) Fallback / leer


def als_dict(r: Kraftstoffreferenz) -> dict:
    return asdict(r)
