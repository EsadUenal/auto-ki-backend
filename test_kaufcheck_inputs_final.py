"""
Test: Getriebe, Verkäuferart und Servicehistorie wirken END-TO-END.

Der Auftrag verlangt ausdruecklich, dass ein neues Feld nicht dekorativ sein
darf. "Der String steht irgendwo im Prompt" gilt deshalb NICHT als Nachweis.
Geprueft wird die fachliche WIRKUNG:

  A) BMW 330i G20, Automatik, Haendler, vollstaendige Historie laut Inserat
     -> Probefahrt prueft Fahrstufen statt Kupplung
     -> Dokumente pruefen Firmendaten statt Ausweis
     -> Servicehistorie erzeugt eine BELEG-Aktion, keinen Risiko-Abzug
  B) VW Golf GTI, Automatik, privat, teilweise Historie
     -> andere Dokumenten-Aktion, andere Verkaeuferfrage
  C) Schaltgetriebe, privat, keine Historie
     -> Probefahrt prueft Kupplung
     -> der Laufleistungs-Prompt darf die Inseratsangabe wiedergeben
  D) alles "Nicht angegeben" -> neutrale Katalogtexte, offene Fragen
  E) Widerspruch: Auswahl Automatik vs. "6-Gang Handschaltung" im Inserat
  F) Widerspruch: Auswahl Benzin vs. "Diesel" im Inserat (Auswahl bleibt hart)
  G) Legacy: gespeicherter Check mit `scheckheftgepflegt=True`
  H) vollstaendig ausgefuelltes Formular
  I) Minimalangaben
  J) Claim-Safety: Zusicherungen werden entschaerft, Aufforderungen NICHT
  K) Verkaeuferart aendert KEINE technische Aussage und keine Rechtsaussage
  L) Getriebe schaerft die Variantenauflösung NICHT (gemessen, kein Fake)

Ohne Netzwerk, ohne Provider, ohne DB-Schreibzugriff auf die Live-Datenbank.

Ausfuehren:  python test_kaufcheck_inputs_final.py
"""
import os
import tempfile

_TMP = tempfile.mkdtemp(prefix="enfal_kcin_")
os.environ["AUTO_KI_DB_PATH"] = os.path.join(_TMP, "test.db")

import app.database as db            # noqa: E402
db.ensure_tables()

from app.models import KaufCheckRequest          # noqa: E402
import app.getriebe as gt                        # noqa: E402
import app.servicehistorie as sh                 # noqa: E402
import app.verkaeuferart as vk                   # noqa: E402
import app.kaufaktionen as ka                    # noqa: E402
import app.key_findings as kf                    # noqa: E402
import app.kaufcheck as kc                       # noqa: E402
import app.laufleistung as ll                    # noqa: E402
import app.marktvergleich as mv                  # noqa: E402
from app.empfehlung_gruende import baue_empfehlung_gruende   # noqa: E402

FEHLER = []


def check(name, cond, detail=""):
    print(f"[{'OK' if cond else 'FEHLER'}] {name}")
    if not cond:
        FEHLER.append(name)
        if detail:
            print(f"        {detail}")


# Baureihe ohne verifizierte Motorvarianten: jede harte Wirkung haengt damit an
# der Nutzerangabe, nicht an ungeprueften DB-Daten.
BAUREIHE = {"id": "bmw-3er-g20", "marke": "BMW", "modell": "3er",
            "generation": "G20", "karosserie": ["Limousine"]}
# Eine Variante, die AUSSCHLIESSLICH Automatik anbietet — Grundlage fuer den
# DB-Widerspruch in E2.
MOTOR_AUTO_ONLY = {"bezeichnung": "330i", "kraftstoff": "benzin", "leistung_ps": 258,
                   "variante_id": "bmw-330i-g20", "getriebe": ["Automatik"]}


def aktionen(req, baureihe=BAUREIHE, motor=None, kontext=None):
    return ka.build_kaufaktionen(req, baureihe, motor, [], laufleistungskontext=kontext)


