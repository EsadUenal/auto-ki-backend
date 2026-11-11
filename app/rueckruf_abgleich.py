from __future__ import annotations

"""
Amtliche Abgleichsmerkmale je Rückruf und strukturierter Abgleich eines
Web-Rückrufdatensatzes (z.B. ADAC-Rückruftabelle) mit einem amtlichen Eintrag.

WARUM
-----
Eine ADAC-Rückruftabelle trägt keine KBA-Referenz und keinen Herstellercode.
Dieselbe Aktion erschien deshalb zweimal im Bericht: als amtlicher KBA-
Rückruf und als Web-Rückrufhinweis (Live-Bericht 20261108-091413: KBA 7704
und die ADAC-Zeile "März 2018 / Kraftstoffaustritt ..."). Textähnlichkeit
trägt das nicht: amtlicher Mangeltext und ADAC-Anlass teilen kein Inhaltswort.

WAS ES TUT
----------
1. Import/Nachtrag (`plane_nachtrag`/`apply_nachtrag`, Wartungskommando): je
   bestehender `rueckruf`-Zeile mit vertrauenswürdiger KBA-Referenz werden aus
   dem amtlichen Gesamtexport ergänzt -- additiv, nur bisher leere Spalten:
   Herstellercode, amtliche Modellspalte, Veröffentlichungsdatum, Fallzahlen
   Deutschland/weltweit, sowie `abgleich_eindeutig_amtlich`: ob der
   Merkmalsschlüssel (Marke, Veröffentlichungsmonat, Produktionsjahre von/bis,
   Fallzahl DE, Fallzahl weltweit) im VOLLSTÄNDIGEN amtlichen Bestand genau
   EINE Aktion bezeichnet (1) oder mit einer anderen kollidiert/unvollständig
   ist (0). Originaldaten (mangel/abhilfe/datum/...) und die Verifikation
   bleiben unberührt (der Fingerprint umfasst diese Spalten nicht).
2. Laufzeit (`eindeutige_amtliche_zuordnung`): ein Web-Datensatz gilt nur dann
   als dieselbe Aktion wie EIN angezeigter amtlicher Eintrag, wenn
     * der amtliche Eintrag im vollständigen Bestand eindeutig ist (1),
     * ALLE Merkmale belegt sind und übereinstimmen (Monat, Produktionsjahre,
       beide Fallzahlen) und die Modellmengen sich gegenseitig decken,
     * und unter den angezeigten Einträgen genau einer passt.
   Fehlt ein Merkmal, widerspricht eines oder passt mehr als einer: keine
   Zuordnung -- beide bleiben sichtbar. Keine Kennung wird abgeleitet.

Generisch: keine Marken-, Modell- oder Aktionssonderlogik.
"""

import collections
import logging
import re

log = logging.getLogger(__name__)

_MONATE = {"januar": 1, "jan": 1, "februar": 2, "feb": 2, "märz": 3, "maerz": 3, "mär": 3,
           "april": 4, "apr": 4, "mai": 5, "juni": 6, "jun": 6, "juli": 7, "jul": 7,
           "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9, "oktober": 10,
           "okt": 10, "november": 11, "nov": 11, "dezember": 12, "dez": 12}

_SP_REFERENZ = "KBA-Referenznummer"
_SP_CODE = "Rückrufcode des Herstellers"
_SP_DATUM = "Veröffentlichungsdatum"
_SP_MARKE = "Marke"
_SP_MODELL = "Modell"
_SP_VON = "Produktionszeitraum von"
_SP_BIS = "Produktionszeitraum bis"
_SP_ANZ_WW = "Anzahl potentiell betroffene Fahrzeugteile und Fahrzeugzubehör weltweit"
_SP_ANZ_DE = "Anzahl potentiell betroffene Fahrzeugteile und Fahrzeugzubehör deutschlandweit"


# ── Normalisierung ──────────────────────────────────────────────────────────

