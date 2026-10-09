from __future__ import annotations

"""
RC-W6 — Canonical-only KBA-Kandidaten: amtliche Rueckrufe fuer Marken, die
VIRA NICHT katalogisiert (kein `baureihe`-Eintrag).

WAS DIESES MODUL IST
---------------------
Ein reines Klassifikations-Modul, analog zu `app/kba_import_kandidaten.py`,
aber fuer genau die Marken, die dessen Markengate (`kand.marke.upper() not
in vira_marken`) heute STILL verwirft. Es liest/klassifiziert, schreibt
nichts — das Schreiben bleibt bei `app/kba_recall_refresh.py::apply_sync()`
(additiv erweitert, s. dort).

`app/kba_import_kandidaten.py::import_kandidaten()` bleibt UNVERAENDERT: der
stille Markengate-Drop ist fuer katalogisierte Marken weiterhin korrekt
(fuer sie entscheidet die bestehende, generationsbasierte Logik). Dieses
Modul ist der explizite, auditierbare GEGENPART fuer alles, was dort
herausfaellt — kein stiller Drop mehr, aber auch KEINE Aenderung an der
bestehenden, battle-getesteten Baureihen-Logik.

WARUM KEINE GENERATION NOETIG IST
----------------------------------
Ohne `baureihe`-Katalogeintrag gibt es keine VIRA-Generation, gegen die
mehrdeutig gemappt werden koennte — die Mehrdeutigkeitsklassen von
`import_kandidaten` (AMBIGUOUS_GENERATION, Randueberlappung, offene
Generation) haben hier keine Entsprechung. Was bleibt: Marke (hartes Gate),
Nameplate (Modelltoken direkt aus der amtlichen KBA-Modellspalte, OHNE
VIRA-Generationsaufloesung), Zeitraum (direkt gegen das amtliche
Produktionsfenster, keine Baureihen-Verengung), Sicherheitsrelevanz,
Variantenbeschraenkung — ALLES bereits bestehende, generische Funktionen aus
`app/kba_reconciliation.py` und `app/kba_import_kandidaten.py`, hier nur
auf einen anderen Kandidatenkreis angewendet.

DIE IMPORTEINHEIT IST EIN (KBA-REFERENZ, NAMEPLATE)-PAAR
----------------------------------------------------------
Dieselbe Regel wie bei `import_kandidaten`: ein amtlicher Datensatz, der
mehrere Modelle nennt ("2, 6, CX-3, 3, CX-9, CX-8, MX-5, CX-5"), erzeugt JE
TOKEN einen eigenen Kandidaten — niemals ein Modell-Freitext-Blob.
"""

import collections

from app.kba_import_kandidaten import ImportKandidat, variantenbeschraenkung_unabbildbar
from app.kba_reconciliation import _modelltokens, kba_marke, normalisiere_referenz

CANONICAL_SAFE_IMPORT = "CANONICAL_SAFE_IMPORT"
CANONICAL_REVIEW = "CANONICAL_REVIEW"
CANONICAL_NOT_SAFETY_RELEVANT = "CANONICAL_NOT_SAFETY_RELEVANT"

CANONICAL_KLASSEN = (CANONICAL_SAFE_IMPORT, CANONICAL_REVIEW, CANONICAL_NOT_SAFETY_RELEVANT)


class CanonicalImportKandidat:
    """Ein (KBA-Referenz, Nameplate)-PAAR fuer eine nicht katalogisierte Marke."""

    __slots__ = ("kba", "canonical_make", "canonical_nameplate", "klasse", "begruendung")

    def __init__(self, kba_kandidat: ImportKandidat, canonical_make: str, canonical_nameplate: str):
        self.kba = kba_kandidat
        self.canonical_make = canonical_make
        self.canonical_nameplate = canonical_nameplate
        self.klasse = CANONICAL_REVIEW
        self.begruendung = ""

    @property
    def referenz(self) -> str:
        return self.kba.referenz

    @property
    def dedupe_key(self) -> tuple[str, str, str]:
        """Stabiler, generischer Dedupe-Schluessel (Abschnitt 6 des Auftrags):
        KBA-Referenz + canonical Marke + canonical Nameplate — NICHT nur die
        Referenz, weil eine amtliche Aktion mehrere Nameplate-Ziele umfassen
        kann und diese NIE zu einem Datensatz verschmolzen werden duerfen."""
        return (normalisiere_referenz(self.referenz), self.canonical_make, self.canonical_nameplate)