def texte(liste):
    """Titel + Aktionstext aller Punkte eines Bereichs, als ein Suchtext."""
    return "\n".join(f"{a.titel}\n{a.aktion}" for a in liste)


def bereich_text(akt, bereich):
    pl = getattr(akt, bereich)
    return texte(pl.fahrzeugspezifisch) + "\n" + texte(pl.basis)


# ══ A) BMW 330i G20 — Automatik, Haendler, vollstaendige Historie ═════════════
print("\n[A] BMW 330i G20 2019, Benzin, 258 PS, Automatik, Haendler, Historie vollstaendig")

req_a = KaufCheckRequest(
    marke="BMW", modell="330i", baujahr=2019, kilometerstand=68000,
    motor="330i", kraftstoff="benzin", leistung_ps=258, preis_eur=29900,
    getriebe="automatik", verkaeuferart="haendler",
    servicehistorie="vollstaendig_angegeben",
)

check("A1 Getriebe kommt kanonisch an", gt.aus_request(req_a) == gt.AUTOMATIK)
check("A2 Verkaeuferart kommt kanonisch an", vk.aus_request(req_a) == vk.HAENDLER)
check("A3 Servicehistorie kommt kanonisch an",
      sh.status(req_a) == sh.VOLLSTAENDIG_ANGEGEBEN)

akt_a = aktionen(req_a)
probe_a = bereich_text(akt_a, "probefahrt")
# Die eigentliche fachliche Wirkung: Automatik-Prueftexte statt Kupplungstexte.
check("A4 Probefahrt nennt Fahrstufen/Automatikgetriebe", "Automatikgetriebe" in probe_a)
check("A5 Probefahrt verlangt KEINE Kupplungspruefung",
      "Kupplung" not in probe_a, probe_a[:300])
check("A6 Probefahrt verlangt keinen Greifpunkt", "Greifpunkt" not in probe_a)

dok_a = bereich_text(akt_a, "dokumente")
check("A7 Dokumente pruefen Firmendaten des Haendlers",
      "Firmenname und Anschrift" in dok_a, dok_a[:300])
check("A8 Haendler-Zweig nennt KEINE Ausweis-Vollmacht-Formel",
      "schriftliche Vollmacht verlangen" not in dok_a)
check("A9 Servicehistorie erzeugt eine Beleg-Aktion",
      "Angegebene Servicehistorie belegen lassen" in dok_a)
check("A10 Beleg-Aktion behauptet keine Vollstaendigkeit",
      "lückenlos" not in dok_a.lower())

kfs_a = kf.build_key_findings_kauf(req_a, BAUREIHE, None, [])
kft_a = "\n".join(f"{f.titel}\n{f.beschreibung}" for f in kfs_a)
check("A11 Key Finding zur Servicehistorie ist als Angabe formuliert",
      "Laut Inserat wird eine vollständige Servicehistorie angegeben." in kft_a, kft_a[:400])
check("A12 Key Finding verstaerkt die Angabe nicht",
      "lückenlos" not in kft_a.lower() and "nachweislich vollständig" not in kft_a.lower())

prompt_a = kc._format_inserat(req_a)
check("A13 Prompt nennt Getriebe", "Getriebe:       Automatik" in prompt_a, prompt_a)
check("A14 Prompt nennt Verkaeufer als Inseratsangabe",
      "Verkäufer:      Händler (laut Inserat)" in prompt_a)
check("A15 Prompt nennt Servicehistorie als Inseratsangabe",
      "Servicehistorie: vollständig angegeben (laut Inserat)" in prompt_a)

gruende_a = baue_empfehlung_gruende(req_a, BAUREIHE, None, [], kfs_a,
                                    "kaufen_nach_besichtigung", False, None)
check("A16 Empfehlungsgrund nutzt denselben kanonischen Satz",
      any("Laut Inserat wird eine vollständige Servicehistorie angegeben." in g
          for g in gruende_a), str(gruende_a))


