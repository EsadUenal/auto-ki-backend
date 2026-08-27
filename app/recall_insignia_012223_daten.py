from __future__ import annotations

"""
Nachtrag zum Recall-Pilot: der fehlende, amtlich belegte Insignia-B-Rueckruf.

WARUM EIN EIGENES MODUL
-----------------------
`app/recall_pilot_daten.py` ist das abgeschlossene, auf master gemergte Ergebnis
des Recall-Piloten (14 geprueffte Zeilen, Marker `recall_pilot_v1`). Es wird
NICHT nachtraeglich veraendert — sonst liefe seine Migration auf bereits
migrierten Datenbanken nicht mehr sauber durch, und die Zuordnung
"welcher Marker hat welche Zeile geschrieben" ginge verloren. Dieser Nachtrag
bekommt deshalb ein eigenes Modul und einen eigenen Marker.

DER FALL
--------
Der Recall-Pilot hatte als offenen P1-Punkt festgehalten, dass ein realer,
sicherheitsrelevanter Insignia-B-Rueckruf im Bestand FEHLT: das Fahrzeug ist
betroffen, VIRA schweigt. Genau dieser Fakt wird hier ergaenzt.

AMTLICHE QUELLE (Stufe A) — erstmals direkt erreichbar
-------------------------------------------------------
Waehrend des Piloten war der Bulk-Export der KBA-Rueckrufdatenbank durchgehend
mit HTTP 503 nicht erreichbar, und die interaktive Suche ist captcha-gesichert;
deshalb konnte damals KEIN einziger Rueckruf gegen die Primaerquelle geprueft
werden. Beim Nachtrag antwortete derselbe Endpunkt

    https://www.kba-online.de/rrdb/buerger/api/rueckruf/export?format=csv&type=cars

mit HTTP 200 und lieferte den vollstaendigen amtlichen Bestand (7.816 Rueckrufe
mit Referenznummer, Herstellercode, Veroeffentlichungsdatum, Marke, Modell,
Mangel, Produktionszeitraum, Abhilfe, Stueckzahlen und Ueberwachungsstatus).
Der hier ergaenzte Fakt stammt Feld fuer Feld aus diesem Datensatz — nicht aus
einer Fachmedien-Wiedergabe.

ZWEI ABWEICHUNGEN DER FACHPRESSE, DIE DIE AMTLICHE QUELLE KORRIGIERT
---------------------------------------------------------------------
1. REFERENZNUMMER. Der Ausgangshinweis (und auto-motor-und-sport) nennt
   "012223". Das KBA fuehrt die Aktion als "12223" — ohne fuehrende Null. Der
   amtliche Wert wird gespeichert; die Sekundaerschreibweise steht in der Notiz.
2. PRODUKTIONSZEITRAUM. auto-motor-und-sport nennt "2018 bis 2020". Das KBA
   nennt "2016 bis 2020". Die Fachpresse hat das Fenster also um zwei Jahre zu
   eng wiedergegeben. Maßgeblich ist die amtliche Angabe.

Das ist zugleich der Beleg dafuer, warum der Pilot Fachmedien nur ergaenzend
zulaesst: sie hatten hier die Referenz UND den Zeitraum falsch.

GENERATIONSFRAGE (dieselbe Pruefung wie bei #546)
--------------------------------------------------
Das KBA nennt "OPEL INSIGNIA" ohne Generationsangabe, und das Feld "Moegliche
Eingrenzung der betroffenen Modelle" steht auf "N/A" — es gibt also KEINE
Varianten-/Motoreinschraenkung. Der amtliche Produktionszeitraum 2016-2020
ueberschneidet formal beide Generationen (Insignia A bis 2017, Insignia B ab
2017).

Anders als bei #546 ist die Zuordnung hier trotzdem belastbar, und zwar aus
drei unabhaengigen Gruenden:

  * Der Insignia B belegt mit 2017-2020 den GROSSTEIL des amtlichen Fensters,
    nicht nur einen Randstreifen (bei #546 waren es 2 von 6 Jahren am Rand).
  * Es gibt KEINE Motor-/Variantenbedingung, die man auf die falsche Generation
    beziehen koennte (bei #546 war genau das die Unsicherheit: 1,3/1,6-l-Diesel
    Euro 6 mit NOx-Speicherkat — unklar, ob der Insignia B den ueberhaupt hat).
  * Die amtliche US-Rueckrufdatenbank NHTSA fuehrt unter Kampagnennummer
    22V465000 exakt denselben Mangel (Softwarefehler im EBCM -> Verlust der
    Bremskraftunterstuetzung -> verlaengerter Bremsweg) und exakt dieselbe
    Abhilfe (EBCM-Software-Update) fuer den Buick Regal der Modelljahre
    2018-2020 — das ist das in Ruesselsheim gebaute Schwestermodell des
    Insignia B. Eine zweite Behoerde bestaetigt damit unabhaengig, dass die
    Fahrzeuge der Insignia-B-Aera die betroffenen sind.

Der gespeicherte Bauzeitraum ist die Schnittmenge des amtlichen Fensters
(2016-2020) mit dem Bauzeitraum des Insignia B (ab 2017): 2017-2020. Die Zeile
behauptet damit nichts, was ueber die Quelle hinausgeht, und nichts, was der
Baureihe widerspricht.
"""

