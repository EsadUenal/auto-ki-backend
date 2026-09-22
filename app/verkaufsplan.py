from __future__ import annotations

"""
VerkaufsCheck RC1: der vollständige Verkaufsfahrplan, DETERMINISTISCH.

Der Report für 8,99 € soll den kompletten Verkauf vorbereiten, nicht nur das
Inserat umformulieren. Alle Bausteine hier entstehen ohne Sprachmodell aus
  * den Angaben des Verkäufers (kanonisch, nie überschrieben),
  * den geprüften DB-Fakten (Baureihe, Motor, Evidence-/Trust-System),
  * der optionalen externen Marktorientierung (app/carapi_provider.py).
Damit bleibt es bei EINEM Gemini-Call pro VerkaufsCheck (der Fließtextbericht);
die on-demand Inseratsoptimierung bleibt wie bisher ein eigener Knopf.

GRUNDREGELN
  * Keine erfundenen Zahlen: Euro-Beträge stammen nur vom Nutzer
    (Preisvorstellung, Untergrenze) oder aus der externen Marktindikation.
    Keine Reparaturpreise, keine Aufschläge je Ausstattung, kein Preisband aus
    einem Einzelwert.
  * Verkäuferangaben bleiben Angaben ("laut deiner Angabe"), nie Nachweise.
  * Bekannte Schwachstellen sind Prüfhinweise ("falls vorhanden"), keine
    Behauptung über dieses Fahrzeug.
  * Nutzerseitiges Deutsch ohne langen Gedankenstrich.

PERSISTENZ
  Alles, was aus dem externen Provider stammt, steht ausschließlich unter
  `plan["markt"]`. `entferne_provider_werte` ersetzt genau diesen Block, bevor
  ein Check gespeichert wird, solange keine Persistenzfreigabe vorliegt
  (AUTO_KI_CARAPI_PERSISTENZ_ERLAUBT). Kein anderer Abschnitt enthält
  Providerzahlen.
"""

import datetime as dt
import re
from typing import Any

from app.carapi_provider import STATUS_OK, Marktbewertung, Marktdauer
from app.hu_termin import ABGELAUFEN, bewerte_hu
from app.key_findings import _ausstattung_treffer
from app.models import VerkaufsCheckRequest

PLAN_VERSION = 1

KEINE_ORIENTIERUNG = "Keine ausreichend belastbare Marktpreisorientierung verfügbar."


# ══ Helfer ═══════════════════════════════════════════════════════════════════

def _eur(n: int | float | None) -> str:
    if n is None:
        return ""
    return f"{round(n):,} €".replace(",", ".")


def _km(n: int | None) -> str:
    return f"{n:,} km".replace(",", ".") if n else ""


def _pct(p: float) -> str:
    return f"{p:+.1f} %".replace(".", ",")


def _norm(s: str | None) -> str:
    return (s or "").strip().lower()


def runde_orientierung(wert: int) -> int:
    """Rundet einen Einzelwert auf eine ehrliche Orientierungsgröße.
    Unter 5.000 € auf 100, unter 10.000 € auf 250, darüber auf 500 €."""
    schritt = 100 if wert < 5_000 else (250 if wert < 10_000 else 500)
    return int(round(wert / schritt) * schritt)


def _text_eingabe(req: VerkaufsCheckRequest) -> str:
    return " ".join(filter(None, [req.motor, req.getriebe, req.inserat_text, req.beschreibung]))


# ── Getriebe: DSG heißt DSG ─────────────────────────────────────────────────
_GETRIEBE_MARKEN = (
    ("dsg", "DSG"), ("s tronic", "S tronic"), ("s-tronic", "S tronic"), ("pdk", "PDK"),
    ("edc", "EDC"), ("powershift", "Powershift"), ("dct", "DCT"),
    ("steptronic", "Steptronic"), ("tiptronic", "Tiptronic"),
)


def getriebe_bezeichnung(req: VerkaufsCheckRequest) -> str | None:
    """Die konkreteste Getriebebezeichnung, die der Nutzer selbst genannt hat."""
    text = f" {_norm(req.getriebe)} {_norm(req.motor)} {_norm(req.inserat_text)} "
    for key, label in _GETRIEBE_MARKEN:
        if re.search(rf"(?<![a-z]){re.escape(key)}(?![a-z])", text):
            return label
    g = (req.getriebe or "").strip()
    return g or None


def _ps_aus_text(text: str | None) -> int | None:
    m = re.search(r"(\d{2,4})\s*ps\b", _norm(text))
    return int(m.group(1)) if m else None


def _kw_aus_text(text: str | None) -> int | None:
    m = re.search(r"(\d{2,4})\s*kw\b", _norm(text))
    return int(m.group(1)) if m else None


def leistung(req: VerkaufsCheckRequest, motor_match: dict | None) -> tuple[int | None, int | None, str]:
    """(kW, PS, quelle). Nutzereingabe schlägt DB (Etappe-1-Regel)."""
    ps, kw = _ps_aus_text(req.motor), _kw_aus_text(req.motor)
    if ps or kw:
        if ps and not kw:
            kw = round(ps * 0.73549875)
        if kw and not ps:
            ps = round(kw / 0.73549875)
        return kw, ps, "angabe"
    if motor_match and motor_match.get("leistung_kw"):
        return motor_match.get("leistung_kw"), motor_match.get("leistung_ps"), "db"
    return None, None, ""


def _varianten_rest(req: VerkaufsCheckRequest, baureihe: dict | None) -> str | None:
    """Ausstattungslinie, die der Nutzer genannt hat: explizites Feld, sonst das,
    was im Modellfeld über den DB-Modellnamen hinausgeht ("Golf GTI" -> "GTI")."""
    if (req.variante or "").strip():
        return req.variante.strip()
    if not baureihe or not req.modell:
        return None
    db_tokens = {t for t in re.split(r"[^a-z0-9]+", _norm(baureihe.get("modell"))) if t}
    gen_tokens = {t for t in re.split(r"[^a-z0-9]+", _norm(baureihe.get("generation"))) if t}
    rest = [t for t in re.split(r"\s+", req.modell.strip())
            if t and _norm(t) not in db_tokens and _norm(t) not in gen_tokens]
    return " ".join(rest) or None


# ── Mängel: technisch vs. optisch, Verneinungen erkennen ─────────────────────
_OPTISCH = ("kratzer", "steinschl", "delle", "beule", "lack", "felge", "bordstein",
            "schramme", "polster", "sitzbezug", "verfärb", "fleck", "gebrauchsspur",
            "optisch", "innenraum", "himmel", "zierleiste", "stoßstange", "stossstange")
_VERNEINUNG = re.compile(r"^\s*(?:keine|kein|ohne)\b", re.IGNORECASE)


def ist_verneinung(eintrag: str) -> bool:
    """"Keine bekannten technischen Mängel" ist KEIN Mangel."""
    return bool(_VERNEINUNG.match(eintrag or ""))