# ══ B) VW Golf VII GTI — Automatik, privat, teilweise ════════════════════════
print("\n[B] VW Golf VII GTI 2018, Benzin, 230 PS, Automatik, privat, Historie teilweise")

req_b = KaufCheckRequest(
    marke="VW", modell="Golf GTI", baujahr=2018, kilometerstand=95000,
    motor="2.0 TSI", kraftstoff="benzin", leistung_ps=230, preis_eur=21500,
    getriebe="automatik", verkaeuferart="privat", servicehistorie="teilweise",
)

akt_b = aktionen(req_b, baureihe=None)
dok_b = bereich_text(akt_b, "dokumente")
frage_b = bereich_text(akt_b, "verkaeuferfragen")
check("B1 Teilweise erzeugt eine Luecken-Aktion",
      "Fehlende Zeiträume der Servicehistorie klären" in dok_b, dok_b[:300])
check("B2 Teilweise behauptet keinen Mangel",
      "nicht gewartet" not in dok_b.lower() and "versäumt" not in dok_b.lower())
check("B3 Privat-Zweig verlangt Ausweis/Vollmacht",
      "schriftliche Vollmacht verlangen" in dok_b, dok_b[:400])
check("B4 Privat-Zweig nennt KEINE Firmendaten",
      "Firmenname und Anschrift" not in dok_b)
check("B5 Privat-Frage bleibt bei Halter/Auftrag",
      "bei Verkauf im Auftrag Vollmacht" in frage_b, frage_b[:300])
check("B6 Privat-Frage nennt keine Vermittlung",
      "Vermittlung" not in frage_b)

kfs_b = kf.build_key_findings_kauf(req_b, None, None, [])
kft_b = "\n".join(f"{f.titel}\n{f.beschreibung}\n{f.aktion or ''}" for f in kfs_b)
check("B7 Key Finding meldet die Teil-Historie als Pruefpunkt",
      "nur teilweise vorhanden" in kft_b, kft_b[:400])
check("B8 Teil-Historie ist KEIN 'vorteil'",
      not any(f.kategorie == "vorteil" and "Servicehistorie" in f.titel for f in kfs_b))


# ══ C) Schaltgetriebe, privat, keine Servicehistorie ════════════════════════
print("\n[C] Opel Astra, Schaltgetriebe, privat, keine Servicehistorie")

req_c = KaufCheckRequest(
    marke="Opel", modell="Astra", baujahr=2015, kilometerstand=142000,
    motor="1.4 Turbo", kraftstoff="benzin", leistung_ps=140, preis_eur=7900,
    getriebe="manuell", verkaeuferart="privat", servicehistorie="nicht_vorhanden",
)

akt_c = aktionen(req_c, baureihe=None)
probe_c = bereich_text(akt_c, "probefahrt")
check("C1 Probefahrt prueft die Kupplung", "Kupplung" in probe_c, probe_c[:300])
check("C2 Probefahrt nennt kein Automatikgetriebe", "Automatikgetriebe" not in probe_c)
check("C3 Probefahrt nennt den Rueckwaertsgang ohne Kratzen",
      "ohne Kratzen" in probe_c)

dok_c = bereich_text(akt_c, "dokumente")
check("C4 Keine Historie erzeugt eine Einschaetzungs-Aktion",
      "Wartung ohne Serviceunterlagen einschätzen" in dok_c, dok_c[:300])
check("C5 Keine Historie wird als Unsicherheit, nicht als Mangel formuliert",
      "nicht nachvollziehbar" in dok_c)

kfs_c = kf.build_key_findings_kauf(req_c, None, None, [])
kft_c = "\n".join(f"{f.titel}\n{f.beschreibung}" for f in kfs_c)
check("C6 Key Finding nennt die Unsicherheit ausdruecklich",
      "Unsicherheit, kein festgestellter Mangel" in kft_c, kft_c[:400])

