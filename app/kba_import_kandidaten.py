from __future__ import annotations

"""
Import-Analyse: welche amtlichen Rueckrufe fehlen im VIRA-Bestand, und welche
davon liessen sich UEBERHAUPT gefahrlos uebernehmen?

WAS DIESES MODUL IST
--------------------
Ein reines DRY-RUN-Modul. Es liest, klassifiziert und sagt Applicability
voraus — es schreibt nichts. Es baut auf `app/kba_reconciliation.py` auf und
benutzt dessen Namensraum-Abbildung, Bauteilgruppen und Zeitraumlogik; die
Regeln werden hier NICHT neu erfunden.

DIE IMPORTEINHEIT IST EIN PAAR, KEIN RUECKRUF
---------------------------------------------
Ein amtlicher Rueckruf nennt haeufig mehrere Modelle ("X5, X6";
"A-KLASSE, C-KLASSE, GLS, ..."). VIRA fuehrt Rueckrufe dagegen JE BAUREIHE.
Dieselbe amtliche Aktion erzeugt also mehrere VIRA-Zeilen — das ist
ausdruecklich KEINE Dublette, sondern die Abbildung des Datenmodells. Der
KBA-Gesamtabgleich hat das an KBA 8124 dokumentiert, die als BMW 1er UND BMW
2er Active Tourer gefuehrt wird.

Fuer die Mengenschaetzung heisst das: die Zahl der fehlenden amtlichen
Rueckrufe und die Zahl der entstehenden VIRA-Zeilen sind zwei verschiedene
Zahlen, und die zweite ist deutlich groesser.

WARUM DIE ZAHL 213 ZU KLEIN WAR
-------------------------------
`kba_reconciliation.fehlende_amtliche()` verlangt, dass der amtliche Datensatz
GENAU EIN Modell nennt und dieses auf GENAU EINE Baureihe passt. Das ist ein
bewusst enger Filter fuer die Frage "was fehlt uns sicher?", aber er verdeckt
alle mehrdeutigen Faelle — genau die, ueber die eine Importentscheidung
eigentlich nachdenken muss. Dieses Modul betrachtet deshalb die volle Menge und
weist die Mehrdeutigkeit aus, statt sie wegzufiltern.

KLASSEN (Vorgabe des Auftrags)
------------------------------
``SAFE_IMPORT``               Zielbaureihe eindeutig, Zeitraum passt, keine
                              Dublette, keine offene Variantenfrage
``AMBIGUOUS_GENERATION``      Modell passt, aber mehrere VIRA-Generationen
                              liegen im amtlichen Produktionsfenster
``VARIANT_SCOPE_UNCLEAR``     Der amtliche Datensatz grenzt auf Motor/Antrieb/
                              Variante ein, die VIRA nicht aufloesen kann
``POSSIBLE_DUPLICATE``        Sehr wahrscheinlich schon durch einen
                              vorhandenen VIRA-Rueckruf abgedeckt
``UNSUPPORTED_MODEL_MAPPING`` Kein belastbares VIRA-Ziel
"""

import collections
import re

from app.kba_reconciliation import (
    STARKE_GRUPPEN, _kba_jahr, _modelltokens, _ueberlappt,
    _vira_modellkandidaten, bauteilgruppen, distinktive_tokens, kba_marke,
    normalisiere_referenz,
)

SAFE_IMPORT = "SAFE_IMPORT"
AMBIGUOUS_GENERATION = "AMBIGUOUS_GENERATION"
VARIANT_SCOPE_UNCLEAR = "VARIANT_SCOPE_UNCLEAR"
POSSIBLE_DUPLICATE = "POSSIBLE_DUPLICATE"
UNSUPPORTED_MODEL_MAPPING = "UNSUPPORTED_MODEL_MAPPING"

IMPORT_KLASSEN = (SAFE_IMPORT, AMBIGUOUS_GENERATION, VARIANT_SCOPE_UNCLEAR,
                  POSSIBLE_DUPLICATE, UNSUPPORTED_MODEL_MAPPING)


