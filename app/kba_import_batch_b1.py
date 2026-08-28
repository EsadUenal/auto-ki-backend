from __future__ import annotations

"""
BATCH B1 — die primaerquellenbestaetigte Teilmenge der Risikoklasse B.

WOHER DIESE MENGE KOMMT
-----------------------
Risikoklasse B sind amtliche Rueckrufe, deren Zielbaureihe `bauzeitraum_bis IS
NULL` traegt. Dort ist ohne Zusatzwissen nicht entscheidbar, ob der Rueckruf
noch diese oder schon die Nachfolgegeneration betrifft. Zwei Auditrunden haben
die Menge eingegrenzt:

    B gesamt (offene Zielgeneration)            315 Zeilen / 239 Rueckrufe
    Fachquellen-Audit: GENERATION_CONFIRMED     249 Zeilen / 207 Rueckrufe
    Primaerquellen-Pruefung: SOURCE_CONFIRMED   100 Zeilen /  97 Rueckrufe

Nur die letzte Menge steht hier zur Uebernahme. Sie stuetzt sich auf 17
Baureihen, deren Generationsgrenze auf einer HERSTELLERSEITE belegt ist
(`app/kba_generation_quellen.py`) — Presseportal, Produktionsnetzwerk-Seite
oder Pressemitteilung, durchgehend Quellenstufe 1.

DIESELBEN TORE WIE BATCH A
--------------------------
Die Generationsfrage ist nicht die einzige Fehlerquelle. Batch A hat fuenf
weitere Tore gebaut, und sie gelten hier unveraendert weiter — die
Primaerquelle beantwortet nur die Frage "welche Generation", nicht "welche
Variante" oder "schon vorhanden?":

    A1  zweites plausibles Generationsziel innerhalb desselben Modelltokens
    A2  amtliche Eingrenzung, die VIRA nicht abbilden kann
    A3  Dublette gegen den vorhandenen Bestand
    A4  Referenzformat und markenuebergreifende Kollision
    A5  paralleler amtlicher Datensatz (wortgleich, gleicher Zeitraum, gleiches
        Datum)

Die Implementierung wird aus `app/kba_import_batch_a.py` importiert statt
kopiert. Wer dort eine Regel aendert, aendert sie auch hier.

WAS BATCH B1 NICHT ANFASST
--------------------------
Die 149 SOURCE_UNCLEAR-Zeilen, die beiden SOURCE_CONTRADICTED-Faelle (BMW iX3
16565R, Audi Q3 16773R), die CROSS_GENERATION-Faelle und die 39 Mischziel-
Zeilen. Sie bleiben unberuehrt im Audit stehen.

DIE GENERATIONSQUELLE GEHOERT IN DIE NOTIZ, NICHT IN DIE QUELLE
----------------------------------------------------------------
Der Rueckruf-FAKT stammt aus der KBA-Rueckrufdatenbank — das bleibt
`quelle`/`url` der Verifikation, wie in Batch A. Die Herstellerquelle belegt
etwas anderes: dass die Zuordnung zu DIESER Generation stimmt. Sie steht
deshalb zusaetzlich in der Notiz und ersetzt die amtliche Quelle nicht.
"""

from app.kba_generation_audit import GENERATION_CONFIRMED, klassifiziere
from app.kba_generation_quellen import SOURCE_CONFIRMED, pruefe
from app.kba_import_batch_a import (
    ALTERNATIV_ANTEIL, KBA_ABRUF, KBA_QUELLE, KBA_URL, LIZENZ, QUELLENVERMERK,
    SAMMELSTEMPEL, _KEINE_EINGRENZUNG, _baujahre, _norm_text, _referenz_marken,
    klasse_a, zweite_generation, ziel_index,
)
from app.kba_import_kandidaten import SAFE_IMPORT
from app.kba_reconciliation import normalisiere_referenz

# ── ID-Vergabe ───────────────────────────────────────────────────────────────
# Batch A belegt 2001-2269. B1 beginnt bewusst in einem eigenen, klar
# getrennten Block, damit sich die beiden Chargen nie ueberlappen koennen —
# auch dann nicht, wenn Batch A spaeter nachwaechst.
ID_BASIS_B1 = 3001


