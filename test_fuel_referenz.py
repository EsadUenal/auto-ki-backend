"""
Kraftstoff-Referenz (Autokosten) — OFFLINE. Kein einziger echter HTTP-Request.

Die amtliche XLSX-Struktur wird als Fixture synthetisch erzeugt (exakt das
Layout des EU Weekly Oil Bulletin: Schluesselzeile 1, Einheitenzeile 3, Daten ab
Zeile 4, neueste zuerst, Preise je 1000 l). Der Netzabruf wird gestubbt.

Gedeckt (Auftrag §28 A-G plus Strukturhaerte):
  A gueltiger aktueller Wert   -> status ok, Datum sichtbar
  B Quelle down                -> letzter guter Wert aus dem Cache
  C Cache stale                -> NICHT als aktuell ausgeben (status veraltet)
  D kein Cache + Quelle down   -> preis None, Rechner bleibt benutzbar
  E Nutzer ueberschreibt       -> reine Frontend-Regel, hier nur dokumentiert
  F Benzin/Diesel getrennt     -> je eigener Wert aus der richtigen Spalte
  G Elektro                    -> gibt es bewusst NICHT (kein Fake-Ladepreis)

    python test_fuel_referenz.py
"""
import asyncio
import datetime as dt
import io
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, ".")

# Cache-Datei in ein Temp-Verzeichnis umlenken, BEVOR das Modul importiert wird:
# der Test darf niemals den echten Produktions-Cache anfassen.
_TMP = Path(tempfile.mkdtemp(prefix="enfal_fuelref_"))
import app.config as config  # noqa: E402
config.DB_PATH = _TMP / "auto_ki.db"

import app.fuel_referenz as fr  # noqa: E402

FEHLER: list[str] = []


def check(name: str, bedingung: bool) -> None:
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}")
    if not bedingung:
        FEHLER.append(name)


# ── Fixture: XLSX im Layout des Weekly Oil Bulletin ──────────────────────────

_EPOCHE = dt.date(1899, 12, 30)


def _serial(d: dt.date) -> int:
    return (d - _EPOCHE).days


