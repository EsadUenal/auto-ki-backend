from __future__ import annotations

"""
Konditionale Admission fuer amtlich eingegrenzte, aber baureihen-sichere Paare
(Root-Cause-Audit RC-3: "Conditional recalls disappear into review").

DER BEFUND
----------
`app/kba_import_kandidaten.py::klassifiziere_kandidat()` berechnet fuer jedes
(Referenz, Baureihe)-Paar bereits eine eigene, pro-Paar-Klasse (`kand.paare`).
Ein Paar wird `VARIANT_SCOPE_UNCLEAR`, wenn die Baureihe fuer sich GENOMMEN
sicher ist (keine Dublette, keine Generations-Mehrdeutigkeit) und EINZIG die
amtliche "Moegliche Eingrenzung der betroffenen Modelle" eine Bedingung nennt,
die VIRA nicht strukturell abbilden kann (z.B. "Es sind ausschliesslich
Fahrzeuge mit 2.0 TFSI und Mild-Hybrid-System betroffen").

Bisher endete so ein Paar IMMER nur in `kba_rueckruf_review` — korrekt
zurueckhaltend gegenueber einer automatischen UNBEDINGTEN Uebernahme (Tor A2
in `app/kba_import_batch_a.py`/`app/kba_active_generation.py` bleibt
UNVERAENDERT: er verweigert weiterhin jede Zeile, deren Eingrenzung nicht
trivial ist). Aber es gab keinen Weg zurueck in eine fahrzeugindividuelle
Bewertung: der Rueckruf blieb fuer IMMER unsichtbar, auch bedingt, auch wenn
ein spaeterer Nutzer genau die gefragte Eigenschaft (z.B. "Mild-Hybrid")
explizit bestaetigt oder verneint.

DIE LOESUNG
-----------
Dieses Modul admittiert ein SOLCHES Paar KANONISCH — aber NIE unbedingt. Die
kanonische Zeile traegt den ROHEN amtlichen Eingrenzungstext in der additiven
Spalte `eingrenzung_amtlich` (siehe `app/database.py::_migrate_schema`).
`app/recall_filter.py::rueckruf_scope()` liest dieses Feld zur LAUFZEIT, pro
angefragtem Fahrzeug, zusammen mit `mangel`/`abhilfe`/`betroffene_baujahre` —
dieselbe Funktion, nicht neu erfunden. Das Ergebnis ist IMMER eine der
bestehenden, bereits konservativen Stufen (SERIES_RELEVANT/VARIANT_POSSIBLE/
NOT_APPLICABLE/UNKNOWN) — NIE eine neue, automatische "betrifft dieses
Fahrzeug"-Behauptung. Kennt das System die abgefragte Eigenschaft (Kraftstoff/
Leistung/Hubraum/Motorcode/Ausstattung) des Fahrzeugs nicht, bleibt die
Applicability UNKNOWN ("Betroffenheit unklar: per FIN pruefen") — sichtbar,
nicht versteckt, aber auch nicht faelschlich bestaetigt.

SICHERHEITSGRENZE (ausdruecklich aus dem Auftrag): NUR Paare mit sicherer
Baureihen-Zuordnung durchlaufen diesen Pfad. Ein Paar, das (AUCH) wegen
Generations-Mehrdeutigkeit oder einer moeglichen Dublette blockiert waere,
bleibt review-only — das prueft dieses Modul erneut und unabhaengig von der
kandidatenweiten `kand.klasse` (die durch ein ANDERES Ziel desselben
mehrmodelligen Datensatzes strenger sein kann, siehe RC-4/"Mechanismus B").

Alle uebrigen Sicherheitstore bleiben WOERTLICH dieselben wie in
`app.kba_active_generation` (A0, A1 pro Ziel, A3, A4, A6 bei offener
Generation) — dieses Modul schreibt sie nicht neu, es importiert sie.
"""

from app.kba_active_generation import (
    _offene_generation_zulaessig, _zweite_generation_je_ziel,
)
from app.kba_import_batch_a import (
    SAMMELSTEMPEL, _KEINE_EINGRENZUNG, _a3_dublette, _baujahre, _norm_text,
    _referenz_marken, ziel_index,
)
from app.kba_import_kandidaten import SAFE_IMPORT, VARIANT_SCOPE_UNCLEAR
from app.kba_reconciliation import normalisiere_referenz
from app.recall_filter import kba_referenz_format_plausibel

# Dieselbe "keine Eingrenzung"-Menge wie Tor A2 in kba_import_batch_a/
# kba_active_generation — hier NICHT exportiert, deshalb unveraendert
# uebernommen statt neu erfunden (dritte Kopie dieses Musters im Projekt).
_LEER_NORMALISIERT = {_norm_text(x).replace(" ", "") for x in _KEINE_EINGRENZUNG}