def _zahl(wert) -> int | None:
    """'18.400' / '18400' / 18400 -> 18400; 'N/A', leer, 0-frei -> None."""
    if wert is None:
        return None
    if isinstance(wert, int):
        return wert or None
    ziffern = re.sub(r"[^\d]", "", str(wert))
    return int(ziffern) if ziffern and int(ziffern) > 0 else None


def _jahr(wert) -> int | None:
    m = re.search(r"(?:19|20)\d{2}", str(wert or ""))
    return int(m.group(0)) if m else None


def _monat_iso(wert) -> str | None:
    """'2018-03-22' / 'März 2018' / '03/2018' / '03.2018' -> '2018-03'."""
    s = str(wert or "").strip().lower()
    m = re.match(r"((?:19|20)\d{2})-(\d{1,2})", s)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}"
    m = re.match(r"([a-zäöü]+)\.?\s+((?:19|20)\d{2})\b", s)
    if m and m.group(1) in _MONATE:
        return f"{m.group(2)}-{_MONATE[m.group(1)]:02d}"
    m = re.match(r"(\d{1,2})\s*[./]\s*((?:19|20)\d{2})\b", s)
    if m and 1 <= int(m.group(1)) <= 12:
        return f"{m.group(2)}-{int(m.group(1)):02d}"
    return None


def _modell_eintraege(text: str | None) -> list[str]:
    """'GS F L10 (01/16 - 08/18), LC Coupé Z10 (06/17 - 03/25)' -> ['gsfl10', 'lccoupz10'];
    'LC, GS-F, RC-F' -> ['lc', 'gsf', 'rcf']. Klammerzusätze (Zeiträume) zählen nicht."""
    ohne_klammern = re.sub(r"\([^)]*\)", " ", text or "")
    out = []
    for teil in re.split(r"[,;/]", ohne_klammern):
        n = re.sub(r"[^a-z0-9]", "", teil.lower())
        if n and n not in ("na", "keine", "keineangaben"):
            out.append(n)
    return out


def modellmengen_decken_sich(web_modelle: str | None, amtlich_modelle: str | None) -> bool:
    """Jedes amtliche Modell ist Präfix eines Web-Eintrags UND jeder Web-Eintrag beginnt mit
    einem amtlichen Modell. Fehlt eine Seite: False (nicht belegt)."""
    web, amt = _modell_eintraege(web_modelle), _modell_eintraege(amtlich_modelle)
    if not web or not amt:
        return False
    return (all(any(w.startswith(a) for w in web) for a in amt)
            and all(any(w.startswith(a) for a in amt) for w in web))


def web_fallzahlen(text: str | None) -> tuple[int | None, int | None]:
    """'207 (Deutschland) 18.400 (weltweit)' -> (207, 18400). Nur ausdrücklich beschriftete Zahlen."""
    s = text or ""
    de = re.search(r"([\d.\s]+?)\s*\(\s*deutschland\s*\)", s, re.IGNORECASE)
    ww = re.search(r"([\d.\s]+?)\s*\(\s*weltweit\s*\)", s, re.IGNORECASE)
    return (_zahl(de.group(1)) if de else None, _zahl(ww.group(1)) if ww else None)


# ── Amtlicher Bestand: Merkmale + Eindeutigkeit ─────────────────────────────

def _marke(roh: str | None) -> str:
    from app.kba_reconciliation import kba_marke
    return kba_marke(roh or "")


def merkmalsschluessel(marke, monat, prod_von, prod_bis, anzahl_de, anzahl_ww) -> tuple | None:
    """None, sobald ein Merkmal fehlt -- ein unvollständiger Schlüssel belegt nichts."""
    teile = (marke or None, monat, prod_von, prod_bis, anzahl_de, anzahl_ww)
    return teile if all(t is not None for t in teile) else None