def baue_xlsx(zeilen: list[tuple[dt.date, float | None, float | None]],
              *, benzin_spalte: str = "BC", diesel_spalte: str = "BD",
              einheit: str = "1000 l",
              benzin_schluessel: str = "DE_price_with_tax_euro95") -> bytes:
    """Minimales, aber strukturgleiches XLSX. Preise werden je 1000 l erwartet."""
    def zelle(ref: str, wert: str, inline: bool = False) -> str:
        if inline:
            return f'<c r="{ref}" t="inlineStr"><is><t>{wert}</t></is></c>'
        return f'<c r="{ref}"><v>{wert}</v></c>'

    xml = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">',
           '<sheetData>']
    # Zeile 1 — Schluessel
    xml.append('<row r="1">'
               + zelle("A1", "Consumer prices", inline=True)
               + zelle(f"{benzin_spalte}1", benzin_schluessel, inline=True)
               + zelle(f"{diesel_spalte}1", "DE_price_with_tax_diesel", inline=True)
               + '</row>')
    # Zeile 2 — Klartext-Produktnamen (fuer den Parser ohne Bedeutung)
    xml.append('<row r="2">' + zelle(f"{benzin_spalte}2", "Euro-super 95  (I)", inline=True) + '</row>')
    # Zeile 3 — Einheiten
    xml.append('<row r="3">'
               + zelle("A3", "Date", inline=True)
               + zelle(f"{benzin_spalte}3", einheit, inline=True)
               + zelle(f"{diesel_spalte}3", einheit, inline=True)
               + '</row>')
    # Ab Zeile 4 — Daten, neueste zuerst
    for i, (datum, benzin, diesel) in enumerate(zeilen, start=4):
        teile = [zelle(f"A{i}", str(_serial(datum)))]
        if benzin is not None:
            teile.append(zelle(f"{benzin_spalte}{i}", repr(benzin)))
        if diesel is not None:
            teile.append(zelle(f"{diesel_spalte}{i}", repr(diesel)))
        xml.append(f'<row r="{i}">' + "".join(teile) + "</row>")
    xml.append("</sheetData></worksheet>")

    puffer = io.BytesIO()
    with zipfile.ZipFile(puffer, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("xl/workbook.xml", "<workbook/>")
        z.writestr("xl/worksheets/sheet1.xml", "".join(xml))
    return puffer.getvalue()


HEUTE = dt.date.today()
AKTUELL = baue_xlsx([
    (HEUTE - dt.timedelta(days=2), 2348.0, 2457.0),
    (HEUTE - dt.timedelta(days=9), 2357.0, 2426.0),
])


# ── Stub fuer den Netzabruf ──────────────────────────────────────────────────

class _NetzAus(Exception):
    pass


def mit_quelle(daten: bytes | Exception, *, cache_leeren: bool = True):
    """Kontext: _hole_datei liefert `daten` (oder wirft), Prozess-Cache optional leer."""
    class _Ctx:
        def __enter__(self):
            self._alt = fr._hole_datei
            if cache_leeren:
                fr._cache = {}

            async def _stub():
                if isinstance(daten, Exception):
                    raise daten
                return daten
            fr._hole_datei = _stub
            return self

        def __exit__(self, *a):
            fr._hole_datei = self._alt
            return False
    return _Ctx()


def hole() -> dict[str, fr.Kraftstoffreferenz]:
    return {r.kraftstoff: r for r in asyncio.run(fr.hole_referenzen())}


def _cache_datei_loeschen() -> None:
    try:
        fr._cache_datei().unlink()
    except OSError:
        pass


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== Parser (rein, ohne Netz) ===")

werte = fr.parse_wob_deutschland(AKTUELL)
check("P1 Benzin wird aus der richtigen Spalte gelesen und auf EUR/l umgerechnet",
      werte["benzin"][0] == 2.348)
check("P2 Diesel ebenso", werte["diesel"][0] == 2.457)
check("P3 Erhebungsdatum kommt aus dem Excel-Serial",
      werte["benzin"][1] == HEUTE - dt.timedelta(days=2))
check("P4 die NEUESTE Zeile gewinnt (Datei ist absteigend sortiert)",
      werte["diesel"][1] == HEUTE - dt.timedelta(days=2))

# Luecke in der neuesten Zeile: je Kraftstoff getrennt weitersuchen.
_lueckig = baue_xlsx([
    (HEUTE - dt.timedelta(days=2), None, 2457.0),
    (HEUTE - dt.timedelta(days=9), 2357.0, 2426.0),
])
_w = fr.parse_wob_deutschland(_lueckig)
check("P5 fehlender Einzelwert kostet nicht beide Kraftstoffe",
      _w["diesel"][0] == 2.457 and _w["benzin"][0] == 2.357
      and _w["benzin"][1] == HEUTE - dt.timedelta(days=9))

# Strukturhaerte: der Parser darf NICHT auf Spaltenbuchstaben vertrauen.
_verschoben = baue_xlsx([(HEUTE, 2100.0, 2200.0)], benzin_spalte="XA", diesel_spalte="XB")
check("P6 Spalte wird ueber den Schluessel gefunden, nicht ueber den Buchstaben",
      fr.parse_wob_deutschland(_verschoben)["benzin"][0] == 2.1)


def _wirft(daten: bytes) -> bool:
    try:
        fr.parse_wob_deutschland(daten)
        return False
    except ValueError:
        return True


check("P7 unerwartete Einheit -> Fehler statt falscher Umrechnung",
      _wirft(baue_xlsx([(HEUTE, 2.348, 2.457)], einheit="l")))
check("P8 fehlende DE-Spalte -> Fehler",
      _wirft(baue_xlsx([(HEUTE, 2348.0, 2457.0)], benzin_schluessel="XX_price_with_tax_euro95")))
check("P9 unplausibler Preis -> Fehler statt Ausgabe",
      _wirft(baue_xlsx([(HEUTE, 99_000.0, 2457.0)])))
check("P10 kaputte Datei -> Fehler", _wirft(b"kein zip"))


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== A) gueltiger aktueller Wert ===")

_cache_datei_loeschen()
with mit_quelle(AKTUELL):
    a = hole()
check("A1 Benzin-Referenz mit Status ok", a["benzin"].status == fr.STATUS_OK
      and a["benzin"].preis == 2.348)
check("A2 Diesel-Referenz mit Status ok", a["diesel"].status == fr.STATUS_OK
      and a["diesel"].preis == 2.457)
