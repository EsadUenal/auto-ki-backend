"""
Kraftstoff-Wortgrenzen und Source-Policy — deterministisch, KEIN Netzwerk.

Zwei getrennte Befunde aus dem Insignia-Live-Retest (2026-08-19):

  1. FUEL: `_kraftstoff_im_text` lief als reines Teilstring-Match. "elektro" traf
     damit den Wortanfang von "Elektron.", so dass die Ausstattungszeile
     "Elektron. Stabilitaets-Programm Plus (ESP)" das Diesel-Inserat 3488196893
     ("Opel Insignia B Business Elegance 2.0 CDTI") hart als Elektroauto verwarf.
     Kein Sonderfall fuer "Elektron.": die Erkennung verlangt jetzt generell
     vollstaendige Fachbegriffe an unicode-sicheren Wortgrenzen.

     Die VORgrenze sperrt bewusst nur Buchstaben, keine Ziffern — Kraftstoff-
     kuerzel stehen regelmaessig direkt am Hubraum ("2.0CDTI"). Eine Ziffern-
     sperre kostete im Korpus 11 echte Diesel.

  2. SOURCE-POLICY: mobile.de ist derzeit keine fuer die automatische
     Marktpreisbildung freigegebene Quelle (keine Erlaubnis/API-Lizenz). Treffer
     duerfen gefunden und diagnostisch gezeigt werden, aber weder Median noch
     Marktspanne noch Marktabdeckung beeinflussen. Das ist eine PRODUKT-/
     COMPLIANCE-Entscheidung und wird getrennt von fachlichen Ablehnungen
     begruendet.

    python test_fuel_source_policy.py
"""
import os
import sys
import tempfile

