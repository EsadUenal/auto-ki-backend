"""
Regressionstests für die Quellenqualität (app/web_search.py: score_domain,
curate_results) — Final Polish "Quellenqualität und Darstellung".

Kein Netzwerk-/Tavily-Aufruf nötig — reine Funktionstests auf synthetischen
Suchergebnissen. Ausführen: python test_quellenqualitaet.py
"""

from app.web_search import (
    score_domain, curate_results, results_to_belege,
    KATEGORIE_MARKTPREISE, KATEGORIE_RUECKRUFE, KATEGORIE_SCHWACHSTELLEN,
    KATEGORIE_TECHNISCHE_DATEN, SOCIAL_MEDIA_AUSSCHLUSS,
)

FEHLER = []


def check(name: str, bedingung: bool, detail: str = ""):
    if bedingung:
        print(f"[OK] {name}")
    else:
        FEHLER.append(f"[FEHLER] {name} — {detail}")


def treffer(url, title="Titel", content="Inhalt"):
    return {"url": url, "title": title, "content": content}


# ---------- 1) Herstellerseiten werden hoch bewertet ----------
check(
    "Herstellerseite (bmw.de) bewertet höher als unbekannte Seite",
    score_domain("https://www.bmw.de/de/modelle/3er.html") > score_domain("https://irgendeinblog.example/artikel"),
)
check(
    "Herstellerseite (mercedes-benz.de) erkannt",
    score_domain("https://www.mercedes-benz.de/passengercars") > 0,
)

# ---------- 2) Amtliche/Fachmedien-Quellen hoch bewertet ----------
check(
    "ADAC höher bewertet als unbekannte Seite",
    score_domain("https://www.adac.de/rund-ums-fahrzeug/") > score_domain("https://irgendeinblog.example/x"),
)
check(
    "KBA (amtlich) höher bewertet als ADAC (Fachmedien)",
    score_domain("https://www.kba.de/DE/Home") > score_domain("https://www.adac.de/rund-ums-fahrzeug/"),
)
check(
    "TÜV höher bewertet als Community",
    score_domain("https://www.tuev-sued.de/") > score_domain("https://www.motor-talk.de/forum"),
)

# ---------- 3) Marktplätze bei Kategorie "marktpreise" geboostet ----------
check(
    "mobile.de bei Kategorie Marktpreise höher als ohne Kategorie",
    score_domain("https://www.mobile.de/auto", kategorie=KATEGORIE_MARKTPREISE)
    > score_domain("https://www.mobile.de/auto", kategorie=None),
)
check(
    "AutoScout24 bei Marktpreisen höher bewertet als Motor-Talk bei Marktpreisen",
    score_domain("https://www.autoscout24.de/angebot", kategorie=KATEGORIE_MARKTPREISE)
    > score_domain("https://www.motor-talk.de/forum", kategorie=KATEGORIE_MARKTPREISE),
)

# ---------- 4) Rückrufe: KBA/Hersteller bevorzugt ----------
check(
    "KBA bei Kategorie Rückrufe geboostet",
    score_domain("https://www.kba.de/rueckrufe", kategorie=KATEGORIE_RUECKRUFE)
    > score_domain("https://www.kba.de/rueckrufe", kategorie=None),
)

# ---------- 5) Schwachstellen: Fachmedien + Community erlaubt, aber niedriger als Fachmedien ----------
check(
    "Motor-Talk bei Schwachstellen zugelassen (Score > 0)",
    score_domain("https://www.motor-talk.de/forum/golf", kategorie=KATEGORIE_SCHWACHSTELLEN) > 0,
)
check(
    "ADAC bei Schwachstellen höher als Motor-Talk",
    score_domain("https://www.adac.de/x", kategorie=KATEGORIE_SCHWACHSTELLEN)
    > score_domain("https://www.motor-talk.de/forum", kategorie=KATEGORIE_SCHWACHSTELLEN),
)

# ---------- 6) Social Media wird hart gesperrt ----------
for domain in ["instagram.com", "tiktok.com", "facebook.com"]:
    check(
        f"{domain} bekommt Sperr-Score",
        score_domain(f"https://www.{domain}/irgendwas") < 0,
    )

