from __future__ import annotations

"""
P1-3 — Deterministische Kaufaktionen ("Was soll ich konkret tun?").

Der Kaufcheck sagt bisher, WELCHE Risiken ein Fahrzeug hat. Dieses Modul ergänzt
die HANDLUNGSEBENE: was bei Besichtigung und Probefahrt zu prüfen ist, was der
Verkäufer gefragt werden muss und welche Dokumente/Nachweise zu verlangen sind.

Grundsätze (identisch zu app/key_findings.py — bewusst dieselbe Bauart):

- KEIN LLM. Kein zusätzlicher Gemini-Call, und der bereits erzeugte Markdown-
  Bericht ist AUSDRÜCKLICH KEINE Datenquelle. Die LLM-Besichtigungs-Checkliste im
  Bericht bleibt davon unberührt und speist hier nichts ein.
- KEINE neue Wahrheit. Es werden ausschließlich bereits deterministisch
  abgeleitete Daten übersetzt: die Insights aus `app/evidence.py::build_insights`
  (Baureihen-Schwachstellen, KBA-Rückrufe, Motorprobleme) sowie die vom Nutzer
  eingegebenen Inserat-Angaben.
- Fahrzeugspezifisch vor generisch. Eine Aktion entsteht NUR, weil für DIESES
  Fahrzeug eine passende Evidence/Angabe existiert — niemals, weil etwas "bei
  Gebrauchtwagen generell sinnvoll" wäre. Ohne Evidence: leere Liste statt
  erfundener Inhalt.
- Vollständig marktpreis-unabhängig (§15). Dieses Modul bekommt weder Marktanalyse
  noch `PriceAssessment` noch `preis_eur` übergeben — eine Preisaktion ist hier
  strukturell nicht konstruierbar. `completed_no_market` liefert deshalb exakt
  dieselben technischen Aktionen wie ein Check mit Marktpreis.

Baujahres-Applicability (§9 / KaufCheck-P0-2): Es gibt hier KEINE eigene
Baujahreslogik. Die Aktionen entstehen aus den bereits nach `_baujahr_passt`
gefilterten Insights; die einzige Stelle, die zusätzlich Rohdaten liest
(Motorproblem-Kosten, siehe `_motorproblem_paare`), verwendet exakt dieselbe
zentrale Funktion aus `app/recall_filter.py` mit derselben Regel: nur ein
eindeutiges `False` schließt aus.

Rückrufe (§10): Es wird NIEMALS behauptet, dass ein konkretes Fahrzeug betroffen
ist — ohne FIN-Prüfung ist das nicht belegbar. Rückrufe erzeugen ausschließlich
FIN-/Nachweis-Aktionen, deren Formulierung der vorhandenen `applicability`-Stufe
folgt. Die Recall-Pipeline selbst wird nicht angefasst.

ZWEI EBENEN (Ausbau zum Kaufbegleiter)

Jeder der vier Bereiche liefert zwei getrennte Listen:

  fahrzeugspezifisch — alles oben Beschriebene: entsteht ausschließlich aus echter
                       Evidence zu DIESEM Fahrzeug. Bleibt kurz oder leer, wenn die
                       Datenlage dünn ist. Hier wird nichts erfunden.
  basis              — der allgemeine professionelle Prüfstandard aus
                       app/pruefplan_basis.py. Behauptet NICHTS über dieses Fahrzeug
                       ("sieh an den typischen Stellen nach Rost", nicht "hier ist
                       Rost") und trägt deshalb korrekterweise keine evidence_ids.

Beide werden NIE zusammengeworfen (§12): die Trennung ist die eigentliche Aussage,
und das Frontend soll "Bei diesem Fahrzeug besonders wichtig" vor "Allgemeine
Checkliste" stellen können. Der Mengenzuwachs kommt deshalb ausdrücklich NICHT aus
einer angehobenen Obergrenze für fahrzeugspezifische Punkte — die bleibt bei
`MAX_SPEZIFISCH_PRO_BEREICH` —, sondern aus dem separaten Basis-Katalog.

Deduplizierung über die Ebenen hinweg (§18): deckt ein fahrzeugspezifischer Punkt
denselben Prüfschritt ab wie ein Basis-Punkt, gewinnt der konkretere und der
Basis-Punkt entfällt für diesen Check. Das steuert das `deckt`-Feld des Katalogs —
bewusst sparsam gesetzt, damit keine inhaltlich VERSCHIEDENE Prüfung verschwindet.

Print-/PDF-Bereitschaft: Jeder Bereich wird als eigenständige `Pruefliste` mit
Bereich, Titelzeile und Fahrzeugbezeichnung ausgegeben — vier unabhängige
Arbeitsblätter. Es gibt bewusst KEIN Sammel-Exportobjekt und keine kombinierte
Liste; hier wird auch noch keine PDF erzeugt, nur die Struktur dafür bereitgestellt.

Evidence-Integrity (erledigt): `kritische_wartung` besaß in der ersten P1-3-Fassung
keine referenzierbare Evidence-ID. `app/evidence.py::build_insights` gibt diese
Wartungspunkte jetzt als eigene Insight-Kategorie "wartung" aus — angehängt NACH
dem Marktvergleich, sodass keine einzige bestehende Insight-Nummer verschoben
wurde. Wartungsaktionen sind damit vollwertig evidenzgebunden.
"""

import logging
import re

from app.evidence import titel_bauteil
from app.getriebe import (
    AUTOMATIK, MANUELL, AUTOMATIK_WORTE as _AUTOMATIK_WORTE,
    MANUELL_WORTE as _MANUELL_WORTE, aus_request as _getriebe_aus_request,
)
from app.servicehistorie import (
    NICHT_VORHANDEN as SH_NICHT_VORHANDEN, TEILWEISE as SH_TEILWEISE,
    UMFANG_UNKLAR as SH_UMFANG_UNKLAR, VOLLSTAENDIG_ANGEGEBEN as SH_VOLLSTAENDIG,
    status as servicehistorie_status,
)
from app.verkaeuferart import (
    HAENDLER as VK_HAENDLER, PRIVAT as VK_PRIVAT, aus_request as verkaeuferart_aus_request,
)

from app.models import Insight, Kaufaktion, Kaufaktionen, Pruefliste
from app.pruefplan_basis import (
    BASIS_BESICHTIGUNG, BASIS_PROBEFAHRT, BASIS_VERKAEUFERFRAGEN, BASIS_DOKUMENTE,
)
from app.recall_filter import _baujahr_passt

log = logging.getLogger(__name__)

BESICHTIGUNG = "besichtigung"
PROBEFAHRT = "probefahrt"
VERKAEUFERFRAGEN = "verkaeuferfragen"
DOKUMENTE = "dokumente"

# Obergrenze NUR für die fahrzeugspezifische Ebene — und bewusst NICHT angehoben.
# Der Umfang des Prüfplans wächst über den separaten Basis-Katalog, nicht dadurch,
# dass mehr aus derselben dünnen Evidence herausgepresst wird. Es gibt weiterhin
# KEINE Mindestzahl: existiert nur ein belastbarer Punkt, bleibt es bei einem;
# existiert keiner, bleibt die Liste leer.
MAX_SPEZIFISCH_PRO_BEREICH = 6

# Rückwärtskompatibler Alias (die erste P1-3-Fassung kannte nur diesen Namen).
MAX_PRO_BEREICH = MAX_SPEZIFISCH_PRO_BEREICH

TYP_SPEZIFISCH = "fahrzeugspezifisch"
TYP_BASIS = "basis"

PRIO_KRITISCH = "kritisch"
PRIO_HOCH = "hoch"
PRIO_MITTEL = "mittel"
# Eigene Stufe statt "mittel": eine allgemeine Basisprüfung ist kein Befund zu
# diesem Fahrzeug und wird deshalb nie in dieselbe Dringlichkeitsskala einsortiert
# (§17). Das UI kann KRITISCH / HOCH / MITTEL / BASIS getrennt darstellen.
PRIO_BASIS = "basis"

# Titelzeile der vier Arbeitsblätter (Print/PDF, §13/§14).
EXPORT_TITEL = {
    "besichtigung":     "Besichtigungs-Checkliste",
    "probefahrt":       "Probefahrt-Checkliste",
    "verkaeuferfragen": "Fragen an den Verkäufer",
    "dokumente":        "Dokumenten-Checkliste",
}

# ── Priorisierung (§11): deterministische Ränge, höher = wichtiger ────────────
# In Bänder gruppiert wie in app/key_findings.py, damit die Reihenfolge stabil und
# nachvollziehbar bleibt. KEINE LLM-Priorisierung, keine Preis-Komponente.
_R_RUECKRUF_VARIANTE = 900   # Rückruf, dessen Variante/Baujahr passt (per FIN prüfen)
_R_SCHWACH_HOCH      = 850   # Schwachstelle mit schweregrad hoch/kritisch
_R_RUECKRUF_SERIE    = 800   # Rückruf für Teile der Baureihe / unklare Betroffenheit
_R_MOTORPROBLEM      = 700   # motorspezifisches Problem (Motor eindeutig erkannt)
_R_SCHWACH_MITTEL    = 600   # Schwachstelle mit schweregrad mittel
_R_DOKUMENT_KERN     = 560   # Unfall-/HU-Nachweis: harte Kaufentscheidungsgrundlage
_R_WARTUNG           = 520   # kritische Wartung laut DB (ohne Insight-ID)
# P2-5: derselbe Wartungspunkt, aber die Laufleistung dieses Fahrzeugs hat ihn
# bereits erreicht oder überschritten.
#
# Der Wert ist gemessen entstanden, nicht geschätzt: mit 545 fiel die Aktion im
# Sanity-Lauf (Audi A3 8P, 160.000 km, Zahnriemen bei 120.000 km) durch
# MAX_SPEZIFISCH_PRO_BEREICH hinter drei Rückruf- und zwei Motorproblem-Aktionen
# heraus und erreichte den Nutzer nie — ausgerechnet bei den Fahrzeugen mit der
# besten Wartungsdatenlage.
#
# 750 ordnet ihn dort ein, wo er fachlich hingehört: ÜBER einem Motorproblem
# (700), das eine Beobachtungsempfehlung ist, aber UNTER jedem Rückruf (800/900)
# und unter jeder schweren Schwachstelle (850). Ein erreichter Wartungspunkt ist
# ein konkret einlösbarer Nachweis-Wunsch — kein festgestellter Mangel und erst
# recht kein Sicherheitsbefund. Deshalb erreicht er auch nie "kritisch".
_R_WARTUNG_RELEVANT  = 750
_R_SCHWACH_GERING    = 400   # Schwachstelle mit schweregrad gering
_R_DOKUMENT_STANDARD = 340   # Scheckheft/Vorbesitzer: wichtig, aber selten K.-o.
_R_ANGABE_FEHLT      = 300   # gezielte Nachfrage zu einer fehlenden Inseratangabe
# Technischer Web-Fallback: belegte Web-Fakten stehen bewusst UNTER den geprüften
# DB-Fakten derselben Art, aber deutlich ÜBER dem allgemeinen Basis-Standard. Sie
# sind quellengebunden und fahrzeugspezifisch, aber nicht redaktionell geprüft.
# Ein Web-Rückruf liegt trotzdem oben: die FIN-Prüfung ist unabhängig von der
# Beleglage sinnvoll und kostet den Nutzer nichts.
_R_WEB_RUECKRUF      = 780
_R_WEB_SCHWACH       = 560
_R_WEB_WARTUNG       = 500
# Basis-Punkte liegen als Band UNTERHALB jeder fahrzeugspezifischen Aktion und
# behalten innerhalb ihres Katalogs die dort definierte fachliche Reihenfolge
# (rang = _R_BASIS - Position). Würde man beide Ebenen je zusammenführen, stünde
# der allgemeine Standard damit automatisch hinter dem Fahrzeugspezifischen.
_R_BASIS             = 200

