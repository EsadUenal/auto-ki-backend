from __future__ import annotations

"""
P1-3 (Ausbau) — Basis-Checklisten: der allgemeine professionelle Prüfstandard.

Die fahrzeugspezifischen Kaufaktionen (app/kaufaktionen.py) entstehen ausschließlich
aus echter Evidence — und sind deshalb bei dünner Datenlage zwangsläufig kurz oder
leer. Damit der Nutzer trotzdem mit einer praktisch brauchbaren Checkliste zum
Termin fahren kann, tritt hier eine ZWEITE Ebene daneben: allgemein anerkannte
Prüfpunkte, die für jeden Gebrauchtwagenkauf gelten.

Strikte Trennung (§3/§12 des Auftrags): Basis-Punkte werden NIE mit den
fahrzeugspezifischen vermischt. Sie tragen `typ="basis"` und liegen in einer
eigenen Liste, damit das Frontend "Bei diesem Fahrzeug besonders wichtig" von
"Allgemeine Checkliste" eindeutig unterscheiden und getrennt anordnen kann.

Warum das KEIN Widerspruch zur Evidence-Regel ist: ein Basis-Punkt behauptet nichts
über DIESES Fahrzeug. Er sagt nicht "hier ist Rost", sondern "sieh an den typischen
Stellen nach Rost". Deshalb ist eine leere `evidence_ids`-Liste hier korrekt und
ehrlich — der Punkt ist ausdrücklich als allgemeiner Prüfstandard gekennzeichnet,
nicht als Befund.

Formulierung (§16, Print-Tauglichkeit): jeder Punkt ist ein kurzer, für sich allein
verständlicher Satz. Die Listen werden später als vier EIGENSTÄNDIGE Arbeitsblätter
gedruckt — ohne die VIRA-Oberfläche daneben. "Dies prüfen." wäre dort wertlos.

Sicherheit (§7): kein Punkt verlangt ein riskantes oder unzulässiges Fahrmanöver.
Keine Vollbremsung im öffentlichen Verkehr, kein Ausreizen der Höchstgeschwindigkeit,
kein Grenzbereich. Wo eine Prüfung freie Strecke braucht, steht das als `hinweis`
dabei.

`deckt` (§18): Schlüssel der Bauteil-Wissenstabelle bzw. Themenschlüssel, die dieser
Basis-Punkt inhaltlich bereits abdeckt. Existiert für denselben Bereich ein
fahrzeugspezifischer Punkt mit diesem Schlüssel, wird der Basis-Punkt ausgeblendet —
der konkretere Punkt gewinnt. Ein Eintrag mit Sternchen ("rueckruf-*") wirkt als
Präfix. Bewusst sparsam gesetzt: nur wo die spezifische Version die allgemeine
WIRKLICH ersetzt, nicht bei bloß verwandten Themen.
"""

# Jeder Eintrag: (schluessel, gruppe, titel, aktion, hinweis|None, deckt)
_Eintrag = tuple[str, str, str, str, str | None, tuple[str, ...]]


