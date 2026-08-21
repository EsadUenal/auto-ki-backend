# Etappe 2 — Qualifikation automatischer Marktdatenquellen

Branch: `etappe2-market-sources` (von `master` = `c9f5600`)
Stand: 2026-08-20
Charakter: **Audit / Recherche.** Keine Produktionsänderung, keine Quelle aktiviert.

Etappe-1-Marktlogik wurde nicht angefasst. Beobachtete Auffälligkeiten stehen in
§14 als reine Notiz.

---

## 1. Source-Datenfluss heute

```
CheckRequest (Marke, Modell, Baujahr, km, Motor, Kraftstoff, Leistung, Getriebe)
  │
  ├─► app/car_lookup.find_baureihe / find_motor        (SQLite-Fahrzeug-DB)
  │       └─► app/marktvergleich.baue_ziel(...)  ──────────────► ziel: dict
  │             (Modelltreue, Generation, Motor-Tokens, Trust-Gates)
  │
  └─► app/vehicle_identity.VehicleIdentity.from_market_context(...)
          │
          ▼
   app/marktrecherche.baue_deep_queries / baue_rare_queries
          │   → list[QueryStufe]  (query, include_domains, raw_content, felder)
          ▼
   app/marktrecherche.vertiefe_marktrecherche(...)          ◄── ADAPTIVE LADDER
          │
          ├─► app/web_search.tavily_search_mit_status(...)   ◄── EINZIGER
          │       POST https://api.tavily.com/search             DATENZUGANG
          │       → list[dict]: {url, title, content, raw_content?}
          │
          ├─► app/web_search.hole_raw_content(...)  (Tavily /extract, Budget)
          │
          ├─► _merge(...)            (URL-Dedupe über alle Stufen)
          │
          └─► app/marktvergleich.analysiere_markt(web_results, ziel, angebot)
                  │
                  ├─ ist_info_domain / ist_teile_suchseite      → verworfen
                  ├─ ist_einzelinserat / ist_kategorieseite     → source_type
                  ├─ app/market_card_segmenter.segmentiere(...) → Kartengrenzen
                  ├─ _extrahiere_aus_text(...)  REGEX über Fließtext
                  │      → Preisbeobachtung (preis, km, jahr, fuel, ps, …)
                  ├─ _bewerte(b, ziel)   ◄── SOURCE-POLICY IST HIER DIE 1. PRÜFUNG
                  │      darf_preisbildend_sein(url)? sonst SOURCE_POLICY_GRUND
                  ├─ _dedupliziere / _cap_pro_url / _trim_ausreisser
                  └─ Median + Quartile + Datenqualität → Marktanalyse
                          │
                          ▼
              app/preisurteil.bewerte_preis(...)  → EIN PriceAssessment
                          │
                          ▼
            app/kaufcheck.py / app/verkaufscheck.py → Bericht
```

**Wo eine neue Datenquelle angeschlossen werden müsste — exakt drei Stellen:**

| # | Stelle | Was passieren müsste |
|---|---|---|
| **A** | `app/marktrecherche.py:_laufe_stufen` (der `tavily_search_mit_status`-Aufruf) | Der einzige Ort, an dem Rohdaten in die Pipeline kommen. Eine strukturierte Provider-Antwort müsste hier **statt** oder **neben** Tavily eingespeist werden. |
| **B** | `app/marktvergleich.py:analysiere_markt` (Zeile 2277) | Erwartet heute `list[dict]` mit `{url, title, content, raw_content}` und **extrahiert per Regex aus Fließtext**. Ein API-Provider liefert bereits Felder — die Regex-Extraktion wäre für ihn unnötig und schädlich. Es braucht einen zweiten Eingang: `Preisbeobachtung` direkt aus API-Feldern bauen, dann in dieselbe `_bewerte(b, ziel)` geben. |
| **C** | `app/config.ALLOWED_MARKET_SOURCES` / `app/web_search.darf_preisbildend_sein` | Die Freigabe. Ohne Eintrag ist jeder Treffer der neuen Quelle nicht preisbildend — unabhängig davon, wie gut die Daten sind. |

Wichtig: **B ist der eigentliche Aufwand**, nicht A. Die gesamte Etappe-1-Bewertung
(`_bewerte`, Modelltreue, Motor-Evidence, Trust-Gates) arbeitet auf
`Preisbeobachtung` und ist providerunabhängig — sie bleibt unverändert nutzbar.
Nur der Weg *bis* zur `Preisbeobachtung` ist heute untrennbar an "Text + Regex"
gebunden.

Test-/Replay-Harness: `_source_policy_testharness.py` gibt reale Domains
(kleinanzeigen.de, autouncle.de) **ausschließlich im Testprozess** frei;
`app/web_search.marktquellen_freigabe(...)` ist der Kontextmanager dazu.
Production sieht weiterhin die leere Liste.

---

## 2. Notwendige VIRA-Datenfelder (aus dem Code abgeleitet, nicht ausgedacht)

Grundlage: `app/models.Preisbeobachtung` und die tatsächlichen Zugriffe in
`app/marktvergleich._bewerte`.