# Bauteilgruppen, die einen Rueckruf sicherheitsrelevant machen. Bewusst die
# Gruppen, die Bremse, Lenkung, Rueckhaltesystem, Fahrwerk, Rad, Brandgefahr,
# Kraftstoff und Hochvolt betreffen — nicht Komfort oder Abgas.
SICHERHEITSGRUPPEN = frozenset({
    "airbag", "gurt", "bremse_hydr", "bremse_mech", "bremse_elektr", "lenkung",
    "fahrwerk", "rad", "hochvolt", "elektrik_brand", "kraftstoff",
})

# Woerter im Feld "Moegliche Eingrenzung der betroffenen Modelle", die eine
# MOTOR-/ANTRIEBSbedingung ausdruecken. VIRA kann eine Rueckrufzeile nur ueber
# den Kraftstoff-Klammerzusatz eingrenzen (app/recall_filter.py) — alles
# Feinere (Hubraum, Motorcode, Getriebe, Bauwoche) laesst sich nicht abbilden.
_VARIANTENWOERTER = re.compile(
    r"(?:\b\d[,.]\d\s*l\b|\bliter\b|\bhubraum\b|motorcode|\bmotor\b|getriebe|"
    r"\beuro\s*\d|\btdi\b|\btfsi\b|\bcdti\b|\bhdi\b|\bdci\b|zylinder|"
    r"allrad|quattro|4matic|xdrive|\bkw\b|\bps\b|bauwoche|fahrgestellnummer|"
    r"\bfin\b|seriennummer)",
    re.IGNORECASE)

# Kraftstoffwoerter, die VIRA ueber den Klammerzusatz SEHR WOHL abbilden kann.
_AUFLOESBAR = re.compile(
    r"(?:\bdiesel\b|\bbenzin\b|plug-?in|\bphev\b|\bhybrid\b|elektro)",
    re.IGNORECASE)

# ── Das Problem der OFFENEN Generationen ────────────────────────────────────
#
# 126 der 416 VIRA-Baureihen haben `bauzeitraum_bis = NULL`. Die
# Zeitraumlogik behandelt ein offenes Ende als "bis heute" — damit schluckt die
# jeweils neueste bekannte Generation JEDEN Rueckruf aus juengerer Produktion,
# auch wenn er in Wahrheit die NACHFOLGEgeneration betrifft, die VIRA noch gar
# nicht kennt.
#
# Der Dry-Run hat das an konkreten Faellen gezeigt: der amtliche Rueckruf
# 16132R (Ausfall Lenkung, Produktion 2025-2026) landete am VW T-Roc A1, der
# 2017 begann; 16905R (Sicherheitsgurt, Produktion 2026) am Audi Q3 II von
# 2018. In beiden Faellen ist inzwischen eine neue Generation auf dem Markt.
#
# Gegenmassnahme, aus den Bestandsdaten hergeleitet statt geraten: die
# MEDIAN-Laufzeit einer abgeschlossenen VIRA-Baureihe betraegt 7 Jahre
# (Mittelwert 7,1; 90. Perzentil 9; n = 290). Beginnt das amtliche
# Produktionsfenster spaeter als `bauzeitraum_von + 7` und ist die Generation
# offen, ist ein Generationswechsel wahrscheinlicher als die Fortsetzung —
# der Fall geht nach AMBIGUOUS_GENERATION.
#
# BEKANNTE GRENZE, bewusst nicht wegdefiniert: ein ungewoehnlich frueher
# Modellwechsel wird davon nicht erfasst. Der BMW iX3 G08 (ab 2020) hat 2025
# einen Nachfolger bekommen — nach fuenf Jahren. Rueckruf 16565R
# (Stromschlaggefahr Hochvoltsystem, Produktion 2025-2026) bliebe deshalb
# SAFE_IMPORT, obwohl er sehr wahrscheinlich das neue Modell betrifft. Keine
# reine Jahresschwelle faengt das ab, ohne zugleich viele korrekte Faelle zu
# verwerfen. Das ist der Kern des verbleibenden False-Positive-Risikos und
# gehoert vor einem echten Import in die manuelle Durchsicht.
MEDIAN_GENERATIONSDAUER = 7