def amtliche_merkmale(zeile: dict) -> dict:
    """Abgleichsmerkmale aus EINER amtlichen Exportzeile (Rohwerte bleiben erhalten)."""
    code = (zeile.get(_SP_CODE) or "").strip()
    modell = (zeile.get(_SP_MODELL) or "").strip()
    datum = (zeile.get(_SP_DATUM) or "").strip()
    return {
        "herstellercode_amtlich": code if code and code.upper() not in ("N/A", "KEINE ANGABE") else None,
        "modell_amtlich": modell or None,
        "veroeffentlicht_amtlich": datum or None,
        "anzahl_de_amtlich": _zahl(zeile.get(_SP_ANZ_DE)),
        "anzahl_weltweit_amtlich": _zahl(zeile.get(_SP_ANZ_WW)),
        "prod_von": _jahr(zeile.get(_SP_VON)),
        "prod_bis": _jahr(zeile.get(_SP_BIS)),
        "marke": _marke(zeile.get(_SP_MARKE)),
    }


def _schluessel_aus_merkmalen(m: dict) -> tuple | None:
    return merkmalsschluessel(m["marke"], _monat_iso(m["veroeffentlicht_amtlich"]),
                              m["prod_von"], m["prod_bis"],
                              m["anzahl_de_amtlich"], m["anzahl_weltweit_amtlich"])


def eindeutigkeitsindex(export_zeilen: list[dict]) -> dict[tuple, set[str]]:
    """Merkmalsschlüssel -> Menge der (normalisierten) KBA-Referenzen im VOLLSTÄNDIGEN Bestand."""
    from app.kba_reconciliation import normalisiere_referenz
    index: dict[tuple, set[str]] = collections.defaultdict(set)
    for z in export_zeilen:
        k = _schluessel_aus_merkmalen(amtliche_merkmale(z))
        ref = normalisiere_referenz(z.get(_SP_REFERENZ))
        if k and ref:
            index[k].add(ref)
    return index


def ist_eindeutig(merkmale: dict, index: dict[tuple, set[str]]) -> bool:
    k = _schluessel_aus_merkmalen(merkmale)
    return bool(k) and len(index.get(k, ())) == 1


# ── Nachtrag auf bestehende Zeilen (Wartungskommando, additiv, wiederholbar) ──

NACHTRAG_SPALTEN = ("herstellercode_amtlich", "modell_amtlich", "veroeffentlicht_amtlich",
                    "anzahl_de_amtlich", "anzahl_weltweit_amtlich", "abgleich_eindeutig_amtlich")

# Mindestumfang eines VOLLSTÄNDIGEN amtlichen Exports (real: 7.953 Aktionen, 10.11.2026).
# Gegen einen Teilbestand ist "eindeutig" nicht belegbar -- ein Auszug fände Kollisionen nicht.
MIN_EXPORT_ZEILEN = 5000

# Marker in `schema_migrations`: der Nachtrag ist für den vorhandenen Bestand gegen einen
# vollständigen Export gelaufen. Erst dann ist der Rückruftabellen-Pfad aktiv (`freigabe_aktiv`).
FREIGABE_MARKER = "rueckruf_abgleich_nachtrag_v1"


class TeilexportFehler(RuntimeError):
    """Export zu klein für eine Eindeutigkeitsaussage gegen den vollständigen Bestand."""


def _soll_werte(m: dict, eindeutig: int) -> dict:
    return {"herstellercode_amtlich": m["herstellercode_amtlich"],
            "modell_amtlich": m["modell_amtlich"],
            "veroeffentlicht_amtlich": m["veroeffentlicht_amtlich"],
            "anzahl_de_amtlich": m["anzahl_de_amtlich"],
            "anzahl_weltweit_amtlich": m["anzahl_weltweit_amtlich"],
            "abgleich_eindeutig_amtlich": eindeutig}