# Der Laufleistungs-Prompt darf die Angabe wiedergeben — aber keine Faelligkeit.
ctx_c = ll.build_laufleistungskontext(req_c, [], heute_jahr=2026)
block_c = ll.prompt_block(ctx_c)
check("C7 Laufleistungskontext traegt die Servicehistorie",
      ctx_c is not None and ctx_c.servicehistorie == sh.NICHT_VORHANDEN)
check("C8 Prompt erlaubt die Wiedergabe der Inseratsangabe",
      "Das Inserat gibt allerdings an" in block_c, block_c[:600])
check("C9 Prompt verbietet weiterhin jede Faelligkeit",
      "NIEMALS, ein Service sei fällig" in block_c)
check("C10 letzter_service_bekannt bleibt False",
      ctx_c.letzter_service_bekannt is False)

# Gegenprobe: ohne die Angabe bleibt das pauschale Verbot stehen.
ctx_c0 = ll.build_laufleistungskontext(
    KaufCheckRequest(marke="Opel", modell="Astra", baujahr=2015, kilometerstand=142000),
    [], heute_jahr=2026)
block_c0 = ll.prompt_block(ctx_c0)
check("C11 Ohne Angabe bleibt 'behaupte nie, die Servicehistorie fehle'",
      "behaupte nie, die Servicehistorie fehle" in block_c0)
check("C12 Ohne Angabe keine Erlaubnis zur Wiedergabe",
      "Das Inserat gibt allerdings an" not in block_c0)


# ══ D) Alles "Nicht angegeben" ══════════════════════════════════════════════
print("\n[D] Alle neuen Felder 'Nicht angegeben'")

req_d = KaufCheckRequest(marke="Audi", modell="A4", baujahr=2017,
                         kilometerstand=110000, preis_eur=15900)

check("D1 Getriebe bleibt unbekannt", gt.aus_request(req_d) is None)
check("D2 Verkaeuferart bleibt unbekannt", vk.aus_request(req_d) is None)
check("D3 Servicehistorie bleibt unbekannt", sh.status(req_d) is None)

akt_d = aktionen(req_d, baureihe=None)
probe_d = bereich_text(akt_d, "probefahrt")
check("D4 Neutraler Katalogtext: Kupplung UND Automatik kommen vor",
      "Kupplung" in probe_d and "Automatik" in probe_d, probe_d[:400])
frage_d = bereich_text(akt_d, "verkaeuferfragen")
check("D5 Offene Frage zur Wartungshistorie",
      "durchgehend geführtes Scheckheft" in frage_d, frage_d[:300])
dok_d = bereich_text(akt_d, "dokumente")
check("D6 Kein Haendler- und kein Privat-Sondertext",
      "Firmenname und Anschrift" not in dok_d
      and "Auch beim Privatkauf" not in dok_d)
check("D7 Prompt nennt keine der drei Angaben",
      "Getriebe:" not in kc._format_inserat(req_d)
      and "Verkäufer:" not in kc._format_inserat(req_d)
      and "Servicehistorie:" not in kc._format_inserat(req_d))

kfs_d = kf.build_key_findings_kauf(req_d, None, None, [])
check("D8 Keine Angabe erzeugt kein Servicehistorie-Finding",
      not any("Servicehistorie" in f.titel or "Servicehistorie" in (f.beschreibung or "")
              for f in kfs_d))


# ══ E) Widerspruch Getriebe ═════════════════════════════════════════════════
print("\n[E] Widerspruch: Auswahl Automatik, Inserat '6-Gang Handschaltung'")

req_e = KaufCheckRequest(
    marke="BMW", modell="320i", baujahr=2019, kilometerstand=70000,
    getriebe="automatik", preis_eur=24000,
    beschreibung="Sehr gepflegter 320i mit 6-Gang Handschaltung, Sportsitze.",
)
kfs_e = kf.build_key_findings_kauf(req_e, None, None, [])
wid_e = [f for f in kfs_e if f.kategorie == "widerspruch"]
check("E1 Der Widerspruch wird gemeldet",
      any("Getriebe-Angabe widerspricht dem Inseratstext" == f.titel for f in wid_e),
      str([f.titel for f in kfs_e]))
