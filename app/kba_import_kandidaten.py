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
# Root-Cause-Closing (Audit RC-5): vorher wurde ein amtlicher, KBA-ueberwachter
# Datensatz, dessen Bauteilgruppe/Unfallfolge nicht als sicherheitsrelevant
# erkannt wurde, per BARE `continue` in `import_kandidaten()` verworfen — NIE
# klassifiziert, NIE in `kba_rueckruf_review` sichtbar, NIE in `ausschluesse`.
# Ein Scan des echten, aktuellen KBA-Exports hat 2.237 von 5.805 ueberwachten
# Datensaetzen (~38,5 %, jede Marke) auf diesem Pfad gefunden — zwei belegte
# Audi-Anhaengevorrichtungs-Rueckrufe (KBA 8718/10703) als Reproduktionsfall.
# `NOT_SAFETY_RELEVANT` macht diese Entscheidung zu einer EXPLIZITEN, auditier-
# baren Klasse statt eines stillen Lochs — sie bleibt von der automatischen
# Uebernahme ausgeschlossen (nur `SAFE_IMPORT` wird von `klasse_a()` admittiert),
# erscheint aber wie jede andere nicht-uebernommene Klasse in `kba_rueckruf_
# review`, mit der erkannten Bauteilgruppe als Begruendung — nachvollziehbar
# und spaeter manuell nachschaerfbar, falls die Sicherheits-Vokabeln einmal
# erweitert werden.
NOT_SAFETY_RELEVANT = "NOT_SAFETY_RELEVANT"

IMPORT_KLASSEN = (SAFE_IMPORT, AMBIGUOUS_GENERATION, VARIANT_SCOPE_UNCLEAR,
                  POSSIBLE_DUPLICATE, UNSUPPORTED_MODEL_MAPPING,
                  NOT_SAFETY_RELEVANT)


# Bauteilgruppen, die einen Rueckruf sicherheitsrelevant machen. Bewusst die
# Gruppen, die Bremse, Lenkung, Rueckhaltesystem, Fahrwerk, Rad, Brandgefahr,
# Kraftstoff und Hochvolt betreffen — nicht Komfort oder Abgas.
#
# "anhaenger" (Audit RC-5): eine sich loesende Anhaengevorrichtung ist ein
# Fremdkoerper-auf-der-Fahrbahn-Risiko wie ein sich loesendes Rad oder eine
# abfallende Stossstange — derselbe Gefahrentyp, den "fahrwerk"/"rad" bereits
# abdecken, nur an einem anderen Bauteil. Generisch fuer JEDE Marke (41
# ueberwachte Faelle ueber mehrere Hersteller im aktuellen KBA-Export nutzen
# diese Bauteilgruppe), nicht nur die beiden Audi-Reproduktionsfaelle.
SICHERHEITSGRUPPEN = frozenset({
    "airbag", "gurt", "bremse_hydr", "bremse_mech", "bremse_elektr", "lenkung",
    "fahrwerk", "rad", "hochvolt", "elektrik_brand", "kraftstoff", "anhaenger",
})

