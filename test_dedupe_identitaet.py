"""
Deduplizierung nach stabiler Identität — deterministisch, KEIN Netzwerk.

Hintergrund (forensischer Audit der BMW-320d-G20-Läufe): der alte Dedupe
deduplizierte über Identität UND Fahrzeug-Fingerabdruck gleichzeitig, nach dem
Prinzip "wer zuerst kommt, gewinnt". Ein anonymes, als ungeeignet bewertetes
Textfragment besetzte damit den Fingerabdruck (Preis+km+Baujahr) und blockierte
dauerhaft das später gefundene, vollständig identifizierte Inserat. Drei echte
G20-Vergleiche gingen ersatzlos verloren:

    3484786731 (22.999 €)   3484778742 (23.900 €)   3480860991 (21.900 €)

Fälle A-H aus der Aufgabenstellung.

    python test_dedupe_identitaet.py
"""
import os
import sys
import tempfile

sys.path.insert(0, ".")
os.environ.setdefault("AUTO_KI_DB_PATH",
                      os.path.join(tempfile.mkdtemp(prefix="vira_dedup_"), "test.db"))

from app.marktvergleich import (                                         # noqa: E402
    _dedupliziere, _identitaets_key, _repraesentant_rang,
)
from app.models import Preisbeobachtung                                  # noqa: E402

_fails: list[str] = []


def check(name, cond):
    print(("[OK] " if cond else "[FAIL] ") + name)
    if not cond:
        _fails.append(name)


def ident(lid, preis, km=145_800, bj=2019, *, vergleichbarkeit="aehnlich",
          domain="kleinanzeigen.de", slug="bmw-320d", body_evidence="card",
          generation="G20", url=None):
    """Vollwertige Karten-Beobachtung mit stabiler Anzeigen-ID."""
    return Preisbeobachtung(
        preis_eur=preis, kilometerstand=km, baujahr=bj,
        quelle_domain=domain,
        quelle_url=url or f"https://www.{domain}/s-autos/bmw-320d-2019/k0c216",
        vergleichbarkeit=vergleichbarkeit, gruende=[],
        source_type="market_category",
        listing_key=f"id:{domain}:{lid}", listing_id=lid,
        detail_url=f"https://www.{domain}/s-anzeige/{slug}/{lid}-216-8139",
        make="BMW", model="3er", generation=generation,
        generation_evidence="explicit_card" if generation else "unknown",
        body="limousine", body_evidence=body_evidence,
        engine_variant="320d", horsepower=190, transmission="automatik",
        extraction_source="raw_content", segmentation_method="detail_link",
        structural_confidence="high", window_fallback_used=False,
    )


def anonym(preis, km=145_800, bj=2019, *, vergleichbarkeit="ungeeignet",
           domain="kleinanzeigen.de"):
    """Anonymes Zeichenfenster-Fragment ohne jede stabile Identität."""
    return Preisbeobachtung(
        preis_eur=preis, kilometerstand=km, baujahr=bj,
        quelle_domain=domain,
        quelle_url=f"https://www.{domain}/s-autos/bmw-320d-weiss/k0c216",
        vergleichbarkeit=vergleichbarkeit, gruende=[],
        source_type="market_category",
        listing_key=f"v:{preis}:{km}:{bj}", listing_id=None, detail_url=None,
        body="limousine", body_evidence="page_context",
        extraction_source="window_fallback", segmentation_method="window_fallback",
        structural_confidence="low", window_fallback_used=True,
    )


def ids(uniq):
    return [b.listing_id for b in uniq]


# ══ A — dieselbe Anzeigen-ID auf drei Suchseiten -> genau eine ═════════════
a_in = [ident("3484786731", 22_999, url="https://www.kleinanzeigen.de/s-autos/a/k0"),
        ident("3484786731", 22_999, url="https://www.kleinanzeigen.de/s-autos/b/k0"),
        ident("3484786731", 22_999, url="https://www.kleinanzeigen.de/s-autos/c/k0")]
a_out, _ = _dedupliziere(a_in)
check("A: dieselbe Listing-ID auf drei Suchseiten -> genau eine Beobachtung",
      len(a_out) == 1 and a_out[0].listing_id == "3484786731")

# ══ B — gleiche Detail-URL, gleiche ID -> genau eine ══════════════════════
b_nur_url = ident("3484786731", 22_999)
b_nur_url.listing_id = None
b_nur_url.listing_key = "url:" + (b_nur_url.detail_url or "")
b_out, _ = _dedupliziere([ident("3484786731", 22_999), b_nur_url])
check("B: ID-Schluessel und Detail-URL-Schluessel derselben Anzeige verschmelzen",
      len(b_out) == 1)
check("B: der Gewinner behaelt die stabile Anzeigen-ID",
      b_out[0].listing_id == "3484786731")

# ══ C — zwei verschiedene IDs, identischer Fingerabdruck -> beide ═════════
c_out, _ = _dedupliziere([ident("3484778742", 23_900), ident("3484999999", 23_900)])
check("C: zwei verschiedene Listing-IDs mit gleichem Preis/km/Baujahr bleiben beide",
      len(c_out) == 2 and ids(c_out) == ["3484778742", "3484999999"])

# ══ D — anonym zuerst, identifiziert spaeter -> identifiziert gewinnt ═════
d_out, _ = _dedupliziere([anonym(22_999), ident("3484786731", 22_999)])
check("D: das identifizierte Listing verdraengt das anonyme Fragment",
      len(d_out) == 1 and d_out[0].listing_id == "3484786731")
check("D: der Gewinner ist strukturell, nicht window_fallback",
      d_out[0].segmentation_method == "detail_link"
      and d_out[0].window_fallback_used is False)

