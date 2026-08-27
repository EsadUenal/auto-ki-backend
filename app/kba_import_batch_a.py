from __future__ import annotations

"""
Batch A: die EINZIGE Teilmenge des KBA-Import-Dry-Runs, die ohne offene
Generationsfrage uebernommen werden kann — und was dafuer zusaetzlich zu
`SAFE_IMPORT` noch geprueft werden muss.

WAS RISIKOKLASSE A IST
----------------------
Der Dry-Run (`app/kba_import_kandidaten.py`) hat 530 amtliche Rueckrufe als
`SAFE_IMPORT` klassifiziert. 240 davon zielen auf eine OFFENE Generation
(`bauzeitraum_bis IS NULL`) — dort ist strukturell nicht entscheidbar, ob der
Rueckruf noch die bekannte oder schon die Nachfolgegeneration betrifft (der
dokumentierte Fall BMW iX3 G08). Genau dieses Restrisiko von 45 % faellt weg,
wenn man sich auf GESCHLOSSENE Zielgenerationen beschraenkt:

    SAFE_IMPORT gesamt            530 Rueckrufe -> 785 VIRA-Zeilen
    davon offene Zielgeneration   240           -> 356      (Klasse B, NICHT hier)
    davon geschlossen             290           -> 429      (Klasse A)

VIER ZUSAETZLICHE TORE — UND WARUM SIE NOETIG SIND
--------------------------------------------------
`SAFE_IMPORT` war fuer eine MENGENSCHAETZUNG gebaut, nicht fuer eine Mutation.
Die Nachpruefung von Klasse A hat vier Luecken gefunden, die alle in dieselbe
Richtung wirken: sie behaupten mehr, als der amtliche Datensatz hergibt.

A1  ZWEITES PLAUSIBLES GENERATIONSZIEL
    Der Dry-Run prueft Mehrdeutigkeit nur unter den Generationen, die alle
    Tore passiert haben. Eine Generation, die knapp an der 2/3-Ueberdeckung
    scheitert, verschwindet lautlos — obwohl gerade sie das Signal ist, dass
    die Zuordnung wackelt. 62 der 290 Rueckrufe haben eine solche zweite
    Generation DESSELBEN Modells, die mindestens die halbe Ueberdeckung des
    Gewinners erreicht (z.B. KBA 11231 Mercedes S-Klasse: W222 75 %, W223
    50 %). Ueberdehnte offene Generationen zaehlen dabei ausdruecklich NICHT
    als Alternative — sie sind bereits regulaer ausgeschlossen.

A2  VARIANTENBEDINGUNG IM FREITEXT
    `_VARIANTENWOERTER` im Dry-Run sucht nach Motor-/Antriebswoertern. Das
    amtliche Feld "Moegliche Eingrenzung der betroffenen Modelle" enthaelt aber
    auch Einschraenkungen ganz anderer Art, die VIRA genauso wenig abbilden
    kann: "Nur Sportage-Modelle, die nicht mit Smart Cruise Control (SCC)
    ausgestattet sind", "Es sind nur Rechtslenker-Fahrzeuge betroffen",
    "Grauimportierte Fahrzeuge aus den USA", Fahrgestellnummernbereiche,
    Chassiscode-Listen, "Kriterien 46, 47". Wer solche Zeilen als allgemeinen
    Baureihen-Rueckruf speichert, behauptet eine breitere Betroffenheit als das
    Amt. Uebernommen wird deshalb nur, was GAR KEINE Eingrenzung traegt oder
    ausdruecklich "keine" bzw. "-" sagt.

    BEWUSST NICHT GEBAUT: eine Ableitung des Kraftstoff-Klammerzusatzes aus
    diesem Feld. Von den vier Klasse-A-Zeilen, in denen ueberhaupt ein
    Kraftstoffwort steht, waeren zwei falsch verstanden worden — "A5, Q5,
    Q5 Hybrid, SQ5 TDI, SQ5 plus TDI" und "C-HR Hybrid" sind MODELLLISTEN,
    keine Antriebsbedingungen. Ein daraus gebautes "(Hybrid)" haette den
    Rueckruf faelschlich auf Hochvoltfahrzeuge verengt. Batch A schreibt
    deshalb NIE einen Qualifier — alle Zeilen sind reine Baureihen-Rueckrufe
    und bleiben damit zur Laufzeit auf der Baureihen-Ebene.

A3  DUBLETTE
    Zusaetzlich zur Referenz- und Bauteilgruppenpruefung des Dry-Runs: keine
    Zeile derselben Baureihe darf denselben Mangeltext (normalisiert) oder
    dieselbe amtliche Referenz bereits tragen.

A4  REFERENZFORMAT UND KOLLISION
    Die Referenz muss einem der drei tatsaechlich vorkommenden amtlichen
    Formate entsprechen (siehe `app/recall_filter.py`) und darf im Bestand
    nicht bereits bei einer ANDEREN Marke stehen — sonst erzeugt der Import
    eine markenuebergreifende Kollision und stuft dabei die vorhandene Zeile
    mit ab.

NUTZUNGSRECHTE UND QUELLENVERMERK
---------------------------------
Der Bestand stammt aus dem oeffentlich angebotenen Gesamtexport der
KBA-Rueckrufdatenbank. Das KBA stellt seine der Oeffentlichkeit zur Verfuegung
gestellten Daten unter die "Datenlizenz Deutschland - Namensnennung -
Version 2.0" (dl-de/by-2-0), die kommerzielle Nutzung, Speicherung,
Veraenderung und Einbindung in Produkte ausdruecklich erlaubt. Pflicht ist der
Quellenvermerk samt Hinweis auf die Veraenderung. `QUELLENVERMERK` unten ist
genau dieser Vermerk; er wird mit jeder importierten Zeile in
`fakt_verifikation` mitgefuehrt.

DETERMINISMUS
-------------
Gleiche Eingabe -> gleiche Zeilen, gleiche IDs. Die Reihenfolge ergibt sich aus
(normalisierte Referenz, baureihe_id); die IDs werden ab `ID_BASIS` fortlaufend
vergeben und sind damit im Seed wie in der Migration identisch.
"""