GEPRUEFT_AM = "2026-08-27"

# Explizite Fakt-ID. Der kanonische Seed (db/seed_fahrzeugdaten.sql) schreibt
# alle Rueckruf-IDs aus, damit sie ueber frische Installationen hinweg stabil
# bleiben. Die Migration setzt die ID deshalb ausdruecklich statt sie dem
# AUTOINCREMENT zu ueberlassen — sonst haette dieselbe Zeile in Live-DB und
# frischer Installation verschiedene IDs, und die Verifikation haette am
# falschen Fakt gehangen (genau die Falle, gegen die der Fingerprint in
# app/fakt_verifikation.py absichert).
NEUER_FAKT_ID = 808

NEUER_RUECKRUF = {
    "id": NEUER_FAKT_ID,
    "baureihe_id": "opel-insignia-b",
    # Amtliches Veroeffentlichungsdatum des KBA (2022-10-12).
    "datum": "2022-10",
    # Schnittmenge des amtlichen Fensters 2016-2020 mit dem Bauzeitraum des
    # Insignia B (ab 2017). KEIN Klammerzusatz: das KBA nennt ausdruecklich
    # keine Varianten-/Motoreinschraenkung ("Eingrenzung: N/A"), der Rueckruf
    # gilt also fuer alle Motorisierungen der Baureihe.
    "betroffene_baujahre": "2017-2020",
    # Amtliche Mangelbezeichnung, woertlich uebernommen.
    "mangel": "Der Ausfall des hydraulischen Bremskraftausgleichs kann zu einem "
              "verlängerten Bremsweg führen, weil das elektronische "
              "Bremssteuermodul nicht korrekt konfiguriert ist.",
    # Amtliche Beschreibung der Massnahme, woertlich uebernommen.
    "abhilfe": "Das elektronische Bremssteuermodul (EBCM) wird neu programmiert.",
    # Amtliche Referenznummer in der Schreibweise des KBA (ohne fuehrende Null).
    "kba_referenz": "12223",
}

