"""
Test: die neun Befunde aus dem echten Production-KaufCheck (BMW 330i G20).

Der Testfall ist EXAKT das Fahrzeug aus dem Live-Run:

    BMW 330i · G20 · 2019 · 78.500 km · Benzin · 258 PS · Automatik ·
    Hinterradantrieb · Händler · 2 Vorbesitzer · HU 08/2027 ·
    Servicehistorie laut Inserat vollständig · letzte Wartung laut Inserat
    ca. 72.000 km · 26.900 EUR

Geprueft wird jeder Befund einzeln, dazu die Dinge, die der echte Lauf RICHTIG
gemacht hat und die nicht regressieren duerfen.

  A  drei Rueckrufe erscheinen in der Zusammenfassung "Kritische Risiken"
  B  der Starterrelais-Rueckruf faellt dort NICHT heraus
  C  die Angabe "2 Vorbesitzer" wird konkret verwendet
  D  die 72.000-km-Wartungsangabe wird erkannt
  E  "Wann war die letzte Wartung?" wird dann NICHT mehr gefragt
  F  keine Behauptung, Teil II sei ein Eigentumsnachweis
  G  Halter, Eigentuemer, Verkaeufer und Vertragspartner bleiben getrennt
  H  kein rhetorischer Gedankenstrich in erzeugten Nutzertexten
  I  kein ". kleingeschriebener Satzanfang"
  J  Servicehistorie bleibt eine ANGABE, das Label ist nicht "Pluspunkt"
  K  Automatik-Pruefpunkte bleiben erhalten
  L  ohne Marktdaten bleibt der Preis unbewertet
  M  FIN-Copy verspricht keine individuelle KBA-Abfrage

Ohne Netzwerk, ohne Provider, ohne DB-Schreibzugriff auf die Live-Datenbank.

Ausfuehren:  python test_kaufcheck_live_run.py
"""
import os
import re
import tempfile

_TMP = tempfile.mkdtemp(prefix="enfal_live_")
os.environ["AUTO_KI_DB_PATH"] = os.path.join(_TMP, "test.db")

import app.database as db            # noqa: E402
db.ensure_tables()

from app.models import Insight, KaufCheckRequest    # noqa: E402
import app.kaufaktionen as ka                        # noqa: E402
import app.key_findings as kf                        # noqa: E402
import app.kaufcheck as kc                           # noqa: E402
import app.laufleistung as ll                        # noqa: E402
import app.wartungsangabe as wa                      # noqa: E402
import app.pruefplan_basis as pb                     # noqa: E402
from app.empfehlung_gruende import baue_empfehlung_gruende      # noqa: E402
from app.rueckruf_konsistenz import ergaenze_fehlende_rueckrufe  # noqa: E402
from app.preisurteil import bewerte_preis            # noqa: E402

FEHLER = []


def check(name, cond, detail=""):
    print(f"[{'OK' if cond else 'FEHLER'}] {name}")
    if not cond:
        FEHLER.append(name)
        if detail:
            print(f"        {detail}")


# ── Der echte Testfall ──────────────────────────────────────────────────────

INSERAT = ("BMW 330i Limousine, Hinterradantrieb, Steptronic Automatik. "
           "Zweiter Halter, Fahrzeug aus erster Hand des Autohauses übernommen. "
           "Letzte Wartung laut Verkäufer bei ca. 72.000 km durchgeführt. "
           "Servicehistorie vollständig, HU neu bis 08/2027.")

REQ = KaufCheckRequest(
    marke="BMW", modell="330i", baujahr=2019, kilometerstand=78500,
    motor="330i", kraftstoff="benzin", leistung_ps=258, getriebe="automatik",
    preis_eur=26900, verkaeuferart="haendler",
    servicehistorie="vollstaendig_angegeben", vorbesitzer=2, tuev_bis="08/2027",
    beschreibung=INSERAT,
)

BAUREIHE = {"id": "bmw-3er-g20", "marke": "BMW", "modell": "3er",
            "generation": "G20", "karosserie": ["Limousine"]}
MOTOR = {"bezeichnung": "330i", "motorcode": "B48B20", "kraftstoff": "benzin",
         "leistung_ps": 258, "variante_id": "bmw-330i-g20", "getriebe": ["Automatik"]}