# Root-Cause-Closing (Befund M): die Bauteilgruppen allein verloren amtliche
# Sicherheitsrückrufe, deren Bauteil in keiner Gruppe steht, obwohl der Text die
# Sicherheitsfolge selbst nennt:
#   "Verschraubung des Hinterachsträgers kann zu kritischen Fahrsituationen führen"
#   "Nicht richtig verschraubte Sitzmechanik ... kann bei einem Unfall das
#    Verletzungsrisiko erhöhen"
# Beide sind vom KBA überwacht und betreffen u.a. den BMW M4 F82. Maßgeblich ist
# deshalb zusätzlich die im amtlichen Text genannte FOLGE. Kein Rückruf und kein
# Fahrzeug steht hier namentlich: die Muster beschreiben Unfall-, Verletzungs-,
# Brand- und Kontrollfolgen.
# Erweiterung (Audit RC-5): "lösen|ablösen|abfallen" erkennt, wenn sich ein
# Bauteil selbst löst/abfällt — nicht aber die semantisch gleichwertige
# Formulierung, dass eine VERBINDUNG zwischen zwei Teilen verloren geht (z.B.
# "kann zum Verlust der Fahrzeugverbindung führen" bei einer brechenden
# Anhängevorrichtung, oder "kann zum Verlust der Verbindung zum Lenkgetriebe
# führen" bei einer anderen Bauteilgruppe). Beides beschreibt denselben
# Gefahrentyp: ein Teil trennt sich ungewollt vom Fahrzeug oder von einem
# sicherheitsrelevanten Gegenstück. Das generische Muster unten verlangt
# "Verlust" UND einen Verbindungsbegriff im selben Satzteil — das vermeidet
# False Positives wie "Wertverlust" (kein Verbindungswort in der Nähe), ohne
# an einen bestimmten Hersteller- oder Bauteilwortlaut gebunden zu sein.
_SICHERHEITSFOLGE = re.compile(
    r"unfall|verletz|kritische[nrm]? fahrsituation|kontrollverlust"
    r"|kontrolle über das fahrzeug|brand|feuer|stromschlag|lebensgefahr"
    r"|sicherheitsrisiko|fahrstabilit|lösen|ablösen|abfallen"
    r"|verlust\s+(?:der|des|von)\s+\S*(?:verbindung|kupplung|befestigung|halterung)"
    r"|verliert\s+\S*(?:verbindung|kupplung|befestigung|halterung)"
    r"|trennt sich (?:von|vom)",
    re.IGNORECASE)

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
                 "variantenbeschraenkung", "duplikate", "applicability", "paare")

    def __init__(self, kba: dict):
        self.kba = kba
        self.ziele: list[dict] = []
        self.klasse = UNSUPPORTED_MODEL_MAPPING
        self.begruendung = ""
        self.generation_eindeutig = False
        self.variantenbeschraenkung = False
        self.duplikate: list[dict] = []
        self.applicability = "series_only"
        # Root-Cause-Closing (Befund M): die Entscheidung JE PAAR
        # (Rueckruf, Baureihe) als [(baureihe_id, klasse, begruendung)]. `klasse`
        # oben bleibt die strengste Klasse ueber alle Ziele: so wurden die
        # historischen Chargen A/B1 entschieden, und ihre eingefrorenen Daten
        # verweisen darauf.
        self.paare: list[tuple[str, str, str]] = []

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
        return self.sicherheitsrelevant_ueber_bauteil or self.sicherheitsrelevant_ueber_folge

    @property
    def sicherheitsrelevant_ueber_bauteil(self) -> bool:
        return bool(bauteilgruppen(self.mangel) & SICHERHEITSGRUPPEN)

    @property
    def sicherheitsrelevant_ueber_folge(self) -> bool:
        return bool(_SICHERHEITSFOLGE.search(f"{self.mangel} {self.massnahme}"))

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
    # sortiert: `tokens` ist ein `set`, dessen Iterationsreihenfolge je
    # Prozessstart per Hash-Seed variiert. Ein Baureihen-Datensatz kann ueber
    # MEHRERE Token erreichbar sein (z.B. "audi-rs-3-sportback-8v" sowohl ueber
    # "A3" als auch ueber "RS3", wenn der amtliche Datensatz "A3, S3, Q2, RS3"
    # nennt). Die Sortierung macht `kandidaten_ziele` unten reproduzierbar;
    # die Ambiguitaetsentscheidung selbst haengt seit dem Root-Cause-Closing
    # ohnehin nicht mehr an "zuerst gesehen" (siehe unten).
    tokens = sorted(_modelltokens(kand.modell))

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
            kand.paare = [(b["id"], AMBIGUOUS_GENERATION, "nur Randueberlappung")
                          for _t, b in randlage]
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
            kand.paare = [(b["id"], AMBIGUOUS_GENERATION, "offene Generation ueberdehnt")
                          for _t, b in ueberdehnt]
            return kand
        kand.klasse = UNSUPPORTED_MODEL_MAPPING
        kand.begruendung = (
            f"kein VIRA-Ziel fuer {km} {sorted(tokens)} im Produktionsfenster "
            f"{kand.prod_von}-{kand.prod_bis}")
        return kand

    # Dieselbe Baureihe kann ueber MEHRERE Token erreichbar sein (siehe oben);
    # `kand.ziele` fuehrt sie deshalb nur einmal — sonst wuerden Dubletten- und
    # Kostenzaehler unten denselben Treffer doppelt zaehlen.
    ziele_je_id = {b["id"]: b for _t, b in kandidaten_ziele}
    kand.ziele = [ziele_je_id[bid] for bid in sorted(ziele_je_id)]

    # ── Generationseindeutigkeit: mehrere Generationen DESSELBEN Modells? ────
    # Ein Rueckruf ueber "X5, X6" trifft zwei MODELLE — das ist eindeutig und
    # ergibt zwei VIRA-Zeilen. Trifft er dagegen zwei GENERATIONEN des X5,
    # laesst sich ohne weitere Angabe nicht sagen, welche gemeint ist.
    #
    # Root-Cause-Closing (KBA-Paar-Closing): eine Baureihe gilt als eindeutig,
    # wenn WENIGSTENS EINER der Token, ueber die sie erreichbar ist, sie ALLEIN
    # trifft — auch wenn ein ANDERER, breiterer Token (z.B. "A3" fuer die
    # RS3-Baureihe) mehrdeutig waere. Der amtliche Datensatz nennt den
    # spezifischeren Namen ja selbst ("... RS3"); ihn zu ignorieren, nur weil
    # zufaellig der breitere Token zuerst verarbeitet wurde, war der Kern eines
    # nicht-deterministischen Bugs: `tokens` ist ein `set`, und je nachdem,
    # welcher Token zuerst an der Reihe war, entschied golden derselbe Paar mal
    # SAFE_IMPORT, mal AMBIGUOUS_GENERATION — bei GLEICHEN Eingabedaten,
    # zwischen zwei Prozeduraufrufen. Betroffen u.a. RS3/RS6/RS7/M2 (11 amtliche
    # Datensaetze im KBA-Gesamtexport vom 2026-08-27).
    je_token: dict[str, set[str]] = collections.defaultdict(set)
    for tok, b in kandidaten_ziele:
        je_token[tok].add(b["id"])
    toks_je_ziel: dict[str, set[str]] = collections.defaultdict(set)
    for tok, b in kandidaten_ziele:
        toks_je_ziel[b["id"]].add(tok)
    mehrdeutige_ids = {bid for bid, toks in toks_je_ziel.items()
                       if not any(len(je_token[t]) == 1 for t in toks)}
    kand.generation_eindeutig = not mehrdeutige_ids

    # ── Variantenbeschraenkung ──────────────────────────────────────────────
    eingr = kand.eingrenzung
    if eingr and _VARIANTENWOERTER.search(eingr):
        # Laesst sie sich wenigstens auf Kraftstoffebene abbilden?
        kand.variantenbeschraenkung = _kraftstoff_qualifier(eingr) is None

    # ── Dublettenverdacht ───────────────────────────────────────────────────
    dubletten_je_ziel: dict[str, list[dict]] = {}
    for z in kand.ziele:
        dubletten_je_ziel[z["id"]] = _moegliche_dubletten(kand, z, recalls_je_baureihe)
        kand.duplikate += dubletten_je_ziel[z["id"]]

    # ── Entscheidung JE PAAR (Root-Cause-Closing, Befund M) ─────────────────
    # Die Klassifikation unten entscheidet fuer den GANZEN Rueckruf nach der
    # strengsten Bedingung irgendeines Ziels. Damit ging jedes Paar verloren,
    # sobald ein ANDERES Ziel eine Dublette oder eine offene Generationsfrage
    # hatte: KBA 14133R (Takata, 2016-2017) galt als Dublette, weil drei andere
    # BMW-Baureihen bereits eine Airbag-Zeile trugen, und fehlte deshalb auch
    # beim M4 F82, der keine hatte. Die Importidentitaet ist das Paar
    # (Rueckruf, Baureihe); jedes Paar wird fuer sich entschieden.
    for bid in sorted(ziele_je_id):
        if dubletten_je_ziel.get(bid):
            gruende = sorted({d["_grund"] for d in dubletten_je_ziel[bid]})
            kand.paare.append((bid, POSSIBLE_DUPLICATE, "; ".join(gruende)))
        elif bid in mehrdeutige_ids:
            kand.paare.append((bid, AMBIGUOUS_GENERATION,
                               f"{sorted(toks_je_ziel[bid])}: keiner der erreichenden "
                               f"Token trifft nur diese Baureihe"))
        elif kand.variantenbeschraenkung:
            kand.paare.append((bid, VARIANT_SCOPE_UNCLEAR,
                               "amtliche Eingrenzung nicht abbildbar"))
        else:
            kand.paare.append((bid, SAFE_IMPORT, "eindeutig, keine Dublette"))

    # ── Klassifikation, strengste Bedingung zuerst ──────────────────────────
    if kand.duplikate:
        kand.klasse = POSSIBLE_DUPLICATE
        gruende = {d["_grund"] for d in kand.duplikate}
        kand.begruendung = (
            f"{len(kand.duplikate)} vorhandene VIRA-Zeile(n) beschreiben "
            f"denselben Vorgang ({'; '.join(sorted(gruende))})")
        return kand

    if mehrdeutige_ids:
        kand.klasse = AMBIGUOUS_GENERATION
        details = "; ".join(f"{bid}: erreichbar nur ueber mehrdeutige Token "
                            f"{sorted(toks_je_ziel[bid])}"
                            for bid in sorted(mehrdeutige_ids))
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
    # Root-Cause-Closing (Befund M): "schon im Bestand" gilt JE PAAR. Vorher fiel
    # ein amtlicher Rueckruf komplett heraus, sobald seine Referenz bei IRGENDEINER
    # Baureihe stand, auch wenn sie bei den uebrigen betroffenen Baureihen fehlte.
    gedeckte_paare = {(normalisiere_referenz(r.get("kba_referenz")), r.get("baureihe_id"))
                      for r in recalls if (r.get("kba_referenz") or "").strip()}

    ziel_idx = _ziel_index(baureihen)
    vira_marken = {kba_marke(b["marke"]) for b in baureihen}

    je_baureihe = collections.defaultdict(list)
    for r in recalls:
        je_baureihe[r["baureihe_id"]].append(r)

    out = []
    for k in kba:
        kand = ImportKandidat(k)
        # `nur_ueberwacht` (nicht vom KBA ueberwacht) und die Markenpruefung
        # (VIRA fuehrt diesen Hersteller ueberhaupt nicht) schliessen eine
        # Baureihen-Zuordnung grundsaetzlich aus, unabhaengig von diesem
        # Fahrzeugbestand — ein Audit-Trail dafuer waere nur Rauschen (jede
        # Marke, die VIRA nicht fuehrt, jeder nicht-amtlich ueberwachte
        # Datensatz). Beide bleiben bewusst ein stiller `continue`.
        if nur_ueberwacht and not kand.ueberwacht:
            continue
        if kand.marke.upper() not in vira_marken:
            continue
        # Audit RC-5: `nur_sicherheitsrelevant` darf NIE wieder ein stiller
        # `continue` sein. Ein ueberwachter, markenbekannter Datensatz, der
        # hier nicht als sicherheitsrelevant gilt, bekommt eine EXPLIZITE,
        # auditierbare Klasse (`NOT_SAFETY_RELEVANT`) statt zu verschwinden —
        # er wird trotzdem in `out` aufgenommen und landet damit (wie jede
        # andere nicht-SAFE_IMPORT-Klasse) in `kba_rueckruf_review`. Das
        # verhindert automatische Uebernahme (nur `SAFE_IMPORT` wird von
        # `klasse_a()`/`ergaenzende_zeilen()` admittiert) OHNE die
        # Entscheidung unsichtbar zu machen. Die Baureihen-Aufloesung
        # (`klassifiziere_kandidat`) wird fuer diese Klasse bewusst NICHT
        # ausgefuehrt — sie wuerde `kand.klasse` ueberschreiben und koennte
        # einen eigentlich nicht-sicherheitsrelevanten Kandidaten wieder auf
        # `SAFE_IMPORT` setzen, was `klasse_a()` dann automatisch admittieren
        # wuerde. Das waere exakt die in §Auftrag ausgeschlossene Wirkung
        # ("DO NOT simply turn the filter off and auto-import thousands of
        # recalls").
        if nur_sicherheitsrelevant and not kand.sicherheitsrelevant:
            kand.klasse = NOT_SAFETY_RELEVANT
            gefunden = sorted(bauteilgruppen(kand.mangel))
            kand.begruendung = (
                "weder eine sicherheitsrelevante Bauteilgruppe noch eine "
                "erkannte Unfall-/Verletzungs-/Brandfolge im amtlichen Text "
                f"(erkannte Bauteilgruppe(n): {gefunden or 'keine'})")
            out.append(kand)
            continue
        kand = klassifiziere_kandidat(kand, ziel_idx, je_baureihe)
        ref = normalisiere_referenz(kand.referenz)
        # Ganz im Bestand (jede aufgeloeste Baureihe traegt die Referenz schon)
        # oder ohne aufloesbares Ziel und irgendwo gefuehrt: kein Kandidat.
        if ref in gedeckt and (not kand.ziel_ids
                               or all((ref, z) in gedeckte_paare for z in kand.ziel_ids)):
            continue
        out.append(kand)

    out.sort(key=lambda x: (x.klasse, x.referenz))
    return out


