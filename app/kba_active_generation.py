from __future__ import annotations

"""Erweiterung des Batch-A-Sicherheitsnetzes auf OFFENE/aktuelle Generationen
UND auf einzelne, fuer sich sichere Paare innerhalb mehrdeutiger
Mehrfach-Modell-Datensaetze (Release-Hardening: "Active Generation Freshness").

ANLASS (Production Smoke BMW 330i G20 2020)
--------------------------------------------
Der Produktions-KaufCheck fuer einen BMW 330i G20 2020 zeigte nur zwei
Rueckrufe, obwohl `bmw-3er-g20-g21` (Baujahr 2019-NULL, also die aktuell noch
laufende Generation) mehrere amtliche Kandidaten hat, die im Review-Bestand
(`kba_rueckruf_review`) feststecken:

  SAFE_IMPORT_OFFENE_GENERATION  10807, 12198   (eindeutiges Ziel, nur durch
                                                  die offene Generation blockiert)
  AMBIGUOUS_GENERATION           16131R, 16790R (mehrmodellige Brandgefahr-
                                                  Rueckrufe, deren KANDIDATEN-
                                                  WEITE Klasse durch ein ANDERES,
                                                  unabhaengiges Modell im selben
                                                  amtlichen Datensatz --
                                                  mutmasslich "2er", das VIRA
                                                  in mehreren gleichzeitig
                                                  laufenden Baureihen fuehrt --
                                                  heruntergezogen wird, obwohl
                                                  das Paar (Referenz, G20/G21)
                                                  fuer sich eindeutig ist)

ZWEI UNABHAENGIGE LUECKEN, EIN MECHANISMUS
--------------------------------------------
1. OFFENE GENERATION (Mechanismus A): `app.kba_import_batch_a.klasse_a()`
   verlangt `bauzeitraum_bis IS NOT NULL` fuer JEDES Ziel eines SAFE_IMPORT-
   Kandidaten -- unabhaengig davon, wie eindeutig/zeitlich stimmig das
   Mapping ist. Jede noch laufende Baureihe kann dadurch NIEMALS automatisch
   neue amtliche Rueckrufe bekommen, und zwar fuer immer, nicht nur bis zur
   naechsten Generation.

2. KANDIDATENWEITE statt PAARWEISE Entscheidung (Mechanismus B): ein
   amtlicher Datensatz kann mehrere Modelle/Baureihen gleichzeitig nennen.
   `app.kba_import_kandidaten.klassifiziere_kandidat()` berechnet dafuer
   BEREITS eine eigene, korrekte Klasse PRO PAAR (`kand.paare` -- siehe
   "Root-Cause-Closing Befund M" dort), aber `klasse_a()`/`pruefe_batch_a()`
   filtern nur nach der KANDIDATENWEITEN, strengsten Klasse (`kand.klasse`).
   Ist AUCH NUR EIN Ziel-Paar mehrdeutig/dublette/variantenbeschraenkt,
   verliert der gesamte Kandidat JEDES seiner Ziele -- auch die, die fuer
   sich genommen eindeutig und sicher sind.

   ROOT-CAUSE-AUDIT RC-4 (Nachtrag): Mechanismus B war in diesem Modul zunaechst
   NUR fuer OFFENE Zielgenerationen geschlossen (`paare_aktive_generation()`
   verlangte bisher zusaetzlich `_offene_generation_zulaessig`, was geschlossene
   Ziele unbedingt ausschloss) -- GESCHLOSSENE Ziele blieben weiterhin
   AUSSCHLIESSLICH auf den kandidatenweiten `klasse_a()`-Pfad angewiesen und
   damit fuer dieselbe Drag-down-Schwaeche anfaellig (reproduziert an den
   Opel-Astra-K-Gasgenerator-Faellen 6625/6490/6665/11331 -- Astra K ist eine
   GESCHLOSSENE Generation). `_generation_zulaessig()` vereinheitlicht das:
   ein SAFE_IMPORT-Paar, das `klasse_a()` candidatenweit nicht erreicht,
   durchlaeuft diesen Pfad jetzt UNABHAENGIG davon, ob seine Zielgeneration
   offen oder geschlossen ist -- Tor A6 gilt weiterhin NUR fuer offene Ziele,
   geschlossene brauchen kein zusaetzliches Tor. Die scharfen Tore A0-A5 in
   `ergaenzende_zeilen()` selbst waren immer schon fuer beide Faelle identisch
   und mussten dafuer nicht geaendert werden.

WARUM EIN EIGENES MODUL statt klasse_a()/pruefe_batch_a() ZU AENDERN
----------------------------------------------------------------------
`app/kba_import_batch_a.py` ist die eingefrorene Logik der historischen
Charge A und wird von mehreren anderen, ebenfalls eingefrorenen Stellen
UNVERAENDERT weiterverwendet (`app/kba_import_batch_b1.py`,
`kba_generation_audit_report.py`, `test_kba_batch_a.py` -- Letzteres
erwartet explizit, dass `klasse_a()` eine offene Zielgeneration IMMER
verwirft). Dieses Modul aendert an Batch A NICHTS. Es wendet dieselben,
bereits battle-getesteten Tore (A0-A5 aus `pruefe_batch_a()`) unveraendert
auf eine ANDERE, zusaetzliche Kandidaten-/Paarmenge an und ergaenzt genau
EIN neues, generisches Tor (A6), das fuer geschlossene Generationen gar nicht
erst anwendbar ist.

TOR A6 -- OFFENES-GENERATIONS-FENSTER
---------------------------------------
Nur wenn die Zielbaureihe eine OFFENE Generation ist, muss das amtliche
Produktionsfenster bei oder NACH dem Start dieser Generation beginnen
(`kand.prod_von >= baureihe['bauzeitraum_von']`). Beginnt es FRUEHER, kann
der Datensatz ebenso gut (noch) die VORGAENGERGENERATION meinen -- Beispiel
aus der Production-Diagnose: KBA 15630R, amtliches Fenster 2018-2025,
G20/G21 beginnt erst 2019 (Vorgaenger F30 endet 2018) -- bleibt
REVIEW_REQUIRED, auch wenn die Baureihenzuordnung selbst eindeutig ist.

ALLES ANDERE bleibt die EXAKT unveraenderte Pruefung aus Batch A:
Referenzformat, Dublettenpruefung, Variantenbeschraenkung, markenueber-
greifende Kollision, parallele amtliche Datensaetze. Dieses Modul ruft dafuer
`pruefe_batch_a()` selbst auf einer pro-Ziel zugeschnittenen Kandidatenkopie
auf, statt die Tore neu zu schreiben.

SICHERHEIT BLEIBT UNVERAENDERT KONSERVATIV
---------------------------------------------
Dieses Modul entscheidet NUR, ob ein amtlicher Rueckruf eine kanonische
`rueckruf`-Zeile fuer eine Baureihe bekommt ("fuer Teile der Baureihe
gemeldet"). Es aendert NICHTS an der fahrzeugindividuellen Betroffenheits-
pruefung (`app/recall_filter.py::_rueckruf_applicability` bzw.
`app/evidence.py`) -- die bleibt exakt so FIN-/VIN-abhaengig wie bisher.
"""