def rueckruf(iid, kurztitel, kba, applicability="series_only"):
    """Ein Rueckruf-Insight wie ihn `build_insights` erzeugt."""
    return Insight(
        id=iid, kategorie="rueckruf", kurztitel=kurztitel,
        titel=f"KBA-Rückruf (Baureihe): {kurztitel}",
        beschreibung=f"Amtlicher Mangeltext zu {kurztitel}.",
        quellen=[{"typ": "rueckruf_kba", "ref": kba}],
        quellen_typen=["rueckruf_kba"], confidence="hoch",
        applicability=applicability, trust="verified",
    )


# Die drei Rueckrufe des echten Laufs.
RUECKRUFE = [
    rueckruf("i1", "Spurstange: Bruchgefahr", "10009"),
    rueckruf("i2", "Gurtschloss: Fehlfunktion", "9839"),
    rueckruf("i3", "Starterrelais: Brandgefahr", "15632R"),
]

AKTIONEN = ka.build_kaufaktionen(
    REQ, BAUREIHE, MOTOR, RUECKRUFE,
    laufleistungskontext=ll.build_laufleistungskontext(REQ, RUECKRUFE, heute_jahr=2026))
FINDINGS = kf.build_key_findings_kauf(REQ, BAUREIHE, MOTOR, RUECKRUFE)
GRUENDE = baue_empfehlung_gruende(REQ, BAUREIHE, MOTOR, RUECKRUFE, FINDINGS,
                                  "kaufen_nach_besichtigung", False, None)


def bereich(name):
    pl = getattr(AKTIONEN, name)
    return pl.fahrzeugspezifisch + pl.basis


def texte(aktionen):
    return "\n".join(f"{a.titel}\n{a.aktion}\n{a.hinweis or ''}" for a in aktionen)


ALLE_BEREICHE = ("besichtigung", "probefahrt", "verkaeuferfragen", "dokumente")
ALLE_AKTIONEN = [a for b in ALLE_BEREICHE for a in bereich(b)]
ALLE_AKTIONSTEXTE = texte(ALLE_AKTIONEN)
FINDING_TEXTE = "\n".join(
    f"{f.titel}\n{f.beschreibung}\n{f.aktion or ''}\n{f.wert or ''}" for f in FINDINGS)
NUTZERTEXT = "\n".join([ALLE_AKTIONSTEXTE, FINDING_TEXTE, "\n".join(GRUENDE)])


# ══ A/B) Rueckruf-Konsistenz in "Kritische Risiken" ═════════════════════════
print("\n[A/B] Rueckrufe bleiben in der Zusammenfassung vollzaehlig")

# Genau der Bericht, den das Modell im echten Lauf geliefert hat: der
# Starterrelais-Rueckruf fehlt, dafuer steht dort ein Softwarethema.
BERICHT_LIVE = """## Fahrzeug erkannt
BMW 3er G20 (2019), Motor B48B20, 258 PS.

## Kaufempfehlung
**KAUFEN NACH BESICHTIGUNG**

## Kritische Risiken
- **Spurstange (KBA-Referenz 10009)**: Bruchgefahr, per FIN prüfen lassen.
- **Gurtschloss (KBA-Referenz 9839)**: Fehlfunktion möglich.
- **Software/Infotainment**: gelegentliche Aussetzer des iDrive bekannt.

## Preis-Einschätzung
Keine belastbare Marktpreisbasis.
"""

neu, ergaenzt = ergaenze_fehlende_rueckrufe(BERICHT_LIVE, RUECKRUFE)
check("A1 Der fehlende Rueckruf wird erkannt",
      ergaenzt == ["Starterrelais: Brandgefahr"], str(ergaenzt))
abschnitt = neu.split("## Kritische Risiken", 1)[1].split("## Preis", 1)[0]
check("B1 Starterrelais steht jetzt in 'Kritische Risiken'",
      "Starterrelais" in abschnitt, abschnitt)
check("B2 Die KBA-Referenz wird mitgenannt", "15632R" in abschnitt)
check("A2 Die beiden bereits genannten Rueckrufe werden NICHT verdoppelt",
      abschnitt.count("Spurstange") == 1 and abschnitt.count("Gurtschloss") == 1,
      abschnitt)
check("A3 Der vom Modell geschriebene Text bleibt unveraendert",
      "gelegentliche Aussetzer des iDrive bekannt" in abschnitt)