wid_txt_e = "\n".join(f"{f.beschreibung}\n{f.wert or ''}" for f in wid_e)
check("E2 Beide Seiten werden genannt",
      "Automatik" in wid_txt_e and "Schaltgetriebe" in wid_txt_e, wid_txt_e)
check("E3 Der Widerspruch wird nicht still aufgeloest",
      "folgen der Auswahl im Formular" in wid_txt_e)
# Die strukturierte Angabe bleibt die harte Eingabe — sonst waere die Meldung
# folgenlos und das Formular eine Attrappe.
check("E4 Die Auswahl bleibt wirksam", gt.aus_request(req_e) == gt.AUTOMATIK)
probe_e = bereich_text(aktionen(req_e, baureihe=None), "probefahrt")
check("E5 Probefahrt folgt der Auswahl (Automatik)",
      "Automatikgetriebe" in probe_e and "Kupplung" not in probe_e)

# E2b: Widerspruch gegen die erkannte Motorvariante (nur bei eindeutiger DB-Lage).
req_e2 = KaufCheckRequest(marke="BMW", modell="330i", baujahr=2019, getriebe="manuell")
kfs_e2 = kf.build_key_findings_kauf(req_e2, BAUREIHE, MOTOR_AUTO_ONLY, [])
check("E6 Widerspruch gegen eindeutige DB-Getriebelage wird gemeldet",
      any("Getriebeart passt nicht zur erkannten Motorisierung" == f.titel for f in kfs_e2),
      str([f.titel for f in kfs_e2]))
# Gegenprobe: bietet die Variante beide Arten an, gibt es KEIN Finding.
MOTOR_BEIDE = {**MOTOR_AUTO_ONLY, "getriebe": ["Manuell", "Automatik"]}
kfs_e3 = kf.build_key_findings_kauf(req_e2, BAUREIHE, MOTOR_BEIDE, [])
check("E7 Mehrdeutige DB-Getriebelage erzeugt KEIN Finding",
      not any("Getriebeart passt nicht" in f.titel for f in kfs_e3))


# ══ F) Widerspruch Kraftstoff ═══════════════════════════════════════════════
print("\n[F] Widerspruch: Auswahl Benzin, Inserat 'Diesel'")

req_f = KaufCheckRequest(
    marke="BMW", modell="320", baujahr=2019, kilometerstand=90000, preis_eur=22000,
    kraftstoff="benzin",
    beschreibung="BMW 320d Diesel, sparsam, ideal für Vielfahrer.",
)
kfs_f = kf.build_key_findings_kauf(req_f, None, None, [])
check("F1 Der Kraftstoff-Widerspruch wird gemeldet",
      any("Kraftstoff-Angabe widerspricht dem Inseratstext" == f.titel for f in kfs_f),
      str([f.titel for f in kfs_f]))
wid_txt_f = "\n".join(f"{f.beschreibung}\n{f.wert or ''}" for f in kfs_f
                      if f.kategorie == "widerspruch")
check("F2 Beide Seiten werden genannt",
      "Benzin" in wid_txt_f and "Diesel" in wid_txt_f, wid_txt_f)
# Die Auswahl bleibt hart (Regression zum Kraftstoff-Feld aus dem Vorpass).
ziel_f = mv.baue_ziel(None, None, req_f, alle_baureihen=[], alle_motorvarianten=[])
check("F3 Marktvergleich folgt weiterhin hart der Auswahl",
      ziel_f.get("kraftstoff_hart") is True and "benzin" in str(ziel_f.get("kraftstoff", "")).lower(),
      str({k: ziel_f.get(k) for k in ("kraftstoff", "kraftstoff_hart")}))
# Gegenprobe: ohne Widerspruch entsteht auch kein Finding.
req_f2 = KaufCheckRequest(marke="BMW", modell="320i", baujahr=2019, kraftstoff="benzin",
                          beschreibung="BMW 320i Benziner, gepflegt.")
