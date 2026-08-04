"""
Reliability-Sprint §10/§11/§12 + §15 E — Verkaufs-Preisstrategie & Quellen-Dedup.

Deterministisch, KEIN Netzwerk.

    python test_verkaufsstrategie.py
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, ".")

from app.models import Marktanalyse, Preisbeobachtung, EvidenceQuelle
from app.preisurteil import verkaufs_strategie, KAT_SCHNELL, KAT_DURCHSCHNITT, KAT_LAENGER
from app.evidence import _verwendete_quellen, _dedup_quellen

_fails = []
def check(name, cond):
    print(("[OK] " if cond else "[FAIL] ") + name)
    if not cond:
        _fails.append(name)


# ══ §15 22 — Preisstufen stammen aus zentraler Marktlogik ════════════════════
m = Marktanalyse(median_eur=17000, spanne_min_eur=14900, spanne_max_eur=20400, datenqualitaet="mittel")
s = verkaufs_strategie(m)
check("22: Schnellverkauf = untere Quartilsgrenze", s["schnellverkaufs_preis"] == 14900)
check("22: Empfohlener Preis = Median", s["empfohlener_preis"] == 17000)
check("22: Maximalpreis = obere Quartilsgrenze", s["maximal_preis"] == 20400)
check("22: monoton schnell <= empfohlen <= maximal",
      s["schnellverkaufs_preis"] <= s["empfohlener_preis"] <= s["maximal_preis"])

# ══ §15 20 — Keine erfundenen Verkaufszeiten (nur Kategorien) ═════════════════
check("20: Verkaufsdauer = Kategorie (schnell)", s["verkaufsdauer_schnell"] == KAT_SCHNELL)
check("20: Verkaufsdauer = Kategorie (empfohlen)", s["verkaufsdauer_empfohlen"] == KAT_DURCHSCHNITT)
check("20: Verkaufsdauer = Kategorie (maximal)", s["verkaufsdauer_maximal"] == KAT_LAENGER)
check("20: Kategorien enthalten keine Tages-/Wochenzahlen",
      not any(ch.isdigit() for ch in (s["verkaufsdauer_schnell"] + s["verkaufsdauer_maximal"])))

# ══ §15 19 — Keine präzisen Preise aus niedriger/fehlender Datenbasis ═════════
check("19: ohne Median -> keine Strategie (None)",
      verkaufs_strategie(Marktanalyse(median_eur=None, datenqualitaet="niedrig")) is None)

# ══ §12/§15 21 — Quellen-Deduplizierung (Domain + Ergebnis) ══════════════════
# Zwei verschiedene URLs derselben Domain, beide nur mit Domain-Titel -> 1 Eintrag
ma_dup = Marktanalyse(
    median_eur=17000, spanne_min_eur=15000, spanne_max_eur=19000, datenqualitaet="mittel",
    beobachtungen=[
        Preisbeobachtung(preis_eur=16000, quelle_domain="12gebrauchtwagen.de",
                         quelle_url="https://12gebrauchtwagen.de/a", vergleichbarkeit="aehnlich"),
        Preisbeobachtung(preis_eur=17500, quelle_domain="12gebrauchtwagen.de",
                         quelle_url="https://12gebrauchtwagen.de/b", vergleichbarkeit="aehnlich"),
        Preisbeobachtung(preis_eur=18000, quelle_domain="autoscout24.de",
                         quelle_url="https://autoscout24.de/x", vergleichbarkeit="aehnlich"),
    ])
quellen = _verwendete_quellen([], ma_dup)
domains = [q.titel for q in quellen]
check("21: dieselbe Domain erscheint NICHT doppelt (12gebrauchtwagen.de)",
      domains.count("12gebrauchtwagen.de") == 1)
check("21: verschiedene Domains bleiben erhalten (autoscout24.de vorhanden)",
      any("autoscout24" in (q.url or "") for q in quellen))

# Exakte URL-Query-Duplikate kollabieren ebenfalls
q_query = _dedup_quellen([
    EvidenceQuelle(typ="web", url="https://mobile.de/x?a=1", titel="Mobile A"),
    EvidenceQuelle(typ="web", url="https://mobile.de/x?a=2", titel="Mobile A"),
])
check("21: gleiche kanonische URL (nur Query verschieden) -> 1 Eintrag", len(q_query) == 1)

print()
if _fails:
    print(f"{len(_fails)} Test(s) fehlgeschlagen.")
    sys.exit(1)
print("Alle Verkaufsstrategie-/Quellen-Dedup-Tests bestanden.")