# ---------- 7) curate_results: Social Media wird herausgefiltert ----------
ergebnis = curate_results([
    treffer("https://www.instagram.com/p/xyz", "Instagram Post"),
    treffer("https://www.adac.de/artikel1", "ADAC Artikel"),
    treffer("https://www.tiktok.com/@user/video", "TikTok Video"),
], max_results=5)
check(
    "Social-Media-Treffer werden aus curate_results entfernt",
    len(ergebnis) == 1 and ergebnis[0]["url"] == "https://www.adac.de/artikel1",
    f"Erhalten: {[r['url'] for r in ergebnis]}",
)

# ---------- 8) curate_results: exakte Duplikate entfernt ----------
ergebnis = curate_results([
    treffer("https://www.adac.de/artikel1?ref=google", "ADAC Artikel"),
    treffer("https://www.adac.de/artikel1", "ADAC Artikel"),
], max_results=5)
check(
    "Exakte URL-Duplikate (nur Query-String unterschiedlich) werden entfernt",
    len(ergebnis) == 1,
    f"Erhalten: {len(ergebnis)} Treffer",
)

# ---------- 9) curate_results: Near-Duplikate (gleiche Domain+Titel) entfernt ----------
ergebnis = curate_results([
    treffer("https://www.autobild.de/artikel-a", "BMW 320d Test"),
    treffer("https://www.autobild.de/artikel-b", "BMW 320d Test"),
], max_results=5)
check(
    "Gleicher Titel + gleiche Domain (andere URL) gilt als Duplikat",
    len(ergebnis) == 1,
    f"Erhalten: {len(ergebnis)} Treffer",
)

# ---------- 10) curate_results: Ranking bevorzugt hochwertige Quellen ----------
ergebnis = curate_results([
    treffer("https://irgendeinblog.example/artikel", "Irgendein Blog"),
    treffer("https://www.adac.de/artikel", "ADAC Artikel"),
    treffer("https://www.bmw.de/modelle", "BMW Modellseite"),
], max_results=3)
check(
    "Herstellerseite/ADAC stehen vor unbekanntem Blog",
    ergebnis[0]["url"] != "https://irgendeinblog.example/artikel",
    f"Reihenfolge: {[r['url'] for r in ergebnis]}",
)
check(
    "Alle 3 Treffer bleiben erhalten wenn max_results >= Anzahl",
    len(ergebnis) == 3,
)

# ---------- 11) curate_results: max_results begrenzt auf "so viele wie nötig" ----------
viele_treffer = [treffer(f"https://www.adac.de/artikel{i}", f"Titel {i}") for i in range(10)]
ergebnis = curate_results(viele_treffer, max_results=4)
check(
    "Kappung auf max_results funktioniert (10 Treffer -> 4)",
    len(ergebnis) == 4,
    f"Erhalten: {len(ergebnis)}",
)

# ---------- 12) curate_results: leere Liste bleibt leer ----------
check("Leere Liste bleibt leer", curate_results([]) == [])

# ---------- 13) results_to_belege: Qualitätslabel wird angehängt ----------
belege = results_to_belege([treffer("https://www.adac.de/artikel", "ADAC Artikel")])
check(
    "Beleg enthält 'qualitaet'-Feld",
    belege[0].get("qualitaet") == "Fachmedien",
    f"Erhalten: {belege[0].get('qualitaet')}",
)
belege = results_to_belege([treffer("https://www.bmw.de/modelle", "BMW Modellseite")])
check(
    "Herstellerseite bekommt Label 'Hersteller'",
    belege[0].get("qualitaet") == "Hersteller",
    f"Erhalten: {belege[0].get('qualitaet')}",
)

# ---------- 14) Technische-Daten-Kategorie boostet Technik-Domains (Bosch/Hella) ----------
check(
    "Bosch bei Kategorie technische_daten/wartung geboostet",
    score_domain("https://www.bosch.de/produkte", kategorie=KATEGORIE_TECHNISCHE_DATEN)
    > score_domain("https://www.bosch.de/produkte", kategorie=None),
)

# ---------- 15) Nicht-URL / defekte Eingaben brechen nicht ----------
check("Leere URL liefert neutralen Score", score_domain("") == 0)
check(
    "curate_results ignoriert Treffer ohne Domain robust",
    curate_results([{"url": "", "title": "kaputt"}], max_results=5) == [],
)

print()
if FEHLER:
    print(f"{len(FEHLER)} FEHLER:\n")
    for f in FEHLER:
        print(f)
    raise SystemExit(1)
else:
    print("Alle Quellenqualitäts-Regressionstests bestanden.")