def maengel_klassifiziert(req: VerkaufsCheckRequest) -> dict[str, list[str]]:
    """{"technisch": [...], "optisch": [...], "verneint": [...]}.

    Strukturierte Felder zuerst (kanonisch). Das Altfeld `maengel` wird nach
    Stichworten zugeordnet; unklare Einträge gelten als technisch (vorsichtiger)."""
    out: dict[str, list[str]] = {"technisch": [], "optisch": [], "verneint": []}

    def _add(art: str, eintrag: str) -> None:
        e = eintrag.strip().rstrip(".")
        if e and e not in out[art]:
            out[art].append(e)

    for e in req.technische_maengel or []:
        _add("verneint" if ist_verneinung(e) else "technisch", e)
    for e in req.optische_maengel or []:
        _add("verneint" if ist_verneinung(e) else "optisch", e)
    for roh in req.maengel or []:
        # Satzweise trennen: "Leichte Steinschläge ... . Keine bekannten technischen Mängel."
        for e in re.split(r"(?<=[.;])\s+", roh):
            if not e.strip():
                continue
            if ist_verneinung(e):
                _add("verneint", e)
            elif any(k in _norm(e) for k in _OPTISCH):
                _add("optisch", e)
            else:
                _add("technisch", e)
    # Doppelt geführte Einträge (neues Feld UND Altfeld) nur einmal.
    for art in out:
        seen: list[str] = []
        for e in out[art]:
            if _norm(e) not in [_norm(s) for s in seen]:
                seen.append(e)
        out[art] = seen
    return out


def _leicht(eintrag: str) -> bool:
    return bool(re.search(r"\b(?:leicht|klein|gering|minimal|vereinzelt)\w*", _norm(eintrag)))


def _monate_seit(mm_jjjj: str | None, heute: dt.date) -> int | None:
    m = re.fullmatch(r"(\d{2})/(\d{4})", (mm_jjjj or "").strip())
    if not m:
        return None
    return (heute.year - int(m.group(2))) * 12 + (heute.month - int(m.group(1)))


# ══ A. Fahrzeug erkannt ══════════════════════════════════════════════════════

def _gleich_starke_varianten(baureihe: dict | None, kw: int | None) -> list[str]:
    if not baureihe or not kw:
        return []
    return sorted({m.get("bezeichnung") for m in baureihe.get("motoren") or []
                   if m.get("leistung_kw") == kw and m.get("bezeichnung")})


def baue_fahrzeug(req, baureihe, motor_match, identitaet) -> dict:
    kw, ps, lquelle = leistung(req, motor_match)
    variante = _varianten_rest(req, baureihe)
    marke = (baureihe or {}).get("marke") or req.marke
    modell = (baureihe or {}).get("modell") or req.modell
    generation = (baureihe or {}).get("generation")
    titel = " ".join(filter(None, [marke, modell,
                                   generation if generation and _norm(generation) != _norm(modell) else None,
                                   variante]))
    zeilen: list[dict] = []

    def z(label, wert, quelle="angabe"):
        if wert not in (None, ""):
            zeilen.append({"label": label, "wert": str(wert), "quelle": quelle})

    if req.erstzulassung:
        z("Erstzulassung", req.erstzulassung)
    else:
        z("Baujahr", req.baujahr)
    z("Laufleistung", _km(req.kilometerstand))
    z("Motor", (req.motor or "").strip() or ((motor_match or {}).get("bezeichnung")),
      "angabe" if (req.motor or "").strip() else "db")
    if kw:
        z("Leistung", f"{kw} kW / {ps} PS", lquelle)
    z("Kraftstoff", req.kraftstoff or (motor_match or {}).get("kraftstoff"),
      "angabe" if req.kraftstoff else "db")
    z("Getriebe", getriebe_bezeichnung(req))
    z("Antrieb", req.antrieb or (motor_match or {}).get("antrieb"), "angabe" if req.antrieb else "db")
    z("Karosserie", req.karosserie)
    if generation:
        bz = ""
        if baureihe.get("bauzeitraum_von"):
            bz = f" (Bauzeit ab {baureihe['bauzeitraum_von']}" + (
                f" bis {baureihe['bauzeitraum_bis']})" if baureihe.get("bauzeitraum_bis") else ")")
        z("Generation", f"{generation}{bz}", "db")
    z("Farbe", req.farbe)

    hinweise: list[str] = []
    if identitaet and not identitaet.get("belastbar"):
        fehlt = identitaet.get("fehlende_angabe") or "die genaue Modellbezeichnung"
        hinweise.append(f"Fahrzeug nicht eindeutig zugeordnet. Ergänze {fehlt}, dann nutzt ENFAL "
                        f"auch die geprüften Modelldaten.")
    varianten = _gleich_starke_varianten(baureihe, kw) if identitaet and identitaet.get("belastbar") else []
    if len(varianten) > 1:
        hinweise.append("Die Motorvariante ist in den Modelldaten nicht eindeutig ("
                        + " oder ".join(varianten) + "). Maßgeblich ist die Angabe in "
                        "deiner Zulassungsbescheinigung.")
    return {"titel": titel or " ".join(filter(None, [req.marke, req.modell])),
            "variante": variante, "zeilen": zeilen, "hinweise": hinweise,
            "belastbar": bool(identitaet and identitaet.get("belastbar"))}


# ══ B/C/D. Markt (NUR hier stehen Providerzahlen) ════════════════════════════

def _einordnung(pct: float) -> str:
    richtung = "über" if pct > 0 else "unter"
    if abs(pct) <= 3:
        return "liegt im Bereich der externen Marktindikation"
    if abs(pct) <= 10:
        return f"liegt leicht {richtung} der externen Marktindikation"
    return f"liegt deutlich {richtung} der externen Marktindikation"


