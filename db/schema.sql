-- Auto-KI Datenbank-Schema
-- Zweistufig: Ebene 1 = Baureihe, Ebene 2 = Motorvariante

PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- ============================================================
-- EBENE 1: Baureihe
-- ============================================================

CREATE TABLE IF NOT EXISTS baureihe (
    id                      TEXT PRIMARY KEY,       -- z.B. "bmw-m4-g82"
    marke                   TEXT NOT NULL,
    modell                  TEXT NOT NULL,
    generation              TEXT NOT NULL,
    bauzeitraum_von         INTEGER,                -- null erlaubt (Admin-Entwurf noch unvollständig)
    bauzeitraum_bis         INTEGER,                -- null = aktuell
    karosserie              TEXT,                   -- JSON-Array, z.B. '["Coupé","Cabrio"]'
    -- Auflösung eines zusammengefassten generation-Felds ("G20/G21") in die
    -- einzelnen Werkscodes und ihre Karosserie. JSON-Objekt, z.B.
    -- '{"G20":"Limousine","G21":"Touring"}'. NULL = keine geprüfte Zuordnung.
    -- Nur explizit verifizierte Einträge, siehe app/chassis_codes.py.
    chassis_codes           TEXT,
    segment                 TEXT,
    vorgaenger              TEXT,

    -- Optische Erkennung
    erkennung_generation    TEXT,
    facelift_merkmale       TEXT,

    -- Zuverlässigkeit & Sicherheit
    adac_pannenkennziffer   TEXT,
    tuev_maengelquote       TEXT,
    dekra_urteil            TEXT,
    euro_ncap_sterne        INTEGER,                -- 0-5, null wenn nicht getestet
    euro_ncap_jahr          INTEGER,

    -- Wartung & Meta
    wartung_oel_km          INTEGER,
    wartung_hu_intervall    TEXT,
    kaufberatung            TEXT,                   -- Fließtext → auch in Vektor-DB

    letzte_aktualisierung   TEXT NOT NULL           -- ISO-Datum, z.B. "2026-05"
);

-- Ausstattungslinien (1:n zu Baureihe)
CREATE TABLE IF NOT EXISTS ausstattungslinie (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    baureihe_id         TEXT NOT NULL REFERENCES baureihe(id) ON DELETE CASCADE,
    name                TEXT NOT NULL,
    typ                 TEXT NOT NULL CHECK(typ IN (
                            'Basis','Ausstattungslinie','M Performance','Echtes M-Modell'
                        )),
    optische_merkmale   TEXT,
    abgrenzung          TEXT
);

-- Schwachstellen der Baureihe (1:n)
CREATE TABLE IF NOT EXISTS schwachstelle_baureihe (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    baureihe_id         TEXT NOT NULL REFERENCES baureihe(id) ON DELETE CASCADE,
    bauteil             TEXT NOT NULL,
    beschreibung        TEXT NOT NULL,
    betroffene_baujahre TEXT,
    schweregrad         TEXT NOT NULL CHECK(schweregrad IN ('gering','mittel','hoch'))
);

-- KBA-Rückrufe (1:n)
CREATE TABLE IF NOT EXISTS rueckruf (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    baureihe_id         TEXT NOT NULL REFERENCES baureihe(id) ON DELETE CASCADE,
    datum               TEXT,
    betroffene_baujahre TEXT,
    mangel              TEXT NOT NULL,
    abhilfe             TEXT,
    kba_referenz        TEXT
);

-- Quellen (1:n)
CREATE TABLE IF NOT EXISTS quelle (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    baureihe_id     TEXT NOT NULL REFERENCES baureihe(id) ON DELETE CASCADE,
    quelle          TEXT NOT NULL,
    url             TEXT,
    abrufdatum      TEXT
);

-- ============================================================
-- EBENE 2: Motorvariante
-- ============================================================

CREATE TABLE IF NOT EXISTS motorvariante (
    variante_id             TEXT PRIMARY KEY,       -- z.B. "bmw-m4-g82-competition"
    baureihe_id             TEXT NOT NULL REFERENCES baureihe(id) ON DELETE CASCADE,
    bezeichnung             TEXT NOT NULL,
    motorcode               TEXT,
    kraftstoff              TEXT NOT NULL CHECK(kraftstoff IN (
                                'Benzin','Diesel','Elektro','Plug-in-Hybrid','Mild-Hybrid'
                            )),
    hubraum_ccm             INTEGER,
    zylinder                INTEGER,
    leistung_ps             INTEGER,                -- HARTE ZAHL, nie halluzinieren
    leistung_kw             INTEGER,
    drehmoment_nm           INTEGER,
    getriebe                TEXT,                   -- JSON-Array
    antrieb                 TEXT CHECK(antrieb IN ('Heck','Front','Allrad')),
    beschleunigung_0_100    REAL,
    vmax_kmh                INTEGER,
    verbrauch_wltp          REAL,                   -- null = nicht gemessen (NEFZ-Ära)
    verbrauch_real          REAL,                   -- ca., aus Spritmonitor
    co2_g_km                INTEGER,
    neupreis_ca_eur         INTEGER,
    heck_emblem             TEXT,
    optische_unterscheidung TEXT,

    -- Standard-Fahrzeugdaten (Phase 1 Wissensqualität) — je Motorisierung, da Tankgröße,
    -- Kofferraum, Anhängelast etc. oft zwischen Kraftstoffart/Antrieb/PHEV-Batterie variieren
    tankgroesse_liter          INTEGER,
    kofferraum_liter           INTEGER,
    batteriekapazitaet_kwh     REAL,        -- nur BEV/PHEV, sonst NULL
    anhaengelast_gebremst_kg   INTEGER,
    anhaengelast_ungebremst_kg INTEGER,
    abgasnorm                  TEXT,        -- z.B. "Euro 6d-ISC-FCM"
    felgengroesse_serie        TEXT         -- z.B. "17 Zoll (Serie), bis 20 Zoll optional"
);

