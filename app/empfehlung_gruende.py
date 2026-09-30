"""Begründung der Kaufempfehlung — getrennt von den Risiken.

RC1-BEFUND
----------
Unter "Warum diese Empfehlung?" standen Software-Probleme, Knarzgeräusche und
Rückrufe — dieselben Punkte wie direkt darunter unter "Warum diese Risiken?".
Ursache: das Modell hat in `empfehlung_evidence_ids` schlicht dieselben IDs
geliefert wie in `risiko_evidence_ids`, und das Frontend rendert beide Blöcke
aus diesen Listen. Risiken erklären aber nicht, warum ein Fahrzeug trotzdem
"nach Besichtigung" in Frage kommt.

Dieses Modul erzeugt die Begründung deterministisch aus dem, was der Check
tatsächlich festgestellt hat — ohne LLM, ohne neue Datenquellen und ohne
Wertung, die keine Quelle trägt (z.B. KEIN "Laufleistung unterdurchschnittlich":
diese Schwelle wurde in einem früheren Audit mangels Beleg bewusst entfernt).
Der Preis ist ausdrücklich eine eigene Dimension: ohne Marktbasis steht hier,
dass er NICHT bewertet wurde.
"""
from __future__ import annotations
from collections import Counter
from app.bekannte_fakten import unfall_detail, unfall_status, UNFALLFREI, UNFALL
from app.fin_hinweis import HINWEIS_FIN

from app.risikothemen import WARTUNG_REGULAER, ist_bekannt, risikoart
from app.servicehistorie import (
    NICHT_VORHANDEN as SH_NICHT_VORHANDEN, TEILWEISE as SH_TEILWEISE,
    UMFANG_UNKLAR as SH_UMFANG_UNKLAR, VOLLSTAENDIG_ANGEGEBEN as SH_VOLLSTAENDIG,
    satz as servicehistorie_satz, status as servicehistorie_status,
)

_HOCH = ("hoch", "kritisch", "sehr hoch")
_VORSICHT = ("nur_mit_werkstattpruefung", "hohes_risiko", "finger_weg")


def _technische_risiken(insights: list) -> list:
    """Die technischen Aussagen der kanonischen Risikomenge.

    Ein regulärer Wartungspunkt ("Zündkerzen alle 60.000 km") ist kein Risiko,
    sondern ein Plan; vorbeugende, zustands- und umbaubezogene Hinweise sind es.
    """
    out = []
    for i in insights:
        kategorie = getattr(i, "kategorie", None)
        if kategorie in ("schwachstelle", "motorproblem", "web_schwachstelle"):
            out.append(i)
        elif kategorie in ("wartung", "web_wartung") and risikoart(i) != WARTUNG_REGULAER:
            out.append(i)
    return out


def _mehrzahl(n: int, einzahl: str, mehrzahl: str) -> str:
    return f"{n} {einzahl if n == 1 else mehrzahl}"


def _technische_datenlage(insights: list, empfehlung: str) -> list[str]:
    risiken = _technische_risiken(insights)
    if not risiken:
        return ["Im Datensatz ist für diese Variante keine Schwachstelle hinterlegt. Das "
                "schließt Defekte nicht aus."]
    bekannt = [i for i in risiken if ist_bekannt(i)]
    gemeldet = [i for i in risiken if not ist_bekannt(i)]
    schwer_bekannt = [i for i in bekannt
                      if (getattr(i, "schweregrad", None) or "").lower() in _HOCH]
    saetze: list[str] = []
    quality = Counter(i.confidence for i in risiken)
    saetze.append(", ".join(f"{quality[q]} {'Hinweis' if quality[q] == 1 else 'Hinweise'} mit Datenqualität {q}"
                            for q in ("hoch", "mittel", "niedrig") if quality[q]) + ".")
    if schwer_bekannt:
        namen = ", ".join(_name(i) for i in schwer_bekannt[:3])
        saetze.append(f"Belegte Schwachstelle mit hohem Schweregrad: {namen}. Vor dem Kauf "
                      f"gezielt prüfen lassen.")
    elif bekannt and all((getattr(i, "schweregrad", None) or "").lower() in ("gering", "mittel",
                                                                                 "moderat")
                         for i in bekannt) and not gemeldet:
        # Die alte Aussage bleibt dort, wo sie stimmt: alle Punkte belegt, alle
        # mit erfasster, nicht hoher Schwere.
        saetze.append("Im Datensatz keine schwerwiegende bekannte Schwachstelle für diese "
                      "Variante.")
    elif bekannt:
        saetze.append(f"Für diese Variante sind {_mehrzahl(len(risiken), 'technischer Hinweis', 'technische Hinweise')} "
                      f"hinterlegt, davon {len(bekannt)} belegt.")
    if gemeldet:
        n = len(gemeldet)
        if n == len(risiken):
            saetze.append(f"Für diese Variante {'ist' if n == 1 else 'sind'} "
                          f"{_mehrzahl(n, 'technischer Hinweis', 'technische Hinweise')} "
                          f"hinterlegt: gezielt nachfragen, für sich allein kein "
                          f"festgestellter Mangel.")
        else:
            saetze.append(f"{_mehrzahl(n, 'weiterer Hinweis ist', 'weitere Hinweise sind')} "
                          f"gemeldet und für sich allein kein "
                          f"festgestellter Mangel.")
        # Low-Evidence darf die Empfehlung nicht unbemerkt tragen. Ist sie streng
        # und ist KEIN technischer Punkt belegt, wird das ausdrücklich gesagt.
        if (empfehlung or "") in _VORSICHT and not bekannt:
            saetze.append("Keine der hinterlegten technischen Schwachstellen ist belegt: die "
                          "strengere Empfehlung ist eine Vorsichtsmaßnahme, keine Feststellung "
                          "eines Mangels.")
    return saetze