import re

from app.kba_import_kandidaten import (
    MEDIAN_GENERATIONSDAUER, SAFE_IMPORT, _ueberdeckung,
)
from app.kba_reconciliation import (
    _modelltokens, _ueberlappt, _vira_modellkandidaten, kba_marke,
    normalisiere_referenz,
)

# ── Amtliche Quelle und Lizenz ───────────────────────────────────────────────
KBA_QUELLE = (
    "KBA-Rueckrufdatenbank, amtlicher Gesamtexport (7.816 Rueckrufe), "
    "abgerufen 2026-08-27"
)
KBA_URL = ("https://www.kba-online.de/rrdb/buerger/api/rueckruf/export"
           "?format=csv&type=cars")
KBA_ABRUF = "2026-08-27"
LIZENZ = "dl-de/by-2-0"
QUELLENVERMERK = (
    "Datenquelle: Kraftfahrt-Bundesamt, Rueckrufdatenbank (Fahrzeuge), "
    f"Abrufdatum {KBA_ABRUF}; Datenlizenz by-2-0 "
    "(https://www.govdata.de/dl-de/by-2-0); Daten veraendert: je Baureihe eine "
    "Zeile, Feldauswahl gekuerzt.")

# ── ID-Vergabe ───────────────────────────────────────────────────────────────
# Weit oberhalb des gewachsenen Bestands (hoechste vergebene ID: 808), damit
# keine Kollision mit kuenftigen Einzelnachtraegen entsteht.
ID_BASIS = 2001

# Eingrenzungstexte, die ausdruecklich KEINE Einschraenkung bedeuten.
_KEINE_EINGRENZUNG = frozenset({"", "-", "--", "keine", "kein", "nein", "n/a", "na"})

_WORTGRENZE = re.compile(r"\W+")

# Anteil der Gewinner-Ueberdeckung, ab dem eine zweite Generation als ebenfalls
# plausibel gilt. Bei 1/2 faellt KBA 11231 (75 % gegen 50 %) heraus, waehrend
# eine reine Randberuehrung (100 % gegen 17 %) den Import nicht blockiert.
ALTERNATIV_ANTEIL = 0.5

# Sammelstempel des amtlichen Bestands: 545 der 7.816 Datensaetze tragen exakt
# dieses Veroeffentlichungsdatum — dreissigmal so viele wie das naechsthaeufigste
# Datum (24). Das ist erkennbar der Zeitpunkt der Erstbefuellung der
# Rueckrufdatenbank, nicht der Tag der Veroeffentlichung eines Rueckrufs aus dem
# Jahr 1995. Solche Zeilen bekommen KEIN Datum statt eines falschen: die beiden
# Anzeigepfade (app/evidence.py, app/recall_filter.py) blenden ein fehlendes
# Datum bereits aus. Der amtliche Rohwert bleibt im Verifikationsvermerk
# erhalten, geht also nicht verloren.
SAMMELSTEMPEL = "2008-01-01"