# ── Das Problem der RANDUEBERLAPPUNG ────────────────────────────────────────
#
# Eine blosse Ueberschneidung reicht als Zuordnung nicht. Zwei Faelle aus der
# Stichprobe zeigen warum:
#
#   KBA 10530 (Ford Galaxy/S-MAX, Produktion 2015-2020) landete am
#   ford-galaxy-second-generation (2006-2015) — Ueberlappung: das eine Jahr
#   2015. Gemeint ist die dritte Generation ab 2015, die VIRA gar nicht kennt.
#
#   KBA 11374 (Toyota Camry, Produktion 2017-2018) landete am
#   toyota-camry-xv50 (2011-2017) — Ueberlappung: das eine Jahr 2017. VIRA hat
#   zwischen XV50 (bis 2017) und XV70 (ab 2019) eine Luecke; der Rueckruf faellt
#   genau hinein.
#
# Beide Male haengt die Zuordnung an einem einzigen Randjahr. Deshalb muss die
# Zielbaureihe den amtlichen Produktionszeitraum UEBERWIEGEND abdecken: mindestens
# zwei Drittel. Die Schwelle ist so gewaehlt, dass sie beide Fehltreffer faengt
# (1/6 = 17 % und 1/2 = 50 %) und keinen der korrekt zugeordneten Faelle aus der
# Stichprobe verwirft — die lagen alle bei 100 %.
MIN_UEBERDECKUNG = 2 / 3


def _ueberdeckung(k_von, k_bis, b_von, b_bis) -> float:
    """Welchen Anteil des AMTLICHEN Fensters deckt die Baureihe ab?

    Jahre inklusive gerechnet: 2019-2020 sind zwei Jahre. Fehlt eine Angabe,
    faellt die Bewertung auf 1.0 zurueck — dann traegt die Entscheidung
    ausschliesslich der Ueberlappungstest, nicht diese Kennzahl.
    """
    if k_von is None or k_bis is None or b_von is None:
        return 1.0
    b_ende = b_bis if b_bis is not None else 2100
    fenster = k_bis - k_von + 1
    if fenster <= 0:
        return 1.0
    schnitt = min(k_bis, b_ende) - max(k_von, b_von) + 1
    return max(0.0, schnitt) / fenster


class ImportKandidat:
    """Ein amtlicher Rueckruf, der im VIRA-Bestand fehlt."""

    __slots__ = ("kba", "ziele", "klasse", "begruendung", "generation_eindeutig",
                 "variantenbeschraenkung", "duplikate", "applicability")

    def __init__(self, kba: dict):
        self.kba = kba
        self.ziele: list[dict] = []
        self.klasse = UNSUPPORTED_MODEL_MAPPING
        self.begruendung = ""
        self.generation_eindeutig = False
        self.variantenbeschraenkung = False
        self.duplikate: list[dict] = []
        self.applicability = "series_only"

    # ── Bequeme Sicht auf die amtlichen Felder ──────────────────────────────
    @property
    def referenz(self) -> str:
        return (self.kba.get("KBA-Referenznummer") or "").strip()

    @property
    def herstellercode(self) -> str:
        return (self.kba.get("Rückrufcode des Herstellers") or "").strip()

    @property
    def marke(self) -> str:
        return (self.kba.get("Marke") or "").strip()

    @property
    def modell(self) -> str:
        return (self.kba.get("Modell") or "").strip()

    @property
    def mangel(self) -> str:
        return (self.kba.get("Mangelbezeichnung") or "").strip()

    @property
    def massnahme(self) -> str:
        return (self.kba.get("Beschreibung der Maßnahme") or "").strip()

    @property
    def eingrenzung(self) -> str:
        e = (self.kba.get("Mögliche Eingrenzung der betroffenen Modelle") or "").strip()
        return "" if e.upper() == "N/A" else e

    @property
    def datum(self) -> str:
        return (self.kba.get("Veröffentlichungsdatum") or "").strip()

    @property
    def ueberwacht(self) -> bool:
        return (self.kba.get("Überwachung der Rückrufaktion durch das KBA")
                or "").strip() == "überwacht"

    @property
    def prod_von(self):
        return _kba_jahr(self.kba.get("Produktionszeitraum von"))

    @property
    def prod_bis(self):
        return _kba_jahr(self.kba.get("Produktionszeitraum bis"))

    @property
    def sicherheitsrelevant(self) -> bool:
        return bool(bauteilgruppen(self.mangel) & SICHERHEITSGRUPPEN)

    @property
    def ziel_ids(self) -> list[str]:
        return sorted({z["id"] for z in self.ziele})