def plane_nachtrag(export_zeilen: list[dict], rueckrufe: list[dict],
                   baureihen_marke: dict[str, str | None], *,
                   min_zeilen: int = MIN_EXPORT_ZEILEN) -> dict:
    """Reiner Plan für BESTEHENDE Zeilen, verknüpft über die amtliche Referenz. Fügt nie eine
    Zeile hinzu, löscht nie, ordnet nie neu zu, ändert nie mangel/abhilfe/datum/Baujahre/Referenz.

    Jeder Lauf bewertet ALLE Zeilen mit vertrauenswürdiger Referenz gegen den aktuellen Export neu:
      * Merkmale weichen ab (neue Kollision, geänderte Fallzahl, ...) -> Update. Eine neu
        auftretende Kollision entzieht damit die bisherige Freigabe (1 -> 0).
      * Referenz fehlt im Export, Marke widerspricht oder Referenz kollidiert markenübergreifend
        -> eine bestehende Freigabe wird entzogen (1 -> 0); sonst bleibt die Zeile unberührt.
      * Alles gleich -> nichts. Ein zweiter Lauf ergibt null Änderungen."""
    from app.kba_reconciliation import normalisiere_referenz
    from app.recall_filter import (
        kba_referenz_format_plausibel, kba_referenz_kollidiert_markenuebergreifend,
    )

    if len(export_zeilen) < min_zeilen:
        raise TeilexportFehler(f"Export hat {len(export_zeilen)} Zeilen (< {min_zeilen}) -- "
                               f"kein vollständiger amtlicher Bestand, Nachtrag abgebrochen.")
    je_ref: dict[str, list[dict]] = collections.defaultdict(list)
    for z in export_zeilen:
        ref = normalisiere_referenz(z.get(_SP_REFERENZ))
        if ref:
            je_ref[ref].append(z)
    index = eindeutigkeitsindex(export_zeilen)

    updates: list[dict] = []
    entzogen: list[dict] = []
    unmatched: list[dict] = []
    konflikte: list[dict] = []
    unveraendert = 0

    def _entziehen(r: dict, grund: str) -> None:
        if r.get("abgleich_eindeutig_amtlich") == 1:
            entzogen.append({"id": r["id"], "kba_referenz": r.get("kba_referenz"), "grund": grund})

    for r in rueckrufe:
        ref_roh = (r.get("kba_referenz") or "").strip()
        if not ref_roh or not kba_referenz_format_plausibel(ref_roh):
            continue
        treffer = je_ref.get(normalisiere_referenz(ref_roh)) or []
        if not treffer:
            unmatched.append({"id": r["id"], "kba_referenz": ref_roh})
            _entziehen(r, "Referenz nicht im aktuellen Export")
            continue
        marke_zeile = r.get("canonical_make") or baureihen_marke.get(r.get("baureihe_id"))
        m = amtliche_merkmale(treffer[0])
        if marke_zeile and _marke(marke_zeile) != m["marke"]:
            konflikte.append({"id": r["id"], "kba_referenz": ref_roh, "grund": "marke"})
            _entziehen(r, "Marke widerspricht dem amtlichen Datensatz")
            continue
        if kba_referenz_kollidiert_markenuebergreifend(ref_roh, marke_zeile):
            konflikte.append({"id": r["id"], "kba_referenz": ref_roh, "grund": "kollision"})
            _entziehen(r, "Referenz kollidiert markenübergreifend")
            continue
        # Mehrere, inhaltlich abweichende Exportzeilen derselben Referenz: Merkmale aus der
        # ersten Zeile (wie im übrigen Import), eindeutig ist das aber nicht.
        abweichend = len({_schluessel_aus_merkmalen(amtliche_merkmale(t)) for t in treffer}) > 1
        soll = _soll_werte(m, 0 if abweichend else int(ist_eindeutig(m, index)))
        if all(r.get(k) == v for k, v in soll.items()):
            unveraendert += 1
            continue
        if r.get("abgleich_eindeutig_amtlich") == 1 and soll["abgleich_eindeutig_amtlich"] == 0:
            entzogen.append({"id": r["id"], "kba_referenz": ref_roh,
                             "grund": "neue Kollision im amtlichen Bestand"})
        updates.append({"id": r["id"], "kba_referenz": ref_roh, **soll,
                        "prod_von": m["prod_von"], "prod_bis": m["prod_bis"]})
    update_ids = {u["id"] for u in updates}
    return {"updates": updates, "nur_entzug": [e for e in entzogen if e["id"] not in update_ids],
            "entzogen": entzogen, "unmatched": unmatched, "konflikte": konflikte,
            "unveraendert": unveraendert, "export_zeilen": len(export_zeilen)}