# (fakt_art, fakt_id, status, quelle, stufe, url, referenz, notiz)
NEUE_VERIFIKATION = (
    "rueckruf", NEUER_FAKT_ID, "verified",
    "KBA-Rueckrufdatenbank, amtlicher Gesamtexport (7.816 Rueckrufe), "
    "Referenznummer 12223, Herstellercode KBT, veroeffentlicht 12.10.2022, "
    "KBA-ueberwacht; unabhaengig bestaetigt durch die amtliche "
    "US-Rueckrufdatenbank NHTSA (Kampagne 22V465000, Schwestermodell Buick Regal)",
    "A",
    "https://www.kba-online.de/rrdb/buerger/api/rueckruf/export?format=csv&type=cars",
    "12223 (Herstellercode KBT)",
    "Erster Rueckruf des gesamten Projekts, der Feld fuer Feld gegen die "
    "amtliche Primaerquelle geprueft wurde. Der KBA-Datensatz nennt: "
    "Referenznummer 12223, Herstellercode KBT, Veroeffentlichung 2022-10-12, "
    "Marke OPEL, Modell INSIGNIA, Produktionszeitraum 2016 bis 2020, "
    "Mangelbezeichnung 'Der Ausfall des hydraulischen Bremskraftausgleichs kann "
    "zu einem verlaengerten Bremsweg fuehren, weil das elektronische "
    "Bremssteuermodul nicht korrekt konfiguriert ist.', Massnahme 'Das "
    "elektronische Bremssteuermodul (EBCM) wird neu programmiert.', 194.032 "
    "Fahrzeuge weltweit und 66.966 in Deutschland, Status 'ueberwacht'. Mangel "
    "und Abhilfe der DB-Zeile sind woertliche Uebernahmen. ZWEI KORREKTUREN "
    "GEGENUEBER DER FACHPRESSE: (1) die Referenz lautet amtlich '12223', nicht "
    "'012223' wie in der Sekundaerberichterstattung (auto-motor-und-sport) und "
    "im urspruenglichen Projekthinweis; (2) der Produktionszeitraum ist amtlich "
    "2016-2020, nicht 2018-2020 wie von auto-motor-und-sport berichtet — die "
    "Fachpresse hatte das Fenster um zwei Jahre zu eng. GENERATION: das KBA "
    "nennt keine Generation und setzt 'Moegliche Eingrenzung der betroffenen "
    "Modelle' auf 'N/A', es gibt also keine Varianten- oder "
    "Motoreinschraenkung. Die Zuordnung zum Insignia B ist dennoch belastbar: "
    "der Insignia B belegt mit 2017-2020 den Grossteil des amtlichen Fensters, "
    "es existiert keine Motorbedingung, die man auf die falsche Generation "
    "beziehen koennte, und die NHTSA fuehrt unter 22V465000 denselben Mangel "
    "und dieselbe Abhilfe fuer den Buick Regal 2018-2020 — das in Ruesselsheim "
    "gebaute Schwestermodell des Insignia B. Der gespeicherte Bauzeitraum "
    "2017-2020 ist die Schnittmenge des amtlichen Fensters mit dem Bauzeitraum "
    "der Baureihe; die Zeile behauptet nichts darueber hinaus. OHNE VIN bleibt "
    "die individuelle Betroffenheit eine FIN-Frage und wird dem Nutzer auch so "
    "angezeigt — 'confirmed_by_vin' wird nicht erzeugt.",
)


def _selbsttest() -> None:
    """Formale Konsistenz des Nachtrags (wird vom Test aufgerufen)."""
    from app.fakt_verifikation import FAKT_ARTEN, STATUS_WERTE, QUELLENSTUFEN

    pflicht = {"id", "baureihe_id", "datum", "betroffene_baujahre", "mangel",
               "abhilfe", "kba_referenz"}
    assert set(NEUER_RUECKRUF) == pflicht, \
        f"Spalten weichen ab: {set(NEUER_RUECKRUF) ^ pflicht}"
    assert NEUER_RUECKRUF["baureihe_id"] == "opel-insignia-b"
    assert NEUER_RUECKRUF["id"] == NEUER_FAKT_ID

    fakt_art, fakt_id, status, quelle, stufe, url, referenz, notiz = NEUE_VERIFIKATION
    assert fakt_art in FAKT_ARTEN and fakt_art == "rueckruf"
    assert fakt_id == NEUER_FAKT_ID, "Verifikation haengt an der falschen Fakt-ID"
    assert status in STATUS_WERTE
    assert stufe in QUELLENSTUFEN
    assert stufe == "A", "dieser Fakt MUSS aus der amtlichen Primaerquelle stammen"
    assert len(notiz or "") >= 200, "Notiz zu duenn fuer einen verified Fakt"
    # §9: eine gespeicherte KBA-Referenz nur an einem amtlich belegten Fakt.
    if NEUER_RUECKRUF["kba_referenz"]:
        assert status == "verified", \
            "kba_referenz gespeichert, aber Fakt nicht verified"
    # Kein Klammer-Qualifier: das KBA nennt keine Varianteneinschraenkung.
    assert "(" not in NEUER_RUECKRUF["betroffene_baujahre"], \
        "Varianten-Qualifier ohne amtliche Grundlage"