def baue_markt(req, fahrzeug: dict, bewertung: Marktbewertung | None,
               dauer: Marktdauer | None, ziel: str) -> dict:
    markt: dict[str, Any] = {"orientierung": None, "dauer": None, "strategie_hinweis": None}

    if bewertung and bewertung.status == STATUS_OK and bewertung.wert_eur:
        wert = runde_orientierung(bewertung.wert_eur)
        a = bewertung.angefragt
        filterteile = [f"Baujahr {a['year']}" if a.get("year") else None,
                       {"petrol": "Benzin", "diesel": "Diesel", "electric": "Elektro",
                        "hybrid": "Hybrid", "lpg": "LPG", "cng": "CNG"}.get(a.get("fuel"))
                       if "fuel" in bewertung.filter else None,
                       f"{a['kw']} kW" if a.get("kw") and "kw" in bewertung.filter else None,
                       _km(a.get("mileage")) if a.get("mileage") and "mileage" in bewertung.filter else None]
        basis = (bewertung.aufgeloest or {}).get("model") or a.get("model")
        spezifitaet = (f"Bewertet wurde das Basismodell „{basis}“"
                       + (f" ({', '.join(t for t in filterteile if t)})" if any(filterteile) else "")
                       + ".")
        unsicher = False
        variante = fahrzeug.get("variante")
        if variante:
            spezifitaet += (f" Die Ausstattungslinie „{variante}“ ist in dieser Bewertung nicht "
                            f"als eigene Stufe garantiert berücksichtigt.")
            unsicher = True
        if "kw" not in bewertung.filter:
            spezifitaet += " Die Motorleistung ist nicht in die Bewertung eingeflossen."
            unsicher = True
        o: dict[str, Any] = {
            "status": "ok",
            "wert_eur": wert,
            "text": f"Externe Marktindikation: ca. {_eur(wert)}",
            "quelle": "CarAPI.dev (Bewertung je Basismodell, Deutschland)",
            "spezifitaet": spezifitaet,
            "unsicherheit": "erhoeht" if unsicher else "normal",
            "hinweis": "Orientierung, kein Gutachten und kein garantierter Verkaufspreis.",
            "vergleich": None,
        }
        if req.preis_vorstellung:
            diff = req.preis_vorstellung - wert
            pct = diff / wert * 100
            satz = f"Deine Preisvorstellung ({_eur(req.preis_vorstellung)}) {_einordnung(pct)}."
            if unsicher and abs(pct) > 3:
                satz += " Wegen der Bewertung auf Basismodell-Ebene ist das eine grobe Einordnung."
            o["vergleich"] = {
                "preisvorstellung_eur": req.preis_vorstellung,
                "differenz_eur": diff,
                "differenz_pct": round(pct, 1),
                "anzeige": ("±0 €" if diff == 0
                            else f"{'+' if diff > 0 else '−'}{_eur(abs(diff))} / {_pct(pct)}"),
                "einordnung": satz,
            }
        markt["orientierung"] = o
        markt["strategie_hinweis"] = _strategie_mit_markt(req, wert, ziel)
    else:
        grund = {
            "keine_daten": "Für dieses Fahrzeug liegen dem Datenanbieter nicht genug Daten vor.",
            "fahrzeug_nicht_abbildbar": "Das Fahrzeug ließ sich nicht eindeutig genug zuordnen.",
            "kein_verwertbarer_wert": "Der Datenanbieter lieferte keinen verwertbaren Wert.",
        }.get(bewertung.grund if bewertung else None)
        markt["orientierung"] = {"status": "nicht_verfuegbar", "text": KEINE_ORIENTIERUNG,
                                 "grund": grund}

    if dauer and dauer.status == STATUS_OK and dauer.median_tage is not None:
        markt["dauer"] = {
            "status": "ok",
            "p25_tage": dauer.p25_tage, "median_tage": dauer.median_tage, "p75_tage": dauer.p75_tage,
            "text": (f"Vergleichbare Angebote bleiben typischerweise etwa {dauer.median_tage} Tage "
                     f"online. Ein Viertel verschwindet innerhalb von rund {dauer.p25_tage} Tagen vom "
                     f"Markt, ein Viertel ist nach {dauer.p75_tage} Tagen noch online."),
            "hinweis": ("Gemessen wird, wann Inserate vom Markt verschwinden. Das umfasst Verkäufe, "
                        "aber auch zurückgezogene und abgelaufene Anzeigen. Es ist keine Zusage, "
                        "wann dein Auto verkauft ist."),
            "quelle": "CarAPI.dev (Inseratsdauer je Basismodell, Deutschland)",
        }
    return markt


def _strategie_mit_markt(req, wert: int, ziel: str) -> str:
    p = req.preis_vorstellung
    if not p:
        return (f"Die externe Marktindikation liegt bei ca. {_eur(wert)}. Nutze sie als "
                f"Ausgangspunkt für deinen Inseratspreis.")
    pct = (p - wert) / wert * 100
    if ziel == "schnell":
        if pct > 3:
            return (f"Für einen schnellen Verkauf liegt deine Preisvorstellung ({_eur(p)}) über der "
                    f"Marktindikation (ca. {_eur(wert)}). Ein Inseratspreis näher an der Indikation "
                    f"spricht mehr Käufer an.")
        return (f"Deine Preisvorstellung ({_eur(p)}) liegt nicht über der Marktindikation "
                f"(ca. {_eur(wert)}). Das passt zu einem schnellen Verkauf.")
    if ziel == "maximal":
        return (f"Für einen möglichst hohen Preis kannst du oberhalb der Marktindikation "
                f"(ca. {_eur(wert)}) starten. Je weiter darüber, desto wichtiger sind belegte "
                f"Argumente (Historie, Zustand, Ausstattung) und desto mehr Geduld brauchst du.")
    if abs(pct) <= 3:
        return (f"Deine Preisvorstellung ({_eur(p)}) liegt im Bereich der Marktindikation "
                f"(ca. {_eur(wert)}). Das passt zu einem ausgewogenen Ansatz.")
    richtung = "über" if pct > 0 else "unter"
    return (f"Deine Preisvorstellung ({_eur(p)}) liegt {richtung} der Marktindikation "
            f"(ca. {_eur(wert)}). Plane beim Inseratspreis ein, dass Käufer vergleichen.")


# ══ C. Strategie (ohne Providerzahlen) ═══════════════════════════════════════

_ZIELE = {
    "schnell": ("Schnell verkaufen", [
        "Inseratspreis eher defensiv ansetzen und wenig Verhandlungsreserve einplanen.",
        "Anfragen zügig beantworten und kurzfristige Besichtigungstermine anbieten.",
        "Alle Unterlagen vorab bereitlegen, damit ein Interessent sofort entscheiden kann.",
    ]),
    "ausgewogen": ("Ausgewogen", [
        "Mit einem realistischen Preis starten und etwas Verhandlungsspielraum einplanen.",
        "Resonanz beobachten: Kommen nach der ersten Zeit kaum ernsthafte Anfragen, den Preis überprüfen.",
        "Preisnachlässe an Gegenleistungen knüpfen (z. B. schnelle Abholung, Barzahlung).",
    ]),
    "maximal": ("Möglichst hoher Preis", [
        "Am oberen Ende deiner Vorstellung starten und mehr Geduld einplanen.",
        "Den Preis mit Belegen stützen: Historie, Zustand, Ausstattung, Fotos.",
        "Erst nach mehreren Wochen ohne passende Angebote schrittweise nachgeben.",
    ]),
}


def normalisiere_ziel(ziel: str | None) -> str:
    z = _norm(ziel)
    return z if z in _ZIELE else "ausgewogen"


def baue_strategie(req) -> dict:
    ziel = normalisiere_ziel(req.verkaufsziel)
    label, schritte = _ZIELE[ziel]
    return {"ziel": ziel, "label": label, "angegeben": _norm(req.verkaufsziel) in _ZIELE,
            "schritte": schritte}


# ══ E/F. Werttreiber und Wertminderer ════════════════════════════════════════