def apply_nachtrag(conn, plan: dict) -> dict:
    """Schreibt NUR die sechs Abgleichspalten (und ein bisher LEERES amtliches Produktionsfenster)
    und setzt danach den Freigabe-Marker -- nur wenn der Plan gegen einen vollständigen Export
    entstand. Transaktion beim Aufrufer (FAIL CLOSED wie app.kba_recall_refresh.apply_sync)."""
    n = 0
    for u in plan["updates"]:
        n += conn.execute(
            "UPDATE rueckruf SET herstellercode_amtlich=?, modell_amtlich=?, "
            "veroeffentlicht_amtlich=?, anzahl_de_amtlich=?, anzahl_weltweit_amtlich=?, "
            "abgleich_eindeutig_amtlich=?, "
            "prod_von_amtlich=COALESCE(prod_von_amtlich, ?), "
            "prod_bis_amtlich=COALESCE(prod_bis_amtlich, ?) WHERE id=?",
            (u["herstellercode_amtlich"], u["modell_amtlich"], u["veroeffentlicht_amtlich"],
             u["anzahl_de_amtlich"], u["anzahl_weltweit_amtlich"],
             u["abgleich_eindeutig_amtlich"], u["prod_von"], u["prod_bis"], u["id"])).rowcount
    for e in plan.get("nur_entzug") or []:
        n += conn.execute("UPDATE rueckruf SET abgleich_eindeutig_amtlich=0 "
                          "WHERE id=? AND abgleich_eindeutig_amtlich=1", (e["id"],)).rowcount
    if plan.get("export_zeilen", 0) >= MIN_EXPORT_ZEILEN:
        conn.execute("INSERT OR IGNORE INTO schema_migrations (name) VALUES (?)", (FREIGABE_MARKER,))
    invalidate_freigabe_cache()
    return {"aktualisiert": n, "entzogen": len(plan.get("entzogen") or [])}


# ── Freigabe des Rückruftabellen-Pfads ──────────────────────────────────────

_FREIGABE_CACHE: dict = {}
_FREIGABE_TTL = 60.0


def invalidate_freigabe_cache() -> None:
    _FREIGABE_CACHE.clear()


def freigabe_aktiv() -> bool:
    """Ob der Nachtrag für den vorhandenen Bestand gelaufen ist (Marker gesetzt). Ohne ihn erzeugt
    eine Rückruftabelle KEINEN Web-Rückruf (Verhalten wie vor dem Tabellenpfad) -- sonst stünde
    eine amtlich bereits erfasste Aktion doppelt im Bericht. Jeder Lesefehler -> False."""
    import time
    jetzt = time.monotonic()
    if _FREIGABE_CACHE and jetzt - _FREIGABE_CACHE["t"] < _FREIGABE_TTL:
        return _FREIGABE_CACHE["wert"]
    try:
        from app.database import get_conn
        with get_conn() as conn:
            wert = bool(conn.execute("SELECT 1 FROM schema_migrations WHERE name=?",
                                     (FREIGABE_MARKER,)).fetchone())
    except Exception:
        wert = False
    _FREIGABE_CACHE.update(t=jetzt, wert=wert)
    return wert