# ══ BESICHTIGUNG ═════════════════════════════════════════════════════════════
# Ziel 12–20 Punkte. Reihenfolge = sinnvoller Ablauf vor Ort: erst außen um das
# Fahrzeug herum, dann Räder/Bremsen, dann unten und im Motorraum, zuletzt innen.
BASIS_BESICHTIGUNG: tuple[_Eintrag, ...] = (
    ("spaltmasse", "Außen",
     "Spaltmaße rundum vergleichen",
     "Türen, Hauben und Kotflügel auf gleichmäßige Spalten prüfen — ungleiche Spaltmaße "
     "deuten auf eine Unfallreparatur hin.",
     None, ("karosserie",)),
    ("lack", "Außen",
     "Lack bei Tageslicht schräg ansehen",
     "Flach über die Flächen schauen: Farbtonunterschiede zwischen den Teilen, Wellen im "
     "Lack und Overspray an Gummidichtungen deuten auf Nachlackierung hin.",
     "Bei Regen oder Dunkelheit ist das kaum erkennbar — möglichst bei Tageslicht ansehen.",
     ()),
    ("rost", "Außen",
     "Rost an den typischen Stellen suchen",
     "Radläufe, Schweller, Türunterkanten, Heckklappe und den Bereich um den Tankdeckel "
     "auf Blasen und Rostkanten absuchen.",
     None, ("rost",)),
    ("scheiben", "Außen",
     "Scheiben und Herstellerzeichen kontrollieren",
     "Alle Scheiben auf Steinschläge und Risse prüfen; ein abweichendes Herstellerzeichen "
     "an einer einzelnen Scheibe deutet auf einen Tausch hin.",
     None, ()),
    ("beleuchtung", "Außen",
     "Beleuchtung vollständig durchschalten",
     "Abblendlicht, Fernlicht, Blinker, Brems-, Rückfahr- und Nebelleuchten einzeln "
     "einschalten und von außen kontrollieren lassen.",
     None, ("beleuchtung",)),
    ("reifen", "Räder und Bremsen",
     "Reifen an allen vier Rädern prüfen",
     "Profiltiefe, DOT-Alter, Fabrikat und Abriebbild vergleichen — einseitiger Abrieb "
     "deutet auf Fahrwerk oder falsche Spureinstellung hin.",
     None, ("raeder",)),
    ("bremsen", "Räder und Bremsen",
     "Bremsscheiben und Beläge durch die Felge ansehen",
     "Auf tiefe Riefen, eine ausgeprägte Rostkante am Scheibenrand und die verbleibende "
     "Belagstärke achten.",
     None, ("bremsen",)),
    ("unterboden", "Unten und Motorraum",
     "Unterboden ansehen, soweit einsehbar",
     "Mit einer Taschenlampe unter das Fahrzeug leuchten: Korrosion, frisch aufgetragener "
     "Unterbodenschutz und verzogene Blechteile sind auffällig.",
     None, ()),
    ("fluessigkeiten", "Unten und Motorraum",
     "Auf Flüssigkeitsverlust achten",
     "Den Boden unter dem Stellplatz und den Motorraum auf Öl-, Kühlmittel- und "
     "Bremsflüssigkeitsspuren prüfen.",
     None, ()),
    ("motorraum", "Unten und Motorraum",
     "Motorraum im kalten Zustand ansehen",
     "Ölstand am Peilstab, Kühlmittelstand und den Öleinfülldeckel auf helle Emulsion "
     "prüfen; ein auffällig frisch gereinigter Motorraum kann Undichtigkeiten verdecken.",
     None, ("oelverlust",)),
    ("kaltstart", "Unten und Motorraum",
     "Kaltstart abwarten",
     "Das Fahrzeug möglichst kalt starten lassen und auf Startdauer, Rauch aus dem "
     "Auspuff und Geräusche in den ersten Sekunden achten.",
     "Vorher vereinbaren, dass der Verkäufer den Motor nicht warmlaufen lässt.",
     ("starterbatterie",)),
    ("warnlampen", "Innen und Elektrik",
     "Warnleuchten beim Einschalten der Zündung beobachten",
     "Alle Kontrollleuchten müssen kurz aufleuchten und danach erlöschen — auch Airbag, "
     "ABS, Motor und Reifendruck.",
     "Eine Leuchte, die gar nicht erst angeht, kann bewusst entfernt worden sein.",
     ()),
    ("innenraum", "Innen und Elektrik",
     "Innenraum-Verschleiß mit der Laufleistung abgleichen",
     "Lenkrad, Schaltknauf, Pedalgummis und Fahrersitz ansehen — starke Abnutzung passt "
     "nicht zu einer niedrigen angegebenen Laufleistung.",
     None, ("innenraum",)),
    ("feuchtigkeit", "Innen und Elektrik",
     "Auf Feuchtigkeit im Innenraum prüfen",
     "Fußräume, Reserveradmulde und Innenhimmel abtasten; ein auffälliger Duftbaum kann "
     "Moder- oder Schimmelgeruch überdecken.",
     None, ("dach_fenster",)),
    ("elektrik", "Innen und Elektrik",
     "Elektrik im Stand durchtesten",
     "Fensterheber, Spiegel, Sitzverstellung, Radio, Display und alle Bedienelemente "
     "einzeln betätigen.",
     None, ("infotainment",)),
    ("klima", "Innen und Elektrik",
     "Klimaanlage im Stand prüfen",
     "Klimaanlage einschalten und prüfen, ob die Luft nach kurzer Zeit spürbar kalt wird; "
     "auf Geruch und Kompressorgeräusch achten.",
     None, ("klimaanlage",)),
    ("fin", "Identität",
     "FIN am Fahrzeug mit den Papieren vergleichen",
     "Die Fahrgestellnummer an Windschutzscheibe und Typschild ablesen und mit der "
     "Zulassungsbescheinigung abgleichen.",
     None, ()),
    ("schluessel", "Identität",
     "Alle Schlüssel ausprobieren",
     "Jeden übergebenen Schlüssel einmal am Fahrzeug testen — ein Ersatzschlüssel kostet "
     "je nach Modell einen dreistelligen Betrag.",
     None, ()),
)


