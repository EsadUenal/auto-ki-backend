from __future__ import annotations

"""
BATCH D — die primaerquellenbestaetigte Teilmenge der verlorenen Paare mit
OFFENER Zielgeneration (Fortsetzung von Batch B1 nach dem KaufCheck-Paar-
Closing).

WOHER DIESE MENGE KOMMT
-----------------------
KaufCheck Root-Cause-Closing (Befund M) hat die Import-Entscheidung von "pro
Rueckruf" auf "pro (Rueckruf, Baureihe)-Paar" umgestellt. Batch C hat davon die
Paare mit GESCHLOSSENER Zielgeneration geschlossen (250 von 339). Die
verbleibenden 290 Paare (165 Rueckrufe, 77 Baureihen) haben eine OFFENE
Zielgeneration — genau das Risiko, fuer das `app/kba_generation_audit.py` und
`app/kba_generation_quellen.py` bereits bei Batch B1 gebaut wurden. Batch B1
selbst kann diese Paare nicht gesehen haben: er filtert auf
`k.klasse == SAFE_IMPORT`, also auf Rueckrufe, die schon RUECKRUFWEIT sicher
waren. Genau diese Paare sind es nicht — sie wurden vom alten, rueckrufweiten
Import verworfen, sind aber PAARWEISE sicher.

DIESELBE ENTSCHEIDUNGSKASKADE WIE BATCH B1
--------------------------------------------
    Fachaudit (`app.kba_generation_audit.klassifiziere`)
        GENERATION_CONFIRMED   -> weiter zur Primaerquelle
        SUCCESSOR_RECALL /
        CROSS_GENERATION /
        GENERATION_UNCLEAR     -> Review, hier ENDGUELTIG (kein Import)
    Primaerquelle (`app.kba_generation_quellen.pruefe`)
        SOURCE_CONFIRMED       -> weiter zu den A-Toren
        sonst                  -> Review

Reproduziert am KBA-Gesamtexport vom 2026-08-27 gegen den aktuellen Bestand
(nach Batch C):
    290 offene Paare (165 Rueckrufe)
    189 GENERATION_CONFIRMED, 77 GENERATION_UNCLEAR, 23 CROSS_GENERATION,
    1 SUCCESSOR_RECALL (VW Tiguan 13779: Produktionsfenster 2023 faellt
    bereits in den 2023 begonnenen Serienanlauf der dritten Generation)
    73 davon zusaetzlich SOURCE_CONFIRMED (Herstellerquelle, Stufe 1)

Die Recherche fuer dieses Closing hat den Stand vom 2026-08-28 unabhaengig
mit journalistischen Quellen (Tier 3) gegengeprueft (2026-09-27) — keine
Abweichung gefunden, die eine bestehende GENERATION_CONFIRMED/SOURCE_CONFIRMED-
Einstufung entkraeftet haette. `app/kba_generation_audit.py` bleibt deshalb
unveraendert; dieses Modul fuegt keine neuen Fachaudit-Eintraege hinzu, um die
Trennung "wer hat wann was mit welcher Quellenstufe belegt" nicht zu
verwischen.

DIESELBEN FUENF TORE WIE BATCH A/B1/C
----------------------------------------
    D1  zweites plausibles Generationsziel (identisch zu Batch A/B1 Tor A1,
        `zweite_generation`)
    D2  amtliche Eingrenzung, die VIRA nicht abbilden kann (A2)
    D3  Dublette gegen den aktuellen Bestand (A3)
    D4  Referenzformat und markenuebergreifende Kollision (A4)
    D5  paralleler amtlicher Datensatz (A5)

Implementierung importiert aus `app/kba_import_batch_a.py` statt sie zu
kopieren.
"""

from app.kba_generation_audit import GENERATION_CONFIRMED, klassifiziere
from app.kba_generation_quellen import SOURCE_CONFIRMED, pruefe
from app.kba_import_batch_a import (
    SAMMELSTEMPEL, _baujahre, _KEINE_EINGRENZUNG, _norm_text, _referenz_marken,
    zweite_generation, ziel_index,
)
from app.kba_import_batch_c import geschlossene_verlorene_paare
from app.kba_reconciliation import normalisiere_referenz

# Batch A 2001-2270, Batch B1 3001-3100, Mixed Target 4001-4035,
# Batch C 5001-5250. Batch D beginnt in einem eigenen Block.
ID_BASIS_D = 6001


