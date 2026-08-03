"""
Test: Ersatzteilsuche-Hilfslogik (app/routers/ersatzteile) — deterministisch, kein Netz.

Deckt die allgemeine (nicht fahrzeugspezifische) Normalisierung/Synonymik, die
Teile-Kategorie und den ehrlichen Mindestnutzen-Fallback (§8/§9) ab.

Ausfuehren:  python test_ersatzteil.py
"""
import os
import tempfile

os.environ["AUTO_KI_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="vira_et_"), "test.db")

from app.routers.ersatzteile import _teil_varianten, _kategorie, _mindestnutzen  # noqa: E402

FEHLER = []


def check(name, cond):
    print(f"[{'OK' if cond else 'FEHLER'}] {name}")
    if not cond:
        FEHLER.append(name)


# ── Synonyme/Varianten (allgemein, kein Fahrzeug-Hardcoding) ─────────────────
v = _teil_varianten("Bremsscheiben vorne")
low = [x.lower() for x in v]
check("1: Originaltext bleibt erste Variante", v[0] == "Bremsscheiben vorne")
check("2: Synonym 'vorderachse' erzeugt", any("vorderachse" in x for x in low))
check("3: englische Übersetzung 'brake discs' o.ä. erzeugt",
      any("brake" in x for x in low))
check("4: begrenzte Variantenzahl (keine Explosion)", len(v) <= 6)

v2 = _teil_varianten("Zündkerzen")
check("5: 'spark plugs' als Synonym", any("spark plugs" in x.lower() for x in v2))

# ── Teile-Kategorie (für Mindestnutzen) ─────────────────────────────────────
check("6: Bremsscheiben -> Kategorie Bremsen", _kategorie("Bremsscheiben vorne") == "Bremsen")
check("7: Ölfilter -> Filter & Service", _kategorie("Ölfilter") == "Filter & Service")
check("8: Stoßdämpfer -> Fahrwerk", _kategorie("Stoßdämpfer hinten") == "Fahrwerk")
check("9: unbekanntes Teil -> None (nichts erfinden)", _kategorie("Glitzer-Aufkleber") is None)

# ── Mindestnutzen: ehrlich, erkennt Fahrzeug+Teil, nennt nächsten Schritt ────
m = _mindestnutzen("BMW M3 E92", "Bremsscheiben vorne")
check("10: Mindestnutzen nennt Fahrzeug", "BMW M3 E92" in m)
check("11: Mindestnutzen nennt Teil", "Bremsscheiben vorne" in m)
check("12: Mindestnutzen nennt Kategorie (Bremsen)", "Bremsen" in m)
check("13: Mindestnutzen nennt konkreten nächsten Schritt (FIN/Maß/OE)",
      ("FIN" in m or "OE" in m.upper()) and ("Durchmesser" in m or "Maß" in m or "mm" in m))
check("14: Mindestnutzen erfindet keine Teilenummer (Hinweis dazu)",
      "keine unbestätigten Teilenummern" in m.lower() or "keine unbestätigte" in m.lower())

print()
if FEHLER:
    print(f"{len(FEHLER)} FEHLER: " + ", ".join(FEHLER))
    raise SystemExit(1)
print("Alle Ersatzteil-Hilfslogik-Tests bestanden.")