# ══ PROBEFAHRT ═══════════════════════════════════════════════════════════════
# Ziel 15–25 Punkte, gegliedert nach dem tatsächlichen Ablauf einer Probefahrt.
# Sicherheitsgrundsatz: jeder Punkt ist im normalen Straßenverkehr regelkonform
# durchführbar. Keine Vollbremsung, kein Ausreizen der Höchstgeschwindigkeit.
BASIS_PROBEFAHRT: tuple[_Eintrag, ...] = (
    # ── Vor Fahrtbeginn ──────────────────────────────────────────────────────
    ("kaltstart", "Vor Fahrtbeginn",
     "Motor kalt starten lassen",
     "Ein bereits warmgelaufener Motor verdeckt Startprobleme und Kaltstartgeräusche — "
     "auf einen echten Kaltstart bestehen.",
     None, ()),
    ("warnlampen", "Vor Fahrtbeginn",
     "Warnleuchten nach dem Start kontrollieren",
     "Nach dem Anspringen darf keine Kontrollleuchte dauerhaft leuchten — besonders "
     "Motor, ABS, Airbag und Ladekontrolle.",
     None, ()),
    ("leerlauf", "Vor Fahrtbeginn",
     "Leerlauf eine Minute beobachten",
     "Die Leerlaufdrehzahl soll ruhig und gleichmäßig bleiben, ohne Schwanken oder "
     "Absacken.",
     None, ()),
    ("startgeraeusche", "Vor Fahrtbeginn",
     "Auf Geräusche direkt nach dem Start hören",
     "Fenster öffnen und in den ersten Sekunden auf Rasseln, Klackern oder Pfeifen aus "
     "dem Motorraum achten.",
     None, ()),
    # ── Anfahren und niedrige Geschwindigkeit ────────────────────────────────
    ("anfahren", "Anfahren und Rangieren",
     "Anfahrverhalten mehrfach prüfen",
     "Mehrmals aus dem Stand anfahren: bei Handschaltung auf Rupfen und den Greifpunkt "
     "der Kupplung achten, bei Automatik auf ruckfreies Losfahren.",
     None, ("kupplung", "zweimassenschwungrad")),
    ("rangieren", "Anfahren und Rangieren",
     "Beim Rangieren mit vollem Lenkeinschlag hinhören",
     "Langsam mit voll eingeschlagenem Lenkrad fahren und auf Knacken oder Mahlen aus "
     "dem Antriebsstrang achten.",
     None, ("allradantrieb",)),
    ("lenkung", "Anfahren und Rangieren",
     "Lenkung bei niedriger Geschwindigkeit prüfen",
     "Die Lenkung soll gleichmäßig leichtgängig sein — kein Rucken, kein plötzliches "
     "Schwergängigwerden, keine Pumpgeräusche.",
     None, ("lenkung",)),
    ("rueckwaerts", "Anfahren und Rangieren",
     "Rückwärtsgang mehrfach einlegen und fahren",
     "Der Rückwärtsgang soll ohne Kratzen einrasten, bei Automatik ohne spürbaren "
     "Schlag beim Einlegen.",
     None, ()),
    # ── Normale Fahrt ────────────────────────────────────────────────────────
    ("geradeauslauf", "Normale Fahrt",
     "Geradeauslauf prüfen",
     "Auf gerader, ebener Strecke das Lenkrad kurz locker halten — das Fahrzeug soll "
     "nicht seitlich ziehen.",
     "Nur bei freier Strecke und ohne Gegenverkehr, Hände am Lenkrad lassen.",
     ()),
    ("beschleunigung", "Normale Fahrt",
     "Gleichmäßige Beschleunigung prüfen",
     "Aus mittlerer Drehzahl zügig beschleunigen: Die Leistung soll ohne Löcher, Ruckeln "
     "oder plötzlichen Einbruch anliegen.",
     None, ("turbolader", "zuendung", "einspritzung")),
    ("schalten", "Normale Fahrt",
     "Alle Gänge einmal durchfahren",
     "Jeden Gang inklusive der oberen Gänge nutzen; bei Automatik auf weiche, nicht "
     "verzögerte Gangwechsel achten.",
     None, ("getriebe", "automatikgetriebe")),
    ("last", "Normale Fahrt",
     "Verhalten unter Last an einer Steigung prüfen",
     "Wenn möglich eine Steigung hochfahren: Die Kupplung darf nicht durchrutschen und "
     "die Drehzahl nicht ohne entsprechenden Vortrieb hochlaufen.",
     None, ()),
    ("fahrbahn", "Normale Fahrt",
     "Über schlechte Fahrbahn fahren",
     "Kopfsteinpflaster oder Bodenwellen nutzen und auf Poltern, Knarzen und Klappern "
     "aus Fahrwerk und Innenraum achten.",
     None, ("fahrwerk", "luftfederung")),
    ("vibrationen", "Normale Fahrt",
     "Auf Vibrationen achten",
     "Lenkrad, Sitz und Pedale auf Vibrationen prüfen, die mit steigender Geschwindigkeit "
     "zunehmen.",
     None, ("raeder",)),
    ("temperatur", "Normale Fahrt",
     "Kühlmitteltemperatur beobachten",
     "Die Temperaturanzeige soll nach dem Warmlaufen konstant bleiben und weder schwanken "
     "noch weiter steigen.",
     None, ("kuehlung",)),
    ("assistenz", "Normale Fahrt",
     "Assistenz- und Komfortsysteme während der Fahrt nutzen",
     "Tempomat, Spurhalte- und Abstandsassistent sowie Rückfahrkamera einmal aktiv "
     "einsetzen, soweit vorhanden.",
     None, ()),
    # ── Bremsen ──────────────────────────────────────────────────────────────
    ("bremswirkung", "Bremsen",
     "Bremswirkung auf freier Strecke prüfen",
     "Aus mäßigem Tempo kontrolliert und deutlich abbremsen — das Fahrzeug soll "
     "gleichmäßig und ohne Nachlassen verzögern.",
     "Nur wenn kein Fahrzeug folgt und der Verkehr es sicher zulässt.",
     ("bremsen",)),
    ("bremse_ziehen", "Bremsen",
     "Auf einseitiges Ziehen beim Bremsen achten",
     "Beim Bremsen soll das Fahrzeug spurtreu bleiben und nicht nach links oder rechts "
     "ziehen.",
     None, ()),
    ("bremse_rubbeln", "Bremsen",
     "Auf Rubbeln beim Bremsen achten",
     "Pulsieren im Bremspedal oder Vibrieren im Lenkrad beim Bremsen deutet auf verzogene "
     "Bremsscheiben hin.",
     None, ()),
    ("feststellbremse", "Bremsen",
     "Feststellbremse an einer Steigung prüfen",
     "An einer Steigung anhalten und das Fahrzeug allein von der Feststellbremse halten "
     "lassen.",
     None, ()),
    # ── Höhere Geschwindigkeit ───────────────────────────────────────────────
    ("spurtreue", "Höhere Geschwindigkeit",
     "Spurtreue bei höherem Tempo prüfen",
     "Auf einer Schnellstraße prüfen, ob das Fahrzeug ruhig und spurtreu bleibt und das "
     "Lenkrad nicht zittert.",
     "Nur im Rahmen der zulässigen Geschwindigkeit und bei passender Verkehrslage.",
     ()),
    ("fahrgeraeusche", "Höhere Geschwindigkeit",
     "Wind- und Abrollgeräusche einordnen",
     "Bei konstantem Tempo das Radio ausschalten und auf auffällige Wind-, Abroll- oder "
     "Heulgeräusche hören.",
     "Nur im Rahmen der zulässigen Geschwindigkeit.",
     ("abgasanlage",)),
    # ── Nach der Fahrt ───────────────────────────────────────────────────────
    ("nach_warnmeldungen", "Nach der Fahrt",
     "Warnmeldungen nach der Fahrt prüfen",
     "Nach dem Abstellen die Zündung erneut einschalten und kontrollieren, ob neue "
     "Meldungen im Display erschienen sind.",
     None, ()),
    ("nach_geruch", "Nach der Fahrt",
     "Auf Gerüche achten",
     "Am Motorraum und an den Rädern auf Geruch nach verbranntem Öl, überhitzter Kupplung "
     "oder heißgelaufenen Bremsen achten.",
     None, ()),
    ("nach_fluessigkeit", "Nach der Fahrt",
     "Stellplatz nach der Fahrt kontrollieren",
     "Das Fahrzeug einige Minuten stehen lassen und den Boden darunter auf frische Tropfen "
     "prüfen.",
     None, ()),
)


