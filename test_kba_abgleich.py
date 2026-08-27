"""
KBA-GESAMTABGLEICH — Zusicherungen.
KEIN Netzwerk, KEIN LLM-Call, KEINE Tavily-Calls.

Der Matcher wird gegen eine feste Fixture geprueft, nicht gegen den
Live-Export: der amtliche Bestand aendert sich taeglich, ein Test darf davon
nicht abhaengen.

  A) Kuratierte Daten formal konsistent
  B) Matcher-Grundlagen (Namensraum, Zeitraum, Referenznormalisierung)
  C) Bauteilgruppen: keine Substring-Fehltreffer
  D) Belegstaerke: ein einzelnes Wort traegt kein `verified`
  E) Eindeutigkeit: Gleichstand ergibt nie EXACT/CORRECTABLE
  F) Der Bestand nach der Migration
  G) §10 Floor-Semantik ueber alle verifizierten Faelle
  H) Keine unbestaetigte Referenz mehr sichtbar
  I) Dubletten entfernt, Kanon erhalten

    python test_kba_abgleich.py
"""
import sqlite3

from app.car_lookup import find_motor
from app.database import get_baureihe, get_conn
from app.empfehlungs_floor import ermittle_floor
from app.evidence import build_insights
from app.fakt_verifikation import fingerprint
from app.kba_abgleich_daten import (
    DUBLETTEN, VERIFIZIERTE_ZUORDNUNGEN, _selbsttest, verifizierte_ids,
)
from app.kba_reconciliation import (
    CONTRADICTED, CORRECTABLE, EXACT, NO_MATCH, PARTIAL,
    bauteilgruppen, distinktive_tokens, kba_marke, klassifiziere,
    normalisiere_referenz, vira_zeitraum, _vira_modellkandidaten,
)
from app.models import KaufCheckRequest

_FEHLER: list[str] = []


def check(name: str, bedingung: bool) -> None:
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}")
    if not bedingung:
        _FEHLER.append(name)


def kba_zeile(**kw) -> dict:
    """Ein amtlicher Datensatz in Fixture-Form."""
    z = {
        "KBA-Referenznummer": "9999", "Rückrufcode des Herstellers": "XYZ",
        "Veröffentlichungsdatum": "2020-05-01", "Marke": "OPEL",
        "Modell": "INSIGNIA", "Mangelbezeichnung": "", "Produktionszeitraum von": "2018",
        "Produktionszeitraum bis": "2020", "Beschreibung der Maßnahme": "",
        "Mögliche Eingrenzung der betroffenen Modelle": "N/A",
    }
    z.update(kw)
    return z


def baureihe(**kw) -> dict:
    b = {"id": "opel-insignia-b", "marke": "Opel", "modell": "Insignia",
         "generation": "B", "bauzeitraum_von": 2017, "bauzeitraum_bis": 2022}
    b.update(kw)
    return b


def recall(**kw) -> dict:
    r = {"id": 1, "baureihe_id": "opel-insignia-b", "datum": "2020-05",
         "betroffene_baujahre": "2018-2020", "mangel": "", "abhilfe": "",
         "kba_referenz": None}
    r.update(kw)
    return r


# ══ A) Kuratierte Daten ══════════════════════════════════════════════════════
print("\n--- A) Kuratierte Daten ---")
try:
    _selbsttest()
    check("A1 Selbsttest laeuft durch", True)
except AssertionError as exc:
    check(f"A1 Selbsttest: {exc}", False)

check("A2 genau 15 verifizierte Zuordnungen", len(VERIFIZIERTE_ZUORDNUNGEN) == 15)
check("A3 genau 3 Dubletten", len(DUBLETTEN) == 3)
check("A4 jede Zuordnung traegt eine amtliche Referenz",
      all(e[4] for e in VERIFIZIERTE_ZUORDNUNGEN))