### MUSS — ohne diese Felder entsteht kein Datenpunkt

| Feld | VIRA-Ziel | Verwendung |
|---|---|---|
| Preis (EUR, Gesamtpreis) | `preis_eur` | Median/Quartile. `_FINANZ_MARKERS` muss Leasing-/Monatsraten ausschließen — bei einer API entfällt dieses Problem. |
| Quell-URL / Listing-ID | `quelle_url`, `listing_id`, `detail_url`, `listing_key` | Dedupe (`_identitaets_key`), `_cap_pro_url`, Belege im Bericht |
| Marke | `make` | `_ist_fremdmodell`, `marke_tokens` |
| Modell | `model` | Harte Modelltreue (`fremd_modelle`) |
| Baujahr / EZ | `baujahr` | Generationsprüfung, `_km_fenster`, Similarity |
| Laufleistung | `kilometerstand` | Similarity, `_km_fenster` |

### SEHR WICHTIG — entscheidet über `sehr_aehnlich` vs. `ungeeignet`

| Feld | VIRA-Ziel | Warum |
|---|---|---|
| Motor-/Verkaufsbezeichnung ("320d", "2.0 CDTI") | `engine_variant` | **Der Etappe-1-Kernbefund.** Ohne belegte Motorbezeichnung greift das Sicherheitsgate: dann sind Modellanker **und** bestätigter Kraftstoff nötig. |
| Kraftstoff | `fuel` | Harte Prüfung, wenn `kraftstoff_hart` |
| Leistung (PS/kW) | `horsepower` | Harte Prüfung, wenn `leistung_hart` |
| Generation / Chassiscode | `generation` + `generation_evidence` | `fremd_generationen` verwirft E90/F30 beim G20 |
| Karosserie | `body` + `body_evidence` | Insignia ST = Sports Tourer, Touring/Avant/Variant |

Anmerkung zur Evidenz: `generation_evidence` und `body_evidence` unterscheiden
`explicit_card` / `explicit_detail` / `page_context` / `window_fallback`. Ein
API-Feld ist strukturell `explicit_detail` — **das ist der größte Qualitätssprung
gegenüber heute**, weil `window_fallback` in Etappe 1 höchstens "bedingt" erreichen
darf.

### NÜTZLICH — heute gelesen, aber nicht ausschlaggebend

`transmission` (Getriebe), Händler/privat, Standort, Inseratsdatum, Bilder,
Ausstattung. Antrieb (Allrad/Heck/Front) ist in `VehicleIdentity.drivetrain`
vorhanden, wird von `Preisbeobachtung` aber **nicht** getragen — also heute
kein Bedarf.

---

## 3. AutoScout24

**Zwei völlig verschiedene Produkte — nicht verwechseln.**

### 3a) AutoScout24 SEARCH API (Deutschland, AutoScout24 GmbH)

| | |
|---|---|
| A Produktname | AutoScout24 SEARCH API |
| B Zweck | Zugriff auf Fahrzeugdaten/Inserate für "Apps, Websites oder eigene Software" |
| C Suchen? | Ja — als Suchschnittstelle beworben, GraphQL, "Millionen Inserate" |
| D Einzel-Listing? | Nicht öffentlich dokumentiert |
| E Felder | **Nicht öffentlich dokumentiert** — Dokumentation liegt hinter dem Zugang |
| F Sandbox | Nicht öffentlich dokumentiert |
| G Auth | Nicht öffentlich dokumentiert |
| H Rate Limits | Nicht öffentlich dokumentiert |
| I Preis | Nicht öffentlich |
| J | **nicht öffentlich / Anbieterantwort ausstehend** |
| K Abgeleitete Werte (Median/Spanne) | **UNKNOWN** — nirgends öffentlich geregelt |
| L Vergleichsangebote anzeigen | **UNKNOWN** |
| M Cachen/Speichern | **UNKNOWN** |
| N Status | **YELLOW** |

Zugangsweg ist offiziell dokumentiert: `searchapi@autoscout24.com`, +49 89 44456-1000.
Das ist ein echter, vorgesehener Vertriebskanal für genau diesen Zweck — deshalb
YELLOW und nicht UNKNOWN.

### 3b) AutoScout24 Listing Creation API (Deutschland)

Schreibend (create/update/delete + Bilder), kostenlos, für Datendienstleister,
Datenpartner-AGB. **Für VIRA nutzlos** — liefert keine Marktdaten heraus.
Status: **RED** (falsches Produkt, nicht falscher Anbieter).

### 3c) AutoScout24.ch Public API (SMG Swiss Marketplace Group)

Vollständig öffentlich spezifiziert (`smg-automotive/autoscout24-api-specs`,
`openapi.yaml`), inkl. **echtem Such-Endpunkt** und Detailabruf:

```
POST /public/v1/listings/search        GET /public/v1/listings/{listingId}
POST /public/v1/listings/count         GET /public/v1/listings/{listingId}/equipment
POST /public/v1/listings/facets        GET /public/v1/makes, /vehicle-categories
POST /public/v1/clients/oauth/token    (OAuth2 Bearer)
Prod: https://api.autoscout24.ch   Pre-Prod: https://api.preprod.autoscout24.dev
```