check("F4 Ohne Widerspruch kein Finding",
      not any("widerspricht dem Inseratstext" in f.titel
              for f in kf.build_key_findings_kauf(req_f2, None, None, [])))


# ══ G) Legacy: gespeicherter Check mit der alten Checkbox ═══════════════════
print("\n[G] Legacy: gespeicherter KaufCheck ohne die neuen Felder")

req_g = KaufCheckRequest(marke="VW", modell="Golf", baujahr=2016,
                         kilometerstand=120000, preis_eur=11000,
                         scheckheftgepflegt=True)
check("G1 Alte Checkbox wird auf 'vollstaendig angegeben' abgebildet",
      sh.status(req_g) == sh.VOLLSTAENDIG_ANGEGEBEN)
check("G2 Prompt zeigt die abgebildete Angabe",
      "Servicehistorie: vollständig angegeben (laut Inserat)" in kc._format_inserat(req_g))
dok_g = bereich_text(aktionen(req_g, baureihe=None), "dokumente")
check("G3 Legacy erzeugt dieselbe Beleg-Aktion",
      "Angegebene Servicehistorie belegen lassen" in dok_g)

# `False` und "fehlt" waren in der alten Oberflaeche nicht unterscheidbar
# (das Frontend sendete beides als fehlendes Feld) — deshalb "keine Angabe".
req_g2 = KaufCheckRequest(marke="VW", modell="Golf", baujahr=2016,
                          scheckheftgepflegt=False)
check("G4 Legacy False bleibt 'keine Angabe'", sh.status(req_g2) is None)
frage_g2 = bereich_text(aktionen(req_g2, baureihe=None), "verkaeuferfragen")
check("G5 Legacy False fuehrt zur offenen Frage",
      "durchgehend geführtes Scheckheft" in frage_g2)

# Das neue Feld gewinnt immer gegen die alte Checkbox.
req_g3 = KaufCheckRequest(marke="VW", modell="Golf", scheckheftgepflegt=True,
                          servicehistorie="nicht_vorhanden")
check("G6 Neues Feld schlaegt die alte Checkbox",
      sh.status(req_g3) == sh.NICHT_VORHANDEN)


# ══ H) Vollstaendig ausgefuellt / I) Minimalangaben ═════════════════════════
print("\n[H/I] Vollstaendiges und minimales Formular")

req_h = KaufCheckRequest(
    marke="Mercedes-Benz", modell="C 220 d", baujahr=2020, kilometerstand=78000,
    motor="C 220 d", kraftstoff="diesel", leistung_ps=194, getriebe="automatik",
    preis_eur=27900, ausstattung=["Navi", "Sitzheizung"],
    beschreibung="Erstbesitz, Serviceunterlagen komplett vorhanden.",
    unfallfrei="ja", vorbesitzer=1, tuev_bis="06/2027",
    verkaeuferart="haendler", servicehistorie="vollstaendig_angegeben",
)
akt_h = aktionen(req_h)
kfs_h = kf.build_key_findings_kauf(req_h, BAUREIHE, None, [])
ctx_h = ll.build_laufleistungskontext(req_h, [], heute_jahr=2026)
check("H1 Vollstaendiges Formular laeuft ohne Fehler durch",
      akt_h is not None and isinstance(kfs_h, list) and ctx_h is not None)
check("H2 Alle drei Angaben stehen im Prompt",
      all(s in kc._format_inserat(req_h)
          for s in ("Getriebe:", "Verkäufer:", "Servicehistorie:")))

req_i = KaufCheckRequest(marke="Fiat", modell="Panda")
akt_i = aktionen(req_i, baureihe=None)
kfs_i = kf.build_key_findings_kauf(req_i, None, None, [])
check("I1 Minimalangaben laufen ohne Fehler durch",
      akt_i is not None and isinstance(kfs_i, list))
