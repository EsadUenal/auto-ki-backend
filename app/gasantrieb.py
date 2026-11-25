"""
GAS-1: Gasantrieb als eigene Achse (Erdgas/CNG, Autogas/LPG, Gasart unbekannt).

WARUM
-----
`motorvariante.kraftstoff` kennt kein Gas: ein Erdgas- oder Autogasfahrzeug steht dort als "Benzin"
(Ottomotor). Ohne eigene Achse lief ein Gasfahrzeug im KaufCheck als gewöhnlicher Benziner; eine Angabe
"CNG" verpuffte in allen Kraftstoff-Normalisierungen. Dieses Modul ist die EINE Stelle für

  * die Normalisierung einer Nutzerangabe (Eingabe/API),
  * die Gasart einer ENFAL-Variante MIT Herkunft (motorvariante_geltung, AF-10/GAS-1),
  * die zusammengeführte Gas-Identität eines Checks (Angabe gegen Datensatz, ohne Umetikettierung),
  * die Anwendbarkeit gasbezogener Fakten/Rückrufe (zusätzliche Achse, nie allein ein variant_match),
  * Verbrauchsangaben mit Einheit, Zyklus und Bezug (Kontext, Chat),
  * neutrale Verkäuferfragen/Unterlagen ohne Fristen oder Prüfpflichten.

GRUNDSÄTZE
----------
  * CNG und LPG sind verschieden. Eine unbekannte Gasart bleibt "GAS_UNBEKANNT", nie geraten.
  * Eine Nutzerangabe bleibt eine Angabe, kein unabhängiger Beleg.
  * Eine mögliche Nachrüstung ist keine belegte Werksausführung.
  * Gasbegriffe in allgemeinen Baureihentexten machen keine Motorvariante zum Gasfahrzeug: die
    Kennzeichnung stammt ausschließlich aus `motorvariante_geltung` (Variantenbezug).
  * Keine kg/l-Gleichsetzung; ein Literwert eines Gasfahrzeugs gilt nur mit belegtem Bezug.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

CNG = "CNG"
LPG = "LPG"
GAS_UNBEKANNT = "GAS_UNBEKANNT"
GASARTEN = (CNG, LPG, GAS_UNBEKANNT)

ANZEIGE = {CNG: "Erdgas (CNG)", LPG: "Autogas (LPG)", GAS_UNBEKANNT: "Gasantrieb, Gasart unbekannt"}
_SCOPE_LABEL = {CNG: "Erdgas-(CNG-)Ausführungen", LPG: "Autogas-(LPG-)Ausführungen",
                GAS_UNBEKANNT: "Gasfahrzeuge"}

# ── Eingabe ──────────────────────────────────────────────────────────────────
# Werte, mit denen eine Nutzerangabe (Feld `antriebsgas` ODER ein Gaswert im Feld `kraftstoff`)
# einen Gasantrieb nennt. "gas" allein heißt ausdrücklich: Gasart unbekannt.
_EINGABE = {
    "cng": CNG, "erdgas": CNG, "erdgas (cng)": CNG, "erdgas/cng": CNG,
    "lpg": LPG, "autogas": LPG, "autogas (lpg)": LPG, "autogas/lpg": LPG,
    "flüssiggas": LPG, "fluessiggas": LPG,
    "gas": GAS_UNBEKANNT, "gas_unbekannt": GAS_UNBEKANNT, "gas (art unbekannt)": GAS_UNBEKANNT,
    "gasantrieb": GAS_UNBEKANNT,
}


def normalisiere_angabe(wert: str | None) -> str | None:
    """Nutzerangabe -> CNG | LPG | GAS_UNBEKANNT | None (kein Gaswert)."""
    if wert is None:
        return None
    t = re.sub(r"\s+", " ", str(wert).strip().lower())
    if t.upper() in GASARTEN:
        return t.upper()
    return _EINGABE.get(t)


# ── Wortlaut ─────────────────────────────────────────────────────────────────
_GRENZE_V, _GRENZE_N = r"(?<![A-Za-zÄÖÜäöüß])", r"(?![A-Za-zÄÖÜäöüß])"
_WORT_CNG = re.compile(_GRENZE_V + r"(?:cng|erdgas\w*)" + _GRENZE_N, re.I)
_WORT_LPG = re.compile(_GRENZE_V + r"(?:lpg|autogas\w*|fl(?:ü|ue)ssiggas\w*)" + _GRENZE_N, re.I)
# Gasanlage als Bauteil, OHNE Gasart. Bewusst nicht: Gasdruckfeder, Gasgenerator (Airbag), Gaspedal,
# Gasentladungslampe, Abgas* (die Vorgrenze schließt "Abgasanlage" aus).
_WORT_GASANLAGE = re.compile(
    _GRENZE_V + r"(?:gasanlage\w*|gastank\w*|gasflasche\w*|gasbeh(?:ä|ae)lter\w*|gasbetrieb\w*"
    r"|gasdruckregler\w*|gasverdampfer\w*|gaseinblas\w*|gasfahrzeug\w*|gasantrieb\w*)" + _GRENZE_N, re.I)
# Herstellerbezeichnungen für Gasantrieb (Variantenname). Nur für Kandidaten OHNE Datensatzzeile
# (Web-Kandidaten im AutoFinder, Vollständigkeitsprüfung): sie nennen einen Gasantrieb, aber keine Gasart.
_WORT_GAS_BEZEICHNUNG = re.compile(
    _GRENZE_V + r"(?:tgi|g-?tec|g-?tron|ecofuel|bi-?fuel|bi-?power|natural\s+power)" + _GRENZE_N, re.I)


def _gasart_im_wortlaut(text: str | None) -> str | None:
    """CNG oder LPG nur, wenn der Text GENAU eine Gasart ausdrücklich nennt."""
    t = text or ""
    cng, lpg = bool(_WORT_CNG.search(t)), bool(_WORT_LPG.search(t))
    if cng and not lpg:
        return CNG
    if lpg and not cng:
        return LPG
    return None


def gas_laut_bezeichnung(text: str | None) -> str | None:
    """Gasantrieb aus einer VARIANTEN-Bezeichnung (Web-Kandidat, Vollständigkeitsprüfung).

    CNG/LPG nur bei ausdrücklichem Wortlaut; ein Herstellerkürzel (TGI, G-TEC, EcoFuel …) oder ein
    Gasbauteil nennt nur "Gasfahrzeug" -> GAS_UNBEKANNT. Kein Gasbegriff -> None."""
    t = text or ""
    art = _gasart_im_wortlaut(t)
    if art:
        return art
    if _WORT_CNG.search(t) or _WORT_LPG.search(t) or _WORT_GAS_BEZEICHNUNG.search(t) \
            or _WORT_GASANLAGE.search(t):
        return GAS_UNBEKANNT
    return None


def gas_bauteil_scope(text: str | None) -> str | None:
    """Gasbezug eines Fakts/Rückrufs (Mangel, Bauteil): CNG | LPG | GAS_UNBEKANNT (Gasanlage ohne
    Gasart, oder beide Gasarten) | None. Herstellerkürzel zählen hier NICHT (kein Bauteil)."""
    t = text or ""
    cng, lpg = bool(_WORT_CNG.search(t)), bool(_WORT_LPG.search(t))
    if cng and not lpg:
        return CNG
    if lpg and not cng:
        return LPG
    if cng or lpg or _WORT_GASANLAGE.search(t):
        return GAS_UNBEKANNT
    return None


# ── Gasart einer ENFAL-Variante ──────────────────────────────────────────────

def gasart_der_variante(m: dict | None) -> tuple[str | None, str | None]:
    """(Gasart, Herkunft) aus `motorvariante_geltung` dieser Variante. Ohne Kennzeichnung (None, None):
    das heißt NICHT "kein Gas belegt", nur "nicht als Gasfahrzeug gekennzeichnet"."""
    g = ((m or {}).get("antriebsgas") or "").strip()
    if not g:
        return None, None
    if g.upper() in (CNG, LPG):
        return g.upper(), "ENFAL-Fahrzeugdaten mit hinterlegter Quelle"
    wortlaut = _gasart_im_wortlaut((m or {}).get("bezeichnung"))
    if wortlaut:
        return wortlaut, "laut Variantenbezeichnung im ENFAL-Datensatz, Gasart nicht unabhängig geprüft"
    return GAS_UNBEKANNT, "ENFAL-Datensatz kennzeichnet ein Gasfahrzeug, Gasart nicht geprüft"


# ── Gas-Identität eines Checks ───────────────────────────────────────────────
ST_DATENSATZ = "datensatz"                     # nur der Datensatz kennt den Gasantrieb
ST_BESTAETIGT = "bestaetigt"                   # Angabe und Datensatz: Gasfahrzeug, Gasart verträglich
ST_NACHRUESTUNG = "nachruestung_moeglich"      # Angabe Gas, erkannte Variante ist kein Gasfahrzeug ab Werk
ST_OHNE_ZUORDNUNG = "angabe_ohne_zuordnung"    # Angabe Gas, keine eindeutige Variante
ST_WIDERSPRUCH_GASART = "widerspruch_gasart"   # CNG gegen LPG
ST_SONDERKONFIGURATION = "sonderkonfiguration" # Gas gegen Diesel/Elektro
ST_ANGABE_BENZIN = "angabe_benzin"             # Angabe ausdrücklich Benzin, Datensatz: Gasvariante
UNGEKLAERT = (ST_WIDERSPRUCH_GASART, ST_SONDERKONFIGURATION, ST_ANGABE_BENZIN)


@dataclass(frozen=True)
class GasIdentitaet:
    art: str                        # CNG | LPG | GAS_UNBEKANNT
    status: str
    angabe: str | None              # normalisierte Nutzerangabe
    datensatz: str | None           # Gasart laut Datensatz
    datensatz_herkunft: str | None
    hinweis: str                    # Nutzertext (Bericht, Fragen)

    @property
    def anzeige(self) -> str:
        return ANZEIGE[self.art]

    @property
    def belegt_durch_datensatz(self) -> bool:
        """Nur dann darf die Gasachse eine Variantenaussage stützen (nie eine bloße Angabe)."""
        return self.datensatz is not None and self.status in (ST_DATENSATZ, ST_BESTAETIGT)

    @property
    def ungeklaert(self) -> bool:
        return self.status in UNGEKLAERT


def _nutzer_fuel(req) -> str | None:
    """Ausdrücklich angegebene Kraftstoffart OHNE Gaswert (benzin/diesel/elektro/hybrid)."""
    roh = (getattr(req, "kraftstoff", None) or "").strip().lower()
    if not roh or normalisiere_angabe(roh):
        return None
    if "diesel" in roh:
        return "diesel"
    if "elektro" in roh or roh in ("ev", "bev"):
        return "elektro"
    if "hybrid" in roh:
        return "hybrid"
    if "benzin" in roh:
        return "benzin"
    return None


def gas_identitaet(req, motor_match: dict | None) -> GasIdentitaet | None:
    """Gasantrieb dieses Checks aus Nutzerangabe und erkannter Variante. None = kein Gasbezug bekannt."""
    angabe = normalisiere_angabe(getattr(req, "antriebsgas", None)) \
        or normalisiere_angabe(getattr(req, "kraftstoff", None))
    ds, ds_herkunft = gasart_der_variante(motor_match)
    nutzer_fuel = _nutzer_fuel(req)
    m = motor_match or {}
    db_fuel = None
    if m:
        from app.kraftstoff_powertrain import canonical_fuel
        db_fuel = canonical_fuel(m.get("kraftstoff"), m.get("bezeichnung"), m.get("motorcode"))
        if (m.get("kraftstoff") or "").strip().lower() == "elektro":
            db_fuel = "elektro"

    if angabe is None and ds is None:
        return None
    if angabe is None:
        if nutzer_fuel == "benzin":
            return GasIdentitaet(ds, ST_ANGABE_BENZIN, None, ds, ds_herkunft,
                                 f"Kraftstoff laut Angabe Benzin, die erkannte ENFAL-Variante ist jedoch ein "
                                 f"Gasfahrzeug ({ANZEIGE[ds]}, {ds_herkunft}). Vor Ort klären, ob eine Gasanlage "
                                 "verbaut ist und welche.")
        if nutzer_fuel in ("diesel", "elektro"):
            return GasIdentitaet(ds, ST_SONDERKONFIGURATION, None, ds, ds_herkunft,
                                 f"Kraftstoff laut Angabe {nutzer_fuel.capitalize()}, die erkannte ENFAL-Variante "
                                 f"ist jedoch ein Gasfahrzeug ({ANZEIGE[ds]}). Ungeklärt: vor Ort klären, was "
                                 "tatsächlich verbaut ist.")
        return GasIdentitaet(ds, ST_DATENSATZ, None, ds, ds_herkunft,
                             f"Herkunft: {ds_herkunft}. Am Fahrzeug nicht geprüft.")
    # Ab hier: ausdrückliche Gasangabe.
    fremd = nutzer_fuel if nutzer_fuel in ("diesel", "elektro") else (
        db_fuel if db_fuel in ("diesel", "elektro") else None)
    if fremd:
        quelle = "laut Angabe" if fremd == nutzer_fuel else "laut erkannter ENFAL-Variante"
        return GasIdentitaet(angabe, ST_SONDERKONFIGURATION, angabe, ds, ds_herkunft,
                             f"Gasantrieb laut Angabe ({ANZEIGE[angabe]}), Kraftstoff {quelle} jedoch "
                             f"{fremd.capitalize()}. Ungeklärte Sonderkonfiguration oder Eingabefehler: vor Ort "
                             "klären, was tatsächlich verbaut ist. ENFAL bewertet eine solche Gasanlage nicht.")
    if ds is not None:
        if angabe != GAS_UNBEKANNT and ds != GAS_UNBEKANNT and angabe != ds:
            return GasIdentitaet(GAS_UNBEKANNT, ST_WIDERSPRUCH_GASART, angabe, ds, ds_herkunft,
                                 f"Die Angabe nennt {ANZEIGE[angabe]}, der ENFAL-Datensatz {ANZEIGE[ds]} "
                                 f"({ds_herkunft}). Die Gasart ist damit ungeklärt und bleibt unbekannt, bis sie "
                                 "vor Ort geklärt ist.")
        art = angabe if angabe != GAS_UNBEKANNT else ds
        return GasIdentitaet(art, ST_BESTAETIGT, angabe, ds, ds_herkunft,
                             "Laut Angabe; der ENFAL-Datensatz führt diese Variante ebenfalls als "
                             f"Gasfahrzeug ({ds_herkunft}). Am Fahrzeug nicht geprüft.")
    if m:
        return GasIdentitaet(angabe, ST_NACHRUESTUNG, angabe, None, None,
                             "Laut Angabe. Die erkannte ENFAL-Variante ist nicht als "
                             "Gasfahrzeug ab Werk gekennzeichnet: eine Werksausführung ist nicht belegt, eine "
                             "nachgerüstete Gasanlage ist möglich. Ausführung und Unterlagen klären.")
    return GasIdentitaet(angabe, ST_OHNE_ZUORDNUNG, angabe, None, None,
                         "Laut Angabe; ohne eindeutige ENFAL-Motorzuordnung nicht unabhängig "
                         "bestätigt. Ob ab Werk oder nachgerüstet, ist offen.")


def feld_eintrag(gas: GasIdentitaet) -> dict:
    """Identitäts-Feldeintrag (dieselbe Form wie app/vehicle_identity._feld_eintrag) plus Gasstatus."""
    from app.vehicle_identity import _feld_eintrag
    if gas.status == ST_DATENSATZ:
        e = _feld_eintrag(gas.art, primary="enfal", confidence="mittel", state="reference_only",
                          reference=gas.datensatz)
    elif gas.status == ST_BESTAETIGT:
        e = _feld_eintrag(gas.art, primary="user", confirmed_by=["enfal"], confidence="mittel",
                          state="user_confirmed", raw_user=gas.angabe, reference=gas.datensatz)
    elif gas.status in (ST_NACHRUESTUNG, ST_OHNE_ZUORDNUNG):
        e = _feld_eintrag(gas.art, primary="user", confidence="niedrig", state="user_only",
                          raw_user=gas.angabe)
    else:
        e = _feld_eintrag(gas.art, primary="user" if gas.angabe else "enfal", confidence="niedrig",
                          state="conflict", raw_user=gas.angabe,
                          reference=ANZEIGE[gas.datensatz] if gas.datensatz else None)
    e.update({"gas_status": gas.status, "gas_hinweis": gas.hinweis, "gas_angabe": gas.angabe,
              "gas_datensatz": gas.datensatz, "gas_datensatz_herkunft": gas.datensatz_herkunft})
    return e


def setze_in_identitaet(identity, gas: GasIdentitaet | None) -> None:
    """Gasachse in die kanonische Identität (nur der KaufCheck ruft das auf). Markiert die Identität
    auch OHNE Gasbezug als bewertet: dann gilt "kein Gasantrieb bekannt" (nicht "kein Gas belegt")."""
    if identity is None:
        return
    identity.gas_bewertet = True
    if gas is None:
        return
    identity.gas = gas.art
    identity.field_evidence["gas"] = feld_eintrag(gas)


def aus_identitaet(identity) -> GasIdentitaet | None:
    """Rekonstruiert die Gas-Identität aus der kanonischen Identität (None = nicht bewertet/kein Gas)."""
    fe = ((getattr(identity, "field_evidence", None) or {}).get("gas")) if identity is not None else None
    if not fe or not getattr(identity, "gas", None):
        return None
    return GasIdentitaet(identity.gas, fe["gas_status"], fe.get("gas_angabe"), fe.get("gas_datensatz"),
                         fe.get("gas_datensatz_herkunft"), fe.get("gas_hinweis") or "")


def gas_bewertet(identity) -> bool:
    """Die Gasachse gilt nur, wo ein Check sie ausdrücklich bewertet hat (KaufCheck). Andere Pfade
    (VerkaufCheck, Chat) bleiben unverändert."""
    return bool(identity is not None and getattr(identity, "gas_bewertet", False))


# ── Anwendbarkeit gasbezogener Fakten/Rückrufe ───────────────────────────────

def scope_passt(scope: str, gas: GasIdentitaet | None, fuel: str | None,
                powertrains=None) -> bool | None:
    """Passt ein Gasbezug (CNG/LPG/GAS_UNBEKANNT) zu diesem Fahrzeug?

    False nur bei positivem Widerspruch: Diesel/Elektro ohne Gasangabe, oder die andere Gasart.
    None (unklar): kein Gasantrieb bekannt (ein Benziner kann nachgerüstet sein), Gasart unbekannt,
    oder ungeklärte Angaben. True: Gasantrieb mit passender Gasart. True allein ist KEINE
    Variantenbetroffenheit (siehe `stuetzt_variante`)."""
    if gas is None:
        pts = set(powertrains or ())
        if fuel in ("diesel", "elektro") or (pts and pts <= {"BEV"}):
            return False
        return None
    if gas.ungeklaert:
        return None
    if scope == GAS_UNBEKANNT:
        return True
    if gas.art == GAS_UNBEKANNT:
        return None
    return gas.art == scope


def stuetzt_variante(gas: GasIdentitaet | None) -> bool:
    """Ob ein passender Gasbezug eine Variantenaussage tragen darf: nur mit Datensatzbezug, nie aus
    einer Nutzerangabe allein (sie bleibt Angabe)."""
    return bool(gas and gas.belegt_durch_datensatz)


def scope_label(scope: str) -> str:
    return _SCOPE_LABEL[scope]


# ── Verbrauch mit Einheit, Zyklus und Bezug ──────────────────────────────────
_BEZUG_TEXT = {CNG: "im Erdgasbetrieb (CNG)", LPG: "im Autogasbetrieb (LPG)", "Benzin": "im Benzinbetrieb"}


def _zahl(v) -> str:
    return f"{float(v):.1f}".replace(".", ",")


def verbrauch_kontextzeilen(m: dict) -> list[str]:
    """Verbrauchszeilen für Modellkontexte (Chat). Ein Datenbankwert ist keine externe Prüfung;
    "WLTP" nur bei belegtem Zyklus; bei Gas nie Liter ohne belegten Bezug, nie kg als Liter."""
    wltp, real = m.get("verbrauch_wltp"), m.get("verbrauch_real")
    zyklus = "WLTP" if m.get("verbrauch_zyklus") == "WLTP" else "Zyklus nicht geprüft"
    gas, _ = gasart_der_variante(m)
    if not gas:
        return [
            (f"Verbrauch laut Verbrauchsspalte ({zyklus}): {_zahl(wltp)} l/100km" if wltp is not None
             else "Verbrauch laut Verbrauchsspalte: nicht erfasst"),
            (f"Verbrauch real (hinterlegter Schätzwert, Spritmonitor): {_zahl(real)} l/100km" if real is not None
             else "Verbrauch real: nicht erfasst"),
        ]
    werte = [v for v in (wltp, real) if v is not None]
    if not werte:
        return ["Verbrauch (Gasfahrzeug): nicht erfasst"]
    einheit = m.get("verbrauch_einheit")
    bezug = m.get("verbrauch_bezug")
    if einheit not in ("kg", "l"):
        return [f"Verbrauch (Gasfahrzeug): hinterlegte Werte {', '.join(_zahl(v) for v in werte)} OHNE belegte "
                "Einheit und ohne belegten Bezug. Keine Verbrauchsangabe daraus ableiten, nicht als Liter lesen."]
    if einheit == "l" and bezug not in _BEZUG_TEXT:
        return [f"Verbrauch (Gasfahrzeug): {', '.join(_zahl(v) for v in werte)} l/100km OHNE belegten Bezug "
                "(Gas oder Benzin). Nicht als Benzinverbrauch und nicht als Gasverbrauch ausgeben."]
    bezug_text = _BEZUG_TEXT.get(bezug) or "im Gasbetrieb"
    zeilen = []
    if wltp is not None:
        zeilen.append(f"Verbrauch {bezug_text} laut Verbrauchsspalte ({zyklus}): {_zahl(wltp)} {einheit}/100km"
                      + (" (kg, NICHT Liter)" if einheit == "kg" else ""))
    if real is not None:
        zeilen.append(f"Verbrauch {bezug_text}, hinterlegter Schätzwert: {_zahl(real)} {einheit}/100km"
                      + (" (kg, NICHT Liter)" if einheit == "kg" else ""))
    return zeilen


_SATZ_BENZIN = re.compile(r"benzin", re.I)


def literwert_belegt(satz: str, *, gas: bool, einheit: str | None, bezug: str | None) -> bool:
    """Darf ein Satz mit einer Literangabe zu einem Gasfahrzeug stehen bleiben?

    Nur, wenn der hinterlegte Wert ein Literwert mit belegtem Bezug ist UND der Satz genau diesen Bezug
    nennt (Benzinbetrieb bzw. Autogas). Ein Literwert ohne belegten Bezug oder für Erdgas ist unbelegt.
    Für Nicht-Gasfahrzeuge entscheidet die bestehende Prüfung (Rückgabe True)."""
    if not gas:
        return True
    if einheit != "l" or bezug not in ("Benzin", LPG):
        return False
    if bezug == "Benzin":
        return bool(_SATZ_BENZIN.search(satz))
    return bool(_WORT_LPG.search(satz))


# ── Verkäuferfragen und Unterlagen (neutral, ohne Fristen/Prüfpflichten) ──────

def basis_fragen(gas: GasIdentitaet | None) -> tuple:
    """Katalogeinträge (schluessel, gruppe, titel, aktion, hinweis, deckt) für die Verkäuferfragen."""
    if gas is None:
        return ()
    if gas.ungeklaert:
        titel = "Ist eine Gasanlage verbaut, und welche Gasart nutzt sie (Erdgas/CNG oder Autogas/LPG)?"
    elif gas.art == GAS_UNBEKANNT:
        titel = "Welche Gasart nutzt das Fahrzeug (Erdgas/CNG oder Autogas/LPG)?"
    else:
        titel = f"Ist die {ANZEIGE[gas.art]}-Anlage ab Werk verbaut oder nachgerüstet?"
    aktion = ("Gasart, Werks- oder Nachrüstung, Hersteller der Gasanlage und bisherige Arbeiten an der "
              "Gasanlage erfragen.")
    if gas.status == ST_NACHRUESTUNG:
        aktion += " Die erkannte Variante ist ab Werk kein Gasfahrzeug: Einbau, Einbauwerkstatt und Zeitpunkt erfragen."
    if gas.ungeklaert:
        aktion += " " + gas.hinweis
    return (("gasanlage", "Gasantrieb", titel, aktion, None, ()),)


def basis_dokumente(gas: GasIdentitaet | None) -> tuple:
    """Katalogeinträge für die Unterlagen — reine Nachweisanforderung, keine Frist, keine Prüfpflicht."""
    if gas is None:
        return ()
    aktion = ("Vorhandene Nachweise zu Einbau, Prüfungen und Arbeiten an der Gasanlage zeigen lassen; die "
              "Angaben zur Gasanlage in den Fahrzeugpapieren mit dem Fahrzeug abgleichen. ENFAL bewertet hier "
              "keine Fristen oder Prüfpflichten.")
    if gas.status in (ST_NACHRUESTUNG, ST_OHNE_ZUORDNUNG):
        aktion += " Bei einer Nachrüstung auch die Einbaunachweise der Anlage zeigen lassen."
    return (("gasanlage_nachweise", "Gasantrieb", "Unterlagen zur Gasanlage zeigen lassen", aktion, None, ()),)


def berichtszeilen(gas: GasIdentitaet | None, fuel: str | None = None) -> list[str]:
    """Zeile(n) für den Fahrzeugabschnitt des KaufCheck-Berichts."""
    if gas is None:
        return []
    zeilen = [f"- Gasantrieb: {gas.anzeige}. {gas.hinweis}"]
    if fuel == "benzin":
        zeilen.append("  Ein Gasfahrzeug ist kein gewöhnlicher Benziner: „Benzin“ in der Kraftstoffzeile "
                      "bezeichnet nur die Motorklasse (Ottomotor), nicht den Antrieb.")
    return zeilen


MARKT_HINWEIS = ("Keine belastbare Marktpreisbewertung verfügbar. Für Gasfahrzeuge führt ENFAL keinen "
                 "Marktvergleich durch: Angebote gewöhnlicher Benziner wären kein passender Vergleich.")