def baue_werttreiber(req, fahrzeug: dict, heute: dt.date) -> list[dict]:
    out: list[dict] = []

    def add(titel, text, art):
        out.append({"titel": titel, "text": text, "art": art})

    wertvoll = _ausstattung_treffer(req.ausstattung or [])
    if req.ausstattung:
        prominent = wertvoll[:4] or [a.strip() for a in req.ausstattung[:3]]
        add("Ausstattung prominent zeigen",
            f"{', '.join(prominent)} gehören in den Titel oder in die ersten Zeilen der Beschreibung. "
            f"Die übrige Ausstattung als vollständige Liste darunter.", "ausstattung")
    getriebe = getriebe_bezeichnung(req)
    motor = (req.motor or "").strip()
    if motor or getriebe:
        add("Motor und Getriebe klar benennen",
            f"{', '.join(filter(None, [motor, getriebe if getriebe and _norm(getriebe) not in _norm(motor) else None]))}: "
            f"Käufer filtern gezielt nach Motor, Leistung und Getriebe.", "modell")
    if req.scheckheftgepflegt is True or _norm(req.wartungsnachweise) == "vollstaendig":
        text = ("Scheckheftgepflegt laut deiner Angabe. Serviceheft und Rechnungen zur Besichtigung "
                "bereitlegen: Das Heft selbst ist der Beleg, nicht die Angabe im Inserat.")
        if req.letzter_service_datum or req.letzter_service_km:
            text += " Letzter Service: " + ", ".join(filter(None, [
                req.letzter_service_datum, _km(req.letzter_service_km)])) + "."
        add("Wartungshistorie belegen", text, "historie")
    if req.vorbesitzer == 1:
        add("Erste Hand", "Ein Vorbesitzer laut deiner Angabe. Das ist für viele Käufer ein Argument.",
            "historie")
    elif isinstance(req.vorbesitzer, int) and req.vorbesitzer > 1:
        add("Vorbesitzer klar angeben",
            f"{req.vorbesitzer} Vorbesitzer laut deiner Angabe. Eine klare Zahl schafft Vertrauen; "
            f"die Halter stehen in der Zulassungsbescheinigung Teil II.", "historie")
    if _norm(req.unfallfrei) == "ja":
        add("Unfallfrei laut deiner Angabe",
            "Käufer fragen gezielt danach. Wenn du Belege hast (z. B. Rechnungen oder ein "
            "Gutachten), lege sie bereit. Ohne Beleg bleibt es deine Angabe.", "historie")
    hu = bewerte_hu(req.tuev_bis, heute=heute, baujahr=req.baujahr)
    if hu and hu.lesbar and hu.status != ABGELAUFEN and (hu.monate_bis_faellig or 0) >= 12:
        add("HU mit langer Restlaufzeit",
            f"HU bis {hu.anzeige}, noch rund {hu.monate_bis_faellig} Monate. Im Inserat nennen, "
            f"den letzten Prüfbericht bereitlegen.", "hu")
    if req.baujahr and req.kilometerstand:
        alter = max(1, heute.year - req.baujahr)
        if alter >= 2 and req.kilometerstand / alter <= 10_000:
            add("Geringe Laufleistung",
                f"Rund {_km(round(req.kilometerstand / alter / 1000) * 1000)} pro Jahr. Mit "
                f"Serviceeinträgen oder HU-Berichten belegbar machen.", "zustand")
    if _norm(req.reifen_zustand) in ("neuwertig", "gut"):
        add("Reifen in gutem Zustand", "Profiltiefe messen und im Inserat nennen.", "raeder")
    if req.zweiter_radsatz is True:
        add("Zweiter Radsatz", "Den zweiten Radsatz (z. B. Winterräder) mit Zustand und Größe nennen "
                               "und fotografieren.", "raeder")
    if req.schluessel_anzahl and req.schluessel_anzahl >= 2:
        add(f"{req.schluessel_anzahl} Schlüssel vorhanden", "Im Inserat erwähnen, Käufer fragen oft danach.",
            "schluessel")
    for label, wert in (("Außen", req.zustand_aussen), ("Innen", req.zustand_innen)):
        if _norm(wert) == "sehr_gut":
            add(f"Zustand {label.lower()} sehr gut", "Mit guten Detailfotos zeigen.", "zustand")
    return out


def baue_wertminderer(req, maengel: dict, listing_analyse, heute: dt.date) -> list[dict]:
    out: list[dict] = []

    def add(titel, text, gewicht):
        out.append({"titel": titel, "text": text, "gewicht": gewicht})

    for e in maengel["technisch"]:
        add(f"Technischer Mangel: {e}",
            "Offen im Inserat nennen und im Preis berücksichtigen. Käufer lassen technische Punkte "
            "meist prüfen.", "hoch")
    for e in maengel["optisch"]:
        add(f"Optischer Mangel: {e}",
            "Ehrlich nennen und fotografieren. Das verhindert Diskussionen bei der Besichtigung.",
            "gering" if _leicht(e) else "mittel")
    if _norm(req.unfallfrei) == "nein":
        add("Unfallschaden", "Art und Reparatur offen beschreiben, Rechnungen oder Gutachten bereitlegen.",
            "hoch")
    if (req.vorschaeden or "").strip():
        add("Vorschäden / Nachlackierungen", f"{req.vorschaeden.strip()}. Offen angeben, Belege bereitlegen.",
            "mittel")
    if (req.tuning or "").strip():
        add("Tuning / Umbauten", f"{req.tuning.strip()}. Kann den Käuferkreis einschränken. "
                                 f"Eintragungen, Gutachten oder ABE bereitlegen.", "mittel")
    if _norm(req.import_status) in ("import", "reimport"):
        add("Import/Reimport" if _norm(req.import_status) == "import" else "Reimport",
            "Offen angeben. Käufer fragen nach Herkunft und Ausstattungsabweichungen.", "gering")
    if _norm(req.reifen_zustand) == "abgefahren":
        add("Reifen abgefahren", "Käufer rechnen neue Reifen gegen. Offen nennen.", "mittel")
    elif _norm(req.reifen_zustand) == "mittel":
        add("Reifen mit mittlerem Profil", "Profiltiefe messen, damit du konkret antworten kannst.", "gering")
    if req.schluessel_anzahl == 1:
        add("Nur ein Schlüssel", "Offen angeben. Ein fehlender Zweitschlüssel ist ein häufiges "
                                 "Verhandlungsargument.", "gering")
    if req.scheckheftgepflegt is False or _norm(req.wartungsnachweise) == "keine":
        add("Keine Wartungsnachweise", "Vorhandene Rechnungen sammeln. Ohne Nachweise ist die "
                                       "Historie für Käufer schwer einzuschätzen.", "mittel")
    elif _norm(req.wartungsnachweise) == "teilweise":
        add("Wartungsnachweise lückenhaft", "Vorhandene Nachweise sortiert bereitlegen und Lücken offen "
                                            "erklären.", "gering")
    monate = _monate_seit(req.letzter_service_datum, heute)
    if monate is not None and monate > 24:
        add("Letzter Service länger her", f"Letzter Service {req.letzter_service_datum}, vor rund "
                                          f"{monate} Monaten. Käufer könnten einen Wartungsstau vermuten.",
            "mittel")
    hu = bewerte_hu(req.tuev_bis, heute=heute, baujahr=req.baujahr)
    if hu and hu.lesbar:
        if hu.status == ABGELAUFEN:
            add("HU abgelaufen", f"HU seit {hu.anzeige} abgelaufen. Das drückt den Preis spürbar; "
                                 f"eine neue HU kann sich lohnen.", "hoch")
        elif (hu.monate_bis_faellig or 0) < 6:
            add("HU bald fällig", f"HU bis {hu.anzeige}. Käufer rechnen die nächste HU gegen.", "mittel")
    fehlend = [f.feld for f in (getattr(listing_analyse, "fehlende_angaben", None) or [])
               if f.wichtigkeit in ("kritisch", "wichtig")]
    if fehlend:
        add("Fehlende Angaben", "Im Inserat fehlen: " + ", ".join(fehlend[:5])
            + ". Fehlende Angaben kosten Vertrauen und Anfragen.", "gering")
    rang = {"hoch": 0, "mittel": 1, "gering": 2}
    return sorted(out, key=lambda x: rang[x["gewicht"]])