check("I2 Minimalangaben erzeugen keinen Laufleistungskontext",
      ll.build_laufleistungskontext(req_i, [], heute_jahr=2026) is None)


# ══ J) Claim-Safety des Berichtsnetzes ═════════════════════════════════════
print("\n[J] Claim-Safety: Zusicherung entschaerfen, Aufforderung erhalten")

VERBOTEN = [
    "Das Fahrzeug ist lückenlos scheckheftgepflegt.",
    "Die Wartungen wurden vollständig durchgeführt.",
    "Der Wagen ist durchgehend gewartet.",
    "Es liegt ein lückenloses Scheckheft vor.",
    "Die vollständige Servicehistorie liegt vor.",
]
for satz in VERBOTEN:
    neu, ersetzt = sh.neutralisiere_claims(satz)
    ok = bool(ersetzt) and not any(
        w in neu.lower() for w in ("lückenlos", "vollständig", "durchgehend"))
    check(f"J1 entschaerft: {satz[:42]}...", ok, f"-> {neu}")

ERLAUBT = [
    "Laut Inserat wird eine vollständige Servicehistorie angegeben.",
    "Die Servicehistorie ist laut Inserat nur teilweise vorhanden.",
    "Zur Servicehistorie enthält das Inserat keine klare Angabe.",
    "Vollständigkeit der Servicehistorie und Belege vor dem Kauf prüfen.",
    "Nachweise für die angegebene Servicehistorie verlangen.",
    "Serviceheft auf durchgehende Einträge mit Stempel prüfen.",
]
for satz in ERLAUBT:
    neu, ersetzt = sh.neutralisiere_claims(satz)
    check(f"J2 unveraendert: {satz[:42]}...", neu == satz and not ersetzt, f"-> {neu}")

# Alle kanonischen Saetze des Moduls muessen ihr eigenes Netz passieren.
for status_wert in (*sh.STATI, None):
    satz = sh.satz(status_wert)
    neu, _ = sh.neutralisiere_claims(satz)
    check(f"J3 kanonischer Satz passiert das Netz ({status_wert})", neu == satz, f"-> {neu}")

# Das Netz haengt am Praedikat: ohne "ist/wurde" wird nichts ersetzt.
_n, _e = sh.neutralisiere_claims("Die Vollständigkeit der Wartung bleibt zu prüfen.")
check("J4 Ohne Zusicherung kein Eingriff", not _e, f"-> {_n}")


# ══ K) Verkaeuferart: keine technische Wirkung, keine Rechtsaussage ════════
print("\n[K] Verkaeuferart aendert keine technische Aussage")

basis = dict(marke="BMW", modell="330i", baujahr=2019, kilometerstand=68000,
             motor="330i", kraftstoff="benzin", leistung_ps=258, preis_eur=29900,
             getriebe="automatik", servicehistorie="vollstaendig_angegeben")
req_k1 = KaufCheckRequest(**basis, verkaeuferart="privat")
req_k2 = KaufCheckRequest(**basis, verkaeuferart="haendler")

ziel_k1 = mv.baue_ziel(BAUREIHE, MOTOR_AUTO_ONLY, req_k1, [BAUREIHE], [MOTOR_AUTO_ONLY])
ziel_k2 = mv.baue_ziel(BAUREIHE, MOTOR_AUTO_ONLY, req_k2, [BAUREIHE], [MOTOR_AUTO_ONLY])
check("K1 Marktziel ist identisch", ziel_k1 == ziel_k2)

kfs_k1 = kf.build_key_findings_kauf(req_k1, BAUREIHE, MOTOR_AUTO_ONLY, [])
kfs_k2 = kf.build_key_findings_kauf(req_k2, BAUREIHE, MOTOR_AUTO_ONLY, [])
check("K2 Key Findings sind identisch",
      [(f.titel, f.beschreibung) for f in kfs_k1] == [(f.titel, f.beschreibung) for f in kfs_k2])