from app.kba_import_batch_a import (
    ALTERNATIV_ANTEIL, SAMMELSTEMPEL, _a3_dublette, _baujahre, _norm_text,
    _referenz_marken, klasse_a, ziel_index,
)
from app.kba_import_kandidaten import (
    MEDIAN_GENERATIONSDAUER, SAFE_IMPORT, _modelltokens, _ueberdeckung,
)
from app.kba_reconciliation import _ueberlappt, match_tier, normalisiere_referenz
from app.recall_filter import kba_referenz_format_plausibel

# Dieselbe "keine Eingrenzung"-Menge wie in app.kba_import_batch_a; dort nicht
# exportiert (Modulkonstante mit Unterstrich), hier unveraendert uebernommen,
# statt eine zweite Quelle der Wahrheit zu erfinden.
_KEINE_EINGRENZUNG = frozenset({"", "-", "--", "keine", "kein", "nein", "n/a", "na"})
_LEER_NORMALISIERT = {_norm_text(x).replace(" ", "") for x in _KEINE_EINGRENZUNG}


def _offene_generation_zulaessig(kand, baureihe: dict) -> bool:
    """Tor A6: eine OFFENE Zielgeneration ist nur zulaessig, wenn das
    amtliche Produktionsfenster nicht vor ihrem eigenen Start beginnt.
    Nur fuer offene Generationen aufgerufen — siehe `_generation_zulaessig`."""
    von = baureihe.get("bauzeitraum_von")
    return kand.prod_von is not None and von is not None and kand.prod_von >= von