check("A4 Andere Abschnitte bleiben unberuehrt",
      "## Fahrzeug erkannt" in neu and "Keine belastbare Marktpreisbasis." in neu)
check("A5 Der ergaenzte Punkt behauptet keine Betroffenheit",
      "nur anhand der FIN klären" in abschnitt and "betroffen ist" in abschnitt)

# Gegenprobe 1: sind alle drei schon da, wird nichts angefasst.
BERICHT_VOLL = BERICHT_LIVE.replace(
    "- **Software/Infotainment**",
    "- **Starterrelais (KBA-Referenz 15632R)**: Brandgefahr.\n- **Software/Infotainment**")
gleich, ergaenzt2 = ergaenze_fehlende_rueckrufe(BERICHT_VOLL, RUECKRUFE)
check("A6 Vollstaendiger Abschnitt bleibt unveraendert",
      gleich == BERICHT_VOLL and ergaenzt2 == [], str(ergaenzt2))

# Gegenprobe 2: fehlt der Abschnitt, wird KEINER erfunden.
OHNE = "## Fahrzeug erkannt\nBMW 3er G20.\n\n## Preis-Einschätzung\nKeine Basis.\n"
gleich2, ergaenzt3 = ergaenze_fehlende_rueckrufe(OHNE, RUECKRUFE)
check("A7 Ohne Abschnitt wird nichts erfunden",
      gleich2 == OHNE and ergaenzt3 == [])

# Gegenprobe 3: Schwachstellen werden NICHT ergaenzt, nur Rueckrufe.
SCHWACH = [Insight(id="s1", kategorie="schwachstelle", titel="Querlenker: Verschleiß",
                   beschreibung="x", confidence="hoch")]
gleich3, ergaenzt4 = ergaenze_fehlende_rueckrufe(BERICHT_LIVE, SCHWACH)
check("A8 Nur Rueckrufe werden ergaenzt, keine Schwachstellen",
      gleich3 == BERICHT_LIVE and ergaenzt4 == [])

# Das LLM sieht nur noch die kanonische Menge und kann keine Rückrufe ergänzen.
check("B3 Prompt bindet die Darstellung an das Canonical Risk Set",
      "Canonical Risk Set sind verbindlich" in kc._SYSTEM)
check("B4 Prompt verbietet neue Rückrufe und Risiken",
      "Keine neuen Risiken, Rückrufe" in kc._SYSTEM)


# ══ C) Vorbesitzer ══════════════════════════════════════════════════════════
print("\n[C] Die Angabe '2 Vorbesitzer' wird konkret verwendet")

# Die Angabe schärft den Basis-Punkt zur Zulassungsbescheinigung, statt eine
# eigene Aktion zu erzeugen. Grund ist gemessen: der Bereich DOKUMENTE ist auf
# MAX_SPEZIFISCH_PRO_BEREICH begrenzt, und bei DIESEM Fahrzeug füllen drei
# Rückrufe, HU-Bericht, Wartungsangabe und Servicehistorie ihn vollständig — die
# frühere Vorbesitzer-Aktion (Rang 340) fiel als letzte heraus. Genau deshalb
# fehlte sie im echten Report. Der Basis-Katalog kennt diese Grenze nicht.
vb = [a for a in bereich("dokumente") if a.id.endswith("zb2")]
check("C1 Der Teil-II-Punkt ist da und traegt die Angabe", len(vb) == 1,
      str([a.id for a in bereich("dokumente")]))
vb_text = f"{vb[0].titel} {vb[0].aktion}" if vb else ""
check("C2 Die konkrete Zahl steht darin", "2 Vorbesitzer" in vb_text, vb_text)
check("C3 Sie wird gegen die Papiere plausibilisiert",
      "Vorhalter" in vb_text and "Teil II" in vb_text
      and "nach dem Grund" in vb_text, vb_text)
check("C3b Die Angabe ueberlebt auch bei drei Rueckrufen",
      "2 Vorbesitzer" in texte(bereich("dokumente")))
check("C4 Keine Wertung der Vorbesitzerzahl",
      not any(w in vb_text.lower() for w in ("wenig", "viele", "gut", "schlecht",
                                             "vorteilhaft", "ungewöhnlich hoch")),
      vb_text)