check("A5 keine Referenz in Sekundaerschreibweise (fuehrende Null)",
      all(not e[4].startswith("0") for e in VERIFIZIERTE_ZUORDNUNGEN))
check("A6 keine Dublette ist zugleich verifiziert",
      not ({d[0] for d in DUBLETTEN} & verifizierte_ids()))


# ══ B) Matcher-Grundlagen ════════════════════════════════════════════════════
print("\n--- B) Matcher-Grundlagen ---")
check("B1 Volkswagen -> VW", kba_marke("Volkswagen") == "VW")
check("B2 Mercedes-AMG -> MERCEDES-BENZ", kba_marke("Mercedes-AMG") == "MERCEDES-BENZ")
check("B3 BMW '3er' -> Token '3'", "3" in _vira_modellkandidaten("BMW", "3er"))
check("B4 Mercedes 'C-Klasse' behaelt den Bindestrich",
      "C-KLASSE" in _vira_modellkandidaten("Mercedes-Benz", "C-Klasse"))
check("B5 'GLC-Klasse' ebenso",
      "GLC-KLASSE" in _vira_modellkandidaten("Mercedes-Benz", "GLC-Klasse"))
check("B6 Referenz '012223' und '12223' sind derselbe Wert",
      normalisiere_referenz("012223") == normalisiere_referenz("12223") == "12223")
check("B7 Zeitraum aus betroffene_baujahre",
      vira_zeitraum(recall(betroffene_baujahre="2018-2020"), baureihe()) == (2018, 2020))
check("B8 ohne Jahresangabe klammert der Bauzeitraum der Baureihe",
      vira_zeitraum(recall(betroffene_baujahre="Alle"), baureihe()) == (2017, 2022))


# ══ C) Bauteilgruppen ohne Substring-Fehltreffer ═════════════════════════════
print("\n--- C) Bauteilgruppen ---")
check("C1 'ABS-Modul' -> bremse_elektr", "bremse_elektr" in bauteilgruppen("ABS-Modul"))
check("C2 'Abschaltung' ist KEIN ABS "
      "(das Kuerzel darf nicht als Teilzeichenkette treffen)",
      "bremse_elektr" not in bauteilgruppen("unerwartete Abschaltung"))
check("C3 'Absicherung' ist kein ABS",
      "bremse_elektr" not in bauteilgruppen("mangelhafte Absicherung"))
check("C4 'Bremspedalplatte' -> bremse_mech (Kompositum trifft weiterhin)",
      "bremse_mech" in bauteilgruppen("Die Bremspedalplatte kann sich loesen"))
check("C5 'Hochvoltspeicher' -> hochvolt",
      "hochvolt" in bauteilgruppen("Kurzschluss im Hochvoltspeicher"))
check("C6 generische Adjektive sind keine distinktiven Tokens",
      not ({"fehlerhafte", "mangelhafte", "software", "update"}
           & distinktive_tokens("Fehlerhafte mangelhafte Software Update")))
check("C7 Bauteilnamen bleiben distinktive Tokens",
      "kraftstoffpumpe" in distinktive_tokens("Ausfall der Kraftstoffpumpe"))


# ══ D) Belegstaerke ══════════════════════════════════════════════════════════
print("\n--- D) Belegstaerke ---")
_kba_1tok = [kba_zeile(Mangelbezeichnung="Die Lenkspindel kann brechen.")]
_b_1tok = klassifiziere(
    recall(mangel="Bruch der Lenkspindel moeglich."), baureihe(), _kba_1tok)
check("D1 ein einzelnes trennscharfes Wort ergibt einen Treffer",
      _b_1tok.klasse in (EXACT, CORRECTABLE))
check("D2 aber NUR mit Belegstaerke 'schwach'", _b_1tok.belegstaerke == "schwach")
check("D3 und darf deshalb NICHT verified werden", not _b_1tok.darf_verified)

_kba_2tok = [kba_zeile(
    Mangelbezeichnung="Die Lenkspindel kann im Kreuzgelenk brechen.",
    **{"Beschreibung der Maßnahme": "Austausch der Lenkspindel."})]