def zeilen_bei_import(kandidaten: list[ImportKandidat],
                      klasse: str = SAFE_IMPORT) -> int:
    """Wie viele VIRA-Zeilen entstuenden beim Import dieser Klasse?

    Nicht die Zahl der Rueckrufe — ein amtlicher Rueckruf ueber mehrere Modelle
    erzeugt je Baureihe eine Zeile.
    """
    return sum(len(k.ziel_ids) for k in kandidaten if k.klasse == klasse)


def paare_bei_import(kandidaten: list[ImportKandidat],
                     klasse: str = SAFE_IMPORT) -> list[tuple[str, str]]:
    """Die (Referenz, Baureihe)-Paare mit dieser PAAR-Klasse (Root-Cause-Closing).

    Das ist die Importeinheit für künftige Chargen: ein Paar wird importiert,
    wenn ES sicher ist, unabhängig davon, was bei anderen Zielbaureihen
    desselben Rückrufs gilt.
    """
    return sorted((k.referenz, bid) for k in kandidaten
                  for bid, kl, _g in k.paare if kl == klasse)


def verlorene_paare(kandidaten: list[ImportKandidat]) -> list[dict]:
    """Paare, die für sich SICHER sind, aber durch die alte Entscheidung pro
    Rückruf nicht importierbar waren: die Rückruf-Klasse war strenger, oder der
    Rückruf galt nur über seine Sicherheitsfolge, nicht über ein Bauteil, als
    sicherheitsrelevant (vorher gar kein Kandidat)."""
    out = []
    for k in kandidaten:
        for bid, kl, _g in k.paare:
            if kl != SAFE_IMPORT:
                continue
            if k.klasse != SAFE_IMPORT:
                grund = f"Rueckruf-Klasse {k.klasse}"
            elif not k.sicherheitsrelevant_ueber_bauteil:
                grund = "nur ueber die Sicherheitsfolge sicherheitsrelevant"
            else:
                continue
            out.append({"referenz": k.referenz, "baureihe_id": bid, "grund": grund,
                        "mangel": k.mangel, "prod_von": k.prod_von, "prod_bis": k.prod_bis})
    return sorted(out, key=lambda x: (x["baureihe_id"], x["referenz"]))