def main() -> None:
    """`python -m app.rueckruf_abgleich <export.csv> [--apply]` -- NUR der Nachtrag der
    Abgleichsmerkmale auf bestehende Zeilen, KEIN Import. Dry-Run ist der Default."""
    import sys
    from app.database import (
        get_alle_baureihen_kurz, get_alle_rueckrufe_fuer_sync, get_conn, invalidate_referenzdaten_cache,
    )
    from app.kba_recall_refresh import parse_export

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = sys.argv[1:]
    apply_ = "--apply" in args
    pfade = [a for a in args if a != "--apply"]
    if not pfade:
        log.error("Nutzung: python -m app.rueckruf_abgleich <pfad-zum-export.csv> [--apply]")
        raise SystemExit(2)
    zeilen = parse_export(pfade[0])
    plan = plane_nachtrag(zeilen, get_alle_rueckrufe_fuer_sync(),
                          {b["id"]: b.get("marke") for b in get_alle_baureihen_kurz()})
    log.info("=== Rückruf-Abgleichsmerkmale: Nachtrag auf bestehende Zeilen (kein Import) ===")
    log.info("Export-Zeilen %d | Updates %d | Freigabe entzogen %d | unverändert %d | "
             "Referenz nicht im Export %d | Konflikte %d", plan["export_zeilen"], len(plan["updates"]),
             len(plan["entzogen"]), plan["unveraendert"], len(plan["unmatched"]),
             len(plan["konflikte"]))
    for e in plan["entzogen"][:20]:
        log.info("  FREIGABE ENTZOGEN id=%s ref=%s: %s", e["id"], e["kba_referenz"], e["grund"])
    if not apply_:
        log.info("Dry-Run -- nichts geschrieben. Mit --apply wird genau dieser Plan angewendet.")
        return
    with get_conn() as conn:
        ergebnis = apply_nachtrag(conn, plan)
    invalidate_referenzdaten_cache()
    log.info("=== --apply: %d Zeile(n) geändert, Freigabe-Marker gesetzt ===", ergebnis["aktualisiert"])


# ── Laufzeit: Web-Datensatz <-> angezeigter amtlicher Eintrag ───────────────

def _passt(datensatz: dict, r: dict) -> bool:
    if r.get("abgleich_eindeutig_amtlich") != 1:
        return False
    monat_web = _monat_iso(datensatz.get("datum"))
    monat_amt = _monat_iso(r.get("veroeffentlicht_amtlich"))
    bz = datensatz.get("bauzeitraum") or ""
    jahre = [int(j) for j in re.findall(r"(?:19|20)\d{2}", bz)]
    de, ww = web_fallzahlen(datensatz.get("anzahl"))
    belegt = (monat_web, monat_amt, jahre, de, ww, r.get("prod_von_amtlich"),
              r.get("prod_bis_amtlich"), r.get("anzahl_de_amtlich"), r.get("anzahl_weltweit_amtlich"))
    if any(not x for x in belegt):
        return False
    return (monat_web == monat_amt
            and (min(jahre), max(jahre)) == (int(r["prod_von_amtlich"]), int(r["prod_bis_amtlich"]))
            and de == int(r["anzahl_de_amtlich"]) and ww == int(r["anzahl_weltweit_amtlich"])
            and modellmengen_decken_sich(datensatz.get("modelle"), r.get("modell_amtlich")))


def eindeutige_amtliche_zuordnung(datensatz: dict | None, amtliche: list[dict]) -> dict | None:
    """Der EINE angezeigte amtliche Eintrag, der dieselbe Aktion ist -- oder None."""
    if not datensatz or not amtliche:
        return None
    passende = [r for r in amtliche if _passt(datensatz, r)]
    from app.kba_reconciliation import normalisiere_referenz
    refs = {normalisiere_referenz(r.get("kba_referenz")) for r in passende}
    return passende[0] if len(refs) == 1 else None


if __name__ == "__main__":
    main()