# ══ E — identifiziert zuerst, anonym spaeter -> identifiziert bleibt ══════
e_out, _ = _dedupliziere([ident("3480860991", 21_900, km=141_000), anonym(21_900, km=141_000)])
check("E: das anonyme Fragment zaehlt nicht zusaetzlich",
      len(e_out) == 1 and e_out[0].listing_id == "3480860991")

# ══ F — drei identische anonyme Fragmente -> eines ═══════════════════════
f_out, _ = _dedupliziere([anonym(19_500, km=109_000), anonym(19_500, km=109_000),
                          anonym(19_500, km=109_000)])
check("F: drei identische anonyme Fragmente bleiben eine Beobachtung",
      len(f_out) == 1 and f_out[0].listing_id is None)

# ══ G — zwei identifizierte + ein anonymes mit gleichem Fingerabdruck ═════
g_out, _ = _dedupliziere([ident("3484786731", 22_999), anonym(22_999),
                          ident("3484888888", 22_999)])
check("G: beide identifizierten Listings bleiben erhalten",
      ids(g_out) == ["3484786731", "3484888888"])
check("G: das anonyme Fragment zaehlt kein zweites Mal",
      len(g_out) == 2 and all(b.listing_id for b in g_out))
g2_out, _ = _dedupliziere([anonym(22_999), ident("3484786731", 22_999),
                           ident("3484888888", 22_999)])
check("G: das Ergebnis haengt nicht von der Reihenfolge des Fragments ab",
      sorted(ids(g2_out)) == ["3484786731", "3484888888"])

# ══ H — Winner-Wahl ist unabhaengig von similarity und Median-Naehe ═══════
# Zwei Beobachtungen DERSELBEN Anzeige: die schwaechere Struktur ist als
# "sehr_aehnlich" bewertet, die starke als "ungeeignet". Gewinnen muss die
# STRUKTURELL bessere — sonst waehlte der Dedupe nach Zielpassung aus.
h_schwach = ident("3484786731", 22_999, vergleichbarkeit="sehr_aehnlich")
h_schwach.segmentation_method = "window_fallback"
h_schwach.structural_confidence = "low"
h_schwach.window_fallback_used = True
h_schwach.body_evidence = "page_context"
h_schwach.detail_url = None
h_stark = ident("3484786731", 22_999, vergleichbarkeit="ungeeignet")
h_out, _ = _dedupliziere([h_schwach, h_stark])
check("H: der strukturell bessere Repraesentant gewinnt, nicht der besser bewertete",
      len(h_out) == 1 and h_out[0].vergleichbarkeit == "ungeeignet"
      and h_out[0].segmentation_method == "detail_link")
check("H: umgekehrte Reihenfolge liefert denselben Gewinner",
      _dedupliziere([h_stark, h_schwach])[0][0].structural_confidence == "high")
check("H: das Rang-Tupel enthaelt kein Zielpassungs-Merkmal",
      _repraesentant_rang(h_stark) > _repraesentant_rang(h_schwach))
h_nah = ident("3484786731", 21_000, vergleichbarkeit="sehr_aehnlich")
h_nah.structural_confidence = "low"
h_nah.segmentation_method = "window_fallback"
h_nah.window_fallback_used = True
h_nah.detail_url = None
h_fern = ident("3484786731", 26_900, vergleichbarkeit="ungeeignet")
check("H: Preisnaehe zum Median entscheidet nicht",
      _dedupliziere([h_nah, h_fern])[0][0].preis_eur == 26_900)

# ── Identitaets-Schluessel ─────────────────────────────────────────────────
check("Schluessel: Anzeigen-ID hat Vorrang",
      _identitaets_key(ident("3484786731", 22_999)) == "id:kleinanzeigen.de:3484786731")
check("Schluessel: Detail-URL mit erkennbarer ID wird auf den ID-Schluessel normalisiert",
      _identitaets_key(b_nur_url) == "id:kleinanzeigen.de:3484786731")
check("Schluessel: ohne Identitaet gibt es keinen stabilen Schluessel",
      _identitaets_key(anonym(19_500)) is None)

# ── Konflikte werden dokumentiert, nicht zurechtgebogen (§5) ──────────────
k_a = ident("3486906354", 22_300, generation="G20")
k_b = ident("3486906354", 22_300, generation="G21")
k_b.body = "kombi"
k_out, konflikte = _dedupliziere([k_a, k_b])
check("Konflikt: dieselbe Anzeige mit widerspruechlicher Generation wird gemeldet",
      any(k["feld"] == "generation" and k["werte"] == ["G20", "G21"] for k in konflikte))
check("Konflikt: auch die Karosserie-Abweichung wird gemeldet",
      any(k["feld"] == "body" for k in konflikte))
check("Konflikt: es bleibt bei EINER Beobachtung", len(k_out) == 1)
check("Konflikt: ohne Widerspruch wird nichts gemeldet",
      _dedupliziere([ident("3484786731", 22_999), ident("3484786731", 22_999)])[1] == [])

# ── Reihenfolge des ersten Auftretens bleibt erhalten ─────────────────────
r_out, _ = _dedupliziere([ident("111111", 20_000, km=100_000),
                          anonym(30_000, km=200_000),
                          ident("222222", 25_000, km=150_000),
                          ident("111111", 20_000, km=100_000)])
check("Reihenfolge: Erstauftritt bleibt erhalten",
      [b.listing_id for b in r_out] == ["111111", None, "222222"])

print()
if _fails:
    print(str(len(_fails)) + " FEHLGESCHLAGEN:")
    for f in _fails:
        print("  - " + f)
    sys.exit(1)
print("Alle Dedupe-Identitaets-Tests bestanden.")