Das ist technisch **exakt** die Form, die VIRA braucht — inklusive
Pre-Prod-Umgebung. **Aber: Schweizer Markt.** Preise, Bestand und
Fahrzeugausstattung sind nicht der deutsche Markt, den VIRA bewertet.
Status: **RED für den VIRA-Anwendungsfall** (Marktabdeckung), wertvoll nur als
Referenz dafür, wie ein solches Mapping aussehen wird — und als Argument
gegenüber AutoScout24 DE, dass der Konzern eine solche Schnittstelle kennt.

### Feld-Mapping (soweit heute belegbar, .ch-Spec als Vorlage)

| AutoScout24 (.ch-Spec) | VIRA |
|---|---|
| `listingId` | `listing_id`, `listing_key` |
| Detail-URL | `detail_url`, `quelle_url` |
| `make` / `model` | `make` / `model` |
| Version (`/versions/search`) | `engine_variant` |
| Erstzulassung | `baujahr` |
| Laufleistung | `kilometerstand` |
| Kraftstoffart | `fuel` |
| PS / kW | `horsepower` |
| Getriebeart | `transmission` |
| Karosserieform | `body` (`body_evidence="detail"`) |
| Preis | `preis_eur` |

Für die deutsche SEARCH API ist dieses Mapping **noch nicht belegbar** — die
GraphQL-Felder sind nicht öffentlich.

---

## 4. mobile.de

| | |
|---|---|
| A Produktname | **Search-API** (auch "Ad-Integration"), `services.mobile.de/docs/search-api.html` |
| B Zweck | Anzeigen suchen und einzeln herunterladen |
| C Suchen? | **Ja**, umfangreich |
| D Einzel-Listing? | **Ja** — `GET /search-api/ad/{ad-key}` |
| E Felder | s.u. — sehr gut, **bereits im Suchergebnis** (Sandbox-verifiziert) |
| F Sandbox | **In der Doku nicht erwähnt** |
| G Auth | HTTP Basic Auth (Benutzername/Passwort), Zugang über Customer Support |
| H Rate Limits | Nicht dokumentiert. Dokumentiert ist: **max. 2000 Anzeigen** über paginierte Ergebnisseiten, Seitengröße max. 100 (Default 20) |
| I Preis | Nicht öffentlich |
| J | **nicht öffentlich / Anbieterantwort ausstehend** |
| K Median/Spanne bilden | **UNKNOWN** — in der technischen Doku nicht geregelt; hängt an den API-AGB |
| L Vergleichsangebote anzeigen | **UNKNOWN** |
| M Cachen/Speichern | **UNKNOWN** |
| N Status | **YELLOW** |

**Technisch der stärkste Kandidat.** Die dokumentierten Filter decken VIRAs
Zielprofil praktisch vollständig ab: Make, Model, Modellbeschreibung,
Fahrzeugklasse/-kategorie, Kraftstoff, **Leistung (kW)**, **Getriebe**, Hubraum,
Erstzulassung, Baujahr, **Laufleistung**, Vorbesitzer, Unfallfrei, Scheckheft,
Farbe, Türen, Sitze, Preisspanne, MwSt-Ausweis, Emissionsklasse, Verbrauch,
**Umkreissuche per PLZ+Radius (nur Deutschland)**, Anbietertyp (Händler/privat),
Änderungs-/Erstellzeitpunkt, Bildanzahl.

**Der Vorbehalt aus der Dokumentanalyse — durch Sandbox-Verifikation überholt:**

> Suchergebnisse liefern *weniger* Daten als der Direktabruf per ad-key.

Aus dieser Aussage der öffentlichen Dokumentation wurde zunächst abgeleitet,
Motor-/Verkaufsbezeichnung, Leistung, Getriebe und Karosserie stünden **nicht**
im Suchergebnis — also genau die Felder, die in Etappe 1 über `sehr_aehnlich`
vs. `ungeeignet` entscheiden.

**Sandbox-Verifikation zeigt einen umfangreicheren Search-Response als aus der
vorherigen Dokumentanalyse abgeleitet.** Der offizielle Sandbox-Zugang liefert
je Anzeige bereits im Suchergebnis: `mobileAdId`, `detailPageUrl`, `make`,
`model`, **`modelDescription`**, `constructionYear`, `firstRegistration`,
`mileage`, `fuel`, **`power`**, **`gearbox`**, **`category`**, `vehicleClass`,
`price`, `seller`, `condition`, `damageUnrepaired`.

Der Direktabruf `GET /search-api/ad/{ad-key}` ergänzt gegenüber der Suche nur
`description`, `kba`, `priceRating` und `consumptionUnit`.

→ **Korrigierte Konsequenz für VIRA:** Für den Kern-Marktvergleich ist der
Detailabruf **derzeit nicht erforderlich** — eine Suchanfrage je Fahrzeug
genügt. Das entschärft den zuvor angenommenen Volumen-/Kostentreiber
(statt 1 + N Requests je Fahrzeug nur 1), bleibt aber ein Punkt für die
kommerzielle Klärung, weil Rate-Limits und Abrechnungsmodell weiterhin
unbekannt sind.