def _norm_text(t: str | None) -> str:
    return _WORTGRENZE.sub(" ", (t or "").lower()).strip()


def _baujahre(kand, baureihe: dict) -> str:
    """Amtliches Produktionsfenster, GESCHNITTEN mit dem Bauzeitraum der Zeile.

    Der amtliche Datensatz nennt EIN Fenster fuer alle betroffenen Modelle;
    VIRA fuehrt je Baureihe eine Zeile. Ein Rueckruf ueber die Produktion
    2016-2018 des Opel Insignia landet bei der Generation B (ab 2017) — dort
    das Jahr 2016 mitzuschreiben wuerde ein Baujahr behaupten, das es fuer
    diese Generation nicht gibt.

    Der Schnitt ist eine reine VERENGUNG aus zwei belegten Angaben (amtliches
    Fenster, VIRA-Bauzeitraum); er erfindet nichts und verbreitert nie. Genau so
    steht es bereits in der kuratierten Zeile #808 (KBA 12223: amtlich
    2016-2020, gespeichert 2017-2020).
    """
    von = max(kand.prod_von, baureihe.get("bauzeitraum_von") or kand.prod_von)
    bis = min(kand.prod_bis, baureihe.get("bauzeitraum_bis") or kand.prod_bis)
    if bis < von:                      # kann nach den Toren nicht auftreten
        von, bis = kand.prod_von, kand.prod_bis
    return str(von) if von == bis else f"{von}-{bis}"


def ziel_index(baureihen: list[dict]) -> dict:
    idx: dict = {}
    for b in baureihen:
        km = kba_marke(b["marke"])
        for tok in _vira_modellkandidaten(b["marke"], b["modell"]):
            idx.setdefault((km, tok), []).append(b)
    return idx


def zweite_generation(kand, idx: dict):
    """Gibt es zu einem Modelltoken eine zweite, ebenso plausible Generation?

    Rueckgabe: (ziel_id, ueberdeckung_ziel, alternativ_id, ueberdeckung_alt)
    der staerksten gefundenen Alternative — oder None.

    Ueberdehnte OFFENE Generationen (Produktionsfenster beginnt mehr als
    `MEDIAN_GENERATIONSDAUER` Jahre nach ihrem Start) zaehlen NICHT als
    Alternative: sie sind bereits durch die regulaere Dry-Run-Regel
    ausgeschlossen und wuerden sonst z.B. jeden Mercedes-G-Klasse-Rueckruf
    ueber die seit Jahrzehnten offene W461 blockieren.
    """
    ziel = set(kand.ziel_ids)
    schlimmster = None
    for tok in _modelltokens(kand.modell):
        gewinner: list = []
        alternativen: list = []
        for b in idx.get((kand.marke.upper(), tok), []):
            von, bis = b.get("bauzeitraum_von"), b.get("bauzeitraum_bis")
            if not _ueberlappt(von, bis, kand.prod_von, kand.prod_bis):
                continue
            if (bis is None and von and kand.prod_von
                    and kand.prod_von > von + MEDIAN_GENERATIONSDAUER):
                continue
            u = _ueberdeckung(kand.prod_von, kand.prod_bis, von, bis)
            (gewinner if b["id"] in ziel else alternativen).append((u, b["id"]))
        if not gewinner or not alternativen:
            continue
        uw, zid = max(gewinner)
        ua, aid = max(alternativen)
        if ua >= uw * ALTERNATIV_ANTEIL:
            if schlimmster is None or ua > schlimmster[3]:
                schlimmster = (zid, uw, aid, ua)
    return schlimmster


def _referenz_marken(recalls: list[dict], baureihen: list[dict]) -> dict:
    marke_je_baureihe = {b["id"]: kba_marke(b["marke"]) for b in baureihen}
    out: dict = {}
    for r in recalls:
        ref = normalisiere_referenz(r.get("kba_referenz"))
        if ref:
            out.setdefault(ref, set()).add(marke_je_baureihe.get(r["baureihe_id"]))
    return out


def klasse_a(kandidaten, baureihen: list[dict]) -> list:
    """SAFE_IMPORT mit ausschliesslich GESCHLOSSENEN Zielgenerationen."""
    bis = {b["id"]: b.get("bauzeitraum_bis") for b in baureihen}
    return [k for k in kandidaten
            if k.klasse == SAFE_IMPORT
            and k.ziel_ids
            and all(bis.get(z) is not None for z in k.ziel_ids)]