# ══ G. Vor dem Verkauf: lohnt sich das? ══════════════════════════════════════

def baue_vorbereitung(req, maengel: dict, heute: dt.date) -> list[dict]:
    out: list[dict] = []

    def add(massnahme, kategorie, begruendung):
        out.append({"massnahme": massnahme, "kategorie": kategorie, "begruendung": begruendung})

    add("Gründliche Innen- und Außenreinigung", "lohnt",
        "Geringer Aufwand, deutlich bessere Fotos und ein besserer erster Eindruck.")
    add("Beleuchtung, Wischer und Flüssigkeiten prüfen", "lohnt",
        "Defekte Lampen oder fehlendes Wischwasser wirken ungepflegt und sind schnell behoben. "
        "Ölstand nach Herstellervorgabe prüfen.")
    if _norm(req.zustand_innen) in ("gebrauchsspuren", "maengel"):
        add("Innenraumaufbereitung", "lohnt", "Flecken und Gerüche fallen bei der Besichtigung sofort auf.")
    optisch = " ".join(maengel["optisch"]).lower()
    if "steinschl" in optisch:
        add("Kleine Steinschläge ausbessern (Lackstift oder Smart Repair)", "optional",
            "Verhindert Rost an den Stellen. Vorher ein Angebot einholen und gegen den Effekt im "
            "Gespräch abwägen.")
    if "felge" in optisch or "bordstein" in optisch:
        add("Felge instand setzen lassen (Smart Repair)", "optional",
            "Fällt auf Fotos auf. Nur sinnvoll, wenn das Angebot im Verhältnis zum Fahrzeugwert steht.")
    if maengel["optisch"]:
        add("Größere Lackierarbeiten vor dem Verkauf", "lohnt_nicht",
            "Die Kosten liegen meist über dem Mehrerlös. Kleine Schäden lieber ehrlich zeigen.")
    if maengel["technisch"]:
        add("Kleine technische Defekte beheben", "optional",
            "Bei überschaubarem Aufwand sinnvoll. Größere Reparaturen eher offen nennen und im Preis "
            "berücksichtigen.")
    hu = bewerte_hu(req.tuev_bis, heute=heute, baujahr=req.baujahr)
    if hu and hu.lesbar and (hu.status == ABGELAUFEN or (hu.monate_bis_faellig or 99) < 3):
        add("Neue HU vor dem Verkauf", "optional",
            "Eine frische HU nimmt Käufern ein Argument. Lohnt sich vor allem, wenn keine größeren "
            "Mängel zu erwarten sind.")
    add("Teure Aufwertungen kurz vor dem Verkauf (z. B. neue Felgen, Folierung)", "lohnt_nicht",
        "Solche Ausgaben holst du beim Verkauf in der Regel nicht wieder herein.")
    rang = {"lohnt": 0, "optional": 1, "lohnt_nicht": 2}
    return sorted(out, key=lambda x: rang[x["kategorie"]])


# ══ H. Inseratspaket ═════════════════════════════════════════════════════════

def _marke_kurz(marke: str | None) -> str:
    return {"volkswagen": "VW", "mercedes-benz": "Mercedes"}.get(_norm(marke), (marke or "").strip())


def baue_inseratspaket(req, fahrzeug: dict, maengel: dict, baureihe: dict | None) -> dict:
    marke = (baureihe or {}).get("marke") or req.marke or ""
    modell = (baureihe or {}).get("modell") or req.modell or ""
    variante = fahrzeug.get("variante") or ""
    modellname = " ".join(filter(None, [modell, variante]))
    motor = (req.motor or "").strip()
    getriebe = getriebe_bezeichnung(req)
    motor_kern = re.sub(r"\s+", " ", re.sub(r"\b(?:dsg|automatik|schaltgetriebe|s[- ]tronic|pdk)\b", "",
                                            motor, flags=re.IGNORECASE)).strip(" ,")
    kw, ps, _ = leistung(req, None)
    ps_text = f"{ps} PS" if ps else ""
    if ps_text and ps_text.lower() in motor_kern.lower():
        ps_text = ""
    wertvoll = _ausstattung_treffer(req.ausstattung or [])
    ez = req.erstzulassung or (str(req.baujahr) if req.baujahr else "")

    ez_label = (f"EZ {ez}" if req.erstzulassung else (f"Bj. {ez}" if ez else None))
    sachlich = ", ".join(filter(None, [
        " ".join(filter(None, [marke, modellname, motor_kern, ps_text, getriebe])),
        ez_label, _km(req.kilometerstand)]))
    stark_teile = [" ".join(filter(None, [_marke_kurz(marke), modellname, motor_kern, getriebe])),
                   ps_text or None]
    if req.scheckheftgepflegt is True:
        stark_teile.append("Scheckheft")
    if _norm(req.unfallfrei) == "ja":
        stark_teile.append("unfallfrei")
    stark_teile += wertvoll[:2]
    verkaufsstark = " | ".join(t for t in stark_teile if t)
    tkm = f"{round(req.kilometerstand / 1000)} Tkm" if req.kilometerstand else None
    kompakt = ", ".join(filter(None, [" ".join(filter(None, [_marke_kurz(marke), modellname, getriebe])),
                                      str(req.baujahr) if req.baujahr else None, tkm]))

    # Faktenblock (nur gesetzte Werte, Angaben als solche)
    fakten: list[str] = []

    def f(label, wert):
        if wert not in (None, ""):
            fakten.append(f"{label}: {wert}")
    f("Erstzulassung" if req.erstzulassung else "Baujahr", ez)
    f("Kilometerstand", _km(req.kilometerstand))
    f("Motor", motor_kern or None)
    f("Leistung", f"{kw} kW / {ps} PS" if kw else None)
    f("Kraftstoff", req.kraftstoff)
    f("Getriebe", getriebe)
    f("Antrieb", req.antrieb)
    f("Karosserie", req.karosserie)
    f("Farbe", req.farbe)
    f("Vorbesitzer", req.vorbesitzer if req.vorbesitzer is not None else None)
    f("HU bis", req.tuev_bis)
    if req.scheckheftgepflegt is not None:
        f("Scheckheft", "ja" if req.scheckheftgepflegt else "nein")
    f("Unfallfrei", {"ja": "ja", "nein": "nein, Unfallschaden vorhanden"}.get(_norm(req.unfallfrei)))
    f("Schlüssel", req.schluessel_anzahl)
    if req.zweiter_radsatz is not None:
        f("Zweiter Radsatz", "ja" if req.zweiter_radsatz else "nein")

    # Kurzbeschreibung (Teaser)
    teaser_fakten = ", ".join(filter(None, [
        f"{motor_kern} mit {ps} PS" if motor_kern and ps and str(ps) not in motor_kern
        else (motor_kern or (f"{ps} PS" if ps else None)),
        getriebe, _km(req.kilometerstand),
        "scheckheftgepflegt" if req.scheckheftgepflegt is True else None,
        f"{req.vorbesitzer} Vorbesitzer" if isinstance(req.vorbesitzer, int) and req.vorbesitzer > 0 else None,
        f"HU bis {req.tuev_bis}" if req.tuev_bis else None]))
    teaser = f"{' '.join(filter(None, [marke, modellname]))}" + (f" ({ez})" if ez else "")
    teaser += f": {teaser_fakten}." if teaser_fakten else "."
    zustand_satz = _zustand_satz(maengel)
    if zustand_satz:
        teaser += " " + zustand_satz

    # Lange Beschreibung (Markdown, Verkäuferstimme)
    abs_: list[str] = []
    einleitung = f"Ich verkaufe meinen {' '.join(filter(None, [marke, modellname]))}"
    einleitung += f" aus {req.baujahr}" if req.baujahr else ""
    einleitung += f" mit {_km(req.kilometerstand)}." if req.kilometerstand else "."
    abs_.append(einleitung)
    abs_.append("**Fahrzeugdaten**\n" + "\n".join(f"- {z}" for z in fakten))
    if req.ausstattung:
        abs_.append("**Ausstattung**\n" + "\n".join(f"- {a.strip()}" for a in req.ausstattung if a.strip()))
    zw: list[str] = []
    if req.scheckheftgepflegt is True:
        zw.append("- Scheckheftgepflegt, Serviceheft liegt vor")
    if req.letzter_service_datum or req.letzter_service_km:
        zw.append("- Letzter Service: " + ", ".join(filter(None, [req.letzter_service_datum,
                                                                _km(req.letzter_service_km)])))
    if req.tuev_bis:
        zw.append(f"- HU bis {req.tuev_bis}")
    if _norm(req.unfallfrei) == "ja":
        zw.append("- Unfallfrei")
    if _norm(req.reifen_zustand):
        zw.append(f"- Reifen: {_REIFEN.get(_norm(req.reifen_zustand), req.reifen_zustand)}")
    for label, wert in (("Zustand außen", req.zustand_aussen), ("Zustand innen", req.zustand_innen)):
        if _ZUSTAND.get(_norm(wert)):
            zw.append(f"- {label}: {_ZUSTAND[_norm(wert)]}")
    if zw:
        abs_.append("**Zustand und Wartung**\n" + "\n".join(zw))
    bekannt = maengel["technisch"] + maengel["optisch"]
    if bekannt or maengel["verneint"]:
        zeilen = [f"- {m}" for m in bekannt] + [f"- {m}" for m in maengel["verneint"]]
        abs_.append("**Bekannte Mängel**\n" + "\n".join(zeilen))
    if (req.vorschaeden or "").strip():
        abs_.append(f"**Vorschäden / Nachlackierungen**\n- {req.vorschaeden.strip()}")
    if (req.tuning or "").strip():
        abs_.append(f"**Umbauten**\n- {req.tuning.strip()}")
    abs_.append("Besichtigung und Probefahrt nach Absprache. Unterlagen liegen zur Einsicht bereit.")
    return {
        "titel": [
            {"art": "sachlich", "text": sachlich},
            {"art": "verkaufsstark", "text": verkaufsstark},
            {"art": "kompakt", "text": kompakt},
        ],
        "kurzbeschreibung": teaser,
        "beschreibung": "\n\n".join(abs_),
        "faktenblock": fakten,
    }