**Feldformate (Sandbox-verifiziert, nicht aus der Doku abgeleitet):**

| Feld | Reales Format | Konsequenz für VIRA |
|---|---|---|
| `power` | nackter `int` **in kW**, ohne Einheitsfeld | Umrechnung **kW × 1,35962 → PS** zwingend; `Preisbeobachtung.horsepower` ist PS (`_RE_PS` in `marktvergleich.py`) |
| `firstRegistration` | String **`"YYYYMM"`** ohne Trennzeichen (z.B. `"200901"`) | Jahr = erste 4 Zeichen; `constructionYear` nur als Fallback (in der Stichprobe oft `null`, wenn `firstRegistration` gesetzt ist) |
| `price.consumerPriceGross` | **Dezimalstring** (`"15200.00"`), nicht `int`/`float`; daneben `currency`, `type` | `float()`-Konvertierung, und `currency == "EUR"` prüfen statt annehmen |
| `vehicleClass` | in der Stichprobe durchgängig `"Car"` | Fahrzeug-Obertyp, **keine** Karosserieinformation — trägt nichts zu `body` bei |

Diese Korrektur betrifft **ausschließlich die technische Feldlage**. An der
Bewertung der Nutzungsrechte ändert sie nichts: Sandbox-Zugang ist kein
Produktivvertrag, Status bleibt **YELLOW**, kommerzielle Nutzungsrechte und
Preis weiterhin **Anbieterantwort ausstehend**.

### Feld-Mapping mobile.de → VIRA

Quelle = wo das Feld tatsächlich herkommt. Alle Angaben Sandbox-verifiziert.

| mobile.de | VIRA | Quelle |
|---|---|---|
| `mobileAdId` | `listing_id`, `listing_key` | Suche |
| `detailPageUrl` | `detail_url`, `quelle_url` | Suche |
| `make` / `model` | `make` / `model` | Suche |
| `modelDescription` | Listing-Freitext (Motor-Evidenz nur geprüft, siehe unten) | **Suche** |
| `firstRegistration` (`YYYYMM`) / `constructionYear` | `baujahr` | Suche |
| `mileage` | `kilometerstand` | Suche |
| `fuel` | `fuel` | Suche |
| `power` (kW) | `horsepower` (kW × 1,35962 → PS) | **Suche** |
| `gearbox` | `transmission` | **Suche** |
| `category` | `body` (nur bei eindeutigem Enum) | **Suche** |
| `vehicleClass` | — (durchgängig `"Car"`, keine Karosserie) | Suche |
| `price.consumerPriceGross` (Dezimalstring, `currency` prüfen) | `preis_eur` | Suche |
| `seller` | Händler/privat (nützlich) | Suche |
| — | `generation` | **nicht geliefert** → bleibt `unknown` |

`modelDescription` ist **Freitext** und mischt Modell, Motor, Ausstattung und
Karosserie ("Focus Turnier 1.6 EB Titanium XENON SHZ FSHZ TEM"). Es ist damit
Listing-Evidenz, aber **keine** exakte Motorbezeichnung — eine Gleichsetzung
`modelDescription == engine_variant` wäre genau die Art ungeprüfter Annahme, die
Etappe 1 aus der Motor-Evidenz entfernt hat.

---

## 5. AutoUncle

| | |
|---|---|
| A Produktname | AutoUncle Automotive API (`b2b.autouncle.com/en-gb/automotive-api`) |
| B Zweck | Fahrzeugbewertung für Automotive-Unternehmen, Banken, Leasing, Versicherung |
| C Suchen? | **UNKNOWN** — beworben wird *Bewertung eines Fahrzeugs*, nicht Inseratssuche |
| D Einzel-Listing? | **UNKNOWN** |
| E Felder | Beworben: `market value`, `deal rating`, `sales-time forecast`, **`live comparables`**. **Kein öffentliches Schema.** |
| F Sandbox | Nicht öffentlich dokumentiert |
| G Auth | API-Key |
| H Rate Limits | Nicht öffentlich |
| I Preis | Nicht öffentlich |
| J | **nicht öffentlich / Anbieterantwort ausstehend** |
| K Median/Spanne selbst bilden | **UNKNOWN** |
| L Vergleichsangebote anzeigen | **UNKNOWN** |
| M Cachen/Speichern | **UNKNOWN** |
| N Status | **YELLOW** |

Beworbene Abdeckung: 8,6 Mio. Live-Inserate/Tag aus 2.600+ Quellen in
**14 EU-Ländern, Deutschland eingeschlossen**, tägliche Aktualisierung,
100+ Bewertungsindikatoren.

### Die entscheidende offene Frage: A oder B?

Die Produktseite nennt "live comparables" als Teil der Antwort, **veröffentlicht
aber kein Response-Schema**. Damit ist nicht belegbar, ob VIRA