_b_2tok = klassifiziere(
    recall(mangel="Bruch der Lenkspindel im Kreuzgelenk moeglich."),
    baureihe(), _kba_2tok)
check("D4 zwei trennscharfe Woerter ergeben Belegstaerke 'stark'",
      _b_2tok.belegstaerke == "stark")
check("D5 und duerfen verified werden", _b_2tok.darf_verified)


# ══ E) Eindeutigkeit ═════════════════════════════════════════════════════════
print("\n--- E) Eindeutigkeit ---")
_zwei_gleiche = [
    kba_zeile(**{"KBA-Referenznummer": "1111",
                 "Mangelbezeichnung": "Die Lenkspindel kann im Kreuzgelenk brechen."}),
    kba_zeile(**{"KBA-Referenznummer": "2222",
                 "Mangelbezeichnung": "Die Lenkspindel kann im Kreuzgelenk brechen."}),
]
_b_gleich = klassifiziere(
    recall(mangel="Bruch der Lenkspindel im Kreuzgelenk."), baureihe(), _zwei_gleiche)
check("E1 zwei gleich starke Kandidaten -> PARTIAL, niemals EXACT/CORRECTABLE",
      _b_gleich.klasse == PARTIAL)
check("E2 und damit auch nicht verified-faehig", not _b_gleich.darf_verified)

_fremd = [kba_zeile(Marke="RENAULT", Modell="KANGOO",
                    **{"KBA-Referenznummer": "6807"})]
_b_fremd = klassifiziere(
    recall(kba_referenz="6807", mangel="Irgendein Mangel."), baureihe(), _fremd)
check("E3 Referenz gehoert amtlich zu einem anderen Fahrzeug -> NO_MATCH/CONTRADICTED",
      _b_fremd.klasse in (CONTRADICTED, NO_MATCH))

_ausserhalb = [kba_zeile(**{"Produktionszeitraum von": "1990",
                            "Produktionszeitraum bis": "1995"})]
_b_ausser = klassifiziere(recall(), baureihe(), _ausserhalb)
check("E4 amtlicher Rueckruf ausserhalb des Baureihen-Bauzeitraums -> kein Kandidat",
      _b_ausser.klasse == NO_MATCH)


# ══ F) Bestand nach der Migration ════════════════════════════════════════════
print("\n--- F) Bestand nach der Migration ---")
with get_conn() as conn:
    _alle = [dict(r) for r in conn.execute(
        "SELECT id,baureihe_id,datum,betroffene_baujahre,mangel,abhilfe,kba_referenz "
        "FROM rueckruf ORDER BY id")]
    _verifs = {r["fakt_id"]: dict(r) for r in conn.execute(
        "SELECT * FROM fakt_verifikation WHERE fakt_art='rueckruf'")}

# BATCH A hat nach dem Gesamtabgleich weitere amtliche Rueckrufe uebernommen
# (app/kba_batch_a_daten.py). Dieser Abschnitt prueft das ERGEBNIS DES
# GESAMTABGLEICHS und blendet die Batch-A-Zeilen deshalb aus — sonst wuerde er
# eine Aussage ueber einen ganz anderen Datenbestand treffen.
from app.kba_batch_a_daten import ZEILEN as _BATCH_A_ZEILEN  # noqa: E402
from app.kba_batch_a_daten import zeilen_ids as _batch_a_ids  # noqa: E402

_BATCH_A = _batch_a_ids()
_abgleich = [r for r in _alle if r["id"] not in _BATCH_A]
check("F0 Gesamtbestand = Abgleichsstand + Batch A",
      len(_alle) == len(_abgleich) + len(_BATCH_A_ZEILEN))
check("F1 746 Rueckrufe aus dem Gesamtabgleich (749 minus 3 Dubletten)",
      len(_abgleich) == 746)