_REIFEN = {"neuwertig": "neuwertig", "gut": "gut", "mittel": "mittleres Profil", "abgefahren": "abgefahren"}
_ZUSTAND = {"sehr_gut": "sehr gut", "gut": "gut", "gebrauchsspuren": "normale Gebrauchsspuren",
            "maengel": "mit Mängeln"}


def _zustand_satz(maengel: dict) -> str:
    teile: list[str] = []
    if maengel["optisch"]:
        teile.append("Optisch: " + "; ".join(maengel["optisch"]) + ".")
    if maengel["technisch"]:
        teile.append("Technisch: " + "; ".join(maengel["technisch"]) + ".")
    for v in maengel["verneint"]:
        teile.append(v.rstrip(".") + ".")
    return " ".join(teile)


# ══ I. Foto-Plan ═════════════════════════════════════════════════════════════

def baue_fotoplan(req, maengel: dict) -> dict:
    motive = [
        ("Vorne schräg (3/4-Ansicht)", "Das wichtigste Foto: wird als Vorschaubild angezeigt."),
        ("Hinten schräg (3/4-Ansicht)", "Gegenüberliegende Seite zum ersten Foto."),
        ("Seite komplett", "Auf Augenhöhe, das ganze Auto im Bild."),
        ("Front gerade", "Scheinwerfer und Frontpartie."),
        ("Heck gerade", "Rückleuchten und Heckpartie."),
        ("Felgen und Reifen", "Eine Felge nah, Profil sichtbar."),
        ("Cockpit mit Lenkrad", "Von der Fahrertür aus, Zündung an ohne Warnleuchten."),
        ("Kilometerstand im Display", "Gut lesbar, bei eingeschalteter Zündung."),
        ("Infotainment / Navigation", "Bildschirm eingeschaltet."),
        ("Sitze vorne", "Fahrersitz mit Wange (Abnutzung ist ein Käuferthema)."),
        ("Rückbank", "Von der hinteren Tür aus."),
        ("Kofferraum", "Leer und sauber."),
        ("Motorraum", "Sauber, aber nicht frisch glänzend eingesprüht."),
        ("Serviceheft und HU-Bericht", "Persönliche Daten, FIN und Kennzeichen abdecken."),
    ]
    if req.zweiter_radsatz is True:
        motive.append(("Zweiter Radsatz", "Räder nebeneinander, Profil sichtbar."))
    if req.schluessel_anzahl:
        motive.append(("Schlüssel", f"Alle {req.schluessel_anzahl} Schlüssel zusammen."))
    for m in (maengel["optisch"] + maengel["technisch"])[:4]:
        motive.append((f"Mangel: {m}", "Ehrlich und nah fotografieren. Das schafft Vertrauen."))
    return {
        "fotos": [{"nr": i + 1, "motiv": mo, "tipp": t} for i, (mo, t) in enumerate(motive)],
        "hinweise": [
            "Bei Tageslicht fotografieren, am besten bei bedecktem Himmel.",
            "Ruhiger, sauberer Hintergrund ohne andere Autos.",
            "Keine Filter und keine starke Bildbearbeitung.",
            "Kennzeichen unkenntlich machen, keine Personen oder Hausnummern im Bild.",
            "Schäden nicht verstecken: Käufer sehen sie bei der Besichtigung ohnehin.",
        ],
    }


# ══ J. Plattformen ═══════════════════════════════════════════════════════════