def _nicht_abbildbare_eingrenzung(kand) -> bool:
    """Dieselbe STRENGE Pruefung wie Tor A2 (nicht die laxere, nur fuer die
    Dry-Run-Anzeige gedachte `kand.variantenbeschraenkung`-Heuristik in
    `klassifiziere_kandidat()`, die ein einzelnes Kraftstoff-/Hybrid-Wort
    irgendwo im Satz bereits als "aufloesbar" werten kann, selbst wenn der
    Rest des Satzes — wie bei "Es sind ausschliesslich Fahrzeuge mit 2.0
    TFSI UND Mild-Hybrid-System betroffen" — weiterhin eine echte, nicht
    abbildbare Bedingung traegt). Tor A2 selbst verlangt striker: NUR ein
    VOLLSTAENDIG leerer/"keine"/"N/A"-Text gilt als unbedenklich."""
    eingr = kand.eingrenzung.strip()
    return _norm_text(eingr).replace(" ", "") not in _LEER_NORMALISIERT


def paare_konditionale_eingrenzung(kandidaten) -> dict:
    """{kand: [ziel_id, ...]} — Paare mit einer ECHTEN, nicht abbildbaren
    amtlichen Eingrenzung (strenge Pruefung, siehe `_nicht_abbildbare_
    eingrenzung`), deren EIGENE (pro-Paar) Klasse weder Dublette noch
    Generations-mehrdeutig ist. Reine Vorauswahl — die scharfen Tore laufen
    erst in `ergaenzende_konditionale_zeilen()`.

    Bewusst NICHT `kl == VARIANT_SCOPE_UNCLEAR` allein: `klassifiziere_
    kandidat()`s `kand.variantenbeschraenkung` kann ein Paar faelschlich als
    `SAFE_IMPORT` fuehren, obwohl die amtliche Eingrenzung (wie oben) eine
    echte Bedingung traegt — Tor A2 in `pruefe_batch_a()`/`ergaenzende_
    zeilen()` prueft davon UNABHAENGIG noch einmal selbst und wuerde ein
    solches Paar dort ohnehin ablehnen (Verteidigung in der Tiefe, kein
    Sicherheitsloch) — es wuerde aber, ohne diese Erweiterung, in KEINEN der
    beiden Pfade fallen und als `SAFE_IMPORT_NICHT_UEBERNOMMEN` im Review
    landen statt konditional nutzbar zu werden. Deshalb hier beide
    pro-Paar-Klassen zulassen, die NICHT Dublette/Mehrdeutig sind."""
    out: dict = {}
    for k in kandidaten:
        if not _nicht_abbildbare_eingrenzung(k):
            continue  # Tor A2 liesse das ohnehin unbedingt durch — kein Fall fuer diesen Pfad
        ziele = [bid for bid, kl, _g in k.paare if kl in (SAFE_IMPORT, VARIANT_SCOPE_UNCLEAR)]
        if ziele:
            out[k] = sorted(ziele)
    return out


def ergaenzende_konditionale_zeilen(kandidaten, baureihen: list[dict],
                                    recalls: list[dict]):
    """Wendet A0/A1/A3/A4(/A6 bei offener Zielgeneration) PRO PAAR auf die in
    `paare_konditionale_eingrenzung()` vorausgewaehlte Menge an — identisch zu
    `app.kba_active_generation.ergaenzende_zeilen()`, nur dass Tor A2 bewusst
    NICHT geprueft wird (das ist hier gerade der Zweck: das Paar hat A2 bereits
    NICHT bestanden) und die admittierte Zeile den rohen Eingrenzungstext
    traegt statt verworfen zu werden.

    Rueckgabe: (zeilen, ausschluesse) — `zeilen` bereits im Format, das
    `apply_sync()` fuer `plan["neue_zeilen_konditional"]` erwartet (dieselben
    Spalten wie `plan["neue_zeilen"]` PLUS `eingrenzung_amtlich`/
    `prod_von_amtlich`/`prod_bis_amtlich`)."""
    vorauswahl = paare_konditionale_eingrenzung(kandidaten)
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

        b = baureihen_je_id.get(ziel)
        if b is not None and b.get("bauzeitraum_bis") is None:
            if not _offene_generation_zulaessig(kand, b):
                ausschluesse.append((*kennung, "A6 offene Generation beginnt vor dem "
                                                "amtlichen Produktionsfenster"))
                continue

        alt = _zweite_generation_je_ziel(kand, ziel, idx)
        if alt:
            zid, uw, aid, ua = alt
            ausschluesse.append((*kennung, f"A1 zweites plausibles Generationsziel: "
                                           f"{zid} {uw:.0%} gegen {aid} {ua:.0%}"))
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

        datum = None if kand.datum.startswith(SAMMELSTEMPEL) else kand.datum
        zeilen.append({
            "baureihe_id": ziel,
            "datum": datum,
            "betroffene_baujahre": _baujahre(kand, baureihen_je_id[ziel]),
            "mangel": kand.mangel,
            "abhilfe": kand.massnahme or None,
            "kba_referenz": ref,
            # Der einzige inhaltliche Unterschied zu einer unbedingten Zeile:
            # die amtliche Eingrenzung bleibt ERHALTEN statt implizit "keine".
            "eingrenzung_amtlich": kand.eingrenzung,
            "prod_von_amtlich": kand.prod_von,
            "prod_bis_amtlich": kand.prod_bis,
        })

    zeilen.sort(key=lambda z: (normalisiere_referenz(z["kba_referenz"]), z["baureihe_id"]))

    # A5: paralleler amtlicher Datensatz — identische Pruefung wie in
    # kba_active_generation.ergaenzende_zeilen()/kba_import_batch_a.pruefe_batch_a().
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
