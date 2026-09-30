"""Presentation of the existing canonical KaufCheck objects.

No identification, risk discovery or severity decisions take place here. Free
LLM prose is deliberately not an output channel: evidence IDs cannot authorize
arbitrary new assertions about a vehicle, equipment, mileage or campaigns.
"""
from __future__ import annotations

import json

from app.bekannte_fakten import aus_request, tuning_status
from app.fin_hinweis import recall_handlung
from app.risikothemen import RISIKO_KATEGORIEN
from app.vergleichstabelle import als_markdown, baue_zeilen

REPORT_RISK_CATEGORIES = (*RISIKO_KATEGORIEN, "rueckruf", "web_rueckruf")


def datenbasis(baureihe, insights, belege, *, identity=None, markt_verfuegbar: bool = False) -> list[str]:
    """Nur TATSÄCHLICH verwendete Quellen (§5.9).

    BEFUND (Production-Run Mazda MX-5, Test 6): "Datenbank + Web" erschien auch
    dann, wenn die Marktrecherche zwar LIEF (Tavily-Treffer mit URL vorhanden),
    aber keiner davon einen belastbaren Marktwert ergab UND kein technischer
    Web-Fakt/keine Web-Identität in den Bericht einfloss. Ein Tavily-Aufruf mit
    Ergebnissen ist kein verwendeter Beleg — er wird es erst, wenn er den
    Marktwert trägt (`markt_verfuegbar`), einen Insight referenziert, oder eine
    Identitätslücke füllt (`identity.field_evidence[...]["provenance"]`).
    """
    sources = ["Inserat-/Nutzereingaben"]
    if baureihe:
        sources.append("ENFAL-Fahrzeugdatenbank")
    for insight in insights:
        for source in insight.quellen:
            if source.typ == "rueckruf_kba" and insight.trust == "verified":
                label = source.titel
            elif source.typ in ("web", "web_technik") and source.url:
                label = "Webrecherche"
            else:
                continue
            if label and label not in sources:
                sources.append(label)
    web_identitaet_genutzt = identity is not None and any(
        "web" in (fe.get("provenance") or []) for fe in identity.field_evidence.values())
    web_markt_genutzt = markt_verfuegbar and any(b.get("url") for b in belege)
    if (web_identitaet_genutzt or web_markt_genutzt) and "Webrecherche" not in sources:
        sources.append("Webrecherche")
    return sources


def kontext(identity, req, insights) -> str:
    """The only vehicle/risk context sent to the presentation LLM."""
    return "KANONISCHER FAHRZEUG- UND RISIKOKONTEXT\n" + json.dumps({
        "vehicle_identity": identity.as_diagnose(),
        "equipment_provided": list(req.ausstattung),
        "accident_status": aus_request(req).unfall,
        "tuning_status": tuning_status(req),
        "canonical_risks": [i.model_dump() for i in insights if i.kategorie in REPORT_RISK_CATEGORIES],
        "guardrails": ["Wartungswerte sind KEINE starre Herstellervorgabe.",
                       "Rückrufbetroffenheit zuerst per FIN prüfen; erst falls betroffen die Durchführung klären."],
    }, ensure_ascii=False)


def _identitaet_bestaetigt(baureihe, identity) -> bool:
    """Ob Marke/Modell durch DB oder Web unabhängig bestätigt sind (§6).

    BEFUND (Production-Run Mazda MX-5, Test 6): die Überschrift "Fahrzeug
    erkannt" stand unverändert über dem Abschnitt, obwohl direkt darunter
    "Generation nicht sicher bekannt" folgte — nur die ungeprüfte Inserat-
    angabe war vorhanden. Eine feste Überschrift kann diesen Unterschied nicht
    zeigen.
    """
    if baureihe is not None:
        return True
    return any(fe.get("status") in ("identified", "web_verified")
              for fe in identity.field_evidence.values())


