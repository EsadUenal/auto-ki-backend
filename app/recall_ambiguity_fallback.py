from __future__ import annotations

"""
Sicherer Ambiguitaets-Fallback fuer echte, verbleibende Modell-/Generations-
Mehrdeutigkeit (Ebene B — Audi-A4-B9-Root-Cause-Fund, KBA 9831/10206).

DER BEFUND
----------
Ebene A (`app.kba_reconciliation.match_tier`, eingebaut in
`app.kba_import_kandidaten.klassifiziere_kandidat` und in beiden
Tor-A1-Implementierungen `app.kba_import_batch_a.zweite_generation`/
`app.kba_active_generation._zweite_generation_je_ziel`) loest die MEISTEN
bisherigen Mehrdeutigkeiten generisch ueber die Match-Staerke auf: ein
direkter Nameplate-Treffer (z.B. "A4" fuer `audi-a4-b9`) gewinnt gegen eine
Baureihe, die denselben Token nur ueber eine `MODELL_MAP`-Alias-Zuordnung
erreicht (z.B. "A4" fuer `audi-rs-4-avant-b9`, eine Performance-Variante).

Aber Ebene A macht die schwaechere Alternative NICHT automatisch
"ausgeschlossen" — das waere genauso falsch wie die bisherige Blockade. Wenn
zwei Baureihen WIRKLICH gleich stark sind (die weit haeufigere Situation:
zwei echte Geschwister-Generationen, beide ueber ihren eigenen Namen
erreicht), bleibt die Zuordnung absichtlich unentschieden — sie landet in
`kba_rueckruf_review`, rein administrativ, fuer KEINEN KaufCheck sichtbar.

Ein amtlicher, ueberwachter, sicherheitsrelevanter Rueckruf, der nur deshalb
nie im Bericht auftaucht, weil VIRA zwei plausible Baureihen nicht
auseinanderhalten kann, ist aus Produktsicht ein echter Rueckschritt — genau
der Fall, den der Audi-A4-B9-Production-Trace aufgedeckt hat (KBA 9831/10206,
vor Ebene A auch fuer `audi-a4-b9` selbst).

DIE LOESUNG
-----------
Dieses Modul liest NUR die schmale, beim Review-Schreiben bereits vorab
gefilterte Teilmenge (`app.database.get_alle_review_fallback_kurz()` —
`kba_rueckruf_review.fallback_baureihen`, s. dort und
`app.kba_recall_refresh.apply_sync()`): Paare, deren Unsicherheit
NACHWEISLICH aus echter Modell-/Generationsaufloesung stammt (nicht aus
einer Randueberlappung oder einer ueberdehnten offenen Generation — zwei
andere, hier bewusst ausgeschlossene Unsicherheitsarten).

Fuer das konkrete Fahrzeug der laufenden Anfrage prueft `rueckruf_scope()`
(app/recall_filter.py — DIESELBE Funktion, DIESELBE Logik wie fuer jede
kanonische Zeile, nicht neu erfunden) die amtliche Eingrenzung und den
Bauzeitraum. Nur wenn das Ergebnis NICHT "NOT_APPLICABLE" ist (also KEIN
nachgewiesener Widerspruch), entsteht ein Insight — und zwar NIEMALS staerker
als `applicability="unclear"` (dieselbe, bereits produktive Stufe, die
FIN-Pruefung statt Gewissheit bedeutet, s. app/fin_hinweis.py) und mit
`trust="unverified_db"` (darf NIE allein die Empfehlung verschaerfen).

Die Zeile wird NIEMALS kanonisch geschrieben — dieses Modul schreibt
ueberhaupt nichts, es liefert nur Insights fuer GENAU DIESEN Check.
"""

import json

from app.recall_filter import (
    RECALL_NOT_APPLICABLE, _baujahr_passt, kba_referenz_anzeige, rueckruf_scope,
)


def _passende_fallback_zeilen(baureihe_id: str, review_rows: list[dict]) -> list[dict]:
    """Review-Zeilen, deren `fallback_baureihen` DIESE Baureihe enthaelt.

    `review_rows` ist bereits `app.database.get_alle_review_fallback_kurz()`
    — nur Zeilen mit nicht-leerer `fallback_baureihen`-Liste."""
    out = []
    for row in review_rows:
        try:
            ziele = json.loads(row.get("fallback_baureihen") or "[]")
        except (TypeError, ValueError):
            continue
        if baureihe_id in ziele:
            out.append(row)
    return out


def ambiguitaet_hinweise(baureihe_id: str, identity, baujahr: int | None,
                         review_rows: list[dict],
                         bereits_kanonische_referenzen: set[str]) -> list[dict]:
    """Strukturierte Fallback-Hinweise fuer GENAU dieses Fahrzeug.

    Rueckgabe: Liste von Dicts mit den Feldern, die `app.evidence.build_insights`
    fuer ein `Insight` braucht (kba_referenz, kba_anzeige, mangel, applicability).
    Leer, wenn kein echter, nicht bereits kanonisch abgedeckter, nicht
    widersprochener Kandidat vorliegt.

    `bereits_kanonische_referenzen` (normalisierte `kba_referenz`-Werte, aus
    `baureihe["rueckrufe"]` der laufenden Anfrage): Phase-4-Dedupe — ein
    Rueckruf, der fuer DIESE Baureihe bereits kanonisch (exakt oder
    konditional) vorliegt, bekommt hier KEINEN zweiten, schwaecheren
    Hinweis. Kanonisch geht immer vor Ambiguitaets-Fallback."""
    from app.kba_reconciliation import normalisiere_referenz

    out = []
    for row in _passende_fallback_zeilen(baureihe_id, review_rows):
        ref_norm = normalisiere_referenz(row.get("kba_referenz"))
        if not ref_norm or ref_norm in bereits_kanonische_referenzen:
            continue

        passt = _baujahr_passt(row.get("produktionszeitraum"), baujahr)
        if passt is False:
            continue   # amtlicher Bauzeitraum widerspricht dem Fahrzeugjahr

        synthetischer_rueckruf = {
            "mangel": row.get("mangel"),
            "abhilfe": None,
            "betroffene_baujahre": row.get("produktionszeitraum"),
            "eingrenzung_amtlich": row.get("eingrenzung_amtlich"),
        }
        scope_status, _begr = rueckruf_scope(synthetischer_rueckruf, identity)
        if scope_status == RECALL_NOT_APPLICABLE:
            continue   # nachgewiesener Widerspruch (z.B. Kraftstoff/Antriebsart) -> kein Hinweis

        kba_anzeige = kba_referenz_anzeige(row.get("kba_referenz"), row.get("marke"))
        out.append({
            "kba_referenz": row.get("kba_referenz"),
            "kba_anzeige": kba_anzeige,
            "mangel": row.get("mangel"),
        })
    return out