def _kraftstoff_qualifier(eingrenzung: str) -> str | None:
    """Der Klammerzusatz, mit dem VIRA diese Eingrenzung abbilden koennte."""
    m = _AUFLOESBAR.search(eingrenzung or "")
    return m.group(0) if m else None


def _ziel_index(baureihen: list[dict]) -> dict:
    idx = collections.defaultdict(list)
    for b in baureihen:
        km = kba_marke(b["marke"])
        for tok in _vira_modellkandidaten(b["marke"], b["modell"]):
            idx[(km, tok)].append(b)
    return idx


def _moegliche_dubletten(kand: ImportKandidat, ziel: dict,
                         recalls_je_baureihe: dict) -> list[dict]:
    """VIRA-Rueckrufe derselben Baureihe, die denselben Vorgang beschreiben.

    Drei unabhaengige Signale, jedes fuer sich ausreichend:
      1. dieselbe amtliche Referenz,
      2. derselbe Herstellercode,
      3. gleiche Bauteilgruppe UND ueberlappender Zeitraum UND mindestens ein
         gemeinsames trennscharfes Wort.
    Das dritte Signal ist bewusst dreifach konjunktiv — sonst wuerde jeder
    zweite Bremsen-Rueckruf als Dublette des naechsten gelten.
    """
    ref = normalisiere_referenz(kand.referenz)
    code = kand.herstellercode.upper()
    k_gruppen = bauteilgruppen(kand.mangel + " " + kand.massnahme)
    k_tokens = distinktive_tokens(kand.mangel + " " + kand.massnahme)
    treffer = []
    for r in recalls_je_baureihe.get(ziel["id"], []):
        if ref and normalisiere_referenz(r.get("kba_referenz")) == ref:
            treffer.append({**r, "_grund": "gleiche KBA-Referenz"})
            continue
        if code and code in ((r.get("abhilfe") or "") + (r.get("mangel") or "")).upper():
            treffer.append({**r, "_grund": "Herstellercode im Text"})
            continue
        r_text = (r.get("mangel") or "") + " " + (r.get("abhilfe") or "")
        # Nur STARKE Gruppen begruenden eine Identitaet. "elektrik_brand"
        # (Kurzschluss, Brandgefahr, Ueberhitzung) und "software" kommen in
        # hunderten unabhaengiger Rueckrufe vor; die Stichprobe hat gezeigt,
        # dass 81 von 120 Dublettenverdachten allein daran hingen — darunter
        # Wasserkastendichtung gegen Kraftstoffleitung und Turbolader-Oelleitung
        # gegen 48-Volt-Bordnetz. Dieselbe Trennung wie in
        # app/kba_reconciliation.py.
        gemeinsam = k_gruppen & bauteilgruppen(r_text) & STARKE_GRUPPEN
        if not gemeinsam:
            continue
        r_jahre = [int(x) for x in re.findall(r"(?:19|20)\d{2}",
                                              r.get("betroffene_baujahre") or "")]
        if r_jahre and not _ueberlappt(min(r_jahre), max(r_jahre),
                                       kand.prod_von, kand.prod_bis):
            continue
        if k_tokens & distinktive_tokens(r_text):
            treffer.append({**r, "_grund": f"Bauteilgruppe {sorted(gemeinsam)} + "
                                           f"Zeitraum + gemeinsame Begriffe"})
    return treffer