def pruefe_batch_a(kandidaten, baureihen: list[dict], recalls: list[dict]):
    """Finale Vor-Mutations-Pruefung. Rueckgabe: (zeilen, ausschluesse).

    `zeilen`: dicts mit genau den Spalten der Tabelle `rueckruf` plus
    `herstellercode` (fuer den Verifikationsvermerk, NICHT fuer die Zeile).
    `ausschluesse`: (referenz, marke, modell, grund) je verworfenem Rueckruf.
    """
    from app.recall_filter import kba_referenz_format_plausibel

    idx = ziel_index(baureihen)
    baureihen_je_id = {b["id"]: b for b in baureihen}
    vorhandene_baureihen = set(baureihen_je_id)
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
    for kand in sorted(klasse_a(kandidaten, baureihen),
                       key=lambda k: (normalisiere_referenz(k.referenz), k.marke)):
        kennung = (kand.referenz, kand.marke, kand.modell)

        fehlend = [z for z in kand.ziel_ids if z not in vorhandene_baureihen]
        if fehlend:
            ausschluesse.append((*kennung, f"A0 Baureihe fehlt: {fehlend}"))
            continue
        if kand.prod_von is None or kand.prod_bis is None or not kand.datum:
            ausschluesse.append((*kennung, "A0 Produktionszeitraum oder Datum fehlt"))
            continue

        alt = zweite_generation(kand, idx)
        if alt:
            zid, uw, aid, ua = alt
            ausschluesse.append((*kennung, f"A1 zweites plausibles Generationsziel: "
                                           f"{zid} {uw:.0%} gegen {aid} {ua:.0%}"))
            continue

        eingr = kand.eingrenzung.strip()
        if _norm_text(eingr).replace(" ", "") not in leer_normalisiert:
            ausschluesse.append((*kennung, f"A2 amtliche Eingrenzung nicht abbildbar: "
                                           f"{eingr[:70]!r}"))
            continue

        ref = kand.referenz.strip()
        if not kba_referenz_format_plausibel(ref):
            ausschluesse.append((*kennung, f"A4 Referenzformat unplausibel: {ref!r}"))
            continue
        fremde = ref_marken.get(normalisiere_referenz(ref), set()) - {kand.marke.upper()}
        if fremde:
            ausschluesse.append((*kennung, f"A4 Referenz steht im Bestand bereits bei "
                                           f"{sorted(fremde)}"))
            continue

        dublette = [z for z in kand.ziel_ids
                    if _norm_text(kand.mangel) in mangel_je_baureihe.get(z, set())
                    or normalisiere_referenz(ref) in ref_je_baureihe.get(z, set())]
        if dublette:
            ausschluesse.append((*kennung, f"A3 Dublette auf {dublette}"))
            continue

        code = kand.herstellercode.strip()
        datum = None if kand.datum.startswith(SAMMELSTEMPEL) else kand.datum
        for ziel in kand.ziel_ids:
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
            })

    zeilen.sort(key=lambda z: (normalisiere_referenz(z["kba_referenz"]), z["baureihe_id"]))

    # ── A5: PARALLELE AMTLICHE DATENSAETZE ──────────────────────────────────
    #
    # Der amtliche Bestand fuehrt vereinzelt DIESELBE Aktion unter zwei
    # Referenzen, getrennt nur durch den Herstellercode: KBA 8408 (4693003) und
    # 8492 (4693004) beschreiben wortgleich dieselbe vorgeschaedigte
    # Sicherungsmutter am Lenkgetriebe, mit demselben Produktionszeitraum und
    # demselben Veroeffentlichungsdatum. Beide zu uebernehmen zeigt dem Nutzer
    # denselben Rueckruf zweimal.
    #
    # Die Bedingung ist bewusst eng — Baureihe, Mangeltext, Baujahre UND Datum
    # muessen uebereinstimmen. Zwei Aktionen mit gleichem Text, aber anderem
    # Zeitraum oder anderem Datum bleiben zwei Zeilen: VW 9777 (Produktion
    # 1997-1999) und 11267 (2000) sind eigenstaendige Rueckrufe, ebenso die
    # Takata-Wellen beim Viano. Behalten wird die kleinere amtliche Referenz;
    # die Reihenfolge ist durch die Sortierung oben festgelegt.
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
        z["id"] = ID_BASIS + i
    return behalten, ausschluesse