def batch_b1_kandidaten(kandidaten, baureihen: list[dict]):
    """Die B-Zeilen, deren Generationsgrenze primaerquellenbestaetigt ist.

    Rueckgabe: Liste von (kandidat, ziel_baureihe_id, primaer_grund).
    """
    bis = {b["id"]: b.get("bauzeitraum_bis") for b in baureihen}
    von = {b["id"]: b.get("bauzeitraum_von") for b in baureihen}
    a_refs = {k.referenz for k in klasse_a(kandidaten, baureihen)}

    out = []
    for k in kandidaten:
        if k.klasse != SAFE_IMPORT or k.referenz in a_refs:
            continue
        for ziel in k.ziel_ids:
            if bis.get(ziel) is not None:
                continue                        # Mischziel — nicht Teil von B1
            fach, _g = klassifiziere(k.prod_von, k.prod_bis, ziel, von.get(ziel))
            if fach != GENERATION_CONFIRMED:
                continue
            quelle, grund = pruefe(k.prod_von, k.prod_bis, ziel, von.get(ziel), fach)
            if quelle != SOURCE_CONFIRMED:
                continue
            out.append((k, ziel, grund))
    return out


def pruefe_batch_b1(kandidaten, baureihen: list[dict], recalls: list[dict]):
    """Finale Vor-Mutations-Pruefung fuer B1. Rueckgabe: (zeilen, ausschluesse).

    Aufbau und Torreihenfolge sind identisch zu
    `app.kba_import_batch_a.pruefe_batch_a`; einziger Unterschied ist die
    Eingangsmenge (primaerquellenbestaetigte B-Zeilen statt Klasse A).
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

    zeilen, ausschluesse = [], []
    for kand, ziel, primaer_grund in sorted(
            batch_b1_kandidaten(kandidaten, baureihen),
            key=lambda t: (normalisiere_referenz(t[0].referenz), t[1])):
        kennung = (kand.referenz, ziel, f"{kand.prod_von}-{kand.prod_bis}")

        # A1 — zweites plausibles Generationsziel desselben Modelltokens.
        alt = zweite_generation(kand, idx)
        if alt:
            zid, uw, aid, ua = alt
            ausschluesse.append((*kennung, f"A1 zweites plausibles "
                                           f"Generationsziel: {zid} {uw:.0%} "
                                           f"gegen {aid} {ua:.0%}"))
            continue

        # A2 — amtliche Eingrenzung, die VIRA nicht abbilden kann.
        eingr = kand.eingrenzung.strip()
        if _norm_text(eingr).replace(" ", "") not in leer_normalisiert:
            ausschluesse.append((*kennung, f"A2 amtliche Eingrenzung nicht "
                                           f"abbildbar: {eingr[:70]!r}"))
            continue

        # A4 — Referenzformat und markenuebergreifende Kollision.
        ref = kand.referenz.strip()
        if not kba_referenz_format_plausibel(ref):
            ausschluesse.append((*kennung, f"A4 Referenzformat unplausibel: {ref!r}"))
            continue
        fremde = ref_marken.get(normalisiere_referenz(ref), set()) - {kand.marke.upper()}
        if fremde:
            ausschluesse.append((*kennung, f"A4 Referenz steht im Bestand bereits "
                                           f"bei {sorted(fremde)}"))
            continue

        # A3 — Dublette gegen den vorhandenen Bestand.
        if (_norm_text(kand.mangel) in mangel_je_baureihe.get(ziel, set())
                or normalisiere_referenz(ref) in ref_je_baureihe.get(ziel, set())):
            ausschluesse.append((*kennung, f"A3 Dublette auf {ziel}"))
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
            "generationsbeleg": primaer_grund,
        })

    # A5 — paralleler amtlicher Datensatz (wortgleich, gleicher Zeitraum,
    # gleiches Datum auf derselben Baureihe). Begruendung siehe Batch A.
    gesehen: dict = {}
    behalten = []
    for z in zeilen:
        schluessel = (z["baureihe_id"], _norm_text(z["mangel"]),
                      z["betroffene_baujahre"], z["datum"])
        if schluessel in gesehen:
            ausschluesse.append((
                z["kba_referenz"], z["baureihe_id"], z["betroffene_baujahre"],
                f"A5 paralleler amtlicher Datensatz zu KBA "
                f"{gesehen[schluessel]} — wortgleich, gleicher Zeitraum, "
                f"gleiches Datum"))
            continue
        gesehen[schluessel] = z["kba_referenz"]
        behalten.append(z)

    for i, z in enumerate(behalten):
        z["id"] = ID_BASIS_B1 + i
    return behalten, ausschluesse
