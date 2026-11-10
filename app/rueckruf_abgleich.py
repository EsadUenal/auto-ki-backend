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


# ── Nachtrag auf bestehende Zeilen (Wartungskommando, additiv, idempotent) ──

NACHTRAG_SPALTEN = ("herstellercode_amtlich", "modell_amtlich", "veroeffentlicht_amtlich",
                    "anzahl_de_amtlich", "anzahl_weltweit_amtlich", "abgleich_eindeutig_amtlich")


def plane_nachtrag(export_zeilen: list[dict], rueckrufe: list[dict],
                   baureihen_marke: dict[str, str | None]) -> dict:
    """Reiner Plan. Nur Zeilen mit format-plausibler, nicht markenübergreifend kollidierender
    Referenz, deren Marke zur amtlichen passt, und die noch nicht nachgetragen sind
    (`abgleich_eindeutig_amtlich IS NULL`)."""
    from app.kba_reconciliation import normalisiere_referenz
    from app.recall_filter import (
        kba_referenz_format_plausibel, kba_referenz_kollidiert_markenuebergreifend,
    )

    je_ref: dict[str, list[dict]] = collections.defaultdict(list)
    for z in export_zeilen:
        ref = normalisiere_referenz(z.get(_SP_REFERENZ))
        if ref:
            je_ref[ref].append(z)
    index = eindeutigkeitsindex(export_zeilen)

    updates, konflikte, unmatched, bereits = [], [], [], 0
    for r in rueckrufe:
        ref_roh = (r.get("kba_referenz") or "").strip()
        if not ref_roh or not kba_referenz_format_plausibel(ref_roh):
            continue
        if r.get("abgleich_eindeutig_amtlich") is not None:
            bereits += 1
            continue
        treffer = je_ref.get(normalisiere_referenz(ref_roh)) or []
        if not treffer:
            unmatched.append({"id": r["id"], "kba_referenz": ref_roh})
            continue
        marke_zeile = r.get("canonical_make") or baureihen_marke.get(r.get("baureihe_id"))
        amtlich = treffer[0]
        m = amtliche_merkmale(amtlich)
        if marke_zeile and _marke(marke_zeile) != m["marke"]:
            konflikte.append({"id": r["id"], "kba_referenz": ref_roh, "grund": "marke"})
            continue
        if kba_referenz_kollidiert_markenuebergreifend(ref_roh, marke_zeile):
            konflikte.append({"id": r["id"], "kba_referenz": ref_roh, "grund": "kollision"})
            continue
        # Mehrere, inhaltlich abweichende Exportzeilen derselben Referenz: Merkmale bleiben
        # erhalten (erste Zeile, wie im übrigen Import), eindeutig ist das aber nicht.
        abweichend = len({_schluessel_aus_merkmalen(amtliche_merkmale(t)) for t in treffer}) > 1
        updates.append({
            "id": r["id"], "kba_referenz": ref_roh,
            **{k: m[k] for k in NACHTRAG_SPALTEN if k in m},
            "abgleich_eindeutig_amtlich": 0 if abweichend else int(ist_eindeutig(m, index)),
            "prod_von": m["prod_von"], "prod_bis": m["prod_bis"],
        })
    return {"updates": updates, "konflikte": konflikte, "unmatched": unmatched,
            "bereits_befuellt": bereits}


def apply_nachtrag(conn, plan: dict) -> dict:
    """Schreibt NUR die additiven Abgleichspalten (und ein bisher LEERES amtliches
    Produktionsfenster); serverseitig nur bei `abgleich_eindeutig_amtlich IS NULL`.
    Transaktion beim Aufrufer (FAIL CLOSED wie app.kba_recall_refresh.apply_sync)."""
    n = 0
    for u in plan["updates"]:
        cur = conn.execute(
            "UPDATE rueckruf SET herstellercode_amtlich=?, modell_amtlich=?, "
            "veroeffentlicht_amtlich=?, anzahl_de_amtlich=?, anzahl_weltweit_amtlich=?, "
            "abgleich_eindeutig_amtlich=?, "
            "prod_von_amtlich=COALESCE(prod_von_amtlich, ?), "
            "prod_bis_amtlich=COALESCE(prod_bis_amtlich, ?) "
            "WHERE id=? AND abgleich_eindeutig_amtlich IS NULL",
            (u["herstellercode_amtlich"], u["modell_amtlich"], u["veroeffentlicht_amtlich"],
             u["anzahl_de_amtlich"], u["anzahl_weltweit_amtlich"],
             u["abgleich_eindeutig_amtlich"], u["prod_von"], u["prod_bis"], u["id"]))
        n += cur.rowcount
    return {"aktualisiert": n}


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