check("C5 Der aktuelle Inseratskontext enthält die Vorbesitzerangabe",
      "Vorbesitzer:    2" in kc._format_inserat(REQ))
# Ohne Angabe bleibt es bei der offenen Frage und beim neutralen Basistext.
ohne_vb = ka.build_kaufaktionen(
    KaufCheckRequest(marke="BMW", modell="330i", baujahr=2019), BAUREIHE, MOTOR, [])
check("C6 Ohne Angabe weiterhin die offene Frage",
      any(a.id.endswith("vorbesitzer") for a in ohne_vb.verkaeuferfragen.fahrzeugspezifisch))
_ohne_text = texte(ohne_vb.dokumente.fahrzeugspezifisch + ohne_vb.dokumente.basis)
check("C7 Ohne Angabe keine erfundene Zahl im Teil-II-Punkt",
      "Vorbesitzer an" not in _ohne_text and "Teil II nennt den letzten Halter" in _ohne_text,
      _ohne_text[:300])


# ══ D/E) Wartungsangabe aus dem Inserat ═════════════════════════════════════
print("\n[D/E] Die 72.000-km-Angabe wird erkannt und nicht erneut erfragt")

angabe = wa.aus_request(REQ)
check("D1 Die Angabe wird extrahiert", angabe is not None and angabe.km == 72000,
      str(angabe))
check("D2 Kein Datum erfunden", angabe is not None and angabe.datum is None)

wartung_aktionen = [a for a in ALLE_AKTIONEN if "wartung-inserat" in a.id]
check("D3 Sie erzeugt konkrete Punkte", len(wartung_aktionen) == 2,
      str([a.id for a in wartung_aktionen]))
w_text = texte(wartung_aktionen)
check("D4 Die Zahl steht im Text", "72.000 km" in w_text, w_text[:200])
check("D5 Sie bleibt eine ANGABE",
      "Im Inserat wird eine letzte Wartung" in w_text
      and "belegt nicht, dass die Arbeiten ausgeführt wurden" in w_text, w_text[:300])

fragen = texte(bereich("verkaeuferfragen"))
check("E1 'Wann war die letzte Wartung' wird NICHT mehr gefragt",
      "Wann war die letzte Wartung" not in fragen, fragen[:400])
check("E2 Stattdessen wird nach Umfang und Beleg gefragt",
      "Was umfasste die im Inserat genannte Wartung" in fragen)

# Gegenprobe: ohne konkrete Angabe bleibt die allgemeine Frage stehen.
REQ_VAGE = KaufCheckRequest(marke="BMW", modell="330i", baujahr=2019,
                            kilometerstand=78500,
                            beschreibung="Gepflegtes Fahrzeug, regelmäßig gewartet.")
vage = ka.build_kaufaktionen(REQ_VAGE, BAUREIHE, MOTOR, [])
vage_fragen = texte(vage.verkaeuferfragen.fahrzeugspezifisch + vage.verkaeuferfragen.basis)
check("E3 Ohne konkrete Angabe bleibt die allgemeine Frage",
      "Wann war die letzte Wartung" in vage_fragen)
check("E4 'regelmäßig gewartet' erfindet keine Zahl", wa.aus_request(REQ_VAGE) is None)

# Der Laufleistungs-Prompt darf die Angabe wiedergeben, aber keine Faelligkeit.
ctx = ll.build_laufleistungskontext(REQ, RUECKRUFE, heute_jahr=2026)
block = ll.prompt_block(ctx)
check("D6 Der Kontext traegt die Angabe",
      ctx is not None and ctx.letzte_wartung_angabe == "bei rund 72.000 km",
      str(getattr(ctx, "letzte_wartung_angabe", None)))
check("D7 Der Prompt nennt sie statt sie zu leugnen",
      "Das Inserat gibt eine letzte Wartung bei rund 72.000 km an" in block, block[:400])
check("D8 Die Faelligkeits-Verbote bleiben",
      "NIEMALS, ein Service sei fällig" in block)
check("D9 letzter_service_bekannt bleibt False", ctx.letzter_service_bekannt is False)
check("D10 Der Prompt fuehrt die Angabe im Inseratsblock",
      "Letzte Wartung laut Inserat: bei rund 72.000 km" in kc._format_inserat(REQ))