- **(A)** nur einen fertigen AutoUncle-Marktwert anzeigen könnte, oder
- **(B)** je Comparable die Felder Preis / km / Baujahr / Marke / Modell /
  Motorbezeichnung / Kraftstoff / Leistung / Quell-URL bekäme und die eigene
  Etappe-1-Analyse darauf anwenden könnte.

Das ist **nicht annehmbar, sondern zu erfragen.** Konkret zu stellende Fragen:

1. Enthält `comparables` je Eintrag Preis, Laufleistung, Erstzulassung, Marke,
   Modell, **Motor-/Verkaufsbezeichnung**, Kraftstoff, Leistung, Karosserie?
2. Ist je Comparable eine **Quell-URL oder stabile Listing-ID** enthalten?
   (VIRA braucht sie für Dedupe **und** für die Belegkette im Bericht.)
3. Wie viele Comparables je Anfrage, und nach welchem Kriterium ausgewählt?
4. Dürfen wir aus den Comparables **eigene** abgeleitete Werte (Median, Spanne,
   Preisurteil) berechnen und ausgeben — oder nur den AutoUncle-Wert zeigen?

Ohne Antwort auf 1 und 2 ist AutoUncle für VIRAs Produktkern **nicht bewertbar**:
Fall A wäre ein Ersatz der Etappe-1-Engine (fremder Marktwert), Fall B ihr idealer
Treibstoff. Beides derselbe Anbieter, völlig verschiedene Produkte.

---

## 6. Kleinanzeigen

| | |
|---|---|
| A Produktname | Partner-/Fahrzeugschnittstellen (`de.kleinanzeigen.com/partner`) |
| B Zweck | **Import** von Daten *nach* Kleinanzeigen (Inserate einstellen/pflegen) |
| C Suchen? | **Nein** — kein offizieller Lesezugang dokumentiert |
| D Einzel-Listing? | Nein |
| E–I | entfällt |
| J | Anbieterantwort ausstehend |
| K / L / M | Nein bzw. entfällt |
| N Status | **RED** (mit offener Partnerschaftsanfrage → siehe §12) |

Die dokumentierten Schnittstellen sind **eingehend**: Daten werden *zu*
Kleinanzeigen importiert (Anzeigenverwaltungs-Tools, Händlersoftware). Ein
offizieller ausgehender Such-/Lesezugang für Dritte ist nicht dokumentiert.

**Ausdrücklich nicht als Lösung geführt:** die im Umlauf befindlichen
Dritt-"Kleinanzeigen-APIs" (Agent-/RapidAPI-/Scraper-Angebote). Das sind
Scraping-Dienste ohne Freigabe des Betreibers — keine Kandidaten,
unabhängig davon, wie gut sie funktionieren.

Bittere Ironie, die im Bericht stehen muss: kleinanzeigen.de ist empirisch die
**einzige** Domain, die VIRA bisher zuverlässig verwertbare Preisdatenpunkte
geliefert hat (siehe `app/search_provider.py`, BMW-320d-Messung) — und
gleichzeitig die einzige der fünf ohne erkennbaren offiziellen Lesezugang.

---

## 7. PKW.de

| | |
|---|---|
| A Produktname | pkw.de API (XML) |
| B Zweck | **Angeschlossene Händler** stellen *ihre eigenen* auf pkw.de inserierten Fahrzeuge im eigenen Webauftritt dar |
| C Suchen? | Innerhalb der **eigenen Händlerpage** (Marke, Preis, Kilometerstand) — kein marktweiter Lesezugang dokumentiert |
| D Einzel-Listing? | Für eigene Fahrzeuge |
| E Felder | XML; Marke, Preis, Kilometerstand ausdrücklich genannt |
| F Sandbox | Nicht dokumentiert |
| G Auth | Nicht dokumentiert |
| H Rate Limits | Nicht dokumentiert |
| I Preis | **Kostenlos** — aber nur für angeschlossene Händler mit ≥24 Monaten Restlaufzeit, mit Pflichthinweis "powered by pkw.de" |
| J | Anbieterantwort ausstehend |
| K/L/M | UNKNOWN |
| N Status | **UNKNOWN**, tendenziell RED für den VIRA-Anwendungsfall |

Zwei Vorbehalte, die den Status drücken: Die belegbare Quelle ist eine
**Pressemitteilung von 2010** — der heutige Stand ist nicht öffentlich
verifizierbar. Und das Produkt ist konstruktiv ein *Händler-Schaufenster*, keine
Marktdatenquelle: VIRA ist kein angeschlossener pkw.de-Händler und würde auch
mit Zugang nur fremde Einzelbestände statt eines Marktquerschnitts sehen.

---

## 8. Weitere seriöse Kandidaten

Bewusst kurz gehalten (Qualität vor Menge). Aufgenommen nur, wo ein realer
offizieller Datenzugang existiert.