def _name(insight) -> str:
    from app.evidence import titel_bauteil
    return (getattr(insight, "bauteil", None) or titel_bauteil(getattr(insight, "titel", ""))
            or "Schwachstelle").strip()


def baue_empfehlung_gruende(req, baureihe: dict | None, motor_match: dict | None,
                            insights: list, key_findings: list, empfehlung: str,
                            markt_verfuegbar: bool, preis_label: str | None,
                            hu=None, generation: str | None = None, identity=None) -> list[str]:
    from app.anzeige import fahrzeug_titel, ohne_wiederholung
    gruende: list[str] = []

    # 1) Identität
    #
    # BEFUND (Production-Run BMW M4 F82): "Fahrzeug eindeutig zugeordnet: BMW M4
    # F82 M4 (...)". Diese Zeile setzte Marke/Modell/Generation/Variante erneut
    # per String-Verkettung zusammen; die Deduplizierung der Hauptüberschrift
    # galt hier nicht, und mehrere DB-Motorcodes erschienen als die EINE
    # Identität. Jetzt: derselbe Formatter (app/anzeige.py) auf derselben
    # kanonischen Identität wie Überschrift und Titel.
    if baureihe and motor_match:
        if identity is not None:
            name = fahrzeug_titel(identity, mit_jahr=False, mit_motor=False)
            name = ohne_wiederholung(name, identity.engine_name)
            code_fe = identity.field_evidence.get("engine_code") or {}
            mehrdeutig = code_fe.get("verification_state") == "ambiguous"
            code = None if mehrdeutig else identity.engine_code
            ps = identity.horsepower
        else:
            name = ohne_wiederholung(baureihe.get("marke"), baureihe.get("modell"),
                                     generation or baureihe.get("generation"),
                                     motor_match.get("bezeichnung"))
            code_fe, mehrdeutig = {}, False
            code, ps = motor_match.get("motorcode"), motor_match.get("leistung_ps")
        # Details nur, soweit sie nicht schon im Namen stehen (Token-Dedup).
        details = [d for d in (code, f"{ps} PS" if ps else None)
                   if d and ohne_wiederholung(name, d) != name]
        satz = f"ENFAL-Referenzvariante zugeordnet: {name}" + (f" ({', '.join(details)})" if details else "")
        if mehrdeutig:
            satz += (". Der genaue Motorcode ist nicht eindeutig (mögliche Motorcodes: "
                     f"{', '.join(code_fe.get('possible_values') or [])})")
        gruende.append(satz + ".")
    elif baureihe:
        gruende.append("Baureihe erkannt, Motorisierung aber nicht eindeutig: "
                       "motorbezogene Aussagen bleiben allgemein.")
    elif identity is not None and any(
            fe.get("primary_source") == "web" or "web" in (fe.get("confirmed_by") or [])
            for fe in identity.field_evidence.values()):
        # DB-Miss mit Web-Identität: dieselbe kanonische Bezeichnung, mit Herkunft.
        gruende.append(f"Fahrzeugidentität über Webquellen plausibilisiert: "
                       f"{fahrzeug_titel(identity, mit_jahr=False)} (keine ENFAL-Referenz).")
        web_risiken = [i for i in insights or [] if str(getattr(i, "kategorie", "")).startswith("web_")]
        if web_risiken:
            n = len(web_risiken)
            gruende.append(f"{n} {'Hinweis' if n == 1 else 'Hinweise'} aus der Webrecherche mit "
                           f"Quellenangabe, nicht aus geprüften ENFAL-Daten.")

    # 2) Inserat in sich stimmig
    widersprueche = [f for f in key_findings or []
                     if getattr(f, "kategorie", None) == "widerspruch"]
    if baureihe and not widersprueche:
        gruende.append("Keine Widersprüche zwischen Baujahr, Kilometerstand, Leistung "
                       "und erkannter Variante gefunden.")

    # 3) Technische Datenlage
    #
    # ROOT-CAUSE-CLOSING (Befund J): hier wurde nur `schweregrad` geprüft. Das Feld
    # gibt es ausschließlich bei Baureihen-Schwachstellen; Motorprobleme und
    # Wartungshinweise haben keins. Beim BMW M4 stand deshalb "keine
    # schwerwiegende bekannte Schwachstelle" direkt über Karten zu Kurbelnabe
    # ("Motorschaden >10.000 €") und Pleuellager. Die Aussage entsteht jetzt aus
    # derselben kanonischen Risikomenge wie die Karten, und sie trennt die drei
    # Achsen: Beleglage (bekannt/gemeldet), Schwere (nur wo erfasst) und Art.
    if baureihe:
        gruende += _technische_datenlage(insights or [], empfehlung)
    rueckrufe = [i for i in insights or [] if getattr(i, "kategorie", None) == "rueckruf"]
    if rueckrufe and all(getattr(i, "applicability", None) in ("series_only", "unclear")
                         for i in rueckrufe):
        gruende.append("Gemeldete Rückrufe gelten für Teile der Baureihe. " + HINWEIS_FIN)

    # 4) Inseratsangaben — ausdrücklich als Angaben, nicht als Tatsachen
    # Servicehistorie: der kanonische Satz aus app/servicehistorie.py, damit dieselbe
    # Angabe hier nicht anders (und nicht stärker) klingt als im Key Finding oder in
    # der Checkliste. Auch die Bestangabe bleibt eine Angabe — deshalb steht die
    # Prüfaufforderung im selben Grund.
    _sh = servicehistorie_status(req)
    if _sh == SH_VOLLSTAENDIG:
        gruende.append(servicehistorie_satz(_sh) + " Vollständigkeit und Belege "
                       "vor dem Kauf prüfen.")
    elif _sh in (SH_TEILWEISE, SH_NICHT_VORHANDEN):
        gruende.append(servicehistorie_satz(_sh) + " Der Wartungsstand ist damit nicht "
                       "belegt und bleibt vor dem Kauf zu klären.")
    elif _sh == SH_UMFANG_UNKLAR:
        gruende.append(servicehistorie_satz(_sh) + " Welche Unterlagen vorliegen, "
                       "bleibt zu erfragen.")
    if hu is not None and getattr(hu, "status", None) == "plausibel":
        gruende.append(f"HU laut Inserat gültig bis {hu.anzeige}. Prüfbericht ansehen.")

    # 5) Warum "nach Besichtigung"
    detail = unfall_detail(req)
    accident = unfall_status(req)
    if accident == UNFALLFREI and detail.eingeschraenkt:
        gruende.append("Laut Inserat sind keine Unfallschäden bekannt (eingeschränkte Angabe). "
                       "Schäden und Nachlackierungen vor Ort klären und schriftlich festhalten.")
    elif accident == UNFALLFREI:
        gruende.append("Laut Inserat unfallfrei. Diese Angabe vor Ort prüfen und schriftlich festhalten.")
    elif accident == UNFALL:
        gruende.append("Unfall/Schaden laut Inserat angegeben. Umfang und Reparaturbelege klären.")
    else:
        gruende.append("Unfallhistorie nicht vollständig bekannt. Vor Kauf Schäden, "
                       "Nachlackierungen und Reparaturhistorie klären.")

    # 6) Preis als eigene Dimension
    if markt_verfuegbar and preis_label:
        gruende.append(f"Preis separat bewertet: {preis_label}.")
    else:
        gruende.append("Preis nicht bewertet: keine belastbare Marktpreisbasis. Die "
                       "Empfehlung ist eine rein technische Einschätzung.")
    return gruende