_mit_ref = [r for r in _abgleich if (r["kba_referenz"] or "").strip()]
check("F2 genau 15 Abgleichszeilen tragen noch eine KBA-Referenz", len(_mit_ref) == 15)
check("F3 und das sind exakt die kuratierten",
      {r["id"] for r in _mit_ref} == verifizierte_ids())
_verified = [f for f, v in _verifs.items()
             if v["status"] == "verified" and f not in _BATCH_A]
check("F4 genau 15 verifizierte Rueckruf-Fakten aus dem Gesamtabgleich",
      len(_verified) == 15)
check("F5 jede Batch-A-Zeile traegt eine eigene amtliche Referenz "
      "(der Referenz-Kahlschlag des Abgleichs hat sie nicht mitgenommen)",
      all((r["kba_referenz"] or "").strip() for r in _alle if r["id"] in _BATCH_A))
check("F5 alle mit Quellenstufe A",
      all(_verifs[f]["quelle_stufe"] == "A" for f in _verified))
check("F6 alle nennen den amtlichen Export als Quelle",
      all("KBA-Rueckrufdatenbank" in (_verifs[f]["quelle"] or "") for f in _verified))
_nach_id = {r["id"]: r for r in _alle}
check("F7 KEIN stale-verification-Eintrag (Fingerprint passt zum Inhalt)",
      all(_verifs[f]["fingerprint"] == fingerprint("rueckruf", _nach_id[f])
          for f in _verified if f in _nach_id))
check("F8 jede Verifikation traegt eine nachvollziehbare Notiz",
      all(len(_verifs[f]["notiz"] or "") >= 120 for f in _verified))


# ══ G) §10 Floor-Semantik ════════════════════════════════════════════════════
print("\n--- G) §10 Floor-Semantik ---")


def _check_fahrzeug(baureihe_id: str, baujahr: int):
    with get_conn() as conn:
        b = conn.execute("SELECT marke,modell,generation FROM baureihe WHERE id=?",
                         (baureihe_id,)).fetchone()
    br = get_baureihe(b["marke"], b["modell"], b["generation"])
    motor = br["motoren"][0]["bezeichnung"] if br.get("motoren") else None
    mm = find_motor(br, motor) if motor else None
    req = KaufCheckRequest(marke=b["marke"], modell=b["modell"], baujahr=baujahr,
                           motor=motor, kraftstoff=(mm or {}).get("kraftstoff"))
    ins = build_insights(br, mm, [], req, check_typ="kauf")
    rr = [i for i in ins if i.kategorie == "rueckruf"]
    return rr, ermittle_floor(rr)

import re as _re  # noqa: E402

_geprueft = _floor_ok = 0
for _eintrag in VERIFIZIERTE_ZUORDNUNGEN:
    _fid, _bid = _eintrag[0], _eintrag[1]
    _j = _re.findall(r"(?:19|20)\d{2}",
                     _nach_id.get(_fid, {}).get("betroffene_baujahre") or "")
    _rr, _fl = _check_fahrzeug(_bid, int(_j[0]) if _j else 2018)
    _hart = [i for i in _rr if i.trust == "verified"
             and i.applicability in ("variant_match", "confirmed_by_vin")]
    _geprueft += 1
    if (_fl is not None) == bool(_hart):
        _floor_ok += 1
check(f"G1 Floor greift genau dann, wenn ein verified Rueckruf hart zutrifft "
      f"({_floor_ok}/{_geprueft} verifizierte Faelle)", _floor_ok == _geprueft)

# Kontrollgruppe: Baureihen OHNE verifizierten Rueckruf duerfen NIE einen
# Rueckruf-Floor tragen.
with get_conn() as conn:
    _ver_br = {r[0] for r in conn.execute(
        "SELECT DISTINCT baureihe_id FROM rueckruf WHERE id IN "
        "(SELECT fakt_id FROM fakt_verifikation WHERE fakt_art='rueckruf' "
        "AND status='verified')")}