# ══ VERKÄUFERFRAGEN ══════════════════════════════════════════════════════════
# Ziel 8–15 Fragen. Nur kaufrelevante Fragen — kein Smalltalk, keine Verhörliste.
# Bewusst KEINE Preisfrage: die Kaufaktionen sind vollständig marktpreisunabhängig.
BASIS_VERKAEUFERFRAGEN: tuple[_Eintrag, ...] = (
    ("eigentuemer", "Verkäufer und Eigentum",
     "Sind Sie der eingetragene Halter, oder verkaufen Sie im Auftrag?",
     "Klärt die Verkaufsberechtigung — bei Verkauf im Auftrag Vollmacht und Ausweis "
     "zeigen lassen.",
     None, ()),
    ("finanzierung", "Verkäufer und Eigentum",
     "Ist das Fahrzeug bezahlt, oder läuft noch eine Finanzierung?",
     "Bei laufender Finanzierung liegt die Zulassungsbescheinigung Teil II meist bei der "
     "Bank — das Eigentum vor der Zahlung klären.",
     None, ()),
    ("unfall", "Historie",
     "Hatte das Fahrzeug einen Unfall oder wurden Teile nachlackiert?",
     "Auch kleine, fachgerecht reparierte Schäden erfragen und die Antwort später im "
     "Kaufvertrag festhalten.",
     None, ("unfall",)),
    ("maengel", "Historie",
     "Welche Mängel oder Auffälligkeiten sind Ihnen aktuell bekannt?",
     "Offen gestellte Frage — die Antwort anschließend mit dem eigenen "
     "Besichtigungsergebnis abgleichen.",
     None, ()),
    ("reparaturen", "Historie",
     "Welche größeren Reparaturen wurden in den letzten zwei Jahren gemacht?",
     "Nach Bauteil, Werkstatt und Kilometerstand fragen und die Rechnungen zeigen lassen.",
     None, ()),
    ("wartung", "Wartung und Technik",
     "Wann war die letzte Wartung, und was wurde dabei gemacht?",
     "Datum, Kilometerstand und Umfang erfragen und den passenden Beleg dazu ansehen.",
     None, ()),
    ("fluessigkeit", "Wartung und Technik",
     "Verliert das Fahrzeug Öl oder andere Flüssigkeiten?",
     "Die Antwort mit dem eigenen Blick unter das Fahrzeug und in den Motorraum abgleichen.",
     None, ()),
    ("hu", "Wartung und Technik",
     "Wann war die letzte Hauptuntersuchung, und was stand im Prüfbericht?",
     "Den Bericht zeigen lassen — dort stehen auch Mängel, die ohne Beanstandung "
     "vermerkt wurden.",
     None, ("hu-bericht",)),
    ("werkstattpruefung", "Wartung und Technik",
     "Wäre eine Untersuchung in einer Werkstatt meiner Wahl möglich?",
     "Ein grundloses Nein ist ein Warnsignal; ein seriöser Verkäufer stimmt in der Regel zu.",
     None, ()),
    ("nutzung", "Nutzung",
     "Wie wurde das Fahrzeug überwiegend genutzt — Kurzstrecke, Langstrecke oder mit Anhänger?",
     "Das Nutzungsprofil erklärt das Verschleißbild und muss zur Laufleistung passen.",
     None, ()),
    ("standzeit", "Nutzung",
     "Stand das Fahrzeug längere Zeit ungenutzt?",
     "Lange Standzeiten belasten Batterie, Reifen, Bremsen und Dichtungen.",
     None, ()),
    ("import", "Nutzung",
     "Ist das Fahrzeug ein Import oder Reimport?",
     "Bei Importfahrzeugen Ausstattung, Serviceintervalle und Papiere besonders genau prüfen.",
     None, ()),
    ("schluessel", "Zubehör",
     "Wie viele Schlüssel gehören zum Fahrzeug?",
     "Ein fehlender Zweitschlüssel kostet je nach Modell einen dreistelligen Betrag — vor "
     "Ort alle Schlüssel testen.",
     None, ()),
    ("raeder", "Zubehör",
     "Gehört ein zweiter Radsatz zum Fahrzeug?",
     "Winter- oder Sommerräder auf Zustand, Alter und Vollständigkeit ansehen.",
     None, ()),
)


