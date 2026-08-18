"""
Verifizierte Zuordnung Chassiscode -> Karosserie innerhalb einer Baureihenfamilie.

Warum es das gibt
-----------------
Manche Baureihen-Datensätze fassen mehrere Werkscodes in EINEM `generation`-Feld
zusammen ("G20/G21"), weil sie technisch dieselbe Baureihe sind und sich nur in der
Karosserie unterscheiden. Für den Marktvergleich ist der Unterschied aber
entscheidend: wer eine G20-Limousine sucht, will keinen G21-Touring im Median.

Nennt ein Inserat den Code nicht selbst — der Normalfall —, lässt er sich über die
Karosserie ableiten, SOFERN für die Familie eine eindeutige Zuordnung hinterlegt ist.

Warum als DATEN und nicht als Regel
-----------------------------------
Eine naheliegende Regel wäre "Codes aus `generation` der Reihe nach mit der
`karosserie`-Liste paaren". Die hätte für 9 Datensätze plausibel ausgesehen — und
dabei `bmw-8er-e63-e64` mitgenommen. Dieser Datensatz ist fachlich FALSCH: E63/E64
ist die 6er-Reihe (2003-2010, Coupé/Cabrio), einen BMW 8er gab es zwischen dem E31
(bis 1999) und dem G15 (ab 2018) nicht. Der Datensatz führt zudem die nie gebauten
Modelle "845Ci" und "850i" mit den Motoren des 6er 645Ci/650i.

Deshalb steht hier eine explizit geprüfte Liste statt einer Ableitungsregel. Was
nicht in dieser Liste steht, bekommt KEINE Zuordnung — nicht einmal eine plausibel
aussehende.

Stand der Prüfung (bewusst zurückhaltend formuliert)
----------------------------------------------------
Die Einträge wurden im Projekt einzeln fachlich betrachtet und plausibilisiert;
dabei wurde `bmw-8er-e63-e64` als falsch erkannt und ausgeschlossen. Externe
Quellen-/Verifikationsnachweise sind derzeit NICHT persistiert — es gibt weder
eine gespeicherte Quelle noch ein Prüfdatum je Eintrag.

Nach der Vertrauensregel in app/verification.py entspricht das der Stufe
`reviewed`, nicht `verified`. Für den Marktvergleich heißt das: diese Zuordnung
trägt derzeit KEINE harte Entscheidung und keine positive Generations-Inference.
Sie wird erst wieder hart wirksam, wenn zu einer Baureihe eine Quelle hinterlegt
ist (`verification` -> chassis_codes -> status "verified" + "source").

BEWUSST NICHT enthalten
-----------------------
- `bmw-8er-e63-e64`  : fachlich falscher Datensatz (s.o.), separate Bereinigung nötig
- `bmw-7er-e65/e66`  : E65/E66 unterscheiden den RADSTAND (kurz/lang), beide Limousine
- `bmw-7er-f01/f02`  : dito
- `bmw-7er-g11/g12`  : dito
- Toyota Supra A40/A50: beide Coupé, keine Karosserieunterscheidung

Die Karosseriewerte sind hier so geschrieben, wie sie auch im `karosserie`-Feld der
Baureihe stehen. Verglichen wird später NICHT wörtlich, sondern über die zentrale
Karosserie-Normalisierung (marktvergleich._karosserie_im_text) — dadurch gilt
Touring == Kombi und Cabrio == Cabriolet, ohne eine zweite Synonymliste zu pflegen.
"""

# {baureihe_id: {chassis_code: karosserie}}
VERIFIZIERTE_CHASSIS_CODES: dict[str, dict[str, str]] = {
    "bmw-3er-g20-g21":     {"G20": "Limousine", "G21": "Touring"},
    "bmw-1er-f20-f21":     {"F20": "5-Türer Schrägheck", "F21": "3-Türer Schrägheck"},
    "bmw-4er-f32-f33-f36": {"F32": "Coupé", "F33": "Cabrio", "F36": "Gran Coupé"},
    "bmw-4er-g22-g23-g26": {"G22": "Coupé", "G23": "Cabrio", "G26": "Gran Coupé"},
    "bmw-6er-e63-e64":     {"E63": "Coupé", "E64": "Cabrio"},
    "bmw-6er-f12-f13-f06": {"F12": "Cabrio", "F13": "Coupé", "F06": "Gran Coupé"},
    "bmw-8er-g15-g14-g16": {"G15": "Coupé", "G14": "Cabrio", "G16": "Gran Coupé"},
}

# Marker in `schema_migrations`, damit der Seed genau EINMAL läuft und spätere
# manuelle Pflege nicht bei jedem App-Start überschrieben wird.
SEED_MARKER = "chassis_codes_seed_v1"