def canonical_kandidaten(kba: list[dict], vira_marken: set[str],
                         bestehende_canonical_paare: set[tuple[str, str, str]] | None = None,
                         *, nur_ueberwacht: bool = True,
                         nur_sicherheitsrelevant: bool = True) -> list[CanonicalImportKandidat]:
    """Klassifiziert amtliche KBA-Zeilen, deren Marke VIRA NICHT katalogisiert.

    `vira_marken` — dieselbe Menge, die `import_kandidaten()` als Markengate
    benutzt (`{kba_marke(b["marke"]) for b in baureihen}`). Eine Zeile, deren
    Marke DARIN steht, wird hier NICHT betrachtet — sie gehoert zum
    bestehenden, unveraenderten Baureihen-Pfad.

    `bestehende_canonical_paare` — bereits importierte (Referenz, Marke,
    Nameplate)-Tripel (siehe `CanonicalImportKandidat.dedupe_key`); wird
    NICHT erneut als Kandidat zurueckgegeben (Idempotenz, Abschnitt 14).

    Deterministisch: gleiche Eingabe, gleiche Reihenfolge, gleiches Ergebnis.
    """
    bestehende_canonical_paare = bestehende_canonical_paare or set()
    gesehen_in_diesem_lauf: set[tuple[str, str, str]] = set()
    out: list[CanonicalImportKandidat] = []

    for k in kba:
        kand = ImportKandidat(k)
        marke_norm = kba_marke(kand.marke)
        if marke_norm in vira_marken:
            continue  # katalogisierte Marke -> bestehender Baureihen-Pfad (import_kandidaten)
        if not marke_norm or not kand.modell.strip():
            continue  # amtliche Zeile ohne Marke/Modell -- kein Zielkandidat moeglich
        # Dieselbe, unveraenderte Vorpruefung wie `import_kandidaten()` (Zeile
        # 616): ein weder ueberwachter noch sicherheitsrelevanter Datensatz
        # bleibt ein stiller Drop -- EXAKT dasselbe, bereits akzeptierte
        # Verhalten fuer katalogisierte Marken, keine neue Regel.
        if nur_ueberwacht and not kand.ueberwacht and not kand.sicherheitsrelevant:
            continue

        for tok in sorted(_modelltokens(kand.modell)):
            key = (normalisiere_referenz(kand.referenz), marke_norm, tok)
            if key in bestehende_canonical_paare or key in gesehen_in_diesem_lauf:
                continue
            gesehen_in_diesem_lauf.add(key)
            ckand = CanonicalImportKandidat(kand, canonical_make=marke_norm, canonical_nameplate=tok)

            # Sicherheitsgate: EXPLIZITE Klasse statt stillem Drop (RC-5-
            # Prinzip, hier auf den canonical-only Pfad uebertragen).
            if nur_sicherheitsrelevant and not kand.sicherheitsrelevant:
                ckand.klasse = CANONICAL_NOT_SAFETY_RELEVANT
                ckand.begruendung = (
                    "weder eine sicherheitsrelevante Bauteilgruppe noch eine erkannte "
                    "Unfall-/Verletzungs-/Brandfolge im amtlichen Text")
                out.append(ckand)
                continue

            # Variantenbeschraenkung: dieselbe Pruefung wie import_kandidaten.
            eingr = kand.eingrenzung
            if variantenbeschraenkung_unabbildbar(eingr):
                ckand.klasse = CANONICAL_REVIEW
                ckand.begruendung = (
                    f"amtliche Eingrenzung nennt eine Bedingung, die generisch nicht "
                    f"abbildbar ist: {eingr[:110]!r}")
                out.append(ckand)
                continue

            ckand.klasse = CANONICAL_SAFE_IMPORT
            ckand.begruendung = (
                f"Marke {marke_norm!r} nicht katalogisiert, Nameplate {tok!r} eindeutig "
                f"aus der amtlichen Modellspalte, sicherheitsrelevant, keine "
                f"unabbildbare Variantenbedingung")
            out.append(ckand)

    out.sort(key=lambda c: (c.klasse, c.referenz, c.canonical_nameplate))
    return out


def zeile_fuer_insert(ckand: CanonicalImportKandidat) -> dict:
    """Eine `CANONICAL_SAFE_IMPORT`-Kandidatin in die Spalten der Tabelle
    `rueckruf` uebersetzt (baureihe_id bleibt NULL). Analog zu
    `app/kba_import_batch_a.py::_baujahre`, aber OHNE Baureihen-Verengung:
    es gibt keine Baureihen-Bauzeitraum, gegen den geschnitten werden
    koennte — das amtliche Produktionsfenster ist hier bereits die volle,
    korrekte Angabe."""
    k = ckand.kba
    von, bis = k.prod_von, k.prod_bis
    betroffene_baujahre = (str(von) if von == bis else f"{von}-{bis}") if (von or bis) else None
    return {
        "baureihe_id": None,
        "datum": k.datum or None,
        "betroffene_baujahre": betroffene_baujahre,
        "mangel": k.mangel,
        "abhilfe": k.massnahme or None,
        "kba_referenz": k.referenz or None,
        "eingrenzung_amtlich": k.eingrenzung or None,
        "prod_von_amtlich": von,
        "prod_bis_amtlich": bis,
        "canonical_make": ckand.canonical_make,
        "canonical_nameplate": ckand.canonical_nameplate,
    }


def zaehle_klassen(kandidaten: list[CanonicalImportKandidat]) -> dict[str, int]:
    zaehler = collections.Counter(c.klasse for c in kandidaten)
    return {klasse: zaehler.get(klasse, 0) for klasse in CANONICAL_KLASSEN}