def klassifiziere_kandidat(kand: ImportKandidat, ziel_idx: dict,
                           recalls_je_baureihe: dict) -> ImportKandidat:
    """Ordnet EINEN amtlichen Rueckruf genau einer Import-Klasse zu."""
    km = kand.marke.upper()
    tokens = _modelltokens(kand.modell)

    # ── Zielbaureihen bestimmen ─────────────────────────────────────────────
    kandidaten_ziele = []
    ueberdehnt = []          # offene Generation, Rueckruf zu jung
    randlage = []            # nur Randueberlappung, Generation deckt zu wenig ab
    for tok in tokens:
        for b in ziel_idx.get((km, tok), []):
            if not _ueberlappt(b.get("bauzeitraum_von"), b.get("bauzeitraum_bis"),
                               kand.prod_von, kand.prod_bis):
                continue
            offen = b.get("bauzeitraum_bis") is None
            von = b.get("bauzeitraum_von")
            if (offen and von and kand.prod_von
                    and kand.prod_von > von + MEDIAN_GENERATIONSDAUER):
                ueberdehnt.append((tok, b))
                continue
            if _ueberdeckung(kand.prod_von, kand.prod_bis, von,
                             b.get("bauzeitraum_bis")) < MIN_UEBERDECKUNG:
                randlage.append((tok, b))
                continue
            kandidaten_ziele.append((tok, b))

    if not kandidaten_ziele:
        if randlage:
            kand.ziele = [b for _t, b in randlage]
            kand.klasse = AMBIGUOUS_GENERATION
            kand.generation_eindeutig = False
            bester = max(
                _ueberdeckung(kand.prod_von, kand.prod_bis,
                              b.get("bauzeitraum_von"), b.get("bauzeitraum_bis"))
                for _t, b in randlage)
            kand.begruendung = (
                f"nur Randueberlappung: die beste VIRA-Generation deckt lediglich "
                f"{bester:.0%} des amtlichen Produktionsfensters "
                f"{kand.prod_von}-{kand.prod_bis} ab — die gemeinte Generation "
                f"fehlt in VIRA oder liegt in einer Bestandsluecke")
            return kand
        if ueberdehnt:
            # Es GAEBE ein Ziel, aber nur ueber ein offenes Generationsende
            # hinweg. Das ist kein fehlendes Mapping, sondern eine offene
            # Generationsfrage.
            kand.ziele = [b for _t, b in ueberdehnt]
            kand.klasse = AMBIGUOUS_GENERATION
            kand.generation_eindeutig = False
            aelteste = min(b.get("bauzeitraum_von") or 0 for _t, b in ueberdehnt)
            kand.begruendung = (
                f"einziges Ziel ist eine OFFENE Generation ab {aelteste}; das "
                f"amtliche Produktionsfenster beginnt {kand.prod_von}, also mehr "
                f"als {MEDIAN_GENERATIONSDAUER} Jahre spaeter — ein "
                f"Generationswechsel ist wahrscheinlicher als die Fortsetzung")
            return kand
        kand.klasse = UNSUPPORTED_MODEL_MAPPING
        kand.begruendung = (
            f"kein VIRA-Ziel fuer {km} {sorted(tokens)} im Produktionsfenster "
            f"{kand.prod_von}-{kand.prod_bis}")
        return kand

    kand.ziele = [b for _t, b in kandidaten_ziele]

    # ── Generationseindeutigkeit: mehrere Generationen DESSELBEN Modells? ────
    # Ein Rueckruf ueber "X5, X6" trifft zwei MODELLE — das ist eindeutig und
    # ergibt zwei VIRA-Zeilen. Trifft er dagegen zwei GENERATIONEN des X5,
    # laesst sich ohne weitere Angabe nicht sagen, welche gemeint ist.
    je_token = collections.defaultdict(set)
    for tok, b in kandidaten_ziele:
        je_token[tok].add(b["id"])
    mehrdeutig = {tok: ids for tok, ids in je_token.items() if len(ids) > 1}
    kand.generation_eindeutig = not mehrdeutig

    # ── Variantenbeschraenkung ──────────────────────────────────────────────
    eingr = kand.eingrenzung
    if eingr and _VARIANTENWOERTER.search(eingr):
        # Laesst sie sich wenigstens auf Kraftstoffebene abbilden?
        kand.variantenbeschraenkung = _kraftstoff_qualifier(eingr) is None

    # ── Dublettenverdacht ───────────────────────────────────────────────────
    for z in kand.ziele:
        kand.duplikate += _moegliche_dubletten(kand, z, recalls_je_baureihe)

    # ── Klassifikation, strengste Bedingung zuerst ──────────────────────────
    if kand.duplikate:
        kand.klasse = POSSIBLE_DUPLICATE
        gruende = {d["_grund"] for d in kand.duplikate}
        kand.begruendung = (
            f"{len(kand.duplikate)} vorhandene VIRA-Zeile(n) beschreiben "
            f"denselben Vorgang ({'; '.join(sorted(gruende))})")
        return kand

    if mehrdeutig:
        kand.klasse = AMBIGUOUS_GENERATION
        details = "; ".join(f"{tok}: {len(ids)} Generationen"
                            for tok, ids in sorted(mehrdeutig.items()))
        kand.begruendung = (
            f"amtliches Produktionsfenster {kand.prod_von}-{kand.prod_bis} "
            f"ueberdeckt mehrere VIRA-Generationen ({details})")
        return kand

    if kand.variantenbeschraenkung:
        kand.klasse = VARIANT_SCOPE_UNCLEAR
        kand.begruendung = (
            f"amtliche Eingrenzung nennt eine Bedingung, die VIRA nicht "
            f"abbilden kann: {eingr[:110]!r}")
        return kand

    kand.klasse = SAFE_IMPORT
    kand.applicability = "series_only"
    kand.begruendung = (
        f"{len(kand.ziel_ids)} eindeutige Zielbaureihe(n), Zeitraum passt, "
        f"keine Dublette, keine offene Variantenfrage")
    return kand