os.environ["AUTO_KI_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="vira_fsp_"), "test.db")
sys.path.insert(0, ".")

# §Source-Policy: Der Production-Default gibt KEINE Marktquelle zum Preisbilden
# frei (app/config.ALLOWED_MARKET_SOURCES ist leer). Dieser Test prueft die
# ANALYSE-ENGINE und braucht dafuer die historischen/synthetischen Testdomains —
# die Freigabe gilt ausschliesslich in diesem Testprozess und ist KEINE
# produktive Qualifikation der Quelle. Siehe _source_policy_testharness.py.
import _source_policy_testharness  # noqa: E402,F401

from types import SimpleNamespace                                        # noqa: E402

from app.marktvergleich import (                                         # noqa: E402
    _bewerte, _eindeutige_karosserie, _extrahiere_aus_text,
    _kraftstoff_im_text, analysiere_markt, baue_ziel,
)
from app.web_search import (                                             # noqa: E402
    SOURCE_POLICY_GRUND, darf_preisbildend_sein,
)

_fails: list[str] = []


def check(name, cond):
    print(("[OK] " if cond else "[FAIL] ") + name)
    if not cond:
        _fails.append(name)


# ══ 1) FUEL — negative Regressionen (Ausstattung ist kein Kraftstoff) ═════════
check("1: 'Elektron. Stabilitaets-Programm' ist NICHT elektro",
      _kraftstoff_im_text("Elektron. Stabilitäts-Programm Plus (ESP)") is None)
check("1b: 'elektronische Wegfahrsperre' ist NICHT elektro",
      _kraftstoff_im_text("elektronische Wegfahrsperre") is None)
check("1c: 'Elektronikpaket' ist NICHT elektro",
      _kraftstoff_im_text("Elektronikpaket") is None)
check("1d: 'elektrisch verstellbare Sitze' ist NICHT elektro",
      _kraftstoff_im_text("elektrisch verstellbare Sitze") is None)
check("1e: 'Elektronik' ist NICHT elektro",
      _kraftstoff_im_text("Komfort-Elektronik") is None)

# ══ 2) FUEL — positive Regressionen ══════════════════════════════════════════
check("2: 'Elektro' bleibt elektro", _kraftstoff_im_text("Elektro") == "elektro")
check("2b: 'Elektroauto' bleibt elektro",
      _kraftstoff_im_text("Elektroauto") == "elektro")
check("2c: 'reines Elektrofahrzeug' bleibt elektro",
      _kraftstoff_im_text("reines Elektrofahrzeug") == "elektro")
check("2d: 'Diesel' bleibt diesel", _kraftstoff_im_text("Diesel") == "diesel")
check("2e: '2.0 CDTI Diesel' bleibt diesel",
      _kraftstoff_im_text("2.0 CDTI Diesel") == "diesel")
check("2f: '2.0CDTI' ohne Leerzeichen bleibt diesel (Ziffer davor)",
      _kraftstoff_im_text("Opel Insignia 2.0CDTI Automatik") == "diesel")
check("2g: '1.6TDI' ebenso", _kraftstoff_im_text("VW Golf 1.6TDI") == "diesel")
check("2h: 'Benziner' bleibt benzin", _kraftstoff_im_text("Benziner") == "benzin")
check("2i: 'Plug-in-Hybrid' bleibt hybrid",
      _kraftstoff_im_text("Plug-in-Hybrid") == "hybrid")
check("2j: Hybrid schlaegt Benzin (Reihenfolge unveraendert)",
      _kraftstoff_im_text("Plug-in-Hybrid TSI") == "hybrid")

# ══ 3) FUEL — die reale Insignia-Karte 3488196893 ════════════════════════════
KARTE_3488196893 = (
    "* [![Opel Insignia B Business Elegance 2.0 CDTI AHK Navi L Thüringen - Gotha "
    "Vorschau](https://img.kleinanzeigen.de/api/v1/prod-ads/images/20/"
    "209d4dea-d70a-4f1d-958c-7c49d111f7fe?rule=$_2.AUTO)\n\n"
    "  2](/s-anzeige/opel-insignia-b-business-elegance-2-0-cdti-ahk-navi-l/"
    "3488196893-216-3718)\n\n  99867 Gotha\n\n  Gestern, 17:45\n\n"
    "  ## [Opel Insignia B Business Elegance 2.0 CDTI AHK Navi L]"
    "(/s-anzeige/opel-insignia-b-business-elegance-2-0-cdti-ahk-navi-l/"
    "3488196893-216-3718)\n\n"
    "  Ausstattungspakete: - Elektron. Stabilitäts-Programm Plus (ESP): "
    "Elektron. Stabilitäts-Programm...\n\n  19.995 €\n\n  84.774 km   EZ 01/2021\n")
check("3: Karte 3488196893 wird als DIESEL erkannt (vorher: elektro)",
      _kraftstoff_im_text(KARTE_3488196893) == "diesel")


# ══ 4) SOURCE-POLICY — zentrale Funktion ═════════════════════════════════════
check("4: kleinanzeigen.de darf preisbildend sein",
      darf_preisbildend_sein("https://www.kleinanzeigen.de/s-anzeige/x/1-216-1") is True)
check("4b: mobile.de darf aktuell NICHT preisbildend sein",
      darf_preisbildend_sein("https://www.mobile.de/fahrzeuge/details.html") is False)
check("4c: auch die Subdomain suchen.mobile.de ist gesperrt",
      darf_preisbildend_sein("https://suchen.mobile.de/auto/opel-insignia.html") is False)
# §Etappe-1-Matrix (Nachtrag): AutoScout24 ist technisch ein Marktplatz
# (_MARKTPLATZ_DOMAINS) und wurde bislang wie mobile.de zu Unrecht als
# preisbildend behandelt — ohne ausdrueckliche Nutzungserlaubnis/API-Lizenz
# gilt dieselbe Sperre.
check("4d: autoscout24.de darf aktuell NICHT preisbildend sein",
      darf_preisbildend_sein("https://www.autoscout24.de/angebote/x") is False)
check("4d2: auch ohne 'www.' gesperrt",
      darf_preisbildend_sein("https://autoscout24.de/angebote/x") is False)
# §Etappe-1-Abschluss: Die Policy ist eine ALLOWLIST mit leerem Production-
# Default. Eine unbekannte Domain ist damit NICHT mehr implizit erlaubt — sie
# ist nur in diesem Testprozess freigegeben, wenn die Harness sie fuehrt.
# Der ungefilterte Production-Default wird in test_source_boundary.py geprueft
# (dort bewusst OHNE Harness).
check("4e: eine Domain ausserhalb der Testfreigabe ist NICHT preisbildend",
      darf_preisbildend_sein("https://voellig-unbekannte-domain.example/x") is False)


# ══ 5) SOURCE-POLICY im Marktvergleich ═══════════════════════════════════════
BAUREIHE = {"id": "opel-insignia-b", "marke": "Opel", "modell": "Insignia",
            "generation": "B"}
MOTOR = {"bezeichnung": "2.0 Diesel (174 PS) (Facelift)", "motorcode": "F20DTH",
         "kraftstoff": "Diesel", "leistung_ps": 174}
REQ = SimpleNamespace(marke="Opel", modell="Insignia Grand Sport", baujahr=2020,
                      kilometerstand=115_000, motor="2.0 Diesel 174 PS",
                      kraftstoff="Diesel", getriebe="Automatik", preis_eur=17_900)
ZIEL = baue_ziel(BAUREIHE, MOTOR, REQ, [BAUREIHE], [])

# Identische Fahrzeugdaten auf beiden Plattformen — der EINZIGE Unterschied ist
# die Quelle. So misst der Test wirklich die Policy und nichts anderes.
def seite(url, titel):
    return {"url": url, "title": titel,
            "content": ("Opel Insignia 2.0 Diesel 17.500 € 112.000 km EZ 05/2020 . "
                        "Opel Insignia 2.0 Diesel 18.200 € 118.000 km EZ 03/2020 . "
                        "Opel Insignia 2.0 Diesel 16.900 € 120.000 km EZ 07/2020 . "
                        "Opel Insignia 2.0 Diesel 17.800 € 109.000 km EZ 02/2020")}


NUR_KA = [seite("https://www.kleinanzeigen.de/s-autos/opel-insignia/k0c216",
                "Opel Insignia gebraucht kaufen | kleinanzeigen.de")]
MIT_MOBILE = NUR_KA + [
    seite("https://suchen.mobile.de/fahrzeuge/search.html?ms=19100",
          "Opel Insignia 2.0 Diesel gebraucht kaufen bei mobile.de")]

ma_ka = analysiere_markt(NUR_KA, ZIEL, 17_900)
ma_mix = analysiere_markt(MIT_MOBILE, ZIEL, 17_900)

check("5: Kleinanzeigen liefert einen Preis-Pool",
      ma_ka.verwendet >= 3 and ma_ka.median_eur is not None)
check("5b: mobile.de erhoeht die Zahl der preisbildenden Listings NICHT",
      ma_mix.verwendet == ma_ka.verwendet)
check("5c: mobile.de veraendert den Median NICHT",
      ma_mix.median_eur == ma_ka.median_eur)
check("5d: mobile.de veraendert die Marktspanne NICHT",
      (ma_mix.spanne_min_eur, ma_mix.spanne_max_eur)
      == (ma_ka.spanne_min_eur, ma_ka.spanne_max_eur))
check("5e: keine verwendete Beobachtung stammt von mobile.de",
      all("mobile.de" not in ((b.detail_url or b.quelle_url) or "")
          for b in ma_mix.beobachtungen))
check("5f: mobile.de zaehlt NICHT als zusaetzliche Plattform (Marktabdeckung)",
      ma_mix.anzahl_domains == ma_ka.anzahl_domains
      and ma_mix.marktabdeckung == ma_ka.marktabdeckung)
check("5g: mobile.de steht in keiner verwendeten Quell-Domain",
      not any("mobile.de" in d for d in ma_mix.quellen_domains))


# ══ 6) SOURCE-POLICY wird SEPARAT begruendet ═════════════════════════════════
URL_MOB = "https://suchen.mobile.de/fahrzeuge/search.html?ms=19100"
TITEL_MOB = "Opel Insignia 2.0 Diesel gebraucht kaufen bei mobile.de"
TEXT_MOB = (TITEL_MOB + "\n\n"
            "Opel Insignia 2.0 Diesel Grand Sport 17.500 € 112.000 km EZ 05/2020 . "
            "Opel Insignia 2.0 Diesel 18.200 € 118.000 km EZ 03/2020")
roh = _extrahiere_aus_text(TEXT_MOB, URL_MOB, "market_category",
                           grenzen=(len(TITEL_MOB) + 1, len(TITEL_MOB) + 2),
                           seiten_body=_eindeutige_karosserie(URL_MOB))
bewertet = [_bewerte(b, ZIEL) for b in roh]
check("6: mobile.de-Datenpunkte werden verworfen",
      bool(bewertet) and all(b.vergleichbarkeit == "ungeeignet" for b in bewertet))
check("6b: Begruendung nennt die Source-Policy woertlich",
      bool(bewertet) and all(SOURCE_POLICY_GRUND in b.acceptance_reason
                             for b in bewertet))
check("6c: Begruendung ist KEINE Qualitaets-/Fahrzeugablehnung",
      bool(bewertet) and all(
          not any(w in b.acceptance_reason.lower()
                  for w in ("falsches modell", "anderes modell", "ungeeignetes fahrzeug",
                            "datenqualität", "andere motorvariante", "andere generation"))
          for b in bewertet))

print()
if _fails:
    print(f"{len(_fails)} FEHLER: " + "; ".join(_fails))
    sys.exit(1)
print("Alle Pruefungen bestanden.")