| Kandidat | Was es liefert | Warum es VIRA (nicht) löst | Status |
|---|---|---|---|
| **Schwacke / Autovista (AutovistaAPI)** | Fahrzeugidentifikation per Code, technische Daten aller europäischen Fahrzeuge, **Restwertprognosen**; als Datenfeed oder API | Deutscher Marktstandard mit echtem API-Produkt. Liefert **Werte, keine Comparables** — VIRAs Etappe-1-Engine hätte nichts zu bewerten. Wäre ein *anderes Produkt*, kein Treibstoff für das bestehende. Für die Fahrzeug-DB (Motorvarianten, technische Daten, Verifikation) dagegen hochrelevant. | **YELLOW** (anderer Zweck) |
| **DAT / SilverDAT** | ca. 400 zertifizierte Schnittstellen, Gebrauchtwagenwerte, VIN-Abfrage, Reparaturkosten | Wie Schwacke: Bewertung statt Inserate. Zugang laut Website auf DMS-/Werkstattsoftware-/Händler-/Versicherungs-/Sachverständigen-Umfeld ausgerichtet. | **YELLOW** (anderer Zweck) |
| **MarketCheck** | Fahrzeuginserate-Aggregator mit API | **Geprüft und verworfen:** Abdeckung US/Kanada/UK — **kein Deutschland.** Damit korrigiert diese Etappe die Vermutung in `app/search_provider.py` (Punkt 2), die eine DE-Abdeckung noch für prüfenswert hielt. | **RED** |
| Zweiter Such-Provider (Serper, Brave, Bing) | allgemeine Websuche | Ändert **nichts** an der Freigabelage: das Ergebnis wären weiterhin Portalseiten ohne Freigabe. Löst kein einziges Etappe-2-Kriterium — nur ein Discovery-Problem, das VIRA nicht (mehr) hat. | **RED** (löst die falsche Frage) |

---

## 9. Source-Matrix

| Source | Offizieller Zugang | Search Listings? | Comparables? | VIRA-Felder ausreichend? | Sandbox? | Preis bekannt? | Nutzungsrecht bestätigt? | Architektur-Fit | Status |
|---|---|---|---|---|---|---|---|---|---|
| **mobile.de Search API** | ja, dokumentiert (Support/API-Account) | **ja** | ja (Einzelanzeigen) | **ja, bereits im Suchergebnis** (Sandbox-verifiziert) | nicht dokumentiert | nein | **nein** | **EASY** | **YELLOW** |
| **AutoScout24 DE SEARCH API** | ja, Vertriebskanal dokumentiert | ja (beworben) | ja (beworben) | **UNKNOWN** (Felder nicht öffentlich) | UNKNOWN | nein | **nein** | **MEDIUM** | **YELLOW** |
| **AutoUncle Automotive API** | ja, B2B-Produkt | UNKNOWN | **beworben, Schema unbelegt** | **UNKNOWN** (alles hängt daran) | UNKNOWN | nein | **nein** | **EASY–MEDIUM** (Fall B) / **HARD** (Fall A) | **YELLOW** |
| **Kleinanzeigen** | nur **eingehend** (Import) | **nein** | nein | — | — | — | nein | — | **RED** |
| **PKW.de** | Händler-Schaufenster-API (Beleg 2010) | nur eigene Fahrzeuge | nein | teilweise (Marke/Preis/km) | nein | ja (0 €, aber Händlerbindung) | nein | — | **UNKNOWN → RED** |
| AutoScout24.ch Public API | **ja, voll öffentlich + Pre-Prod** | **ja** | ja | **ja** | **ja** | nein | nein | EASY | **RED** (Schweiz, falscher Markt) |
| Schwacke/Autovista | ja | nein | nein (Werte) | anderer Zweck | UNKNOWN | nein | nein | — | **YELLOW** (anderer Zweck) |
| DAT SilverDAT | ja | nein | nein (Werte) | anderer Zweck | UNKNOWN | nein | nein | — | **YELLOW** (anderer Zweck) |
| MarketCheck | ja | ja | ja | **keine DE-Abdeckung** | — | ja (ab $8) | — | — | **RED** |

**Kein einziger GREEN.** Für GREEN fehlt bei jedem Kandidaten dasselbe: eine
Anbieterbestätigung, dass unser konkreter Anwendungsfall (abgeleitete Marktwerte
in einem Endkundenprodukt) abgedeckt ist.

---

## 10. Architektur-Fit

| Provider | Fit | Begründung |
|---|---|---|
| **mobile.de** | **EASY** | Strukturiertes JSON/XML, dokumentierte Pagination (max. 2000, Seitengröße ≤100), HTTP Basic Auth, stabile Ad-ID als `listing_key`, Detailseiten-URL frei Haus. Normalisierung minimal: kW→PS, Kraftstoff-/Getriebe-Enums auf VIRAs Vokabular. **Kein einziger Regex nötig.** Der zuvor angenommene zweistufige Abruf (search → ad-key) entfällt: Sandbox-Verifikation zeigt alle kernrelevanten Felder bereits im Suchergebnis. |
| **AutoScout24 DE** | **MEDIUM** | GraphQL statt REST — der bestehende `httpx`-Client trägt das, aber Query-Aufbau und Feldauswahl sind ein neues Muster im Projekt. Größeres Risiko: die Felder sind unbekannt; MEDIUM ist eine Schätzung unter Vorbehalt, keine Messung. |
| **AutoUncle Fall B** (Comparables mit Feldern) | **EASY–MEDIUM** | Ein Aufruf je Fahrzeug statt einer Query-Ladder — die gesamte `marktrecherche`-Stufenlogik würde für diesen Provider entfallen. Offen: ob je Comparable eine stabile ID/URL kommt (sonst bricht `_dedupliziere` / `_cap_pro_url`). |
| **AutoUncle Fall A** (nur Marktwert) | **HARD** | Kein Architekturproblem, sondern ein Produktproblem: es gäbe nichts zu bewerten. `analysiere_markt`, `_bewerte`, `preisurteil` — die komplette Etappe-1-Engine wäre umgangen. Das ist keine Integration, das ist ein Produktwechsel. |
| Kleinanzeigen / PKW.de | — | Nicht bewertbar, kein Lesezugang. |