def baue_plattformen() -> dict:
    return {
        "kanaele": [
            {"kanal": "Große Fahrzeugbörsen",
             "text": "Spezialisierte Autobörsen mit Filtern nach Modell, Motor und Ausstattung. "
                     "Dort vergleichen Käufer gezielt."},
            {"kanal": "Allgemeine Kleinanzeigenportale",
             "text": "Eher regionale Käufer. Gut als zweiter Kanal neben einer Autobörse."},
            {"kanal": "Marken- oder Modell-Communitys",
             "text": "Foren und Clubs erreichen Liebhaber gesuchter Varianten. Regeln der Community "
                     "zu Verkaufsanzeigen beachten."},
            {"kanal": "Händler-Ankauf",
             "text": "Am schnellsten und mit wenig Aufwand, meist aber unter dem Preis eines "
                     "Privatverkaufs."},
        ],
        "hinweis": "Gebühren und Laufzeiten direkt beim jeweiligen Anbieter prüfen. ENFAL wertet "
                   "diese Plattformen nicht automatisch aus.",
    }


# ══ K. Verhandlung ═══════════════════════════════════════════════════════════

def baue_verhandlung(req, maengel: dict, strategie: dict) -> dict:
    p, u = req.preis_vorstellung, req.preis_untergrenze
    preise: dict[str, Any] = {"preisvorstellung_eur": p, "untergrenze_eur": u, "spielraum_eur": None,
                              "hinweise": []}
    if p and u:
        if u <= p:
            preise["spielraum_eur"] = p - u
        else:
            preise["hinweise"].append("Deine Untergrenze liegt über der Preisvorstellung. Bitte prüfen.")
    elif p:
        preise["hinweise"].append("Lege vor dem ersten Gespräch eine Untergrenze fest und nenne sie "
                                  "niemandem.")
    if strategie["ziel"] != "schnell":
        preise["hinweise"].append("Wenn du Verhandlungsspielraum willst, setze den Inseratspreis etwas "
                                  "über deine Preisvorstellung und kennzeichne ihn als Verhandlungsbasis.")

    argumente: list[dict] = []
    for m in maengel["optisch"][:3]:
        argumente.append({"einwand": m, "antwort": "Ist im Inserat genannt und fotografiert; der Zustand "
                                                    "ist im Preis berücksichtigt."})
    for m in maengel["technisch"][:2]:
        argumente.append({"einwand": m, "antwort": "Offen genannt und im Preis berücksichtigt. Ein "
                                                    "Werkstattbefund hilft, wenn der Käufer unsicher ist."})
    if isinstance(req.vorbesitzer, int) and req.vorbesitzer >= 2:
        argumente.append({"einwand": f"{req.vorbesitzer} Vorbesitzer",
                          "antwort": "Die Historie ist über Serviceheft und Unterlagen nachvollziehbar."
                          if req.scheckheftgepflegt else "Offen angegeben; vorhandene Unterlagen zeigen."})
    if req.scheckheftgepflegt is True:
        argumente.append({"einwand": "Wartung",
                          "antwort": "Serviceheft und Rechnungen liegen zur Einsicht bereit."})
    argumente.append({"einwand": "Reifen",
                      "antwort": {"neuwertig": "Reifen sind neuwertig; Profiltiefe kann gemessen werden.",
                                  "gut": "Reifen sind in gutem Zustand; Profiltiefe kann gemessen werden."}
                      .get(_norm(req.reifen_zustand),
                           "Profiltiefe vorher selbst messen, damit du konkret antworten kannst.")})
    if req.kilometerstand:
        argumente.append({"einwand": "Laufleistung",
                          "antwort": "Mit Serviceeinträgen und HU-Berichten belegbar."})
    return {"preise": preise, "argumente": argumente,
            "fairness": "Bleib sachlich, nenne bekannte Mängel selbst und mach keine Zusagen, die du "
                        "nicht belegen kannst."}


# ══ L/M. Dokumente und Übergabe ══════════════════════════════════════════════

def baue_dokumente(req) -> list[dict]:
    d = [
        {"dokument": "Zulassungsbescheinigung Teil I (Fahrzeugschein)", "pflicht": True},
        {"dokument": "Zulassungsbescheinigung Teil II (Fahrzeugbrief)", "pflicht": True},
        {"dokument": "Letzter HU-Bericht", "pflicht": False},
        {"dokument": "Serviceheft / Wartungsnachweise", "pflicht": False},
        {"dokument": "Rechnungen zu Reparaturen und Verschleißteilen", "pflicht": False},
        {"dokument": f"Alle Schlüssel{f' ({req.schluessel_anzahl})' if req.schluessel_anzahl else ''}",
         "pflicht": True},
        {"dokument": "Bordbuch / Bedienungsanleitung", "pflicht": False},
        {"dokument": "Schriftlicher Kaufvertrag (zweifach)", "pflicht": True},
    ]
    if _norm(req.import_status) in ("import", "reimport"):
        d.append({"dokument": "Übereinstimmungsbescheinigung (CoC)", "pflicht": False})
    if (req.tuning or "").strip():
        d.append({"dokument": "Eintragungen, Gutachten oder ABE zu Umbauten", "pflicht": True})
    if req.zweiter_radsatz is True:
        d.append({"dokument": "Zweiter Radsatz (mit übergeben oder separat vereinbaren)", "pflicht": False})
    return d


def baue_uebergabe() -> dict:
    return {
        "punkte": [
            "Identität des Käufers prüfen (Ausweis) und seine Daten in den Kaufvertrag übernehmen.",
            "Kaufvertrag vollständig ausfüllen: FIN, Kilometerstand, bekannte Mängel, Datum und "
            "Uhrzeit der Übergabe, Unterschriften beider Seiten.",
            "Zahlung sicher prüfen: Bargeld vor Ort prüfen oder die Überweisung erst als Gutschrift auf "
            "dem eigenen Konto abwarten. Eine Überweisungsbestätigung allein reicht nicht.",
            "Übergabe protokollieren: Schlüssel, Dokumente, Zubehör und Kilometerstand festhalten.",
            "Verkauf der Kfz-Versicherung melden.",
        ],
        "abmeldung": ("Zwei übliche Wege: Du meldest das Fahrzeug vor der Übergabe selbst ab, oder der "
                      "Käufer meldet es zeitnah um. Haltet im Kaufvertrag fest, wer das bis wann "
                      "erledigt."),
        "hinweis": "Keine Rechtsberatung. Musterkaufverträge bieten z. B. Automobilclubs an.",
    }


# ══ N. Was jetzt? ════════════════════════════════════════════════════════════

def baue_naechste_schritte(req, maengel, vorbereitung, fotoplan, inserat, listing_analyse,
                           markt: dict) -> list[str]:
    s: list[str] = []
    if getattr(listing_analyse, "probleme", None):
        s.append("Widersprüche zwischen Beschreibung und Angaben klären.")
    lohnt = [v["massnahme"] for v in vorbereitung if v["kategorie"] == "lohnt"]
    if lohnt:
        s.append(f"Fahrzeug vorbereiten: {lohnt[0]}.")
    s.append(f"Fotos nach Foto-Plan erstellen ({len(fotoplan['fotos'])} Motive).")
    s.append("Unterlagen bereitlegen: Zulassungsbescheinigung I und II, HU-Bericht, Serviceheft.")
    s.append(f"Inserat veröffentlichen, z. B. mit dem Titel „{inserat['titel'][0]['text']}“.")
    if (markt.get("dauer") or {}).get("status") == "ok":
        s.append("Preis überprüfen, wenn nach der typischen Inseratsdauer (siehe Marktdauer) kaum "
                 "ernsthafte Anfragen kommen.")
    else:
        s.append("Preis überprüfen, wenn nach einigen Wochen kaum ernsthafte Anfragen kommen.")
    # Führende Nummerierung entfernen (Frontend nummeriert selbst) und deduplizieren.
    sauber: list[str] = []
    for x in s:
        x = re.sub(r"^\s*\d+[.)]\s*", "", x).strip()
        if x and x not in sauber:
            sauber.append(x)
    return sauber[:5]