def _generation_zulaessig(kand, baureihe: dict) -> bool:
    """Root-Cause-Audit RC-4 ("Mechanismus B"): die Unterscheidung
    offen/geschlossen entscheidet nur noch, WELCHES zusaetzliche Tor gilt,
    nicht mehr OB ein Paar diesen Pfad ueberhaupt erreichen darf. Eine
    geschlossene Zielgeneration braucht kein weiteres Tor (ihr Fenster ist
    durch den amtlichen Bauzeitraum selbst begrenzt); eine offene Generation
    durchlaeuft zusaetzlich Tor A6. Vorher liefen GESCHLOSSENE Ziele
    AUSSCHLIESSLICH ueber die kandidatenweite Pruefung in `klasse_a()` — ein
    fuer sich sicheres Paar verlor sein Ziel vollstaendig, sobald ein ANDERES,
    unabhaengiges Ziel desselben mehrmodelligen amtlichen Datensatzes
    mehrdeutig war (reproduziert u.a. an den Astra-K-Gasgenerator-Faellen
    6625/6490/6665/11331 im Root-Cause-Audit)."""
    if baureihe.get("bauzeitraum_bis") is not None:
        return True
    return _offene_generation_zulaessig(kand, baureihe)


def paare_aktive_generation(kandidaten, baureihen: list[dict]) -> dict:
    """{kand: [ziel_id, ...]} -- SAFE_IMPORT-PAARE (aus `kand.paare`), die
    NICHT schon durch `klasse_a()` abgedeckt sind (weil die kandidatenweite
    Klasse strenger war als die paarweise — unabhaengig davon, ob die
    Zielbaureihe offen oder geschlossen ist) und `_generation_zulaessig`
    erfuellen. Reine Vorauswahl -- die scharfen Tore A0-A5 laufen erst in
    `ergaenzende_zeilen()`, UNVERAENDERT fuer offene wie geschlossene Ziele."""
    bereits = {id(k) for k in klasse_a(kandidaten, baureihen)}
    baureihen_je_id = {b["id"]: b for b in baureihen}
    out: dict = {}
    for k in kandidaten:
        if id(k) in bereits:
            continue
        ziele = []
        for bid, kl, _g in k.paare:
            if kl != SAFE_IMPORT:
                continue
            b = baureihen_je_id.get(bid)
            if b is not None and _generation_zulaessig(k, b):
                ziele.append(bid)
        if ziele:
            out[k] = sorted(ziele)
    return out


def _zweite_generation_je_ziel(kand, ziel_id: str, idx: dict):
    """Wie `app.kba_import_batch_a.zweite_generation()`, aber auf die Token
    beschraenkt, die GENAU dieses Ziel erreichen -- eine Mehrdeutigkeit bei
    einem ANDEREN, unabhaengigen Ziel desselben mehrmodelligen Datensatzes
    (z.B. "2er" bei einem herstellerweiten Brandschutz-Rueckruf) blockiert
    dieses Ziel nicht mehr."""
    schlimmster = None
    for tok in sorted(_modelltokens(kand.modell)):
        kandidaten_fuer_tok = idx.get((kand.marke.upper(), tok), [])
        if ziel_id not in {b["id"] for b in kandidaten_fuer_tok}:
            continue
        gewinner, alternativen = [], []
        for b in kandidaten_fuer_tok:
            von, bis = b.get("bauzeitraum_von"), b.get("bauzeitraum_bis")
            if not _ueberlappt(von, bis, kand.prod_von, kand.prod_bis):
                continue
            if (bis is None and von and kand.prod_von
                    and kand.prod_von > von + MEDIAN_GENERATIONSDAUER):
                continue
            u = _ueberdeckung(kand.prod_von, kand.prod_bis, von, bis)
            stufe = match_tier(b["marke"], b["modell"], tok)
            (gewinner if b["id"] == ziel_id else alternativen).append((u, b["id"], stufe))
        if not gewinner or not alternativen:
            continue
        # Match-Staerke (Ebene A) -- siehe dieselbe Begruendung in
        # `app.kba_import_batch_a.zweite_generation()`: eine Alternative
        # blockiert nur, wenn sie den Token mindestens so stark erreicht
        # wie das Ziel selbst.
        ziel_stufe = min(s for _u, _bid, s in gewinner)
        alternativen_gefiltert = [(u, bid) for u, bid, s in alternativen if s <= ziel_stufe]
        if not alternativen_gefiltert:
            continue
        uw, zid = max((u, bid) for u, bid, _s in gewinner)
        ua, aid = max(alternativen_gefiltert)
        if ua >= uw * ALTERNATIV_ANTEIL:
            if schlimmster is None or ua > schlimmster[3]:
                schlimmster = (zid, uw, aid, ua)
    return schlimmster