# ══ DOKUMENTE ════════════════════════════════════════════════════════════════
# Ziel 8–15 Punkte. Grundsatz (§10): Ein Basis-Punkt behauptet NIE, ein Dokument
# fehle — er sagt "prüfen" bzw. "zeigen lassen".
BASIS_DOKUMENTE: tuple[_Eintrag, ...] = (
    ("zb1", "Fahrzeugpapiere",
     "Zulassungsbescheinigung Teil I ansehen",
     "Im Fahrzeugschein Halterdaten, Erstzulassung, technische Daten und den nächsten "
     "HU-Termin prüfen.",
     None, ()),
    ("zb2", "Fahrzeugpapiere",
     "Zulassungsbescheinigung Teil II zeigen lassen",
     "Der Fahrzeugbrief weist das Eigentum nach und nennt die Zahl der Vorhalter — ohne "
     "ihn sollte kein Kauf stattfinden.",
     None, ()),
    ("fin", "Fahrzeugpapiere",
     "FIN in den Papieren mit dem Fahrzeug abgleichen",
     "Die Fahrgestellnummer aus Teil I und Teil II mit der Nummer an Fahrzeug und "
     "Typschild vergleichen.",
     None, ()),
    ("ausweis", "Fahrzeugpapiere",
     "Ausweis des Verkäufers mit den Papieren abgleichen",
     "Der Name im Ausweis muss zum Halter in Teil II passen — sonst eine schriftliche "
     "Vollmacht verlangen.",
     None, ()),
    ("hu", "Prüfungen und Wartung",
     "Letzten HU-Bericht ansehen",
     "Der Prüfbericht listet auch geringe Mängel und den damaligen Kilometerstand.",
     None, ("hu-bericht",)),
    ("serviceheft", "Prüfungen und Wartung",
     "Serviceheft oder digitales Serviceprotokoll durchsehen",
     "Auf durchgehende Einträge mit Datum, Kilometerstand und Werkstattstempel achten.",
     None, ("scheckheft",)),
    ("wartungsrechnungen", "Prüfungen und Wartung",
     "Wartungsrechnungen zeigen lassen",
     "Rechnungen belegen den tatsächlichen Umfang der Arbeiten deutlich besser als ein "
     "Stempel im Heft.",
     None, ()),
    ("reparaturrechnungen", "Prüfungen und Wartung",
     "Reparaturrechnungen der letzten Jahre ansehen",
     "Sie zeigen, welche Bauteile bereits ersetzt wurden und was als Nächstes ansteht.",
     None, ()),
    ("km_plausibel", "Prüfungen und Wartung",
     "Kilometerstände in den Unterlagen auf Plausibilität prüfen",
     "Die Kilometerstände aus HU-Bericht, Serviceheft und Rechnungen chronologisch "
     "vergleichen — sie müssen durchgehend ansteigen.",
     None, ()),
    ("rueckruf", "Prüfungen und Wartung",
     "Nach Nachweisen zu durchgeführten Rückrufaktionen fragen",
     "Werkstattbelege oder eine Bestätigung des Herstellers zeigen lassen.",
     None, ("rueckruf-*",)),
    ("schluessel", "Übergabe",
     "Anzahl der Schlüssel und Fernbedienungen festhalten",
     "Im Kaufvertrag schriftlich vermerken, wie viele Schlüssel übergeben werden.",
     None, ()),
    ("bordunterlagen", "Übergabe",
     "Bordmappe und Bedienungsanleitung prüfen",
     "Vollständige Bordunterlagen sprechen für eine gepflegte Fahrzeughistorie.",
     None, ()),
    ("umbauten", "Übergabe",
     "Unterlagen zu Umbauten und Zubehör verlangen",
     "Für Anhängerkupplung, andere Felgen, Fahrwerk oder Tuning müssen Gutachten oder "
     "Eintragungen vorliegen.",
     None, ()),
    ("kaufvertrag", "Übergabe",
     "Kaufvertrag vor der Unterschrift vollständig lesen",
     "Zusicherungen zu Unfallfreiheit, Laufleistung und bekannten Mängeln müssen "
     "schriftlich im Vertrag stehen.",
     None, ()),
)