def batch_d_kandidaten(kba, recalls, baureihen):
    """Die offenen verlorenen Paare, deren Generationsgrenze
    primaerquellenbestaetigt ist.

    Rueckgabe: Liste von (kandidat, ziel_baureihe_id, fach_grund, primaer_grund,
    verlust_grund).
    """
    von = {b["id"]: b.get("bauzeitraum_von") for b in baureihen}
    _geschlossen, offen, kand_je_ref = geschlossene_verlorene_paare(kba, recalls, baureihen)

    out = []
    for o in offen:
        kand = kand_je_ref.get(o["referenz"])
        ziel = o["baureihe_id"]
        if kand is None:
            continue
        fach, fach_grund = klassifiziere(kand.prod_von, kand.prod_bis, ziel, von.get(ziel))
        if fach != GENERATION_CONFIRMED:
            continue
        quelle, primaer_grund = pruefe(kand.prod_von, kand.prod_bis, ziel, von.get(ziel), fach)
        if quelle != SOURCE_CONFIRMED:
            continue
        out.append((kand, ziel, fach_grund, primaer_grund, o["grund"]))
    return out


def alle_offenen_entscheidungen(kba, recalls, baureihen):
    """Fuer JEDES offene Paar die vollstaendige Entscheidungskette — auch die
    verworfenen. Grundlage der Review-Liste (`app/kba_batch_d_daten.py`,
    `REVIEW`). Rueckgabe: Liste von dicts, ein Eintrag je Paar.
    """
    von = {b["id"]: b.get("bauzeitraum_von") for b in baureihen}
    bau_je_id = {b["id"]: b for b in baureihen}
    _geschlossen, offen, kand_je_ref = geschlossene_verlorene_paare(kba, recalls, baureihen)

    out = []
    for o in offen:
        kand = kand_je_ref.get(o["referenz"])
        ziel = o["baureihe_id"]
        b = bau_je_id.get(ziel, {})
        eintrag = {
            "referenz": o["referenz"], "baureihe_id": ziel,
            "marke": b.get("marke"), "modell": b.get("modell"),
            "generation": b.get("generation"),
            "amtliche_modelle": kand.modell if kand else None,
            "mangel": kand.mangel if kand else o.get("mangel"),
            "prod_von": o["prod_von"], "prod_bis": o["prod_bis"],
            "verlust_grund": o["grund"],
        }
        if kand is None:
            eintrag.update(status="REVIEW", grund="Kandidat nicht mehr auffindbar")
            out.append(eintrag)
            continue
        fach, fach_grund = klassifiziere(kand.prod_von, kand.prod_bis, ziel, von.get(ziel))
        eintrag["fachaudit"] = fach
        eintrag["fachaudit_grund"] = fach_grund
        if fach != GENERATION_CONFIRMED:
            eintrag.update(status="REVIEW", grund=fach_grund)
            out.append(eintrag)
            continue
        quelle, primaer_grund = pruefe(kand.prod_von, kand.prod_bis, ziel, von.get(ziel), fach)
        eintrag["primaerquelle"] = quelle
        eintrag["primaerquelle_grund"] = primaer_grund
        if quelle != SOURCE_CONFIRMED:
            eintrag.update(status="REVIEW", grund=primaer_grund)
            out.append(eintrag)
            continue
        eintrag.update(status="KANDIDAT_FUER_IMPORT", grund=primaer_grund)
        out.append(eintrag)
    return out