ctx_k1 = ll.build_laufleistungskontext(req_k1, [], heute_jahr=2026)
ctx_k2 = ll.build_laufleistungskontext(req_k2, [], heute_jahr=2026)
check("K3 Laufleistungskontext ist identisch", ctx_k1 == ctx_k2)

# Und die Checkliste unterscheidet sich AUSSCHLIESSLICH in Unterlagen/Fragen.
akt_k1, akt_k2 = aktionen(req_k1, motor=MOTOR_AUTO_ONLY), aktionen(req_k2, motor=MOTOR_AUTO_ONLY)
check("K4 Besichtigung und Probefahrt bleiben unberuehrt",
      bereich_text(akt_k1, "besichtigung") == bereich_text(akt_k2, "besichtigung")
      and bereich_text(akt_k1, "probefahrt") == bereich_text(akt_k2, "probefahrt"))
check("K5 Dokumente unterscheiden sich",
      bereich_text(akt_k1, "dokumente") != bereich_text(akt_k2, "dokumente"))

RECHTSBEGRIFFE = ("gewährleistung", "sachmängelhaftung", "sachmangelhaftung", "garantie",
                  "widerruf", "haftungsausschluss", "rücktritt", "verbrauchsgüterkauf")
alles_k = "\n".join(bereich_text(a, b) for a in (akt_k1, akt_k2)
                    for b in ("besichtigung", "probefahrt", "verkaeuferfragen", "dokumente"))
alles_k += "\n".join(f"{f.titel} {f.beschreibung}" for f in kfs_k1 + kfs_k2)
alles_k += "\n".join(baue_empfehlung_gruende(req_k1, BAUREIHE, MOTOR_AUTO_ONLY, [], kfs_k1,
                                             "kaufen_nach_besichtigung", False, None))
alles_k += "\n".join(baue_empfehlung_gruende(req_k2, BAUREIHE, MOTOR_AUTO_ONLY, [], kfs_k2,
                                             "kaufen_nach_besichtigung", False, None))
treffer = [w for w in RECHTSBEGRIFFE if w in alles_k.lower()]
check("K6 Keine Rechtsaussagen in den erzeugten Texten", not treffer, str(treffer))
check("K7 Keine Pauschalwertung der Verkaeuferart",
      "Händler = sicher" not in alles_k and "Privat = riskant" not in alles_k
      and "seriöser Händler" not in alles_k)


# ══ L) Getriebe schaerft die Variantenauflösung NICHT ═════════════════════
print("\n[L] Getriebe wirkt nicht auf die Variantenauflösung")

from app.car_lookup import find_motor   # noqa: E402

MOTOREN = [
    {"variante_id": "a", "bezeichnung": "330i", "kraftstoff": "benzin",
     "leistung_ps": 258, "getriebe": ["Automatik"]},
    {"variante_id": "b", "bezeichnung": "320d", "kraftstoff": "diesel",
     "leistung_ps": 190, "getriebe": ["Manuell", "Automatik"]},
]
BR = {**BAUREIHE, "motoren": MOTOREN}
treffer_l = find_motor(BR, "330i", "330i")
check("L1 find_motor trifft die Variante ueber die Bezeichnung",
      (treffer_l or {}).get("variante_id") == "a", str(treffer_l))
# Nachweis der Signatur: `find_motor` nimmt gar keinen Getriebeparameter an —
# eine Getriebeschaerfung waere nur durch einen Umbau moeglich, den die Datenlage
# nicht rechtfertigt (0 von 32 gleichnamigen Variantengruppen trennbar).
import inspect   # noqa: E402
check("L2 find_motor hat keinen Getriebeparameter",
      "getriebe" not in inspect.signature(find_motor).parameters)


# ══ Ergebnis ══════════════════════════════════════════════════════════════
print("\n" + "=" * 62)
if FEHLER:
    print(f"{len(FEHLER)} FEHLER:")
    for f in FEHLER:
        print(f"  - {f}")
    raise SystemExit(1)
print("Alle Pruefungen bestanden.")