check("A3 Datenstand wird mitgeliefert",
      a["benzin"].quelle_datum == (HEUTE - dt.timedelta(days=2)).isoformat())
check("A4 Quelle wird benannt", "Weekly Oil Bulletin" in a["benzin"].quelle)
check("A5 kein 'Livepreis'-Versprechen (der Hinweis schliesst es ausdruecklich aus)",
      "kein livepreis" in a["benzin"].hinweis.lower() and "Wochenwert" in a["benzin"].hinweis)
check("A5b kein Tankstellenpreis-Versprechen",
      "abweichen" in a["benzin"].hinweis and "an deiner Tankstelle" not in a["benzin"].hinweis)
check("A6 Produkt ehrlich benannt (E5, nicht E10)", "E5" in a["benzin"].produkt)
check("A7 Einheit und Land sind gesetzt",
      a["benzin"].einheit == "EUR/l" and a["benzin"].land == "DE")


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== B) Quelle down -> letzter guter Wert ===")

with mit_quelle(_NetzAus("down")):
    b = hole()
check("B1 Cache traegt den Wert weiter", b["benzin"].preis == 2.348)
check("B2 kein Abruf noetig, Status bleibt belastbar", b["benzin"].status == fr.STATUS_OK)


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== C) veralteter Stand wird nicht als aktuell ausgegeben ===")

_alt = baue_xlsx([(HEUTE - dt.timedelta(days=120), 1750.0, 1650.0)])
_cache_datei_loeschen()
with mit_quelle(_alt):
    c = hole()
check("C1 Status 'veraltet' statt 'ok'", c["benzin"].status == fr.STATUS_VERALTET)
check("C2 der Wert bleibt sichtbar, aber mit Datum", c["benzin"].preis == 1.75
      and c["benzin"].quelle_datum == (HEUTE - dt.timedelta(days=120)).isoformat())
check("C3 Hinweis benennt das Alter ausdruecklich",
      "nicht abrufbar" in c["benzin"].hinweis or "älteren Wert" in c["benzin"].hinweis)


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== D) kein Cache + Quelle down -> Feld bleibt leer ===")

_cache_datei_loeschen()
with mit_quelle(_NetzAus("down")):
    d = hole()
check("D1 kein erfundener Preis", d["benzin"].preis is None and d["diesel"].preis is None)
check("D2 Status nicht_verfuegbar", d["benzin"].status == fr.STATUS_NICHT_VERFUEGBAR)
check("D3 Hinweis verweist auf die eigene Eingabe", "eigenen Preis" in d["benzin"].hinweis)


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== Fallback-Wert nur, wenn ausdruecklich konfiguriert ===")

_alt_fb = (fr.FUEL_REFERENZ_FALLBACK_BENZIN, fr.FUEL_REFERENZ_FALLBACK_DIESEL)
fr.FUEL_REFERENZ_FALLBACK_BENZIN = "1,99"
fr.FUEL_REFERENZ_FALLBACK_DIESEL = ""
_cache_datei_loeschen()
with mit_quelle(_NetzAus("down")):
    f = hole()
check("E1 konfigurierter Ersatzwert wird genutzt und als solcher markiert",
      f["benzin"].preis == 1.99 and f["benzin"].status == fr.STATUS_FALLBACK)
check("E2 Ersatzwert nennt keine amtliche Quelle",
      "Weekly Oil Bulletin" not in f["benzin"].quelle and f["benzin"].quelle_datum is None)
check("E3 ohne Konfiguration bleibt es leer (kein stiller Default)",
      f["diesel"].preis is None and f["diesel"].status == fr.STATUS_NICHT_VERFUEGBAR)
fr.FUEL_REFERENZ_FALLBACK_BENZIN, fr.FUEL_REFERENZ_FALLBACK_DIESEL = _alt_fb


# ══════════════════════════════════════════════════════════════════════════════
print("\n=== Schalter, Elektro, Router ===")

_alt_erlaubt = fr.FUEL_REFERENZ_ERLAUBT
fr.FUEL_REFERENZ_ERLAUBT = False
_cache_datei_loeschen()
with mit_quelle(AKTUELL):
    g = hole()