# Zuschläge (nie negativ, damit die Bänder ihre Reihenfolge behalten).
_BONUS_SICHERHEIT = 40       # Bauteil mit unmittelbarer Sicherheitsrelevanz
_BONUS_KOSTEN     = 20       # Reparaturkosten aus der DB bekannt

_SCHWELLE_KRITISCH = 850
_SCHWELLE_HOCH = 560


def _prioritaet(rang: int) -> str:
    if rang <= _R_BASIS:
        return PRIO_BASIS
    if rang >= _SCHWELLE_KRITISCH:
        return PRIO_KRITISCH
    if rang >= _SCHWELLE_HOCH:
        return PRIO_HOCH
    return PRIO_MITTEL


# ── Normalisierung / stabile IDs (§14) ───────────────────────────────────────

_UMLAUTE = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss",
                          "Ä": "ae", "Ö": "oe", "Ü": "ue"})


def _norm(text: str | None) -> str:
    """Kleinschreibung, Umlaute aufgelöst, alles Nicht-Alphanumerische zu Leerzeichen.

    Bewusst KEIN Stemming: die Bauteil-Bezeichnungen der DB sind ein weitgehend
    kontrolliertes Vokabular ('Turbolader', 'AGR-Ventil', 'Zylinderkopfdichtung'),
    Substring-Matching auf normalisiertem Text reicht dafür aus.
    """
    t = (text or "").strip().lower().translate(_UMLAUTE)
    t = re.sub(r"[^a-z0-9]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _slug(text: str | None, maxlen: int = 40) -> str:
    """Stabiler, inhaltsbasierter ID-Bestandteil — KEINE UUID, kein Zufall.

    Gleiche Evidence + gleiche Aktionsart ergeben bei wiederholter Ausführung exakt
    dieselbe ID. Die ID hängt bewusst NICHT an der laufenden Insight-Nummer
    ('schwachstelle-3'), sondern am Inhalt (Bauteil/KBA-Referenz) — dadurch bleibt
    sie auch stabil, wenn sich die Reihenfolge der Insights einmal verschiebt.
    """
    s = _norm(text).replace(" ", "-")
    return (s[:maxlen].rstrip("-")) or "sonstiges"


# ── Bauteil-Wissenstabelle ───────────────────────────────────────────────────
#
# EINE zentrale Tabelle statt verstreuter if-Ketten. Sie beantwortet pro Bauteil
# drei feste Fragen:
#   1. Was kann man VOR ORT (Stand, Motorraum, Innenraum) daran prüfen?
#   2. Gibt es ein Symptom, das WÄHREND DER FAHRT fachlich zuverlässig beobachtbar
#      ist?  -> `probefahrt=None` heißt ausdrücklich NEIN (§6).
#   3. Ist das Bauteil unmittelbar sicherheitsrelevant (Priorisierung)?
#
# Die Tabelle erzeugt KEINE Aktion von sich aus — sie beschreibt nur, WIE eine
# vorhandene Evidence zu prüfen wäre. Ohne Evidence zu diesem Bauteil passiert
# nichts.
#
# `schluessel` ist zugleich der Dedup-Schlüssel (§13): eine Baureihen-Schwachstelle
# "AGR-Ventil" und ein Motorproblem "AGR-Kühler" landen auf demselben Schlüssel und
# ergeben EINE Aktion mit BEIDEN Evidence-IDs statt zwei fast identischer Punkte.
#
# Reihenfolge = Auswertungsreihenfolge: spezifischere Einträge stehen VOR
# allgemeineren ('hochvolt' vor 'batterie', 'agr' vor 'kuehlung', 'automatik' vor
# 'getriebe', alles vor dem generischen 'motor').
_KOMPONENTEN: tuple[dict, ...] = (
    # ── Sicherheit / Fahrwerk ────────────────────────────────────────────────
    dict(schluessel="bremsen", muster=("brems",), sicherheit=True,
         besichtigung="Bremsscheiben auf Riefen, Rostkanten und Mindeststärke prüfen, "
                      "Belagstärke an allen vier Rädern ansehen.",
         probefahrt="Bremsverhalten prüfen: Bremsweg, Rubbeln in Lenkrad oder Pedal, "
                    "Quietschen und einseitiges Ziehen bei kräftiger Bremsung."),
    dict(schluessel="lenkung", muster=("lenkung", "servolenk", "lenkgetriebe"), sicherheit=True,
         besichtigung="Lenkspiel im Stand prüfen und beim Einschlagen auf Knacken oder "
                      "Schleifen achten.",
         probefahrt="Lenkverhalten prüfen: Geradeauslauf, Rückstellung nach der Kurve und "
                    "Geräusche beim Rangieren mit vollem Einschlag."),
    dict(schluessel="fahrwerk",
         muster=("fahrwerk", "vorderachse", "hinterachse", "querlenker", "stossdaempfer",
                 "domlager", "federbein", "radaufhaengung", "achse", "koppelstange"),
         sicherheit=True,
         besichtigung="Fahrwerk sichtprüfen: Federn, Dämpfer und Achsmanschetten auf Bruch, "
                      "Ölaustritt und Risse; Fahrzeug an jeder Ecke einfedern lassen.",
         probefahrt="Auf Poltern, Knarzen oder Klappern von der Achse achten: besonders auf "
                    "Kopfsteinpflaster und in Bodenwellen."),
    dict(schluessel="luftfederung", muster=("luftfeder", "niveaureg"), sicherheit=False,
         besichtigung="Fahrzeugniveau nach längerem Stand prüfen: ein einseitig abgesenktes "
                      "Fahrzeug deutet auf eine Undichtigkeit hin.",
         probefahrt="Auf häufigen Kompressorlauf und ein ungleichmäßiges Niveau während der "
                    "Fahrt achten."),
    dict(schluessel="raeder", muster=("reifen", "felge", "radlager", "radschraube", "spur"),
         sicherheit=True,
         besichtigung="Profiltiefe, DOT-Alter und gleichmäßigen Abrieb aller vier Reifen prüfen; "
                      "einseitiger Abrieb deutet auf Fahrwerk oder Spur hin.",
         probefahrt="Auf Brummen oder Heulen achten, das sich mit der Geschwindigkeit ändert "
                    "(typisch für Radlager)."),
    dict(schluessel="airbag", muster=("airbag", "gurt", "rueckhalte"), sicherheit=True,
         besichtigung="Airbag-Kontrollleuchte beim Einschalten der Zündung beobachten: sie muss "
                      "aufleuchten und wieder erlöschen.",
         probefahrt=None),
    dict(schluessel="rost", muster=("rost", "korrosion", "durchrostung"), sicherheit=False,
         besichtigung="Radläufe, Schweller, Türunterkanten, Kofferraumboden und Unterboden auf "
                      "Rost prüfen: auch unter Bodenmatte und Reserveradmulde.",
         probefahrt=None),

    # ── Antrieb / Getriebe ───────────────────────────────────────────────────
    dict(schluessel="automatikgetriebe",
         muster=("automatikgetriebe", "getriebe automatik", "automatik", "dsg", "dkg",
                 "s tronic", "multitronic", "wandler", "cvt", "powershift"),
         sicherheit=False,
         besichtigung="Getriebe und Getriebeglocke auf Ölaustritt prüfen, soweit von außen "
                      "einsehbar.",
         probefahrt="Schaltverhalten prüfen: Schaltschläge, Ruckeln, verzögerte Gangwechsel und "
                    "Verhalten beim Anfahren aus dem Stand sowie beim Rückwärtseinlegen."),
    dict(schluessel="getriebe", muster=("getriebe", "schaltung"), sicherheit=False,
         besichtigung="Getriebe auf Ölaustritt prüfen und die Schaltung im Stand durchschalten.",
         probefahrt="Alle Gänge inklusive Rückwärtsgang durchschalten und auf Hakeln, Kratzen "
                    "und Herausspringen unter Last achten."),
    dict(schluessel="kupplung", muster=("kupplung",), sicherheit=False,
         besichtigung=None,
         probefahrt="Kupplung prüfen: Greifpunkt, Rupfen beim Anfahren und Durchrutschen unter "
                    "Last (im hohen Gang kräftig beschleunigen)."),
    dict(schluessel="zweimassenschwungrad", muster=("zweimassenschwungrad", "zms", "schwungrad"),
         sicherheit=False, besichtigung=None,
         probefahrt="Auf Rasseln im Leerlauf und beim Auskuppeln sowie auf Vibrationen beim "
                    "Anlassen und Abstellen des Motors achten."),
    dict(schluessel="allradantrieb",
         muster=("haldex", "allrad", "differential", "kardan", "antriebswelle", "verteilergetriebe"),
         sicherheit=False, besichtigung=None,
         probefahrt="Beim Rangieren mit vollem Lenkeinschlag auf Knacken und Rupfen im "
                    "Antriebsstrang achten."),

    # ── Motor / Abgas ────────────────────────────────────────────────────────
    dict(schluessel="turbolader", muster=("turbolader", "turbo", "lader", "ladedruck"),
         sicherheit=False,
         besichtigung="Ladeluftschläuche und den Bereich um den Turbolader auf Ölnebel und "
                      "Ölspuren prüfen.",
         probefahrt="Auf Leistungsverlust, einsetzenden Notlauf, Pfeifen oder Heulen unter Last "
                    "und blauen Rauch beim Beschleunigen achten."),
    dict(schluessel="partikelfilter", muster=("partikelfilter", "dpf", "russfilter", "ottopartikel"),
         sicherheit=False,
         besichtigung="Auspuffendrohr auf starke Rußablagerungen prüfen und den Fehlerspeicher "
                      "bzw. Warnleuchten beachten.",
         probefahrt="Längere Strecke mit Landstraßen- oder Autobahnanteil fahren und auf "
                    "Leistungsverlust, Notlauf oder eine einsetzende Regeneration achten."),
    # AGR bewusst OHNE Probefahrt-Symptom: ein defektes AGR äußert sich in der Praxis
    # überwiegend als Fehlerspeichereintrag/Motorkontrollleuchte, nicht zuverlässig als
    # fahrbares Symptom (§6). Nennt die DB-Beschreibung ausdrücklich ein Fahrsymptom,
    # greift stattdessen das Text-Tor `_FAHRSYMPTOME`.
    dict(schluessel="agr", muster=("agr", "abgasrueckfuehr"), sicherheit=False,
         besichtigung="Motorkontrollleuchte prüfen und den Motorraum im Bereich der "
                      "Abgasrückführung auf Rußspuren kontrollieren.",
         probefahrt=None),
    dict(schluessel="adblue", muster=("adblue", "scr", "nox", "abgasrein", "abgasnachbehandlung"),
         sicherheit=False,
         besichtigung="AdBlue-Füllstand und Warnmeldungen im Bordcomputer prüfen; Fehlerspeicher "
                      "auslesen lassen.",
         probefahrt=None),
    dict(schluessel="einspritzung",
         muster=("injektor", "einspritzdues", "einspritzpumpe", "hochdruckpumpe", "common rail",
                 "einspritzanlage", "tandempumpe"),
         sicherheit=False,
         besichtigung="Motor kalt starten lassen und auf unrunden Lauf sowie auf Rußspuren an den "
                      "Injektorsitzen achten.",
         probefahrt="Auf unrunden Motorlauf, Ruckeln bei konstanter Fahrt und Leistungsverlust "
                    "unter Last achten."),
    # Steuerkette bewusst OHNE Probefahrt: das aussagekräftige Symptom (Rasseln) tritt
    # in den ersten Sekunden nach dem KALTSTART auf — das ist eine Besichtigungs-, keine
    # Fahrbeobachtung.
    dict(schluessel="steuerkette", muster=("steuerkette", "kettenspanner", "steuertrieb"),
         sicherheit=False,
         besichtigung="Motor KALT starten lassen (vorher nicht warmlaufen lassen) und in den "
                      "ersten Sekunden auf Rasseln aus dem Steuerkettenbereich achten.",
         probefahrt=None),
    dict(schluessel="zahnriemen", muster=("zahnriemen", "riementrieb", "keilrippenriemen"),
         sicherheit=False,
         besichtigung="Nachweis über den letzten Zahnriemenwechsel prüfen und den Riemen, soweit "
                      "einsehbar, auf Risse und Verglasung kontrollieren.",
         probefahrt=None),
    dict(schluessel="zuendung", muster=("zuendspul", "zuendkerz", "zuendmodul", "zuendanlage"),
         sicherheit=False,
         besichtigung="Motorkontrollleuchte prüfen und den Motor im Leerlauf auf unrunden Lauf "
                      "abhören.",
         probefahrt="Unter Volllast beschleunigen und auf Zündaussetzer, Ruckeln und "
                    "Leistungseinbrüche achten."),
    dict(schluessel="oelverlust",
         muster=("oelverbrauch", "oelverlust", "kolbenring", "ventilschaftdicht",
                 "kurbelgehaeuseentlueftung", "kge", "oelpumpe", "oelwanne", "oellec"),
         sicherheit=False,
         besichtigung="Motorölstand nach Herstellervorgabe prüfen (Peilstab oder "
                      "elektronische Anzeige im Fahrzeugmenü), Motor und Stellplatz auf "
                      "Ölspuren kontrollieren und den Öleinfülldeckel auf Emulsion ansehen.",
         probefahrt=None),
    dict(schluessel="zylinderkopf", muster=("zylinderkopfdichtung", "zylinderkopf", "kopfdichtung"),
         sicherheit=False,
         besichtigung="Kühlmittel auf Ölspuren und den Öldeckel auf mayonnaiseartige Emulsion "
                      "prüfen; nach dem Kaltstart auf weißen Rauch achten.",
         probefahrt=None),
    dict(schluessel="kuehlung",
         muster=("wasserpumpe", "kuehlmittel", "kuehlsystem", "thermostat", "kuehler",
                 "ladeluftkuehler", "kuehlung"),
         sicherheit=False,
         besichtigung="Kühlmittelstand und den Kühlerbereich auf Leckagen, Trockenspuren und "
                      "Dichtmittelreste prüfen.",
         probefahrt="Kühlmitteltemperatur während der Fahrt beobachten: sie sollte nach dem "
                    "Warmlaufen konstant bleiben."),
    dict(schluessel="sensorik",
         muster=("sensor", "luftmassenmesser", "lmm", "drosselklappe", "drallklappen",
                 "steuergeraet", "motorsteuer"),
         sicherheit=False,
         besichtigung="Fehlerspeicher auslesen lassen und auf eine aktive Motorkontrollleuchte "
                      "achten: auch auf sporadisch gespeicherte Einträge.",
         probefahrt=None),
    dict(schluessel="abgasanlage", muster=("auspuff", "abgasanlage", "katalysator", "kruemmer"),
         sicherheit=False,
         besichtigung="Abgasanlage von unten auf Durchrostung, Flickstellen und lose Aufhängungen "
                      "prüfen.",
         probefahrt="Auf dröhnende oder blecherne Abgasgeräusche unter Last achten."),

    # ── Elektrik / Komfort ───────────────────────────────────────────────────
    dict(schluessel="hochvoltbatterie",
         muster=("hochvolt", "traktionsbatterie", "antriebsbatterie", "hv batterie"),
         sicherheit=False,
         besichtigung="Angezeigte Reichweite und (falls im Bordmenü verfügbar) den "
                      "Batteriegesundheitswert (SoH) prüfen.",
         probefahrt=None),
    dict(schluessel="starterbatterie",
         muster=("12v", "starterbatterie", "batterie", "lichtmaschine", "generator",
                 "anlasser", "startproblem", "startverhalten"),
         sicherheit=False,
         besichtigung="Fahrzeug KALT starten lassen und das Startverhalten beobachten; Alter und "
                      "Ladezustand der Batterie erfragen.",
         probefahrt=None),
    dict(schluessel="infotainment",
         muster=("infotainment", "idrive", "mmi", "navi", "display", "bordcomputer",
                 "software", "elektronik", "elektrik", "bussystem"),
         sicherheit=False,
         besichtigung="Alle elektrischen Funktionen im Stand durchtesten: Display/Infotainment, "
                      "Bedienelemente, Fensterheber, Beleuchtung: auf Neustarts und Aussetzer achten.",
         probefahrt=None),
    dict(schluessel="klimaanlage", muster=("klima",), sicherheit=False,
         besichtigung="Klimaanlage einschalten und prüfen, ob sie spürbar und dauerhaft kühlt; "
                      "auf Geruch und Kompressorgeräusch achten.",
         probefahrt=None),
    dict(schluessel="dach_fenster",
         muster=("fensterheber", "panoramadach", "schiebedach", "dach", "wasserablauf",
                 "undicht", "wassereinbruch", "feuchtigkeit"),
         sicherheit=False,
         besichtigung="Fenster und Dach mehrfach öffnen und schließen; Dichtungen, Wasserabläufe, "
                      "Fußräume und Innenhimmel auf Feuchtigkeit und Wasserränder prüfen.",
         probefahrt=None),
    dict(schluessel="beleuchtung",
         muster=("beleuchtung", "scheinwerfer", "xenon", "ruckfahrkamera", "kamera", "assistenz"),
         sicherheit=False,
         besichtigung="Alle Leuchten sowie Kamera- und Assistenzanzeigen im Stand einzeln "
                      "durchschalten.",
         probefahrt=None),
    dict(schluessel="innenraum",
         muster=("innenraum", "sitz", "polster", "verkleidung", "armaturenbrett", "lenkrad"),
         sicherheit=False,
         besichtigung="Sitze, Verkleidungen und Bedienelemente auf Verschleiß, Risse und "
                      "Feuchtigkeit prüfen. Abnutzung muss zur angegebenen Laufleistung passen.",
         probefahrt=None),
    dict(schluessel="karosserie", muster=("lack", "karosserie", "tuer", "haube", "spaltmass"),
         sicherheit=False,
         besichtigung="Spaltmaße, Lackstruktur und Farbtonunterschiede rundum prüfen. "
                      "Abweichungen deuten auf eine Reparatur hin.",
         probefahrt=None),
    # Generischer Motor-Eintrag ganz am Ende: greift nur, wenn kein spezifischerer
    # Eintrag passt (z.B. Bauteil schlicht "Motor").
    dict(schluessel="motor", muster=("motor", "aggregat"), sicherheit=False,
         besichtigung="Motorraum auf Ölspuren, Leckagen und auffällig frische Reinigungsspuren "
                      "prüfen; Kaltstartverhalten und Leerlauf beobachten.",
         probefahrt="Auf ungewöhnliche Motorgeräusche, Leistungsverlust und Rauchentwicklung "
                    "unter Last achten."),
)


def _komponente(bauteil: str | None) -> dict | None:
    """Erster passender Tabelleneintrag — oder None (dann greift der Fallback)."""
    n = _norm(bauteil)
    if not n:
        return None
    for eintrag in _KOMPONENTEN:
        if any(m in n for m in eintrag["muster"]):
            return eintrag
    return None


# ── Zweites Probefahrt-Tor: explizites Fahrsymptom im Evidence-TEXT (§6) ──────
#
# Die Bauteil-Tabelle deckt den fachlichen Regelfall ab. Nennt die DB-Beschreibung
# darüber hinaus AUSDRÜCKLICH ein Fahrsymptom, ist auch das eine belastbare
# Grundlage — dann stammt die Beobachtbarkeit direkt aus der Evidence selbst.
# Beides sind bewusst die EINZIGEN zwei Tore: existiert weder ein Eintrag mit
# Probefahrt-Symptom noch ein Symptomwort im Text, entsteht KEINE Probefahrt-Aktion
# ("Bauteil X kann ausfallen" allein reicht nicht).
_FAHRSYMPTOME: tuple[tuple[tuple[str, ...], str], ...] = (
    (("ruckel", "ruckelt", "aussetzer", "zundaussetzer"),
     "Auf Ruckeln und Aussetzer achten: bei konstanter Fahrt ebenso wie beim Beschleunigen."),
    (("schaltverhalten", "schaltruck", "schaltschlag", "gangwechsel", "schaltet"),
     "Schaltverhalten prüfen: Schaltschläge, Ruckeln und verzögerte Gangwechsel."),
    (("poltern", "knarz", "klapper"),
     "Auf Poltern, Knarzen oder Klappern achten: besonders auf schlechter Fahrbahn."),
    (("rassel", "klacker"),
     "Auf Rasseln oder Klackern aus dem Antriebsbereich achten."),
    (("leistungsverlust", "notlauf", "leistungseinbruch"),
     "Auf Leistungsverlust oder einen einsetzenden Notlauf unter Last achten."),
    (("vibration", "unwucht"),
     "Auf Vibrationen in Lenkrad oder Aufbau bei höherer Geschwindigkeit achten."),
    (("quietsch", "pfeif", "heul", "schleif"),
     "Auf Quietschen, Pfeifen, Heulen oder Schleifen achten, das sich mit Last oder "
     "Geschwindigkeit ändert."),
    (("geraeusch",),
     "Auf ungewöhnliche Geräusche während der Fahrt achten und die Fahrsituation notieren, "
     "in der sie auftreten."),
)


def _fahrsymptom_aus_text(*texte: str | None) -> str | None:
    """Erstes ausdrücklich genanntes Fahrsymptom in der Evidence-Beschreibung."""
    n = " ".join(_norm(t) for t in texte if t)
    if not n:
        return None
    for worte, satz in _FAHRSYMPTOME:
        if any(w in n for w in worte):
            return satz
    return None


# ── Hilfsfunktionen auf den Insights ─────────────────────────────────────────

_HOHE_SCHWERE = ("hoch", "kritisch", "sehr hoch")
_KOSTEN_ZAHL = re.compile(r"\d")


def _bauteil_aus_schwachstelle(i: Insight) -> str:
    """Bauteil einer Baureihen-Schwachstelle.

    `build_insights` legt das Bauteil als `quellen[0].ref` ab; der Titel ist
    `"<Bauteil> — bekannte Schwachstelle"`. Primär wird `ref` gelesen (belastbar),
    der Titel dient nur als Rückfallebene.
    """
    for q in i.quellen:
        if q.typ == "datenbank" and q.ref:
            return q.ref.strip()
    return titel_bauteil(i.titel) or "Schwachstelle"


def _bauteil_aus_motorproblem(i: Insight) -> str:
    """Bauteil eines Motorproblems aus dem Insight-Titel.

    `build_insights` bildet `"<Bauteil> (<Motorbezeichnung>)"` — die Motorbezeichnung
    steht immer als LETZTE Klammergruppe. Bauteile mit eigener Klammer
    ("Dieselpartikelfilter (DPF)") bleiben dadurch erhalten. Nur Rückfallebene:
    bevorzugt wird das Rohdaten-Pairing in `_motorproblem_paare`.
    """
    return re.sub(r"\s*\([^()]*\)\s*$", "", i.titel).strip() or "Motorproblem"


def _motorproblem_paare(insights: list[Insight], motor_match: dict | None,
                        baujahr: int | None) -> list[tuple[Insight, dict]]:
    """Ordnet jedem Motorproblem-Insight seinen DB-Rohsatz zu (für `bauteil`/`kosten_ca`).

    `build_insights` iteriert `motor_match["schwachstellen_motor"]` in DB-Reihenfolge
    und überspringt genau die Sätze, für die `_baujahr_passt(...) is False` gilt.
    Hier wird DIESELBE zentrale Funktion mit derselben Regel verwendet (§9: keine
    zweite, abweichende Baujahreslogik) und die gefilterte Liste positionsweise mit
    den Insights gepaart.

    Sicherung gegen stille Fehlzuordnung: jedes Paar wird über die `beschreibung`
    verifiziert. Passt sie nicht, wird der Rohsatz VERWORFEN (kein `kosten_ca`,
    Bauteil kommt dann aus dem Titel) statt einer falschen Zuordnung.
    """
    mp = [i for i in insights if i.kategorie == "motorproblem"]
    if not mp or not motor_match:
        return []
    roh = [s for s in (motor_match.get("schwachstellen_motor") or [])
           if _baujahr_passt(s.get("baujahre"), baujahr) is not False]
    paare: list[tuple[Insight, dict]] = []
    for n, insight in enumerate(mp):
        satz = roh[n] if n < len(roh) else None
        if satz is not None and (satz.get("beschreibung") or "").strip() == insight.beschreibung:
            paare.append((insight, satz))
        else:
            if satz is not None:
                log.info("Kaufaktionen: Motorproblem-Rohsatz passt nicht zum Insight %s "
                         "— Rohdaten verworfen", insight.id)
            paare.append((insight, {}))
    return paare


def _kostenhinweis(kosten_ca: str | None) -> str | None:
    """`kosten_ca` nur übernehmen, wenn wirklich ein Betrag drinsteht.

    Die Spalte ist Freitext und enthält u.a. '—' oder 'Herstellergarantie/Rückruf'.
    Ohne Ziffer wird nichts ausgegeben — lieber kein Kostenhinweis als ein leerer.
    """
    t = (kosten_ca or "").strip()
    return t if t and _KOSTEN_ZAHL.search(t) else None


def _mangel_kurz(insight) -> str:
    """Kurzer Rückruftitel — bevorzugt das strukturierte Feld `kurztitel`.

    KaufCheck RC1: vorher wurde der Insight-Titel am ersten ":" zerlegt und nach
    60 Zeichen mit "…" abgeschnitten ("Aufgrund fehlerhafter Auslegung kann es bei
    hohen Belastunge…"). Akzeptiert aus Kompatibilität auch einen Titel-String.
    """
    kurz = getattr(insight, "kurztitel", None)
    if kurz:
        return kurz
    titel = insight if isinstance(insight, str) else (getattr(insight, "titel", "") or "")
    teil = titel.split(":", 1)[-1].strip()
    return (teil[:60].rstrip() + "…") if len(teil) > 61 else teil


def _kba_ref(i: Insight) -> str | None:
    for q in i.quellen:
        if q.typ == "rueckruf_kba" and q.ref:
            return q.ref.strip()
    return None


# ── Sammler ──────────────────────────────────────────────────────────────────

class _Sammler:
    """Sammelt Aktionen, dedupliziert konservativ und sortiert deterministisch.

    Deduplizierung (§13) greift pro Bereich über den Schlüssel (Bereich + Bauteil-/
    Themenschlüssel). Trifft dieselbe Sache zweimal zu (Baureihen-Schwachstelle UND
    Motorproblem am selben Bauteil), entsteht EINE Aktion, die BEIDE Evidence-IDs
    trägt — der höhere Rang und der bereits gesetzte Text gewinnen.

    Ausdrücklich KEIN Duplikat ist dieselbe Sache in VERSCHIEDENEN Bereichen: eine
    Besichtigungsprüfung und eine Verkäuferfrage zum selben Bauteil sind zwei
    unterschiedliche fachliche Handlungen und bleiben beide erhalten.
    """

    def __init__(self) -> None:
        self._pro_bereich: dict[str, dict[str, Kaufaktion]] = {
            BESICHTIGUNG: {}, PROBEFAHRT: {}, VERKAEUFERFRAGEN: {}, DOKUMENTE: {}}

    def add(self, bereich: str, schluessel: str, titel: str, aktion: str, rang: int,
            *, evidence_ids: list[str] | None = None, kategorie: str | None = None,
            schweregrad: str | None = None, kostenhinweis: str | None = None,
            gruppe: str | None = None) -> None:
        if not aktion:
            return
        vorhanden = self._pro_bereich[bereich].get(schluessel)
        if vorhanden is not None:
            for ev in evidence_ids or []:
                if ev not in vorhanden.evidence_ids:
                    vorhanden.evidence_ids.append(ev)
            if rang > vorhanden.rang:
                vorhanden.rang = rang
                vorhanden.prioritaet = _prioritaet(rang)
            if kostenhinweis and not vorhanden.kostenhinweis:
                vorhanden.kostenhinweis = kostenhinweis
            return
        self._pro_bereich[bereich][schluessel] = Kaufaktion(
            id=f"{_ID_PREFIX[bereich]}-{schluessel}",
            bereich=bereich, typ=TYP_SPEZIFISCH, titel=titel, aktion=aktion,
            prioritaet=_prioritaet(rang), rang=rang,
            evidence_ids=list(evidence_ids or []), kategorie=kategorie,
            schweregrad=schweregrad, kostenhinweis=kostenhinweis, gruppe=gruppe,
        )

    def liste(self, bereich: str) -> list[Kaufaktion]:
        """Höchste Relevanz zuerst; bei Ranggleichheit stabil nach ID (§12)."""
        aktionen = sorted(self._pro_bereich[bereich].values(),
                          key=lambda a: (-a.rang, a.id))
        return aktionen[:MAX_SPEZIFISCH_PRO_BEREICH]

    def schluessel(self, bereich: str) -> set[str]:
        """Themen-/Bauteilschlüssel, die in diesem Bereich fahrzeugspezifisch belegt
        sind — Grundlage für die Basis-Dedup (§18).

        Bewusst die Schlüssel der AUSGEGEBENEN Aktionen, nicht aller gesammelten:
        ein Punkt, der durch `MAX_SPEZIFISCH_PRO_BEREICH` herausfällt, darf den
        allgemeinen Basis-Punkt nicht mitreißen — sonst verschwände die Prüfung
        vollständig aus der Checkliste.
        """
        return {a.id.split("-", 1)[1] for a in self.liste(bereich)}


_ID_PREFIX = {
    BESICHTIGUNG: "besichtigung",
    PROBEFAHRT: "probefahrt",
    VERKAEUFERFRAGEN: "frage",
    DOKUMENTE: "dokument",
}


# ── Öffentliche API ──────────────────────────────────────────────────────────

def _fahrzeug_kurzbezeichnung(req, baureihe: dict | None) -> str | None:
    """Kopfzeile für den Ausdruck, z.B. "BMW 3er G20 (2020)".

    Bevorzugt die erkannte Baureihe (sauber normalisiert), sonst die Angaben aus dem
    Inserat. Ist beides leer, bleibt das Feld None statt einer Platzhalterzeile.
    """
    if baureihe:
        teile = [baureihe.get("marke"), baureihe.get("modell"), baureihe.get("generation")]
    else:
        teile = [getattr(req, "marke", None), getattr(req, "modell", None)]
    name = " ".join(str(t).strip() for t in teile if t and str(t).strip())
    baujahr = getattr(req, "baujahr", None)
    if name and baujahr:
        return f"{name} ({baujahr})"
    return name or None


# ── Getriebeabhängige Basistexte (RC1) ───────────────────────────────────────
#
# Der Basis-Katalog ist fahrzeugneutral und mischte deshalb Handschaltung und
# Automatik in einem Satz ("bei Handschaltung auf den Greifpunkt der Kupplung
# achten, bei Automatik …") — bis hin zu "Die Kupplung darf nicht durchrutschen"
# bei einem Automatik-BMW. Ist das Getriebe bekannt, wird genau die passende
# Formulierung verwendet; ist es unbekannt, bleibt der neutrale Katalogtext.
#
# Die ERKENNUNG der Getriebeart liegt jetzt in app/getriebe.py — dort gewinnt die
# strukturierte Nutzerangabe vor dem Freitext. Bis dahin war sie geraten: ohne
# Getriebewort im Inserat blieb sie unbekannt, und die Texte unten wurden nie
# eingesetzt. Namen und Werte bleiben unverändert, damit bestehende Aufrufer und
# Tests (`from app.kaufaktionen import AUTOMATIK, getriebe_art`) weiterlaufen.
_BASIS_GETRIEBE: dict[tuple[str, str], dict[str, str]] = {
    ("probefahrt", "anfahren"): {
        AUTOMATIK: "Mehrmals aus dem Stand anfahren: Das Automatikgetriebe soll ohne "
                   "Verzögerung und ohne Ruck anfahren.",
        MANUELL: "Mehrmals aus dem Stand anfahren und auf Rupfen sowie den Greifpunkt der "
                 "Kupplung achten.",
    },
    ("probefahrt", "rueckwaerts"): {
        AUTOMATIK: "Fahrstufe R mehrfach einlegen: ohne spürbaren Schlag, das Fahrzeug soll "
                   "sauber rückwärts anfahren.",
        MANUELL: "Der Rückwärtsgang soll ohne Kratzen einrasten.",
    },
    ("probefahrt", "schalten"): {
        AUTOMATIK: "Alle Fahrstufen durchfahren: Gangwechsel sollen weich und ohne "
                   "Verzögerung kommen, auch beim Zurückschalten.",
        MANUELL: "Jeden Gang inklusive der oberen Gänge einlegen: ohne Kratzen, Hakeln "
                 "oder Herausspringen.",
    },
    ("probefahrt", "last"): {
        AUTOMATIK: "Wenn möglich eine Steigung hochfahren: Die Drehzahl darf nicht ohne "
                   "entsprechenden Vortrieb hochlaufen (Hinweis auf ein durchrutschendes "
                   "Getriebe).",
        MANUELL: "Wenn möglich eine Steigung hochfahren: Die Kupplung darf nicht "
                 "durchrutschen und die Drehzahl nicht ohne entsprechenden Vortrieb "
                 "hochlaufen.",
    },
    ("probefahrt", "nach_geruch"): {
        AUTOMATIK: "Am Motorraum und an den Rädern auf Geruch nach verbranntem Öl oder "
                   "heißgelaufenen Bremsen achten.",
    },
}


def getriebe_art(req, motor_match: dict | None) -> str | None:
    """automatik | manuell | None.

    Rangfolge jetzt: strukturierte Nutzerangabe > eindeutiger Freitext >
    eindeutige DB-Optionen (app/getriebe.py). Die beiden hinteren Stufen sind
    unverändert; die erste ist neu.
    """
    return _getriebe_aus_request(req, motor_match)


# ── Verkäuferabhängige Basistexte ────────────────────────────────────────────
#
# Dieselbe Bauart wie `_BASIS_GETRIEBE`: die Verkäuferart ERSETZT den Text eines
# bereits vorhandenen Basis-Punktes, sie fügt KEINEN neuen hinzu. Der Prüfplan
# wird dadurch genauer, nicht länger (§20 des Auftrags).
#
# Streng auf Unterlagen und Fragen begrenzt. KEINE Rechtsaussagen (Gewährleistung,
# Sachmängelhaftung, Garantie, Widerruf) — dafür hat ENFAL keine geprüfte
# fachliche Grundlage, siehe app/verkaeuferart.py. Und keine Pauschalwertung:
# beide Zweige sagen dasselbe, nämlich "Angaben mit Unterlagen abgleichen".
_BASIS_VERKAEUFER: dict[tuple[str, str], dict[str, str]] = {
    ("dokumente", "ausweis"): {
        VK_PRIVAT: "Der Name im Ausweis muss zum Halter in Teil II passen: sonst eine "
                   "schriftliche Vollmacht verlangen.",
        VK_HAENDLER: "Firmenname und Anschrift auf Kaufvertrag und Rechnung müssen zum "
                     "Betrieb vor Ort passen. Verkauft der Händler nur in Vermittlung, "
                     "steht der Verkäufer weiterhin in Teil II.",
    },
    ("dokumente", "kaufvertrag"): {
        VK_PRIVAT: "Auch beim Privatkauf einen schriftlichen Vertrag verwenden: "
                   "Zusicherungen zu Unfallfreiheit, Laufleistung und bekannten Mängeln "
                   "müssen darin stehen, mündliche Aussagen sind später nicht belegbar.",
        VK_HAENDLER: "Kaufvertrag und Rechnung vor der Unterschrift zusammen lesen: "
                     "Zusagen aus dem Verkaufsgespräch zu Aufbereitung, Reparaturen und "
                     "mitgeliefertem Zubehör müssen schriftlich darin auftauchen.",
    },
    ("verkaeuferfragen", "eigentuemer"): {
        VK_PRIVAT: "Klärt die Verkaufsberechtigung: bei Verkauf im Auftrag Vollmacht und "
                   "Ausweis zeigen lassen.",
        VK_HAENDLER: "Klärt, ob der Betrieb im eigenen Namen oder in Vermittlung verkauft: "
                     "bei Vermittlung ist der eingetragene Halter der Verkäufer.",
    },
    ("verkaeuferfragen", "reparaturen"): {
        VK_HAENDLER: "Nach Bauteil, Werkstatt und Kilometerstand fragen und die Rechnungen "
                     "zeigen lassen — auch die der eigenen Aufbereitung vor dem Verkauf.",
    },
}


def _basis_liste(bereich: str, katalog, belegte_schluessel: set[str],
                 fahrzeug: str | None, getriebe: str | None = None,
                 verkaeufer: str | None = None) -> list[Kaufaktion]:
    """Baut die Basis-Checkliste eines Bereichs aus dem Katalog.

    Dedup über die Ebenen hinweg (§18): Ein Basis-Punkt entfällt, wenn ein
    fahrzeugspezifischer Punkt DESSELBEN Bereichs bereits einen der in `deckt`
    genannten Schlüssel belegt — dann steht die konkretere Formulierung ohnehin
    weiter oben. Ein `deckt`-Eintrag mit Sternchen wirkt als Präfix ("rueckruf-*"
    trifft "rueckruf-009695"). `deckt` ist im Katalog bewusst sparsam gesetzt:
    inhaltlich VERSCHIEDENE Prüfungen sollen nie gegenseitig verschwinden.

    Reihenfolge: exakt die fachliche Katalogreihenfolge (Ablauf vor Ort bzw. während
    der Fahrt), abgebildet über einen absteigenden Rang — deterministisch und ohne
    Umsortierung nach Priorität, denn alle Basis-Punkte sind gleichrangig.
    """
    out: list[Kaufaktion] = []
    for n, (schluessel, gruppe, titel, aktion, hinweis, deckt) in enumerate(katalog):
        if _wird_abgedeckt(deckt, belegte_schluessel):
            continue
        rang = _R_BASIS - n
        if getriebe:
            aktion = _BASIS_GETRIEBE.get((bereich, schluessel), {}).get(getriebe, aktion)
        if verkaeufer:
            aktion = _BASIS_VERKAEUFER.get((bereich, schluessel), {}).get(verkaeufer, aktion)
        out.append(Kaufaktion(
            id=f"{_ID_PREFIX[bereich]}-basis-{schluessel}",
            bereich=bereich, typ=TYP_BASIS, titel=titel, aktion=aktion,
            prioritaet=PRIO_BASIS, rang=rang, evidence_ids=[],
            kategorie="basis", gruppe=gruppe, hinweis=hinweis,
        ))
    return out


def _wird_abgedeckt(deckt: tuple[str, ...], belegte_schluessel: set[str]) -> bool:
    for eintrag in deckt or ():
        if eintrag.endswith("*"):
            praefix = eintrag[:-1]
            if any(k.startswith(praefix) for k in belegte_schluessel):
                return True
        elif eintrag in belegte_schluessel:
            return True
    return False


def build_kaufaktionen(req, baureihe: dict | None, motor_match: dict | None,
                       insights: list[Insight],
                       laufleistungskontext=None) -> Kaufaktionen:
    """Baut die vier Prüflisten aus Insights + Inserat-Angaben + Basis-Katalog.

    Bewusst OHNE Marktanalyse-/Preisparameter (§20): dieses Modul kann strukturell
    keine Preisaussage erzeugen. `research_status="completed_no_market"` liefert
    damit exakt dieselben Checklisten wie ein Check mit Marktpreis.

    Es werden KEINE neuen DB-Abfragen ausgeführt — `baureihe`, `motor_match` und
    `insights` sind die bereits aufbereiteten Daten des laufenden Checks.

    `laufleistungskontext` (P2-5, optional) schärft vorhandene Wartungspunkte, für
    die die Laufleistung dieses Fahrzeugs relevant geworden ist. Er erzeugt KEINE
    zusätzlichen Aktionen und keinen neuen Bereich: derselbe Dedup-Schlüssel wie
    `_aus_wartung` sorgt dafür, dass aus einer allgemeinen Wartungsfrage eine
    konkrete wird — und dass es bei EINER Aktion bleibt. Fehlt der Kontext (Alt-
    Aufrufe, Tests), verhält sich die Funktion exakt wie zuvor.
    """
    s = _Sammler()
    baujahr = getattr(req, "baujahr", None)

    # P2-5 ZUERST: der `_Sammler` behält bei gleichem Schlüssel den zuerst
    # eingetragenen TEXT und hebt nur den Rang an. Der laufleistungsbezogene,
    # konkretere Text soll gewinnen — deshalb steht dieser Aufruf vor
    # `_aus_wartung`/`_aus_web_evidence`, die denselben Schlüssel belegen.
    _aus_laufleistung(s, laufleistungskontext)
    _aus_schwachstellen(s, insights)
    _aus_motorproblemen(s, insights, motor_match, baujahr)
    _aus_rueckrufen(s, insights)
    _aus_wartung(s, insights)
    # Technischer Web-Fallback: NACH den DB-Quellen. Der `_Sammler` führt
    # gleichnamige Schlüssel zusammen und behält Text und Rang des zuerst
    # eingetragenen — ein geprüfter DB-Fakt zum Turbolader gewinnt damit
    # automatisch gegen einen Web-Fakt zum selben Bauteil, ohne Sonderfall.
    _aus_web_evidence(s, insights)
    _aus_inserat(s, req)

    fahrzeug = _fahrzeug_kurzbezeichnung(req, baureihe)
    getriebe = getriebe_art(req, motor_match)
    verkaeufer = verkaeuferart_aus_request(req)
    kataloge = {
        BESICHTIGUNG:     BASIS_BESICHTIGUNG,
        PROBEFAHRT:       BASIS_PROBEFAHRT,
        VERKAEUFERFRAGEN: BASIS_VERKAEUFERFRAGEN,
        DOKUMENTE:        BASIS_DOKUMENTE,
    }
    listen = {}
    for bereich, katalog in kataloge.items():
        spezifisch = s.liste(bereich)
        listen[bereich] = Pruefliste(
            bereich=bereich,
            export_title=EXPORT_TITEL[bereich],
            fahrzeug=fahrzeug,
            fahrzeugspezifisch=spezifisch,
            basis=_basis_liste(bereich, katalog, s.schluessel(bereich), fahrzeug,
                               getriebe, verkaeufer),
        )
    return Kaufaktionen(
        besichtigung=listen[BESICHTIGUNG],
        probefahrt=listen[PROBEFAHRT],
        verkaeuferfragen=listen[VERKAEUFERFRAGEN],
        dokumente=listen[DOKUMENTE],
    )


# ── 1) Baureihen-Schwachstellen ──────────────────────────────────────────────

def _rang_schwachstelle(schweregrad: str | None, komp: dict | None) -> int:
    s = (schweregrad or "").strip().lower()
    if s in _HOHE_SCHWERE:
        rang = _R_SCHWACH_HOCH
    elif s in ("mittel", "moderat"):
        rang = _R_SCHWACH_MITTEL
    else:
        rang = _R_SCHWACH_GERING
    if komp and komp["sicherheit"]:
        rang += _BONUS_SICHERHEIT
    return rang


# ── Art einer Schwachstelle (RC1) ───────────────────────────────────────────
#
# Das Frage-Template lautete für JEDE Schwachstelle "Wurde am Bauteil „X“
# bereits gearbeitet …?". Bei "Knarzgeräusche Innenraum" ergab das die
# Frage nach dem "Bauteil Knarzgeräusche". Die DB-Spalte heißt zwar `bauteil`,
# enthält aber auch Symptome und Softwarethemen. Die Art wird deshalb aus dem
# Text bestimmt — generisch, ohne Fahrzeug-Sonderfall.
GERAEUSCH, SOFTWARE, BAUTEIL = "geraeusch", "software", "bauteil"

_GERAEUSCH_WORTE = ("geraeusch", "knarz", "klapper", "quietsch", "poltern", "rassel",
                    "brumm", "pfeif", "heul", "klacker", "knack", "dröhn", "droehn")
# Bewusst KEIN "app": das steckt auch in "Heckklappe" oder "Kappe".
_SOFTWARE_WORTE = ("software", "infotainment", "idrive", "mmi", "navi",
                   "konnektiv", "bluetooth", "update")


def schwachstellen_art(bauteil: str | None) -> str:
    n = _norm(bauteil)
    if any(w in n for w in _GERAEUSCH_WORTE):
        return GERAEUSCH
    if any(w in n for w in _SOFTWARE_WORTE):
        return SOFTWARE
    return BAUTEIL


def _ist_bekannt(i: Insight) -> bool:
    """Dieselbe Einstufung wie der Insight-Titel (app/evidence.py): verifiziert und
    nicht als Einzelbericht beschrieben."""
    return (i.titel or "").endswith("bekannte Schwachstelle")


def _gruppe(i: Insight) -> str:
    return "Bekannte Schwachstelle" if _ist_bekannt(i) else "Gemeldeter Hinweis"


def _herkunft_satz(i: Insight) -> str:
    return ("Bekannte Schwachstelle dieser Baureihe" if _ist_bekannt(i)
            else "Für diese Baureihe gemeldeter Punkt")


def verkaeuferfrage(bauteil: str, art: str) -> str:
    if art == GERAEUSCH:
        return (f"Sind Ihnen Auffälligkeiten zum Thema „{bauteil}“ bekannt, und wurde "
                f"deswegen schon etwas nachgebessert oder ersetzt?")
    if art == SOFTWARE:
        return (f"Gab es Störungen im Bereich „{bauteil}“, und ist der aktuelle "
                f"Software-Stand eingespielt?")
    return f"Wurde am Bauteil „{bauteil}“ bereits gearbeitet oder etwas ersetzt?"


def _aus_schwachstellen(s: _Sammler, insights: list[Insight]) -> None:
    """Bekannte Baureihen-Schwachstelle -> Besichtigung (+ ggf. Probefahrt) + Frage.

    Die Insights sind bereits baujahrgefiltert (P0-2) — eine Schwachstelle, die
    nachweislich nicht für dieses Baujahr gilt, ist hier gar nicht mehr enthalten
    und kann folglich keine Aktion erzeugen (§9).
    """
    for i in insights:
        if i.kategorie != "schwachstelle":
            continue
        bauteil = _bauteil_aus_schwachstelle(i)
        art = schwachstellen_art(bauteil)
        # Ein Geräusch ist kein Bauteil: die Komponententabelle würde "Knarzgeräusche
        # Innenraum" über das Wort "Innenraum" auf die Verschleißprüfung der Sitze
        # abbilden — fachlich eine andere Prüfung.
        komp = None if art == GERAEUSCH else _komponente(bauteil)
        schluessel = komp["schluessel"] if komp else _slug(bauteil)
        rang = _rang_schwachstelle(i.schweregrad, komp)
        gruppe = _gruppe(i)

        # Besichtigung: Tabellentext, sonst der evidenzgebundene Fallback auf das
        # konkrete Bauteil (§5 — kein generischer 30-Punkte-Katalog).
        if art == GERAEUSCH:
            besichtigung = (f"Auf den gemeldeten Punkt „{bauteil}“ achten: im Stand die "
                            f"betroffenen Verkleidungen und Bedienelemente bewegen, bei der "
                            f"Probefahrt auf schlechter Fahrbahn hinhören.")
        else:
            besichtigung = (komp or {}).get("besichtigung") or (
                f"{bauteil} und den umliegenden Bereich auf erkennbare Auffälligkeiten prüfen "
                f"(Zustand, Leckagen, Geräusche, Warnmeldungen)."
            )
        s.add(BESICHTIGUNG, schluessel, bauteil, besichtigung, rang,
              evidence_ids=[i.id], kategorie="schwachstelle", schweregrad=i.schweregrad,
              gruppe=gruppe)

        # Probefahrt NUR über eines der beiden Tore (§6).
        symptom = (komp or {}).get("probefahrt") or _fahrsymptom_aus_text(i.beschreibung)
        if symptom:
            s.add(PROBEFAHRT, schluessel, bauteil, symptom, rang,
                  evidence_ids=[i.id], kategorie="schwachstelle", schweregrad=i.schweregrad,
              gruppe=gruppe)

        s.add(VERKAEUFERFRAGEN, schluessel,
              verkaeuferfrage(bauteil, art),
              f"{_herkunft_satz(i)}: nach durchgeführten Reparaturen oder Updates fragen "
              "und Rechnungen bzw. Werkstattbelege zeigen lassen.",
              rang, evidence_ids=[i.id], kategorie="schwachstelle", schweregrad=i.schweregrad,
              gruppe=gruppe)

        # Dokumentenebene nur bei wirklich teuren/schweren Punkten — sonst würde die
        # Dokumentenliste mit jeder Kleinigkeit volllaufen.
        if (i.schweregrad or "").strip().lower() in _HOHE_SCHWERE:
            s.add(DOKUMENTE, schluessel, f"Reparaturnachweis {bauteil}",
                  f"Falls wegen „{bauteil}“ gearbeitet wurde: Rechnung oder Werkstattbeleg "
                  f"mit Datum und Kilometerstand vorlegen lassen.",
                  rang, evidence_ids=[i.id], kategorie="schwachstelle", schweregrad=i.schweregrad,
              gruppe=gruppe)


# ── 2) Motorprobleme ─────────────────────────────────────────────────────────

def _aus_motorproblemen(s: _Sammler, insights: list[Insight], motor_match: dict | None,
                        baujahr: int | None) -> None:
    """Motorspezifisches Problem -> Besichtigung (+ ggf. Probefahrt) + Frage.

    Motorprobleme entstehen in `build_insights` ausschließlich bei EINDEUTIG
    erkannter Motorvariante — es gibt hier also keine Aktion "falls Motor X".
    """
    for i, satz in _motorproblem_paare(insights, motor_match, baujahr):
        bauteil = (satz.get("bauteil") or "").strip() or _bauteil_aus_motorproblem(i)
        komp = _komponente(bauteil)
        schluessel = komp["schluessel"] if komp else _slug(bauteil)
        kosten = _kostenhinweis(satz.get("kosten_ca"))
        rang = _R_MOTORPROBLEM + (_BONUS_SICHERHEIT if komp and komp["sicherheit"] else 0) \
            + (_BONUS_KOSTEN if kosten else 0)

        besichtigung = (komp or {}).get("besichtigung") or (
            f"{bauteil} und den umliegenden Bereich auf erkennbare Auffälligkeiten prüfen "
            f"(Zustand, Leckagen, Geräusche, Warnmeldungen)."
        )
        s.add(BESICHTIGUNG, schluessel, bauteil, besichtigung, rang,
              evidence_ids=[i.id], kategorie="motorproblem", kostenhinweis=kosten,
              gruppe="Bekanntes Motorproblem")

        symptom = (komp or {}).get("probefahrt") or _fahrsymptom_aus_text(i.beschreibung)
        if symptom:
            s.add(PROBEFAHRT, schluessel, bauteil, symptom, rang,
                  evidence_ids=[i.id], kategorie="motorproblem", kostenhinweis=kosten,
              gruppe="Bekanntes Motorproblem")

        kosten_satz = f" Bekannte Reparaturkosten laut Datenlage: {kosten}." if kosten else ""
        s.add(VERKAEUFERFRAGEN, schluessel,
              f"Wurde „{bauteil}“ bei diesem Motor bereits repariert oder ersetzt?",
              f"Bekanntes Problem dieser Motorisierung: nach Reparatur, Datum, Kilometerstand "
              f"und Rechnung fragen.{kosten_satz}",
              rang, evidence_ids=[i.id], kategorie="motorproblem", kostenhinweis=kosten,
              gruppe="Bekanntes Motorproblem")

        s.add(DOKUMENTE, schluessel, f"Reparaturnachweis {bauteil}",
              f"Falls „{bauteil}“ bereits bearbeitet wurde: Rechnung oder Werkstattbeleg mit "
              f"Datum und Kilometerstand vorlegen lassen.",
              rang, evidence_ids=[i.id], kategorie="motorproblem", kostenhinweis=kosten,
              gruppe="Bekanntes Motorproblem")


# ── 3) Rückrufe (§10 — konservativ, nie "dein Auto ist betroffen") ───────────

# Stufen, bei denen Baujahr/Variante zum Fahrzeug passen. Auch hier gilt: das ist
# NICHT "betroffen", sondern "kann betroffen sein — per FIN prüfen".
_RUECKRUF_PASSEND = ("confirmed_by_vin", "variant_match")

# Wortgleich mit `app.evidence.TRUST_VERIFIED`. Hier als Literal, weil
# app/evidence.py dieses Modul selbst nicht importieren darf und ein Import in
# die Gegenrichtung nur wegen einer Zeichenkette keinen neuen Modulzyklus
# rechtfertigt. Der Wert ist Teil des persistierten Datenmodells
# (`fakt_verifikation.status`) und aendert sich nicht.
_TRUST_VERIFIED = "verified"


def _aus_rueckrufen(s: _Sammler, insights: list[Insight]) -> None:
    """Rückruf -> FIN-Prüfung + Durchführungsnachweis. KEINE Besichtigungsaktion.

    Ein Rückruf ist vor Ort nicht sichtbar prüfbar — die einzig belastbare Handlung
    ist die FIN-Abfrage beim Hersteller/KBA bzw. der Nachweis der Werkstatt. Die
    Formulierung folgt strikt der vorhandenen `applicability`-Stufe; die bestehende
    Recall-Pipeline wird nicht verändert.
    """
    for i in insights:
        if i.kategorie != "rueckruf":
            continue
        kba = _kba_ref(i)
        mangel = _mangel_kurz(i)
        schluessel = f"rueckruf-{_slug(kba) if kba else _slug(mangel)}"
        passend = i.applicability in _RUECKRUF_PASSEND
        # FLOOR-SAFETY-AUDIT (Batch A): der Rang haengt nicht mehr allein an der
        # Applicability. Seit `recall_filter` einen nur baureihenweiten Rueckruf
        # korrekt bei "series_only" belaesst, waeren sonst amtlich verifizierte
        # Sicherheitsrueckrufe fuer genau dieses Modell und Baujahr im Rang
        # gefallen — obwohl die auszufuehrende HANDLUNG identisch ist (FIN
        # pruefen, Durchfuehrungsnachweis verlangen) und die Beleglage amtlich.
        # Der WORTLAUT unterscheidet weiterhin sauber zwischen "fuer diese
        # Variante gemeldet" und "fuer Teile dieser Baureihe gemeldet"; nur die
        # Reihenfolge behandelt beide gleich.
        amtlich_belegt = (getattr(i, "trust", None) or "") == _TRUST_VERIFIED
        rang = _R_RUECKRUF_VARIANTE if (passend or amtlich_belegt) else _R_RUECKRUF_SERIE
        kba_zusatz = f" (KBA-Referenz {kba})" if kba else ""

        if passend:
            frage = f"Wurde die Rückrufaktion zu „{mangel}“ bereits durchgeführt?"
            frage_aktion = ("Für diese Variante ist eine Rückrufaktion gemeldet. Nach dem "
                            "Werkstattnachweis fragen und zusätzlich die FIN beim Hersteller "
                            "oder KBA auf offene Rückrufaktionen prüfen lassen.")
        else:
            frage = f"Ist bekannt, ob dieses Fahrzeug von der Rückrufaktion zu „{mangel}“ betroffen ist?"
            frage_aktion = ("Für Teile dieser Baureihe ist eine Rückrufaktion gemeldet. Ob genau "
                            "dieses Fahrzeug betroffen ist, lässt sich nur anhand der FIN beim "
                            "Hersteller oder KBA klären.")
        s.add(VERKAEUFERFRAGEN, schluessel, frage, frage_aktion, rang,
              evidence_ids=[i.id], kategorie="rueckruf",
              gruppe="Rückrufaktion")

        s.add(DOKUMENTE, schluessel, f"Rückrufaktion „{mangel}“",
              f"FIN beim Hersteller oder KBA auf offene Rückrufaktionen prüfen{kba_zusatz} und, "
              f"falls bereits erledigt, den Durchführungsnachweis der Werkstatt vorlegen lassen.",
              rang, evidence_ids=[i.id], kategorie="rueckruf",
              gruppe="Rückrufaktion")


# ── 4) Kritische Wartung (DB, ohne Insight-ID — siehe Modulkopf) ─────────────

# Aus dem Insight-Titel "<Bauteil> — kritischer Wartungspunkt (<Motor>)" das Bauteil
# zurückgewinnen (build_insights baut ihn genau so auf).
_WARTUNG_TITEL = re.compile(r"^(?P<bauteil>.+?)(?:\s+—\s+|:\s+)kritischer Wartungspunkt")


def _bauteil_aus_wartung(i: Insight) -> str:
    """Bauteil eines Wartungs-Insights. `quellen[0].ref` trägt es direkt (von
    build_insights gesetzt); der Titel ist nur die Rückfallebene."""
    for q in i.quellen:
        if q.typ == "motorvarianten" and q.ref:
            return q.ref.strip()
    m = _WARTUNG_TITEL.match(i.titel)
    return (m.group("bauteil").strip() if m else i.titel.strip()) or "Wartungspunkt"


def _aus_wartung(s: _Sammler, insights: list[Insight]) -> None:
    """Kritische Wartungspunkte der erkannten Motorvariante -> Frage + Dokument.

    Evidence-Integrity (behoben): Diese Aktionen lesen jetzt die Insight-Kategorie
    "wartung" statt der Rohdaten und tragen damit eine VALIDE Evidence-ID. Zuvor
    blieb `evidence_ids` hier leer, weil `build_insights` diese DB-Tabelle nicht
    ausgab — genau der offene Punkt aus der ersten P1-3-Fassung.

    Die Applicability kommt weiterhin ausschließlich über die Motorvariante:
    `build_insights` erzeugt diese Insights nur bei EINDEUTIG erkanntem Motor
    (`kritische_wartung` hängt an `variante_id` und hat keine Baujahres-Spalte). Es
    gibt hier also nach wie vor keine eigene Baujahreslogik.

    Bewusst KEINE Besichtigungsaktion: ein Wartungsintervall ist vor Ort nicht
    prüfbar, sondern nur über Nachweise.
    """
    for i in insights:
        if i.kategorie != "wartung":
            continue
        bauteil = _bauteil_aus_wartung(i)
        komp = _komponente(bauteil)
        schluessel = f"wartung-{komp['schluessel'] if komp else _slug(bauteil)}"
        intervall_satz = f" {i.beschreibung}" if i.beschreibung else ""

        s.add(VERKAEUFERFRAGEN, schluessel,
              f"Wann wurde „{bauteil}“ zuletzt gemacht: bei welchem Kilometerstand?",
              f"Wartungspunkt mit erhöhter Bedeutung für diese Motorisierung.{intervall_satz} "
              f"Nach Datum, Kilometerstand und Beleg fragen.",
              _R_WARTUNG, evidence_ids=[i.id], kategorie="wartung",
              gruppe="Wartung und Technik")

        s.add(DOKUMENTE, schluessel, f"Wartungsnachweis {bauteil}",
              f"Beleg über die letzte Durchführung von „{bauteil}“ zeigen lassen. "
              f"Rechnung oder Eintrag im Serviceheft mit Datum und Kilometerstand.",
              _R_WARTUNG, evidence_ids=[i.id], kategorie="wartung",
              gruppe="Prüfungen und Wartung")


# ── 4b) Technischer Web-Fallback ─────────────────────────────────────────────

def _web_bauteil(i: Insight) -> str:
    """Bauteil-Label eines Web-Insights aus dem Titel ("Turbolader — Hinweis …")."""
    return titel_bauteil(i.titel).split("(")[0].strip() or "Fahrzeug"


def _aus_web_evidence(s: _Sammler, insights: list[Insight]) -> None:
    """Belegte Web-Fakten -> dieselben vier Bereiche wie DB-Evidence.

    Es gelten EXAKT dieselben P1-3-Regeln wie für DB-Fakten — die Herkunft ändert
    die Sorgfalt nicht:

      * Probefahrt nur über die beiden bestehenden Tore (Bauteil mit fachlich
        beobachtbarem Fahrsymptom ODER ein im Evidence-TEXT ausdrücklich genanntes
        Symptom). Es wird kein Symptom erfunden, nur weil eine Webquelle ein Bauteil
        nennt.
      * Jede Aktion trägt die Evidence-ID des Web-Insights — quellengebunden bis in
        die Checkliste.
      * Dedup über denselben Bauteilschlüssel wie die DB-Fakten. Läuft der
        Web-Fallback neben vorhandenen DB-Daten (Trigger "motor_fehlt"/"konflikt"),
        gewinnt der bereits eingetragene DB-Punkt.
      * Rückrufe erzeugen weiterhin KEINE Besichtigungs- oder Probefahrtaktion —
        vor Ort nicht prüfbar, nur FIN und Nachweis.

    Der Text macht die Herkunft sichtbar ("laut Webrecherche"), damit ein
    ausgedruckter Prüfplan ohne die ENFAL-Oberfläche nicht so wirkt, als käme der
    Punkt aus der ENFAL-Fahrzeugdatenbank.
    """
    for i in insights:
        if not i.kategorie.startswith("web_"):
            continue
        art = i.kategorie.removeprefix("web_")
        bauteil = _web_bauteil(i)
        komp = _komponente(bauteil)
        schluessel = komp["schluessel"] if komp else _slug(bauteil)

        if art == "rueckruf":
            s.add(VERKAEUFERFRAGEN, f"rueckruf-web-{schluessel}",
                  f"Ist bekannt, ob für dieses Fahrzeug eine Rückrufaktion offen ist?",
                  "Eine Webrecherche nennt für dieses Modell eine Rückrufaktion. Ob genau "
                  "dieses Fahrzeug betroffen ist, lässt sich nur anhand der FIN beim "
                  "Hersteller oder KBA klären: nach einem Werkstattnachweis fragen.",
                  _R_WEB_RUECKRUF, evidence_ids=[i.id], kategorie="web_rueckruf",
                  gruppe="Rückrufaktion")
            s.add(DOKUMENTE, f"rueckruf-web-{schluessel}",
                  "Rückrufstatus über die FIN prüfen lassen",
                  "Laut Webrecherche existiert für dieses Modell eine Rückrufaktion. FIN beim "
                  "Hersteller oder KBA auf offene Rückrufaktionen prüfen und, falls bereits "
                  "erledigt, den Durchführungsnachweis der Werkstatt vorlegen lassen.",
                  _R_WEB_RUECKRUF, evidence_ids=[i.id], kategorie="web_rueckruf",
                  gruppe="Prüfungen und Wartung")
            continue

        if art == "wartung":
            s.add(VERKAEUFERFRAGEN, f"wartung-web-{schluessel}",
                  f"Wann wurde „{bauteil}“ zuletzt gemacht: bei welchem Kilometerstand?",
                  f"Eine Webrecherche nennt für dieses Modell ein Intervall zu „{bauteil}“. "
                  f"Nach Datum, Kilometerstand und Beleg fragen.",
                  _R_WEB_WARTUNG, evidence_ids=[i.id], kategorie="web_wartung",
                  gruppe="Wartung und Technik")
            s.add(DOKUMENTE, f"wartung-web-{schluessel}", f"Wartungsnachweis {bauteil}",
                  f"Beleg über die letzte Durchführung von „{bauteil}“ zeigen lassen. "
                  f"Rechnung oder Eintrag im Serviceheft mit Datum und Kilometerstand.",
                  _R_WEB_WARTUNG, evidence_ids=[i.id], kategorie="web_wartung",
                  gruppe="Prüfungen und Wartung")
            continue

        # art == "schwachstelle"
        besichtigung = (komp or {}).get("besichtigung") or (
            f"{bauteil} und den umliegenden Bereich auf erkennbare Auffälligkeiten prüfen "
            f"(Zustand, Leckagen, Geräusche, Warnmeldungen)."
        )
        s.add(BESICHTIGUNG, schluessel, bauteil,
              f"{besichtigung} (Hinweis stammt aus der Webrecherche, nicht aus der "
              f"ENFAL-Fahrzeugdatenbank.)",
              _R_WEB_SCHWACH + (_BONUS_SICHERHEIT if komp and komp["sicherheit"] else 0),
              evidence_ids=[i.id], kategorie="web_schwachstelle",
              gruppe="Hinweis aus der Webrecherche")

        symptom = (komp or {}).get("probefahrt") or _fahrsymptom_aus_text(i.beschreibung)
        if symptom:
            # Auch hier die Herkunft im TEXT, nicht nur in der Gruppe: die vier
            # Prueflisten werden einzeln ausgedruckt (§13 P1-3) und stehen dann ohne
            # jede Oberflaeche da. Ein Punkt ohne Herkunftshinweis waere auf Papier
            # nicht mehr von einem geprueften DB-Punkt zu unterscheiden.
            s.add(PROBEFAHRT, schluessel, bauteil,
                  f"{symptom} (Hinweis stammt aus der Webrecherche, nicht aus der "
                  f"ENFAL-Fahrzeugdatenbank.)",
                  _R_WEB_SCHWACH + (_BONUS_SICHERHEIT if komp and komp["sicherheit"] else 0),
                  evidence_ids=[i.id], kategorie="web_schwachstelle",
                  gruppe="Hinweis aus der Webrecherche")

        s.add(VERKAEUFERFRAGEN, schluessel,
              f"Wurde am Bauteil „{bauteil}“ bereits gearbeitet oder etwas ersetzt?",
              f"Eine Webrecherche nennt „{bauteil}“ als bekannten Schwachpunkt dieses "
              f"Modells: nach durchgeführten Reparaturen fragen und Rechnungen bzw. "
              f"Werkstattbelege zeigen lassen.",
              _R_WEB_SCHWACH, evidence_ids=[i.id], kategorie="web_schwachstelle",
              gruppe="Hinweis aus der Webrecherche")


# ── 4c) Laufleistungsbezogene Wartungspunkte (P2-5) ──────────────────────────

# Der Schlüssel MUSS exakt dem entsprechen, den `_aus_wartung` bzw.
# `_aus_web_evidence` für dasselbe Bauteil bilden — sonst entstünde ein zweiter,
# fast gleichlautender Punkt statt eines geschärften.
_LAUF_SCHLUESSEL_PRAEFIX = {"db_wartung": "wartung-", "web_wartung": "wartung-web-"}


def _aus_laufleistung(s: _Sammler, kontext) -> None:
    """Wartungspunkte, die bei DIESER Laufleistung relevant sind -> Frage + Nachweis.

    Der Kontext liefert ausschließlich Hinweise, die aus einer EXISTIERENDEN
    Evidence stammen und einen konkret auswertbaren Kilometerpunkt tragen — ein
    unverified `wartung_oel_km` kommt dort nie an (app/laufleistung.py, Stufe C).
    `evidence_id` ist deshalb immer gesetzt und immer echt.

    Ein Punkt, der noch weit entfernt liegt, erreicht diese Funktion gar nicht
    (Status "entfernt" erzeugt keinen Hinweis) — es entsteht also keine Aktion
    ohne Anlass, und die Basislisten wachsen nicht.

    Wie überall in P1-3 gilt: KEINE Besichtigungs- und KEINE Probefahrtaktion.
    Ob eine Wartung durchgeführt wurde, ist weder im Stand noch während der Fahrt
    feststellbar, sondern ausschließlich über Belege.
    """
    if kontext is None:
        return
    for w in getattr(kontext, "wartungshinweise", None) or []:
        komp = _komponente(w.bauteil)
        basis = komp["schluessel"] if komp else _slug(w.bauteil)
        schluessel = f"{_LAUF_SCHLUESSEL_PRAEFIX.get(w.herkunft, 'wartung-')}{basis}"
        rang = (_R_WARTUNG_RELEVANT if w.status in ("im_bereich", "darueber")
                else _R_WARTUNG)

        s.add(VERKAEUFERFRAGEN, schluessel,
              f"Wurde „{w.bauteil}“ bereits gemacht, und wenn ja, wann und bei welchem Kilometerstand?",
              f"{w.hinweis} Nach Datum, Kilometerstand und Beleg fragen.",
              rang, evidence_ids=[w.evidence_id], kategorie="wartung",
              gruppe="Wartung und Technik")

        s.add(DOKUMENTE, schluessel, f"Wartungsnachweis {w.bauteil}",
              f"{w.hinweis} Rechnung oder Eintrag im Serviceheft mit Datum und "
              f"Kilometerstand zeigen lassen.",
              rang, evidence_ids=[w.evidence_id], kategorie="wartung",
              gruppe="Prüfungen und Wartung")


# ── 5) Inserat-Angaben (§8 — nur was der Nutzer tatsächlich angegeben hat) ───

def _aus_inserat(s: _Sammler, req) -> None:
    """Dokumenten- und Nachfrage-Aktionen aus den Inserat-Angaben.

    Strikte Regel (§8): Es wird NIE behauptet, ein Dokument fehle, wenn die Daten das
    nicht hergeben. Die beste Servicehistorie-Angabe erzeugt eine PRÜF-Aktion
    ("Nachweise ansehen"), niemals eine Mangel-Aussage — und sie entfernt kein
    Risiko. Fehlt eine Angabe ganz (None), ist das eine offene FRAGE — keine
    Feststellung.
    """
    # Servicehistorie: vier Zustände statt eines Ja/Nein. Jeder Zustand führt zu
    # einer ANDEREN Handlung — genau das war mit einer Checkbox nicht möglich.
    # `servicehistorie_status` liest das neue Feld und bildet die alte Checkbox ab,
    # gespeicherte Checks verhalten sich also unverändert.
    sh = servicehistorie_status(req)
    if sh == SH_VOLLSTAENDIG:
        # Kein Risiko-Abzug: "vollständig angegeben" ist eine Behauptung des
        # Inserats. Die Aktion prüft genau diese Behauptung.
        s.add(DOKUMENTE, "scheckheft", "Angegebene Servicehistorie belegen lassen",
              "Laut Inserat wird eine vollständige Servicehistorie angegeben. Serviceheft "
              "bzw. digitales Serviceprotokoll durchsehen und auf durchgehende Einträge mit "
              "Stempel, Datum und Kilometerstand achten; die passenden Rechnungen dazu "
              "zeigen lassen.",
              _R_DOKUMENT_STANDARD, kategorie="inserat", gruppe="Angaben aus dem Inserat")
    elif sh == SH_TEILWEISE:
        s.add(DOKUMENTE, "scheckheft", "Fehlende Zeiträume der Servicehistorie klären",
              "Die Servicehistorie ist laut Inserat nur teilweise vorhanden. Vorhandene "
              "Einträge und Rechnungen chronologisch durchgehen und festhalten, welche "
              "Zeiträume und Kilometerstände nicht belegt sind — diese Lücken vor dem Kauf "
              "ansprechen.",
              _R_DOKUMENT_STANDARD + 20, kategorie="inserat", gruppe="Angaben aus dem Inserat")
    elif sh == SH_UMFANG_UNKLAR:
        s.add(VERKAEUFERFRAGEN, "scheckheft",
              "Welche Serviceunterlagen liegen konkret vor?",
              "Laut Inserat ist eine Servicehistorie vorhanden, ihr Umfang bleibt offen: vor "
              "der Besichtigung erfragen, ob Serviceheft, digitales Serviceprotokoll oder "
              "einzelne Rechnungen vorliegen, und die Unterlagen vor Ort zeigen lassen.",
              _R_ANGABE_FEHLT + 30, kategorie="inserat", gruppe="Angaben aus dem Inserat")
    elif sh == SH_NICHT_VORHANDEN:
        s.add(DOKUMENTE, "scheckheft", "Wartung ohne Serviceunterlagen einschätzen",
              "Laut Inserat liegt keine Servicehistorie vor. Damit ist der Wartungsstand "
              "dieses Fahrzeugs nicht nachvollziehbar: nach einzelnen Werkstattrechnungen "
              "fragen und offene Wartungspunkte im Kaufpreis berücksichtigen.",
              _R_DOKUMENT_STANDARD + 40, kategorie="inserat", gruppe="Angaben aus dem Inserat")
    else:
        s.add(VERKAEUFERFRAGEN, "scheckheft",
              "Gibt es ein durchgehend geführtes Scheckheft oder eine digitale Servicehistorie?",
              "Die Wartungshistorie geht aus dem Inserat nicht hervor: vor der Besichtigung "
              "klären und die Nachweise vor Ort zeigen lassen.",
              _R_ANGABE_FEHLT, kategorie="inserat", gruppe="Angaben aus dem Inserat")

    tuev = (getattr(req, "tuev_bis", None) or "").strip()
    if tuev:
        s.add(DOKUMENTE, "hu-bericht", f"HU-Bericht zum angegebenen Termin ({tuev}) ansehen",
              "Den letzten Prüfbericht der Hauptuntersuchung zeigen lassen: die dort vermerkten "
              "Mängel und der Kilometerstand zeigen, was zuletzt beanstandet wurde.",
              _R_DOKUMENT_KERN, kategorie="inserat", gruppe="Angaben aus dem Inserat")
    else:
        s.add(VERKAEUFERFRAGEN, "hu-bericht",
              "Bis wann läuft die HU, und liegt der letzte Prüfbericht vor?",
              "Das Inserat nennt kein HU-Datum. Termin und Prüfbericht erfragen, denn eine "
              "fällige Hauptuntersuchung kann kurzfristig Kosten verursachen.",
              _R_ANGABE_FEHLT + 20, kategorie="inserat", gruppe="Angaben aus dem Inserat")

    unfall = (getattr(req, "unfallfrei", None) or "").strip().lower()
    if unfall in ("nein", "false", "unfallschaden", "unfall"):
        s.add(DOKUMENTE, "unfall", "Unfallreparatur dokumentieren lassen",
              "Das Inserat weist das Fahrzeug als nicht unfallfrei aus. Reparaturrechnungen, "
              "Schadensumfang und, falls vorhanden, ein Gutachten zeigen lassen.",
              _R_DOKUMENT_KERN + 40, kategorie="inserat", gruppe="Angaben aus dem Inserat")
    elif unfall in ("ja", "true"):
        s.add(DOKUMENTE, "unfall", "Unfallfreiheit schriftlich festhalten",
              "Das Inserat gibt das Fahrzeug als unfallfrei an: diese Zusicherung in den "
              "Kaufvertrag aufnehmen statt sie nur mündlich zu vereinbaren.",
              _R_DOKUMENT_STANDARD, kategorie="inserat", gruppe="Angaben aus dem Inserat")
    else:
        s.add(VERKAEUFERFRAGEN, "unfall",
              "Ist das Fahrzeug unfallfrei, und gab es lackierte oder ersetzte Teile?",
              "Das Inserat macht dazu keine eindeutige Angabe: vor der Besichtigung klären "
              "und die Antwort später im Kaufvertrag festhalten.",
              _R_ANGABE_FEHLT + 10, kategorie="inserat", gruppe="Angaben aus dem Inserat")

    if getattr(req, "vorbesitzer", None) is None:
        s.add(VERKAEUFERFRAGEN, "vorbesitzer",
              "Wie viele Vorbesitzer hat das Fahrzeug, und wer ist im Fahrzeugbrief eingetragen?",
              "Die Zahl der Vorbesitzer fehlt im Inserat: vor Ort mit Teil II der "
              "Zulassungsbescheinigung (Fahrzeugbrief) abgleichen.",
              _R_ANGABE_FEHLT, kategorie="inserat", gruppe="Angaben aus dem Inserat")
    else:
        s.add(DOKUMENTE, "vorbesitzer", "Zulassungsbescheinigung mit der Inseratangabe abgleichen",
              f"Das Inserat nennt {req.vorbesitzer} Vorbesitzer: mit Teil II der "
              f"Zulassungsbescheinigung abgleichen und prüfen, ob der Verkäufer dort eingetragen ist.",
              _R_DOKUMENT_STANDARD, kategorie="inserat", gruppe="Angaben aus dem Inserat")