# ══ Inseratsqualität: Vollständigkeit ≠ Textqualität ≠ Transparenz ═══════════

def _label(erfuellt: int, gesamt: int) -> str:
    if erfuellt == gesamt:
        return "sehr_gut"
    if erfuellt >= gesamt - 1:
        return "gut"
    return "verbesserbar"


def baue_inseratsqualitaet(req, maengel: dict, listing_analyse) -> dict:
    text = (req.inserat_text or "").strip()
    textq = None
    if text:
        t = _norm(text)
        kriterien = [
            ("Mindestens 300 Zeichen", len(text) >= 300),
            ("Nennt den Kilometerstand", bool(re.search(r"\d[\d.]*\s*(?:km|tkm)\b", t))),
            ("Nennt Baujahr oder Erstzulassung", bool(re.search(r"\b(?:19|20)\d{2}\b", t))),
            ("Nennt Motor oder Leistung", bool(re.search(r"\b\d{2,3}\s*(?:ps|kw)\b|tsi|tdi|tfsi|hybrid|"
                                                         r"diesel|benzin|elektro", t))),
            ("Keine Großbuchstaben- oder Ausrufezeichen-Häufung",
             "!!" not in text and not re.search(r"\b[A-ZÄÖÜ]{6,}\b", text)),
            ("Gegliedert (Absätze oder Aufzählung)", "\n" in text),
        ]
        erf = sum(1 for _, ok in kriterien if ok)
        textq = {"label": _label(erf, len(kriterien)),
                 "kriterien": [{"kriterium": k, "erfuellt": ok} for k, ok in kriterien]}
    trans = [
        ("Unfallstatus angegeben", bool(req.unfallfrei)),
        ("Mängel genannt oder ausdrücklich keine bekannt",
         bool(maengel["technisch"] or maengel["optisch"] or maengel["verneint"])),
        ("Wartungsnachweis angegeben", req.scheckheftgepflegt is not None or bool(req.wartungsnachweise)),
        ("HU-Termin angegeben", bool(req.tuev_bis)),
        ("Anzahl Vorbesitzer angegeben", req.vorbesitzer is not None),
    ]
    terf = sum(1 for _, ok in trans if ok)
    la = listing_analyse
    return {
        "vollstaendigkeit": {"vorhanden": getattr(la, "vorhanden", None), "gesamt": getattr(la, "gesamt", None)},
        "textqualitaet": textq,
        "transparenz": {"label": _label(terf, len(trans)),
                        "kriterien": [{"kriterium": k, "erfuellt": ok} for k, ok in trans]},
    }


# ══ Schwachstellen und Rückrufe (Evidence-System wiederverwenden) ════════════

def baue_pruefhinweise(insights) -> dict:
    schwach: list[dict] = []
    rueck: list[dict] = []
    for i in insights or []:
        kat = getattr(i, "kategorie", None)
        if kat in ("schwachstelle", "motorproblem") and getattr(i, "confidence", None) in ("hoch", "mittel"):
            if len(schwach) < 4:
                schwach.append({
                    "titel": i.titel,
                    "text": "Vor der Besichtigung selbst prüfen. Falls vorhanden: offen kommunizieren "
                            "und im Preis berücksichtigen.",
                    "datenqualitaet": i.confidence,
                })
        elif kat == "rueckruf":
            rueck.append({
                "titel": getattr(i, "kurztitel", None) or i.titel,
                "text": "Per FIN beim Hersteller oder in der KBA-Rückrufdatenbank prüfen, ob die Aktion "
                        "erledigt ist. Ein Nachweis über die Erledigung kann beim Verkauf Vertrauen "
                        "schaffen.",
            })
    return {"schwachstellen": schwach, "rueckrufe": rueck[:4],
            "hinweis": "Das sind bekannte Punkte dieser Baureihe, keine Aussage über dein Fahrzeug."
            if schwach else None}


# ══ Einstieg ═════════════════════════════════════════════════════════════════

def baue_verkaufsplan(req: VerkaufsCheckRequest, baureihe: dict | None, motor_match: dict | None,
                      identitaet: dict | None, insights, listing_analyse,
                      bewertung: Marktbewertung | None, dauer: Marktdauer | None,
                      heute: dt.date | None = None) -> dict:
    heute = heute or dt.date.today()
    maengel = maengel_klassifiziert(req)
    fahrzeug = baue_fahrzeug(req, baureihe, motor_match, identitaet)
    strategie = baue_strategie(req)
    markt = baue_markt(req, fahrzeug, bewertung, dauer, strategie["ziel"])
    vorbereitung = baue_vorbereitung(req, maengel, heute)
    inserat = baue_inseratspaket(req, fahrzeug, maengel, baureihe if fahrzeug["belastbar"] else None)
    fotoplan = baue_fotoplan(req, maengel)
    return {
        "version": PLAN_VERSION,
        "fahrzeug": fahrzeug,
        "markt": markt,
        "strategie": strategie,
        "werttreiber": baue_werttreiber(req, fahrzeug, heute),
        "wertminderer": baue_wertminderer(req, maengel, listing_analyse, heute),
        "vorbereitung": vorbereitung,
        "inserat": inserat,
        "fotoplan": fotoplan,
        "plattformen": baue_plattformen(),
        "verhandlung": baue_verhandlung(req, maengel, strategie),
        "dokumente": baue_dokumente(req),
        "uebergabe": baue_uebergabe(),
        "pruefhinweise": baue_pruefhinweise(insights),
        "inseratsqualitaet": baue_inseratsqualitaet(req, maengel, listing_analyse),
        "naechste_schritte": baue_naechste_schritte(req, maengel, vorbereitung, fotoplan, inserat,
                                                    listing_analyse, markt),
    }


PERSISTENZ_HINWEIS = ("Die externe Marktorientierung wird aus Lizenzgründen nicht dauerhaft "
                      "gespeichert. Sie war beim ursprünglichen Check sichtbar.")


def entferne_provider_werte(ergebnis: dict) -> dict:
    """Vor dem Speichern: Providerzahlen aus `verkaufsplan.markt` entfernen.
    Idempotent; lässt Checks ohne Verkaufsplan unverändert."""
    plan = (ergebnis or {}).get("verkaufsplan")
    if not isinstance(plan, dict) or not isinstance(plan.get("markt"), dict):
        return ergebnis
    m = plan["markt"]
    hatte = ((m.get("orientierung") or {}).get("status") == "ok"
             or (m.get("dauer") or {}).get("status") == "ok")
    if not hatte:
        return ergebnis
    neu = dict(ergebnis)
    neu["verkaufsplan"] = {**plan, "markt": {
        "orientierung": {"status": "nicht_gespeichert", "text": PERSISTENZ_HINWEIS},
        "dauer": None, "strategie_hinweis": None}}
    return neu