check("F1 abgeschalteter Schalter -> kein Abruf, kein Preis",
      g["benzin"].preis is None and g["benzin"].status == fr.STATUS_NICHT_VERFUEGBAR)
fr.FUEL_REFERENZ_ERLAUBT = _alt_erlaubt

check("G1 es gibt KEINE Strom-/Elektro-Referenz (kein erfundener Ladepreis)",
      set(fr._SCHLUESSEL) == {"benzin", "diesel"})


def _code_ohne_doku(pfad: str) -> tuple[list[float], list[str]]:
    """Zahlen- und String-Literale des AUSFUEHRBAREN Codes (ohne Docstrings).

    Der Modul-Docstring erklaert ausdruecklich, WARUM 1,75 €/l nicht mehr als
    Platzhalter taugt und dass kein KI-Provider beteiligt ist — diese Prosa darf
    die Pruefung nicht ausloesen. Geprueft wird deshalb der Code selbst.
    """
    import ast
    baum = ast.parse(Path(pfad).read_text(encoding="utf-8"))
    docstrings = set()
    for knoten in ast.walk(baum):
        if isinstance(knoten, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            koerper = getattr(knoten, "body", [])
            if koerper and isinstance(koerper[0], ast.Expr) and isinstance(koerper[0].value, ast.Constant) \
                    and isinstance(koerper[0].value.value, str):
                docstrings.add(id(koerper[0].value))
        # Ein alleinstehender String-Ausdruck ist ebenfalls Doku, kein Code.
        if isinstance(knoten, ast.Expr) and isinstance(knoten.value, ast.Constant) \
                and isinstance(knoten.value.value, str):
            docstrings.add(id(knoten.value))
    zahlen: list[float] = []
    texte: list[str] = []
    for knoten in ast.walk(baum):
        if isinstance(knoten, ast.Constant) and id(knoten) not in docstrings:
            if isinstance(knoten.value, (int, float)) and not isinstance(knoten.value, bool):
                zahlen.append(float(knoten.value))
            elif isinstance(knoten.value, str):
                texte.append(knoten.value)
    return zahlen, texte


_zahlen, _texte = _code_ohne_doku("app/fuel_referenz.py")
# Ein hartcodierter Kraftstoffpreis waere eine DEZIMALzahl im Preisband (1,75 /
# 2,35 / 0,35). Ganze Zahlen in diesem Bereich sind Indizes und Zaehler
# (zeilen[3:], len < 4, timedelta(days=2)) und keine Preise; die beiden
# Plausibilitaetsschranken 0.20/5.00 sind ausdruecklich Grenzen, keine Werte.
_schranken = {0.20, 5.00}
_preisartig = [z for z in _zahlen
               if 0.20 < z < 5.00 and z != int(z) and z not in _schranken]
check("G2 kein hartcodierter Beispielpreis im ausfuehrbaren Code", _preisartig == [])
if _preisartig:
    print("      gefunden:", _preisartig)

_zahlen_r, _texte_r = _code_ohne_doku("app/routers/autokosten.py")
_router = Path("app/routers/autokosten.py").read_text(encoding="utf-8")
check("R1 Endpunkt verlangt den Consumer-API-Key", "verify_api_key(request)" in _router)
check("R2 Endpunkt haengt an keinem Login/Kontingent",
      "get_current_user_id" not in _router and "require_check_access" not in _router)
_main = Path("app/main.py").read_text(encoding="utf-8")
check("R3 Router ist in der App registriert", "autokosten.router" in _main)
check("R4 kein KI-/Search-Provider im ausfuehrbaren Autokosten-Pfad",
      not any(w in t.lower() for t in _texte + _texte_r for w in ("gemini", "tavily", "carapi")))
_importe = [z.module or "" for z in __import__("ast").walk(
    __import__("ast").parse(Path("app/fuel_referenz.py").read_text(encoding="utf-8")))
    if isinstance(z, __import__("ast").ImportFrom)]
check("R5 der Provider importiert keinen KI-/Search-Baustein",
      not any(w in m for m in _importe for w in ("llm", "gemini", "web_search", "marktrecherche")))

print()
print(f"{len(FEHLER)} FAIL" if FEHLER else "ALLE TESTS GRÜN")
for f_ in FEHLER:
    print("  -", f_)
sys.exit(1 if FEHLER else 0)