_kontrolle = [r for r in _alle if r["baureihe_id"] not in _ver_br][:22]
_ohne_floor = 0
for _r in _kontrolle:
    _j = _re.findall(r"(?:19|20)\d{2}", _r["betroffene_baujahre"] or "")
    _rr, _fl = _check_fahrzeug(_r["baureihe_id"], int(_j[0]) if _j else 2015)
    if _fl is None:
        _ohne_floor += 1
check(f"G2 kein Rueckruf-Floor ohne verifizierten Fakt "
      f"({_ohne_floor}/{len(_kontrolle)} Kontrollfaelle)",
      _ohne_floor == len(_kontrolle))
check("G3 die Kontrollgruppe ist gross genug (>= 20 reale Faelle)",
      len(_kontrolle) >= 20)


# ══ H) Keine unbestaetigte Referenz mehr sichtbar ════════════════════════════
print("\n--- H) Referenzen ---")
_ERFUNDEN = ("008064", "7607", "011603", "8079", "009699", "012903", "80 14 11",
             "6640", "009696", "5774", "6497", "9940", "10360")
check("H1 keine der bekannten erfundenen Nummern steht noch in der Datenbank",
      not any((r["kba_referenz"] or "") in _ERFUNDEN for r in _alle))
check("H2 jede verbliebene Referenz haengt an einem verified Fakt",
      all(_verifs.get(r["id"], {}).get("status") == "verified" for r in _mit_ref))
check("H3 keine Referenz in Sekundaerschreibweise mit fuehrender Null",
      not any((r["kba_referenz"] or "").startswith("0") for r in _mit_ref))


# ══ I) Dubletten ═════════════════════════════════════════════════════════════
print("\n--- I) Dubletten ---")
_ids = {r["id"] for r in _alle}
check("I1 alle drei Dubletten sind entfernt",
      not ({d[0] for d in DUBLETTEN} & _ids))
check("I2 alle drei kanonischen Zeilen sind erhalten",
      {d[1] for d in DUBLETTEN} <= _ids)
_paare = [(r["baureihe_id"], (r["mangel"] or "").strip(), (r["abhilfe"] or "").strip())
          for r in _abgleich]
check("I3 keine wortgleiche Dublette mehr im Abgleichsbestand",
      len(_paare) == len(set(_paare)))
# BATCH A darf wortgleiche Zeilen enthalten — der amtliche Bestand fuehrt
# eigenstaendige Aktionen mit identischer Mangelbezeichnung (VW 9777 fuer die
# Produktion 1997-1999 gegen 11267 fuer 2000; die Takata-Wellen beim Viano).
# Was NICHT vorkommen darf: zwei Zeilen, die sich in NICHTS unterscheiden — also
# gleicher Text, gleicher Zeitraum, gleiches Datum auf derselben Baureihe. Genau
# das schliesst Tor A5 in app/kba_import_batch_a.py aus.
_a_paare = [(r["baureihe_id"], (r["mangel"] or "").strip(),
             r["betroffene_baujahre"], r["datum"])
            for r in _alle if r["id"] in _BATCH_A]
check("I3b Batch A enthaelt keine ununterscheidbare Zeile",
      len(_a_paare) == len(set(_a_paare)))
check("I3c keine Batch-A-Zeile wiederholt eine Abgleichszeile wortgleich",
      not ({(r["baureihe_id"], (r["mangel"] or "").strip(),
             (r["abhilfe"] or "").strip())
            for r in _alle if r["id"] in _BATCH_A} & set(_paare)))
check("I4 keine verwaiste Verifikation (jeder Fakt existiert noch)",
      all(f in _ids for f in _verifs))


# ══════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
if _FEHLER:
    print(f"{len(_FEHLER)} FEHLER:")
    for f in _FEHLER:
        print("  -", f)
    raise SystemExit(1)
print("ALLE KBA-ABGLEICH-TESTS GRUEN")