def ergaenzende_zeilen(kandidaten, baureihen: list[dict], recalls: list[dict]):
    """Wendet die Tore A0-A5 (identische Schwellen/Logik wie
    `app.kba_import_batch_a.pruefe_batch_a()`) PRO PAAR auf die in
    `paare_aktive_generation()` vorausgewaehlte Menge an.

    Rueckgabe: (zeilen, ausschluesse) -- `zeilen` in der Form, die
    `apply_sync()` bereits fuer `plan["neue_zeilen"]` erwartet; `ausschluesse`
    als (referenz, marke, modell, ziel_id, grund)-5-Tupel (eine Stelle mehr
    als bei `pruefe_batch_a()`, da hier pro ZIEL statt pro Kandidat
    entschieden wird)."""
    vorauswahl = paare_aktive_generation(kandidaten, baureihen)
    if not vorauswahl:
        return [], []

    baureihen_je_id = {b["id"]: b for b in baureihen}
    idx = ziel_index(baureihen)
    ref_marken = _referenz_marken(recalls, baureihen)
    mangel_je_baureihe: dict = {}
    mangel_je_baureihe_ohne_referenz: dict = {}
    ref_je_baureihe: dict = {}
    for r in recalls:
        mangel_je_baureihe.setdefault(r["baureihe_id"], set()).add(_norm_text(r["mangel"]))
        r_ref = (r.get("kba_referenz") or "").strip()
        if r_ref and kba_referenz_format_plausibel(r_ref):
            ref_je_baureihe.setdefault(r["baureihe_id"], set()).add(normalisiere_referenz(r_ref))
        else:
            mangel_je_baureihe_ohne_referenz.setdefault(
                r["baureihe_id"], set()).add(_norm_text(r["mangel"]))

    paare = sorted(
        ((kand, ziel) for kand, ziele in vorauswahl.items() for ziel in ziele),
        key=lambda p: (normalisiere_referenz(p[0].referenz), p[1]))

    zeilen, ausschluesse = [], []
    for kand, ziel in paare:
        kennung = (kand.referenz, kand.marke, kand.modell, ziel)

        if kand.prod_von is None or kand.prod_bis is None or not kand.datum:
            ausschluesse.append((*kennung, "A0 Produktionszeitraum oder Datum fehlt"))
            continue

        alt = _zweite_generation_je_ziel(kand, ziel, idx)
        if alt:
            zid, uw, aid, ua = alt
            ausschluesse.append((*kennung, f"A1 zweites plausibles Generationsziel: "
                                           f"{zid} {uw:.0%} gegen {aid} {ua:.0%}"))
            continue

        eingr = kand.eingrenzung.strip()
        if _norm_text(eingr).replace(" ", "") not in _LEER_NORMALISIERT:
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

        if _a3_dublette(kand, ziel, ref, mangel_je_baureihe,
                        mangel_je_baureihe_ohne_referenz, ref_je_baureihe):
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
            # Root-Cause-Audit RC-2: wie in kba_import_batch_a.pruefe_batch_a()
            # — hier ebenfalls immer trivial (Tor A2 ungeaendert), aber
            # verlustfrei statt implizit "N/A".
            "eingrenzung_amtlich": eingr or None,
            "prod_von_amtlich": kand.prod_von,
            "prod_bis_amtlich": kand.prod_bis,
        })

    zeilen.sort(key=lambda z: (normalisiere_referenz(z["kba_referenz"]), z["baureihe_id"]))

    # A5: paralleler amtlicher Datensatz -- identische Pruefung wie in
    # pruefe_batch_a(), hier auf dem eigenen `zeilen`-Zwischenstand.
    gesehen: dict = {}
    behalten = []
    for z in zeilen:
        schluessel = (z["baureihe_id"], _norm_text(z["mangel"]), z["betroffene_baujahre"],
                     z["datum"])
        if schluessel in gesehen:
            ausschluesse.append((
                z["kba_referenz"], None, None, z["baureihe_id"],
                f"A5 paralleler amtlicher Datensatz zu KBA {gesehen[schluessel]}"))
            continue
        gesehen[schluessel] = z["kba_referenz"]
        behalten.append(z)

    return behalten, ausschluesse
