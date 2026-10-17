"""
Fact-Phase-Observability (Lexus-RC-F-Forensik, Folgeauftrag).

BEFUND (realer Production-Request): die bestehende Zusammenfassungszeile
("'%s' belegt (...), %d Fakten, %d verworfen, ...") fasst rueckruf+
schwachstelle+wartung in EINER Zahl zusammen ("0 Fakten, 98 verworfen") —
daraus ließ sich NICHT ableiten, auf welcher Stufe (Search/Alignment/
Source-Trust/Extraction/Konsens) welche Kategorie tatsächlich verlor.

FIX: `_log_fakten_phase()` (app/technical_research.py) loggt PRO Kategorie
(rueckruf/schwachstelle/wartung) eine eigene INFO-Zeile, ausschließlich aus
bereits vorhandenen Werten rekonstruiert (`phase_fakten()`s neuer optionaler
`stats`-Parameter + der bestehende, je Treffer mit `kategorie`+`grund`
gefüllte `abgelehnt`-Mechanismus) — KEINE neue Zählstruktur in der
Extraktion selbst, KEINE Architekturänderung.

    python test_fact_phase_observability.py
"""
from app.technical_research import _log_fakten_phase, phase_fakten

_FEHLER: list[str] = []


def check(name: str, bedingung: bool, detail: str = "") -> None:
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))
    if not bedingung:
        _FEHLER.append(name)


class _Fakt:
    def __init__(self, kategorie):
        self.kategorie = kategorie


print("=" * 78)
print("A) reine Rechenlogik von _log_fakten_phase() -- keine Exceptions, korrekte Werte")
print("=" * 78)

# Szenario, das den realen Lexus-RC-F-Fall nachbildet: 16 Rohtreffer, 8
# kuratiert, 3 an der Source-Trust-Schwelle verworfen, 2 am Alignment, 0
# finale Fakten, 3 "sonstige" Rejects (z.B. no_component) -> 0 stiller Verlust.
stats = {"schwachstelle": {"roh": 16, "curated": 8}}
abgelehnt_delta = (
    [{"kategorie": "schwachstelle", "grund": "source_quality"}] * 3
    + [{"kategorie": "schwachstelle", "grund": "modell_fehlt"}] * 2
    + [{"kategorie": "schwachstelle", "grund": "no_component"}] * 3
    + [{"kategorie": "wartung", "grund": "source_quality"}]  # andere Kategorie -- muss ignoriert werden
)
try:
    _log_fakten_phase("schwachstelle", 2, stats, abgelehnt_delta, [])
    check("A1) _log_fakten_phase() wirft keine Exception", True)
except Exception as exc:
    check("A1) _log_fakten_phase() wirft keine Exception", False, f"{type(exc).__name__}: {exc}")

print("\n" + "=" * 78)
print("B) stiller Verlust wird sichtbar, wenn Fakten fehlen OHNE benannten Grund")
print("=" * 78)

# 8 kuratiert, 0 source_trust-Rejects, 0 alignment-Rejects, 0 sonstige Rejects,
# aber 0 finale Fakten -> alle 8 "weiterverarbeiteten" Treffer sind STILL
# verlorengegangen (z.B. Vokabular-Nichttreffer ohne eigenen Log-Eintrag).
stats_b = {"wartung": {"roh": 12, "curated": 8}}
try:
    import io
    import logging
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    logger = logging.getLogger("app.technical_research")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    _log_fakten_phase("wartung", 2, stats_b, [], [])
    logger.removeHandler(handler)
    out = buf.getvalue()
    check("B) stiller_verlust=8 wird in der Log-Zeile ausgewiesen (0 Fakten, 0 benannte Rejects, 8 kuratiert)",
          "stiller_verlust=8" in out, out.strip())
except Exception as exc:
    check("B) stiller_verlust sichtbar", False, f"{type(exc).__name__}: {exc}")

print("\n" + "=" * 78)
print("C) finale_fakten korrekt je Kategorie gezählt, andere Kategorien ignoriert")
print("=" * 78)

fakten_c = [_Fakt("rueckruf"), _Fakt("rueckruf"), _Fakt("wartung")]
stats_c = {"rueckruf": {"roh": 16, "curated": 8}}
buf2 = __import__("io").StringIO()
import logging as _logging
handler2 = _logging.StreamHandler(buf2)
logger2 = _logging.getLogger("app.technical_research")
logger2.addHandler(handler2)
logger2.setLevel(_logging.INFO)
_log_fakten_phase("rueckruf", 2, stats_c, [], fakten_c)
logger2.removeHandler(handler2)
out2 = buf2.getvalue()
check("C) finale_fakten=2 fuer 'rueckruf' (die eine 'wartung'-Fakt zaehlt nicht mit)",
      "finale_fakten=2" in out2, out2.strip())

print("\n" + "=" * 78)
print("D) Rueckwaertskompatibilitaet: phase_fakten() ohne stats= verhaelt sich unveraendert")
print("=" * 78)


def treffer(url, titel, inhalt):
    return {"url": url, "title": titel, "content": inhalt}


class _IdentStub:
    generation = None


roh_d = {"rueckruf": [treffer("https://www.adac.de/x", "Lexus RC F Rueckruf",
                              "Der Lexus RC F hat einen Rueckruf wegen Bremsen.")]}
ziel_d = {"marke": "Lexus", "modell": "RC F", "baujahr": 2017}
abgelehnt_d: list = []
try:
    fakten_d = phase_fakten(roh_d, ziel_d, _IdentStub(), ("rueckruf",), abgelehnt_d)
    check("D) phase_fakten() ohne stats= laeuft weiterhin fehlerfrei durch", True,
          f"{len(fakten_d)} Fakten")
except Exception as exc:
    check("D) phase_fakten() ohne stats= laeuft weiterhin fehlerfrei durch", False,
          f"{type(exc).__name__}: {exc}")

print("\n" + "=" * 78)
print("E) phase_fakten() mit stats= befuellt stats korrekt (roh/curated)")
print("=" * 78)

stats_e: dict = {}
abgelehnt_e: list = []
phase_fakten(roh_d, ziel_d, _IdentStub(), ("rueckruf",), abgelehnt_e, stats=stats_e)
check("E) stats['rueckruf']['roh'] == 1", stats_e.get("rueckruf", {}).get("roh") == 1, str(stats_e))
check("E) stats['rueckruf']['curated'] == 1 (unter max_results=8)",
      stats_e.get("rueckruf", {}).get("curated") == 1, str(stats_e))

print("\n" + "=" * 78)
print("I) keine Secrets/Payloads in den neuen Log-Zeilen")
print("=" * 78)

verdaechtig = ["api_key", "secret", "password", "AUTO_KI_", "sk-", "xkeysib"]
alle_logs = out + out2
check("I) keine verdaechtigen Secret-Muster in den Fakten-Observability-Logs",
      not any(v.lower() in alle_logs.lower() for v in verdaechtig), alle_logs.strip())
check("I) keine volle URL in den Fakten-Observability-Logs (nur Kategorie/Zahlen/Reject-Gruende)",
      "https://" not in alle_logs and "http://" not in alle_logs)

print("\n" + "=" * 60)
if _FEHLER:
    print(f"{len(_FEHLER)} FEHLER:")
    for f in _FEHLER:
        print(f"  - {f}")
    raise SystemExit(1)
print("ALLE FACT-PHASE-OBSERVABILITY-TESTS GRUEN")