-- Motorspezifische Schwachstellen (1:n)
CREATE TABLE IF NOT EXISTS schwachstelle_motor (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    variante_id     TEXT NOT NULL REFERENCES motorvariante(variante_id) ON DELETE CASCADE,
    bauteil         TEXT NOT NULL,
    beschreibung    TEXT NOT NULL,
    baujahre        TEXT,
    kosten_ca       TEXT
);

-- Kritische Wartung (1:n)
CREATE TABLE IF NOT EXISTS kritische_wartung (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    variante_id TEXT NOT NULL REFERENCES motorvariante(variante_id) ON DELETE CASCADE,
    bauteil     TEXT NOT NULL,
    intervall   TEXT,
    hinweis     TEXT
);

-- ============================================================
-- Hilfreich: Such-Indizes
-- ============================================================

CREATE INDEX IF NOT EXISTS idx_baureihe_marke_modell ON baureihe(marke, modell);
CREATE INDEX IF NOT EXISTS idx_baureihe_generation ON baureihe(generation);
CREATE INDEX IF NOT EXISTS idx_motorvariante_baureihe ON motorvariante(baureihe_id);
CREATE INDEX IF NOT EXISTS idx_motorvariante_motorcode ON motorvariante(motorcode);

-- ============================================================
-- Verifikation EINZELNER Fakten (Verification-Pilot)
-- ============================================================
-- `baureihe.verification` arbeitet auf Baureihen-/Kategorieebene: setzt man dort
-- z.B. schwachstellen=verified, gelten ALLE Schwachstellen dieser Baureihe als
-- geprueft. Fuer eine ehrliche Verifikation reicht das nicht — es gibt keine
-- Baureihe, deren Fakten alle gleichzeitig geprueft wurden.
--
-- Diese Tabelle verifiziert deshalb den EINZELNEN Fakt. Sie ersetzt
-- `baureihe.verification` nicht (das bleibt fuer den Marktvergleich zustaendig),
-- sondern ergaenzt es fuer Schwachstellen, Motorprobleme, Rueckrufe und Wartung.
--
-- `fingerprint` ist der Kern der Sicherung: ein SHA-256 ueber die inhalts-
-- tragenden Felder des Fakts zum Zeitpunkt der Pruefung. Die numerischen IDs
-- sind AUTOINCREMENT und werden bei einem Admin-Neuschreiben
-- (app/db_writer.py: DELETE + INSERT) neu vergeben. Ohne Fingerprint wuerde eine
-- Verifikation dann still an einem ANDEREN Fakt haengen. Stimmt der Fingerprint
-- nicht mehr, faellt der Fakt automatisch auf `unverified_db` zurueck.
CREATE TABLE IF NOT EXISTS fakt_verifikation (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    -- 'schwachstelle_baureihe' | 'schwachstelle_motor' | 'rueckruf' | 'kritische_wartung'
    fakt_art        TEXT NOT NULL,
    fakt_id         INTEGER NOT NULL,
    fingerprint     TEXT NOT NULL,
    -- 'verified' | 'partially_verified' | 'rejected'
    -- NUR 'verified' darf trust=verified tragen; 'partially_verified' bleibt
    -- ausdruecklich unverified_db (Thema belegt, Zuschnitt aber nicht).
    status          TEXT NOT NULL,
    quelle          TEXT NOT NULL,
    quelle_stufe    TEXT NOT NULL,          -- 'A' | 'B' | 'C' (Quellenhierarchie)
    url             TEXT,
    referenz        TEXT,                   -- z.B. amtliche Rueckruf-/Aktionsnummer
    geprueft_am     TEXT NOT NULL,
    notiz           TEXT,
    UNIQUE(fakt_art, fakt_id)
);

CREATE INDEX IF NOT EXISTS idx_fakt_verifikation_art
    ON fakt_verifikation(fakt_art, fakt_id);