---

## 11. MarketDataProvider — Empfehlung

**Es existiert bereits eine Abstraktion — aber die falsche.**

`app/search_provider.py` definiert `SearchProvider.search(query, ...) -> (results, fehler)`.
Das ist eine **Suchmaschinen**-Schnittstelle: Query rein, Textseiten raus. Genau
das Modell, das kein API-Provider bedient. Ein zweiter Tavily ließe sich
dahinter hängen; mobile.de nicht.

Empfohlene *kleinste* Schnittstelle — **eine Ebene höher**:

```python
class MarketDataProvider(Protocol):
    name: str
    async def find_comparables(
        self, identity: VehicleIdentity, *, limit: int = 20,
    ) -> tuple[list[Preisbeobachtung], bool]:
        """Rohe, UNBEWERTETE Vergleichsbeobachtungen + technischer-Fehler-Flag.
        Bewertung bleibt ausschliesslich in marktvergleich._bewerte(b, ziel)."""
```

Der entscheidende Zuschnitt: der Provider liefert `Preisbeobachtung` **vor** der
Bewertung. Damit bleibt die gesamte Etappe-1-Logik (Modelltreue, Motor-Evidence,
Trust-Gates, Similarity, Dedupe, Median, Preisurteil) unangetastet und
providerunabhängig — sie ist der Teil, der geprüft ist und nicht neu verhandelt
werden darf.

Implementierungen später: `TavilyTextProvider` (kapselt den heutigen Weg
Query-Ladder → Regex → `Preisbeobachtung`), `MobileDeProvider`,
`AutoScout24Provider`, `FixtureProvider` (Replays aus `diagnose_runs/`).

**Betroffene Dateien bei der späteren Migration:**

| Datei | Eingriff |
|---|---|
| `app/marktrecherche.py` | `_laufe_stufen` ruft den Provider statt `tavily_search_mit_status` |
| `app/marktvergleich.py` | `analysiere_markt` braucht einen zweiten Eingang: fertige `Preisbeobachtung`-Liste statt `web_results`; `_extrahiere_aus_text` wandert in den Tavily-Provider |
| `app/search_provider.py` | wird zur *inneren* Schnittstelle des Tavily-Providers |
| `app/kaufcheck.py` / `app/verkaufscheck.py` | Providerauswahl statt fester Tavily-Annahme |
| `_source_policy_testharness.py`, `test_source_boundary.py` | Freigabe je Provider statt nur je Domain |

**Migrationsrisiko: MITTEL–HOCH, und deshalb heute nicht angefasst.**
`analysiere_markt` ist die Stelle, an der Etappe 1 ihre gesamte Ehrlichkeitslogik
verankert hat (Kategorie-/Einzelinserat-Klassifikation, `window_fallback`,
`source_type`, Hintergrund-Domains). Ein zweiter Eingang dort ist kein Refactor,
sondern eine zweite Wahrheit — die BMW-Replays und die 44er-Suite sind der
einzige Schutz davor, und die müssen **vor** dem Umbau als Regressionsnetz
festgeschrieben werden.

**Empfehlung: nicht jetzt bauen.** Die Schnittstelle wird erst dann korrekt
zuschneidbar, wenn wir das erste reale Provider-Schema in der Hand haben. Eine
heute erfundene Abstraktion würde mit hoher Wahrscheinlichkeit an der echten
Antwort vorbeigehen — dieselbe Falle, in die `SearchProvider` bereits getappt ist.

---

## 12. Fehlende Provider-Antworten

| Provider | Anfrage raus | Antwort | Ohne Antwort nicht entscheidbar |
|---|---|---|---|
| mobile.de | ja | **ausstehend** | Preis-/Volumenmodell, Rate-Limits, AGB zu abgeleiteten Werten, Produktivzugang (Sandbox liegt vor) |
| AutoUncle | ja | **ausstehend** | **Comparables-Schema (Fall A vs. B)**, URL/ID je Comparable, Recht auf eigene Ableitungen |
| Kleinanzeigen | ja | **ausstehend** | ob überhaupt ein Lesezugang existiert |
| PKW.de | ja | **ausstehend** | ob die API von 2010 heute noch existiert und für Nicht-Händler zugänglich ist |
| AutoScout24 DE | separat, direkt | **ausstehend** | Feldumfang der SEARCH API, Auth, Sandbox, Preis, Nutzungsrechte |