check("D11 Bekannte Wartung bleibt im Kontext und wird nicht erneut erfragt",
      "Letzte Wartung laut Inserat: bei rund 72.000 km" in kc._format_inserat(REQ)
      and "Wann war die letzte Wartung" not in fragen)

# Widerspruch: Wartung oberhalb des Tachostands wird gemeldet, nicht geglaettet.
REQ_WIDER = KaufCheckRequest(
    marke="BMW", modell="330i", baujahr=2019, kilometerstand=60000,
    beschreibung="Letzte Wartung bei ca. 72.000 km durchgeführt.")
wider = ka.build_kaufaktionen(REQ_WIDER, BAUREIHE, MOTOR, [])
wider_text = texte(wider.verkaeuferfragen.fahrzeugspezifisch)
check("D12 Wartung ueber dem Tachostand wird als Widerspruch gemeldet",
      "Wie passt die genannte Wartung zum angegebenen Kilometerstand?" in wider_text,
      wider_text[:300])
check("D13 Der Widerspruch wird nicht still aufgeloest",
      "Eine der beiden Angaben stimmt nicht" in wider_text)


# ══ F/G) Zulassungsbescheinigung und Verkaeuferrollen ══════════════════════
print("\n[F/G] Teil II ist kein Eigentumsnachweis")

check("F1 Keine Eigentumsnachweis-Behauptung",
      "weist das Eigentum nach" not in NUTZERTEXT, "")
EIGENTUM_VERBOTEN = (
    "fahrzeugbrief weist das eigentum",
    "teil ii beweist",
    "belegt das eigentum",
    "eigentumsnachweis ist teil ii",
)
treffer_f = [w for w in EIGENTUM_VERBOTEN if w in NUTZERTEXT.lower()]
check("F2 Keine Variante der Eigentumsbehauptung", not treffer_f, str(treffer_f))
dok = texte(bereich("dokumente"))
check("F3 Teil II wird ausdruecklich als KEIN Eigentumsnachweis benannt",
      "Ein Eigentumsnachweis ist das Dokument nicht" in dok, dok[:400])

check("G1 Halter wird nicht mit dem Verkaeufer gleichgesetzt",
      "ist der eingetragene Halter der Verkäufer" not in NUTZERTEXT)
check("G2 Keine Behauptung, der Verkaeufer stehe in Teil II",
      "steht der Verkäufer weiterhin in Teil II" not in NUTZERTEXT)
fragen_alle = texte(bereich("verkaeuferfragen"))
check("G3 Haendler: Vertragspartner wird erfragt",
      "wer der Vertragspartner ist" in fragen_alle, fragen_alle[:400])
check("G4 Haendler: eigener Name oder Auftrag wird erfragt",
      "im eigenen Namen oder im Auftrag" in fragen_alle)
check("G5 Haendler: Vollmacht bei Auftragsverkauf",
      "Vollmacht" in fragen_alle or "Verkaufsberechtigung" in fragen_alle)
check("G6 Die vier Rollen werden ausdruecklich getrennt",
      "Diese Rollen müssen nicht dieselbe Person sein" in dok, dok[:500])
RECHTSBEGRIFFE = ("gewährleistung", "sachmängelhaftung", "garantie", "widerruf",
                  "haftungsausschluss", "rücktritt", "verbrauchsgüterkauf")
treffer_g = [w for w in RECHTSBEGRIFFE if w in NUTZERTEXT.lower()]
check("G7 Keine Rechtsberatung als Ersatz", not treffer_g, str(treffer_g))


# ══ H/I) Schreibstil ════════════════════════════════════════════════════════
print("\n[H/I] Gedankenstriche und Satzanfaenge")

# Rhetorisch = von Leerzeichen umgeben. "2019–2021" und "E-Mail" bleiben erlaubt.
RE_RHETORISCH = re.compile(r"(?:\s)[—–](?:\s)")


def dash_treffer(quelle: str, text: str):
    return [(quelle, m.group(0).join(("…", "…")), text[max(0, m.start() - 55):m.start() + 55])
            for m in RE_RHETORISCH.finditer(text or "")]


treffer_h = []
for a in ALLE_AKTIONEN:
    for feld in (a.titel, a.aktion, a.hinweis, a.gruppe):
        treffer_h += dash_treffer(a.id, feld or "")
