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

_HOCH = ("hoch", "kritisch", "sehr hoch")


def baue_empfehlung_gruende(req, baureihe: dict | None, motor_match: dict | None,
                            insights: list, key_findings: list, empfehlung: str,
                            markt_verfuegbar: bool, preis_label: str | None,
                            hu=None, generation: str | None = None) -> list[str]:
    gruende: list[str] = []

    # 1) Identität
    if baureihe and motor_match:
        name = " ".join(filter(None, [baureihe.get("marke"), baureihe.get("modell"),
                                      generation or baureihe.get("generation")]))
        motor = motor_match.get("bezeichnung") or ""
        code = motor_match.get("motorcode")
        ps = motor_match.get("leistung_ps")
        details = ", ".join(filter(None, [code, f"{ps} PS" if ps else None]))
        gruende.append(f"Fahrzeug eindeutig zugeordnet: {name} {motor}"
                       + (f" ({details})" if details else "") + ".")
    elif baureihe:
        gruende.append("Baureihe erkannt, Motorisierung aber nicht eindeutig — "
                       "motorbezogene Aussagen bleiben allgemein.")

    # 2) Inserat in sich stimmig
    widersprueche = [f for f in key_findings or []
                     if getattr(f, "kategorie", None) == "widerspruch"]
    if baureihe and not widersprueche:
        gruende.append("Keine Widersprüche zwischen Baujahr, Kilometerstand, Leistung "
                       "und erkannter Variante gefunden.")

    # 3) Technische Datenlage
    schwach = [i for i in insights or []
               if getattr(i, "kategorie", None) in ("schwachstelle", "motorproblem")]
    if baureihe:
        if not any((getattr(i, "schweregrad", None) or "").lower() in _HOCH for i in schwach):
            gruende.append("Im Datensatz keine schwerwiegende bekannte Schwachstelle für "
                           "diese Variante.")
    rueckrufe = [i for i in insights or [] if getattr(i, "kategorie", None) == "rueckruf"]
    if rueckrufe and all(getattr(i, "applicability", None) in ("series_only", "unclear")
                         for i in rueckrufe):
        gruende.append("Gemeldete Rückrufe gelten für Teile der Baureihe — ob genau dieses "
                       "Fahrzeug betroffen ist, klärt eine FIN-Abfrage.")

    # 4) Inseratsangaben — ausdrücklich als Angaben, nicht als Tatsachen
    if getattr(req, "scheckheftgepflegt", None) is True:
        gruende.append("Laut Inserat scheckheftgepflegt — Vollständigkeit und Belege "
                       "vor dem Kauf prüfen.")
    if hu is not None and getattr(hu, "status", None) == "plausibel":
        gruende.append(f"HU laut Inserat gültig bis {hu.anzeige} — Prüfbericht ansehen.")

    # 5) Warum "nach Besichtigung"
    if empfehlung == "kaufen_nach_besichtigung":
        gruende.append("Zustand, Unfallfreiheit und Wartung sind Inseratsangaben — "
                       "sie lassen sich erst bei der Besichtigung bestätigen.")

    # 6) Preis als eigene Dimension
    if markt_verfuegbar and preis_label:
        gruende.append(f"Preis separat bewertet: {preis_label}.")
    else:
        gruende.append("Preis nicht bewertet: keine belastbare Marktpreisbasis — die "
                       "Empfehlung ist eine rein technische Einschätzung.")
    return gruende