---

## 13. Wer zuerst — wenn Zugang kommt

1. **mobile.de** — technisch bester Kandidat. Der einzige mit **öffentlich
   dokumentiertem Feldumfang, dokumentierter Pagination und dokumentiertem
   Auth-Verfahren**. Das Mapping ist heute schon schreibbar, EASY, und die
   Deutschland-Umkreissuche ist ein Feature, das VIRA per Websuche nie hatte.
   Sandbox-Zugang liegt vor und ist technisch verifiziert; ein Detailabruf je
   Treffer ist für den Kernvergleich nicht nötig.
2. **AutoUncle** — wirtschaftlich wahrscheinlich der interessanteste, **falls
   Fall B**. Ein API-Aufruf je Fahrzeug gegen eine ganze Query-Ladder, 14 Länder,
   DE inklusive, tägliche Aktualisierung. Das Risiko ist binär: bei Fall A ist es
   kein Kandidat, sondern ein Produktwechsel. Deshalb nicht Platz 1 — die
   Vorbedingung ist unbelegt.
3. **AutoScout24 DE SEARCH API** — beste Backup-Option. Größter deutscher
   Marktplatz neben mobile.de, offizieller Vertriebskanal für genau diesen Zweck,
   und der Konzern betreibt in der Schweiz nachweislich bereits eine passende
   Such-API. Nachrangig nur, weil heute **null** technische Details öffentlich
   belegbar sind.

---

## 14. Etappe-1-Beobachtungen (NUR NOTIERT, NICHT REPARIERT)

**(a) Freigabeliste matcht per Teilstring — gemessen, nicht vermutet.**
`app/web_search._enthaelt_domain` prüft `eintrag in domain`. Gemessen mit
Freigabe `{"mobile.de"}`:

| URL | `darf_preisbildend_sein` |
|---|---|
| `https://suchen.mobile.de/x` | True *(gewollt — Subdomain)* |
| `https://www.premiummobile.de/x` | **True** *(fremde Domain)* |
| `https://mobile.de.angebot-billig.ru/x` | **True** *(fremde Domain)* |

Heute folgenlos, weil die Production-Allowlist leer ist. Es wird **genau in dem
Moment** scharf, in dem Etappe 3 die erste Quelle freischaltet — dann könnte eine
fremde Domain den Marktpreis bestimmen. Gehört vor jeder Aktivierung behoben.

**(b) `app/search_provider.py`, Vorschlag 2 ist inzwischen widerlegt.**
Der Docstring führt MarketCheck als "prüfen, ob DE-Abdeckung vorhanden ist
(NICHT verifiziert)". Diese Etappe hat es geprüft: US/Kanada/UK, kein
Deutschland. Reine Dokumentationskorrektur, kein Codefehler.

---

## 15. Ergebnis

- **Production-Allowlist weiterhin leer: JA.** `ALLOWED_MARKET_SOURCES` unverändert.
  Keine Quelle aktiviert, keine Umgebungsvariable gesetzt.
- **Keine Produktionsänderung.** Einziges Artefakt dieser Etappe ist dieses Dokument.
- **ETAPPE 2 = PENDING.**
  Die technische Qualifikation ist so weit abgeschlossen, wie sie ohne
  Anbieterzugang gehen kann: drei belastbare YELLOW-Kandidaten, zwei belegte
  Ausschlüsse (Kleinanzeigen ohne Lesezugang, MarketCheck ohne DE-Abdeckung),
  ein klarer Erstintegrationskandidat. **BLOCKED wäre falsch** — es fehlt keine
  Arbeit von uns, sondern fünf ausstehende Anbieterantworten. **PASS wäre
  unehrlich** — kein Kandidat ist GREEN, und kein Nutzungsrecht ist bestätigt.

---

## Quellen

Alle Aussagen oben stammen aus offizieller Anbieterdokumentation bzw. offiziellen
Anbieterseiten. Es wurde **keine** Fahrzeugbörse gescrapt, gecrawlt oder über
inoffizielle Endpunkte abgefragt.

- mobile.de Search API: https://services.mobile.de/docs/search-api.html
- mobile.de API-Übersicht: https://services.mobile.de/manual/index.html
- AutoScout24 SEARCH API (DE): https://www.autoscout24.de/haendlerportal/schnittstelle-search-api/
- AutoScout24 Listing Creation API (DE): https://www.autoscout24.de/haendlerportal/api/
- AutoScout24.ch OpenAPI-Spec: https://github.com/smg-automotive/autoscout24-api-specs
- AutoUncle Automotive API: https://b2b.autouncle.com/en-gb/automotive-api
- Kleinanzeigen Partnerschnittstellen: https://de.kleinanzeigen.com/partner
- pkw.de API (Pressemitteilung 2010): https://www.adzine.de/2010/03/pkw-de-bietet-haendlern-neue-schnittstelle-online-publishing/
- DAT Schnittstellen: https://www.dat.de/schnittstellen/
- Schwacke/Autovista API: https://schwacke.de/produkt/autovistaapi/
- MarketCheck APIs: https://www.marketcheck.com/apis/