for f in FINDINGS:
    for feld in (f.titel, f.beschreibung, f.aktion, f.wert):
        treffer_h += dash_treffer(f.titel, feld or "")
for g in GRUENDE:
    treffer_h += dash_treffer("empfehlungsgrund", g)
check("H1 Kein rhetorischer Gedankenstrich in Checkliste, Findings, Gruenden",
      not treffer_h, "\n        ".join(t[2] for t in treffer_h[:4]))

# Auch der gesamte Basis-Katalog, unabhaengig von diesem einen Fahrzeug.
treffer_h2 = []
for katalog, name in ((pb.BASIS_BESICHTIGUNG, "besichtigung"),
                      (pb.BASIS_PROBEFAHRT, "probefahrt"),
                      (pb.BASIS_VERKAEUFERFRAGEN, "verkaeuferfragen"),
                      (pb.BASIS_DOKUMENTE, "dokumente")):
    for eintrag in katalog:
        for feld in eintrag[1:5]:
            if isinstance(feld, str):
                treffer_h2 += dash_treffer(name, feld)
check("H2 Basis-Katalog frei von rhetorischen Gedankenstrichen",
      not treffer_h2, "\n        ".join(t[2] for t in treffer_h2[:4]))

# Und die verkaeufer-/getriebeabhaengigen Ersatztexte, in ALLEN Varianten.
treffer_h3 = []
for schluessel, varianten in list(ka._BASIS_VERKAEUFER.items()) + list(ka._BASIS_GETRIEBE.items()):
    for variante, text in varianten.items():
        treffer_h3 += dash_treffer(f"{schluessel}/{variante}", text)
check("H3 Auch die Ersatztexte sind frei davon",
      not treffer_h3, "\n        ".join(t[2] for t in treffer_h3[:4]))

# ". kleingeschrieben" — der Grammatikfehler aus dem echten Report.
#
# Deutsche Abkürzungen enden ebenfalls auf einen Punkt und stehen völlig korrekt
# vor einem Kleinbuchstaben ("Serviceheft bzw. digitales Serviceprotokoll").
# Sie sind KEINE Satzgrenze. Ohne diese Ausnahme meldet der Scanner lauter
# richtige Sätze, und der echte Befund geht im Rauschen unter.
ABKUERZUNGEN = {"bzw", "ca", "z", "b", "d", "h", "u", "a", "ggf", "ggfs", "evtl",
                "inkl", "exkl", "zzgl", "max", "min", "usw", "etc", "nr", "abs",
                "vgl", "sog", "bspw", "mind", "tsd", "mio", "ggü", "lt"}
RE_KLEIN = re.compile(r"[.!?]\s+[a-zäöüß]")
RE_WORT_DAVOR = re.compile(r"([A-Za-zÄÖÜäöüß]+)$")


def echte_satzgrenze(text: str, pos: int) -> bool:
    """True, wenn an `pos` wirklich ein Satz endet (und keine Abkürzung steht)."""
    davor = RE_WORT_DAVOR.search(text[:pos])
    return not (davor and davor.group(1).lower() in ABKUERZUNGEN)


treffer_i = []
for quelle, text in ([(a.id, f"{a.titel}\n{a.aktion}\n{a.hinweis or ''}") for a in ALLE_AKTIONEN]
                     + [(f.titel, f"{f.beschreibung}\n{f.aktion or ''}") for f in FINDINGS]
                     + [("empfehlungsgrund", g) for g in GRUENDE]):
    for m in RE_KLEIN.finditer(text):
        # Ein Zeilenumbruch trennt hier zwei Felder, keinen Satz.
        if "\n" in m.group(0):
            continue
        if not echte_satzgrenze(text, m.start()):
            continue
        treffer_i.append((quelle, text[max(0, m.start() - 45):m.start() + 45]))
check("I1 Kein kleingeschriebener Satzanfang", not treffer_i,
      "\n        ".join(t[1] for t in treffer_i[:4]))
check("I2 Unbekannte Unfallhistorie wird ausdrücklich als offen benannt",
      any("Unfallhistorie nicht vollständig bekannt" in g for g in GRUENDE), str(GRUENDE))
