"""
Reliability-Sprint §6/§7/§13 + §15 B/D — Kanonisches Preisurteil & Quality-Gate.

Deterministisch, KEIN Netzwerk.

    python test_preisurteil.py
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, ".")

from app.models import Marktanalyse, Preisbeobachtung
from app.preisurteil import bewerte_preis, preis_bewertung_aus_verdict
from app.marktvergleich import _datenqualitaet
from app.marktrecherche import research_status
from app.key_findings import build_key_findings_kauf

_fails = []
def check(name, cond):
    print(("[OK] " if cond else "[FAIL] ") + name)
    if not cond:
        _fails.append(name)


def ma(median, lo, hi, quali="hoch"):
    return Marktanalyse(gefunden=20, verwendet=12, median_eur=median,
                        spanne_min_eur=lo, spanne_max_eur=hi, datenqualitaet=quali)


# ══ §15 B — Preisverdikt in vier Lagen ═══════════════════════════════════════
# 6) innerhalb der Spanne, deutlich über Median -> oberes_segment (NICHT marktgerecht)
pa = bewerte_preis(ma(20000, 17000, 25000), 24000, check_typ="kauf")
check("6: in Spanne + deutlich über Median -> oberes_segment", pa.verdict == "oberes_segment")
check("6: begründung nennt Median-Lage UND oberes Ende",
      "über dem median" in pa.begruendung.lower() and "oberen" in pa.begruendung.lower())
check("6: NICHT 'marktgerecht'", pa.verdict != "marktgerecht")

# 7) innerhalb der Spanne, nahe Median -> marktgerecht
pa = bewerte_preis(ma(20000, 17000, 25000), 20400, check_typ="kauf")
check("7: in Spanne, nahe Median -> marktgerecht", pa.verdict == "marktgerecht")

# 8) außerhalb oberer Spanne -> ueber / deutlich_ueber
pa = bewerte_preis(ma(20000, 17000, 25000), 27000, check_typ="kauf")
check("8: über oberer Spanne (+35 %) -> deutlich_ueber", pa.verdict == "deutlich_ueber")
# knapp über der Spanne, aber nur mäßig über dem Median (+6 %) -> "ueber" (nicht "deutlich")
pa = bewerte_preis(ma(24000, 21000, 25000), 25500, check_typ="kauf")
check("8b: knapp über oberer Spanne, +6 % zum Median -> ueber", pa.verdict == "ueber")

# 9) unter Median
pa = bewerte_preis(ma(20000, 17000, 25000), 17500, check_typ="kauf")
check("9: in Spanne, klar unter Median (-12.5 %) -> unter", pa.verdict == "unter")
pa = bewerte_preis(ma(20000, 17000, 25000), 15000, check_typ="kauf")
check("9b: unter der Spanne (-25 %) -> deutlich_unter", pa.verdict == "deutlich_unter")

# ── position_in_range korrekt ─────────────────────────────────────────────────
check("pos: 24000 in [17000,25000] -> oberes_drittel",
      bewerte_preis(ma(20000, 17000, 25000), 24000).position_in_range == "oberes_drittel")
check("pos: 27000 -> ueber_spanne",
      bewerte_preis(ma(20000, 17000, 25000), 27000).position_in_range == "ueber_spanne")

# ── ohne Median -> unbekannt ──────────────────────────────────────────────────
pa = bewerte_preis(Marktanalyse(datenqualitaet="niedrig"), 20000)
check("unbekannt: ohne Median -> verdict unbekannt", pa.verdict == "unbekannt")

# ══ §15 10 — Alle Bereiche nutzen DASSELBE Urteil ════════════════════════════
# Der Marktvergleich-Insight + Key Findings leiten aus demselben pa ab.
from app.evidence import build_insights
class _Req:  # minimales Request-Objekt
    baujahr = 2019; kilometerstand = 120000; kraftstoff = "Diesel"; motor = "320d"
    marke = "BMW"; modell = "3er"
m = ma(20000, 17000, 25000)
m.angebot_eur = 24000; m.differenz_eur = 4000; m.differenz_pct = 20.0
insights = build_insights(None, None, [{"typ": "web", "titel": "x", "url": "https://autoscout24.de/a", "qualitaet": "Marktplatz"}],
                          _Req(), check_typ="kauf", marktanalyse=m)
pa = bewerte_preis(m, 24000, check_typ="kauf")
kf = build_key_findings_kauf(_Req(), None, None, insights, pa)
preis_kf = [f for f in kf if f.kategorie == "preis"]
check("10: Key-Findings-Preisurteil = kanonisches Verdikt (oberes_segment)",
      bool(preis_kf) and preis_kf[0].titel == "Oberes Marktsegment")
check("10: preis_bewertung-Mapping konsistent zum Verdikt",
      preis_bewertung_aus_verdict(pa.verdict) == "teuer")

# ══ §15 D 14-17 — Datenqualität & research_status ════════════════════════════
def obs(preis, dom, bj=2019, km=120000):
    return Preisbeobachtung(preis_eur=preis, baujahr=bj, kilometerstand=km,
                            quelle_domain=dom, quelle_url=f"https://{dom}/{preis}",
                            vergleichbarkeit="aehnlich")

# hoch: 8 Treffer, 2 Portale, attributvollständig, enge Streuung (rel ~0.1)
hoch = [obs(20000, "mobile.de"), obs(20500, "mobile.de"), obs(21000, "autoscout24.de"),
        obs(21500, "autoscout24.de"), obs(20800, "mobile.de"), obs(21200, "autoscout24.de"),
        obs(20300, "mobile.de"), obs(21100, "autoscout24.de")]
check("D: 8 nahe Treffer aus 2 Portalen, enge Streuung -> hoch",
      _datenqualitaet(hoch, 20800, 20300, 21500) == "hoch")

# mittel: 4 Treffer, 2 Portale, breitere (noch kontrollierte) Streuung
mittel = [obs(18000, "mobile.de"), obs(22000, "autoscout24.de"),
          obs(20000, "mobile.de"), obs(24000, "autoscout24.de")]
check("D: 4 Treffer, 2 Portale, breitere Streuung -> mittel",
      _datenqualitaet(mittel, 21000, 19000, 23500) == "mittel")

# niedrig: nur 1 Portal
niedrig = [obs(20000, "mobile.de"), obs(21000, "mobile.de"), obs(22000, "mobile.de")]
check("D: nur 1 Portal -> niedrig", _datenqualitaet(niedrig, 21000, 20000, 22000) == "niedrig")

# 14/15: niedrige Qualität -> research_failed (kein fertiger Check)
check("14/15: niedrig -> research_failed", research_status(ma(None, None, None, "niedrig")) == "research_failed")
check("15b: kein Median -> research_failed",
      research_status(Marktanalyse(median_eur=None, datenqualitaet="mittel")) == "research_failed")
# 16: hoch -> completed_high
check("16: hoch + Median -> completed_high", research_status(ma(20000, 17000, 25000, "hoch")) == "completed_high")
# 17: mittel -> completed_medium
check("17: mittel + Median -> completed_medium", research_status(ma(20000, 17000, 25000, "mittel")) == "completed_medium")

print()
if _fails:
    print(f"{len(_fails)} Test(s) fehlgeschlagen.")
    sys.exit(1)
print("Alle Preisurteil-/Quality-Gate-Tests bestanden.")