def bericht(req, identity, baureihe, motor, insights, actions, reasons, recommendation,
            price_assessment, market_available, mileage, hu, sources, fahrzeugkontext=None) -> str:
    facts = aus_request(req)
    ueberschrift = ("## Fahrzeug erkannt" if _identitaet_bestaetigt(baureihe, identity)
                    else "## Fahrzeugidentität eingeschränkt")
    lines = [ueberschrift, "", "Datenbasis: " + "; ".join(sources) + ".", ""]
    labels = {"make": "Marke", "model": "Modell", "generation": "Generation", "year": "Baujahr",
              "engine_name": "Motor", "engine_code": "Motorcode / Motorfamilie", "fuel": "Kraftstoff",
              "powertrain": "Antriebsart", "transmission": "Getriebe", "drivetrain": "Antrieb",
              "horsepower": "Leistung (PS)"}
    status_labels = {"provided": "laut Inserat, nicht unabhängig bestätigt",
                     "plausible": "aus ENFAL-Daten plausibilisiert",
                     "identified": "ENFAL-Referenz eindeutig zugeordnet, keine Prüfung am Fahrzeug",
                     "unknown": "nicht sicher bekannt"}
    for name, label in labels.items():
        value = getattr(identity, name)
        state = identity.field_evidence.get(name, {}).get("status", "unknown")
        lines.append(f"- {label}: {value if value is not None else 'nicht sicher bekannt'} ({status_labels[state]}).")
    if req.ausstattung:
        lines += ["", "Ausstattung laut Inserat: " + ", ".join(req.ausstattung) + ". Vor Ort prüfen."]
    if req.vorbesitzer is not None:
        lines.append(f"Vorbesitzer laut Inserat: {req.vorbesitzer}.")
    if facts.letzte_wartung:
        lines.append(f"Letzte Wartung laut Inserat {facts.letzte_wartung.anzeige()}. Umfang und Belege prüfen.")
    if fahrzeugkontext and fahrzeugkontext.facelift_merkmale:
        lines += ["", "Generationsmerkmale aus der Datenbank (keine Ausstattungsaussage über dieses Fahrzeug): "
                  + fahrzeugkontext.facelift_merkmale]
    lines += ["", "## Kaufempfehlung", "", "**" + recommendation.upper().replace("_", " ") + "**", ""]
    lines += ["- " + reason for reason in reasons]
    lines += ["", "## Relevante Risiken und Hinweise", ""]
    risks = [i for i in insights if i.kategorie in REPORT_RISK_CATEGORIES]
    if not risks:
        lines.append("Keine freigegebenen technischen Risikothemen im aktuellen Kontext. Das schließt Defekte nicht aus.")
    for i in risks:
        lines += [f"### {i.titel}", "", i.beschreibung,
                  f"Datenqualität: {i.confidence}. Schweregrad: {i.schweregrad or 'nicht erfasst'}."]
        if i.recall_status:
            lines.append(recall_handlung(i.recall_status))
        elif i.einfluss:
            lines.append(i.einfluss)
        lines.append("")
    lines += ["## Inserat im Vergleich", "", als_markdown(baue_zeilen(
        req, baureihe, motor, hu=hu, laufleistungskontext=mileage,
        price_assessment=price_assessment, markt_verfuegbar=market_available, fakten=facts))]
    if mileage and mileage.km_pro_jahr is not None:
        lines += ["", f"Rechnerisch {mileage.km_pro_jahr:,} km pro Jahr. "
                  "Keine qualitative Laufleistungsbewertung ohne definierte Vergleichsbasis.".replace(",", ".")]
    lines += ["", "## Preis-Einschätzung", "", price_assessment.label if market_available else
              "Keine belastbare Marktpreisbewertung verfügbar."]
    for section, title in (("verkaeuferfragen", "Verkäuferfragen"), ("besichtigung", "Besichtigung"),
                           ("probefahrt", "Probefahrt"), ("dokumente", "Dokumente und finale Checkliste")):
        plan = getattr(actions, section)
        lines += ["", f"## {title}", ""]
        for action in plan.fahrzeugspezifisch + plan.basis:
            lines.append(f"- **{action.titel}** {action.aktion}")
    return "\n".join(lines)