# Gegenprobe: der Scanner darf nicht einfach immer grün sein.
_fehlerprobe = "Zustand und Wartung sind Inseratsangaben. sie lassen sich prüfen."
check("I3 Der Scanner schlaegt beim echten Fehlermuster an",
      any(echte_satzgrenze(_fehlerprobe, m.start())
          for m in RE_KLEIN.finditer(_fehlerprobe)))
_abkprobe = "Serviceheft bzw. digitales Serviceprotokoll durchsehen."
check("I4 Der Scanner ignoriert deutsche Abkuerzungen",
      not any(echte_satzgrenze(_abkprobe, m.start())
              for m in RE_KLEIN.finditer(_abkprobe)))


# ══ J) Servicehistorie bleibt eine Angabe ══════════════════════════════════
print("\n[J] Servicehistorie ist kein 'Pluspunkt'")

sh_findings = [f for f in FINDINGS if "servicehistorie" in f.titel.lower()]
check("J1 Das Finding existiert", len(sh_findings) == 1, str([f.titel for f in FINDINGS]))
if sh_findings:
    f = sh_findings[0]
    check("J2 Eigene Kategorie fuer ungepruefte Inseratsangaben",
          f.kategorie == "inseratangabe", f.kategorie)
    check("J3 Der kanonische Satz bleibt",
          "Laut Inserat wird eine vollständige Servicehistorie angegeben." in f.beschreibung,
          f.beschreibung)
    check("J4 Die Pruefaufforderung bleibt",
          "Belege und" in f.beschreibung and "prüfen" in f.beschreibung)
check("J5 Keine Verstaerkung zur Tatsache",
      "lückenlos" not in NUTZERTEXT.lower()
      and "nachweislich vollständig" not in NUTZERTEXT.lower())


# ══ K/L) Was richtig war, bleibt richtig ═══════════════════════════════════
print("\n[K/L] Keine Regression bei Getriebe und Preis")

probe = texte(bereich("probefahrt"))
check("K1 Automatik: Anfahrverhalten", "Automatikgetriebe soll ohne" in probe)
check("K2 Automatik: Fahrstufe R", "Fahrstufe R mehrfach einlegen" in probe)
check("K3 Automatik: alle Fahrstufen", "Alle Fahrstufen durchfahren" in probe)
check("K4 Automatik: durchrutschendes Getriebe", "durchrutschendes" in probe)
check("K5 Keine Kupplungspruefung bei Automatik", "Kupplung" not in probe, probe[:200])

pa = bewerte_preis(None, REQ.preis_eur, check_typ="kauf")
check("L1 Ohne Marktdaten kein Preisurteil", pa.verdict == "unbekannt", pa.verdict)
check("L2 Kein Median erfunden", pa.median_eur is None)
check("L3 Der Angebotspreis wird nicht zum Marktwert",
      getattr(pa, "difference_eur", None) is None)


# ══ M) FIN-Copy ════════════════════════════════════════════════════════════
print("\n[M] FIN-Copy verspricht keine individuelle KBA-Abfrage")

from app.fin_hinweis import HINWEIS_FIN   # noqa: E402
import app.recall_filter as rfilt          # noqa: E402

check("M1 Keine 'FIN beim Hersteller oder KBA'-Aufforderung mehr",
      "Hersteller oder KBA" not in NUTZERTEXT and "Hersteller/KBA" not in NUTZERTEXT,
      NUTZERTEXT[:200])
check("M2 Der kanonische Hinweis nennt Hersteller bzw. Vertragswerkstatt",
      "Vertragswerkstatt der Marke" in HINWEIS_FIN, HINWEIS_FIN)
check("M3 recall_filter nutzt denselben Hinweis", rfilt._HINWEIS_FIN == HINWEIS_FIN)
check("M4 Die KBA-Referenz bleibt als Quelle sichtbar",
      "KBA-Referenz" in ALLE_AKTIONSTEXTE, ALLE_AKTIONSTEXTE[:200])
check("M5 Kein Markenname hartkodiert",
      "BMW Partner" not in HINWEIS_FIN and "BMW" not in HINWEIS_FIN)


# ══ Ergebnis ═══════════════════════════════════════════════════════════════
print("\n" + "=" * 62)
if FEHLER:
    print(f"{len(FEHLER)} FEHLER:")
    for f in FEHLER:
        print(f"  - {f}")
    raise SystemExit(1)
print("Alle Pruefungen bestanden.")