def pruefe_batch_d(kba, recalls, baureihen):
    """Finale Vor-Mutations-Pruefung fuer Batch D.

    Rueckgabe: (zeilen, ausschluesse, review). `review` enthaelt ALLE Paare,
    die nicht importiert werden — ob schon am Fachaudit/an der Primaerquelle
    gescheitert oder erst an einem der fuenf D-Tore.
    """
    from app.recall_filter import kba_referenz_format_plausibel

    idx = ziel_index(baureihen)
    baureihen_je_id = {b["id"]: b for b in baureihen}
    ref_marken = _referenz_marken(recalls, baureihen)
    leer_normalisiert = {_norm_text(x).replace(" ", "") for x in _KEINE_EINGRENZUNG}
    mangel_je_baureihe: dict = {}
    ref_je_baureihe: dict = {}
    for r in recalls:
        mangel_je_baureihe.setdefault(r["baureihe_id"], set()).add(_norm_text(r["mangel"]))
        ref = normalisiere_referenz(r.get("kba_referenz"))
        if ref:
            ref_je_baureihe.setdefault(r["baureihe_id"], set()).add(ref)

    alle_offen = alle_offenen_entscheidungen(kba, recalls, baureihen)
    review = [e for e in alle_offen if e["status"] == "REVIEW"]
    kandidaten = [(kand, ziel, fg, pg, vg)
                  for kand, ziel, fg, pg, vg
                  in batch_d_kandidaten(kba, recalls, baureihen)]

    zeilen = []
    for kand, ziel, fach_grund, primaer_grund, verlust_grund in sorted(
            kandidaten, key=lambda t: (normalisiere_referenz(t[0].referenz), t[1])):
        kennung = (kand.referenz, ziel)

        alt = zweite_generation(kand, idx)
        if alt:
            zid, uw, aid, ua = alt
            review.append({
                "referenz": kand.referenz, "baureihe_id": ziel,
                "prod_von": kand.prod_von, "prod_bis": kand.prod_bis,
                "status": "REVIEW",
                "grund": f"D1 zweites plausibles Generationsziel: {zid} "
                        f"({uw:.0%}) gegen {aid} ({ua:.0%})"})
            continue

        eingr = kand.eingrenzung.strip()
        if _norm_text(eingr).replace(" ", "") not in leer_normalisiert:
            review.append({
                "referenz": kand.referenz, "baureihe_id": ziel,
                "prod_von": kand.prod_von, "prod_bis": kand.prod_bis,
                "status": "REVIEW",
                "grund": f"D2 amtliche Eingrenzung nicht abbildbar: {eingr[:70]!r}"})
            continue

        ref = kand.referenz.strip()
        if not kba_referenz_format_plausibel(ref):
            review.append({
                "referenz": kand.referenz, "baureihe_id": ziel,
                "prod_von": kand.prod_von, "prod_bis": kand.prod_bis,
                "status": "REVIEW", "grund": f"D4 Referenzformat unplausibel: {ref!r}"})
            continue
        fremde = ref_marken.get(normalisiere_referenz(ref), set()) - {kand.marke.upper()}
        if fremde:
            review.append({
                "referenz": kand.referenz, "baureihe_id": ziel,
                "prod_von": kand.prod_von, "prod_bis": kand.prod_bis,
                "status": "REVIEW",
                "grund": f"D4 Referenz steht im Bestand bereits bei {sorted(fremde)}"})
            continue

        if (_norm_text(kand.mangel) in mangel_je_baureihe.get(ziel, set())
                or normalisiere_referenz(ref) in ref_je_baureihe.get(ziel, set())):
            review.append({
                "referenz": kand.referenz, "baureihe_id": ziel,
                "prod_von": kand.prod_von, "prod_bis": kand.prod_bis,
                "status": "REVIEW", "grund": "D3 Dublette gegen den aktuellen Bestand"})
            continue

        code = kand.herstellercode.strip()
        datum = None if kand.datum.startswith(SAMMELSTEMPEL) else kand.datum
        zeilen.append({
            "baureihe_id": ziel,
            "datum": datum,
            "betroffene_baujahre": _baujahre(kand, baureihen_je_id[ziel]),
            "mangel": kand.mangel,
            "abhilfe": kand.massnahme or None,
            "kba_referenz": ref,
            "herstellercode": "" if code.upper() in {"", "N/A"} else code,
            "amtlicher_zeitraum": f"{kand.prod_von}-{kand.prod_bis}",
            "amtliches_datum": kand.datum,
            "amtliche_modelle": kand.modell,
            "generationsbeleg": primaer_grund,
        })

    zeilen.sort(key=lambda z: (normalisiere_referenz(z["kba_referenz"]), z["baureihe_id"]))

    # D5 — parallele amtliche Datensaetze (identisch zu Batch A/C Gate A5/C5).
    gesehen: dict = {}
    behalten = []
    for z in zeilen:
        schluessel = (z["baureihe_id"], _norm_text(z["mangel"]),
                      z["betroffene_baujahre"], z["datum"])
        if schluessel in gesehen:
            review.append({
                "referenz": z["kba_referenz"], "baureihe_id": z["baureihe_id"],
                "prod_von": None, "prod_bis": None, "status": "REVIEW",
                "grund": f"D5 paralleler amtlicher Datensatz zu KBA "
                        f"{gesehen[schluessel]} — wortgleich, gleicher Zeitraum, "
                        f"gleiches Datum"})
            continue
        gesehen[schluessel] = z["kba_referenz"]
        behalten.append(z)

    for i, z in enumerate(behalten):
        z["id"] = ID_BASIS_D + i
    return behalten, review