def import_kandidaten(kba: list[dict], recalls: list[dict],
                      baureihen: list[dict], *,
                      nur_ueberwacht: bool = True,
                      nur_sicherheitsrelevant: bool = True) -> list[ImportKandidat]:
    """Alle amtlichen Rueckrufe, die im Bestand fehlen — klassifiziert.

    Deterministisch: gleiche Eingabe, gleiche Reihenfolge, gleiches Ergebnis.
    Sortiert nach KBA-Referenz, damit der Report stabil bleibt.
    """
    gedeckt = {normalisiere_referenz(r.get("kba_referenz"))
               for r in recalls if (r.get("kba_referenz") or "").strip()}
    gedeckt.discard("")

    ziel_idx = _ziel_index(baureihen)
    vira_marken = {kba_marke(b["marke"]) for b in baureihen}

    je_baureihe = collections.defaultdict(list)
    for r in recalls:
        je_baureihe[r["baureihe_id"]].append(r)

    out = []
    for k in kba:
        kand = ImportKandidat(k)
        if nur_ueberwacht and not kand.ueberwacht:
            continue
        if nur_sicherheitsrelevant and not kand.sicherheitsrelevant:
            continue
        if normalisiere_referenz(kand.referenz) in gedeckt:
            continue
        if kand.marke.upper() not in vira_marken:
            continue
        out.append(klassifiziere_kandidat(kand, ziel_idx, je_baureihe))

    out.sort(key=lambda x: (x.klasse, x.referenz))
    return out


def zeilen_bei_import(kandidaten: list[ImportKandidat],
                      klasse: str = SAFE_IMPORT) -> int:
    """Wie viele VIRA-Zeilen entstuenden beim Import dieser Klasse?

    Nicht die Zahl der Rueckrufe — ein amtlicher Rueckruf ueber mehrere Modelle
    erzeugt je Baureihe eine Zeile.
    """
    return sum(len(k.ziel_ids) for k in kandidaten if k.klasse == klasse)
