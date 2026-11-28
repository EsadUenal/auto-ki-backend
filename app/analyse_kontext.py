"""Kontext für Rückfragen zu einem gespeicherten Check (CHAT-1, Punkt 5).

Der Rückfrage-Chat sieht NUR, was dieser Builder aus dem gespeicherten Check baut. Vorher war das
`bericht` plus 9 Kurzfelder, hart auf 8.000 Zeichen von vorn gekürzt (davor schon einmal auf
60.000): Vorbehalte, Nutzerangaben und Zuordnungen, die die sichtbare Ausgabe zeigt, erreichten den
Prompt nicht, und was hinter Zeichen 8.000 stand, fehlte ohne jeden Hinweis.

Jetzt gilt:
  - EINE gemeinsame Grenze (`app.llm.ANALYSE_KONTEXT_MAX`), keine zweite stille Kopfkürzung.
  - Herkunften bleiben getrennt: Angaben des Nutzers (kein Beleg), Vorbehalte und Grenzen der
    Analyse, Bericht.
  - Vorbehalte stehen vor dem Bericht und werden zusammen mit ihren Fakten erhalten; der Bericht
    bekommt, was übrig bleibt, und wird abschnittsweise ausgewählt.
  - Was nicht hineinpasst, wird unter "NICHT IM KONTEXT" benannt — der Assistent darf daraus nie
    schließen, der vollständige Bericht enthalte es nicht.

Der Builder liest nur bereits gespeicherte Felder; die Check-Produzenten bleiben unverändert.
"""
from __future__ import annotations

import json
import re
from typing import Any

# Obergrenzen je Block (Zeichen). Sie sorgen dafuer, dass der Bericht nicht von Vorbehalten
# verdraengt wird und umgekehrt nicht alle Vorbehalte vom Bericht.
_CAP_VORBEHALTE = 1500
_CAP_NUTZER = 900
_CAP_HINWEISE = 1600      # Kauf: Wartungs-/Rueckruf-/Risikohinweise mit Beleglage
_CAP_PLAN = 2000          # Verkauf: Auszug aus dem Verkaufsplan
_CAP_KEYFINDINGS = 700
_MIN_BERICHT = 1200       # darunter wird der Bericht nur noch als Abschnittsliste gefuehrt

_STATUS_TEXT = {
    "completed_high": "abgeschlossen mit belastbarer Marktdatenbasis",
    "completed_medium": "abgeschlossen mit eingeschränkter Marktdatenbasis",
    "completed_no_market": "abgeschlossen OHNE belastbaren Marktpreis (keine Preisangabe belegt)",
    "research_failed": "Recherche fehlgeschlagen",
}

_KURZFASSUNG = (
    ("empfehlung_anzeige", "Empfehlung"),
    ("empfehlung", "Empfehlung (Rohwert)"),
    ("preis_bewertung", "Preisbewertung"),
    ("marktpreis_min", "Marktpreis von (EUR)"),
    ("marktpreis_max", "Marktpreis bis (EUR)"),
    ("empfohlener_preis", "Empfohlener Preis (EUR)"),
    ("schnellverkaufs_preis", "Schnellverkaufspreis (EUR)"),
    ("maximal_preis", "Maximalpreis (EUR)"),
)

_NUTZER_KEYS_LANG = {"inserat_text", "beschreibung", "inserat_titel", "tuning", "vorschaeden"}
# Geldbetraege der Eingabe sind Wunsch oder Inseratsangabe des Nutzers, kein Marktpreis und kein Beleg.
_NUTZER_PREIS_KEYS = {
    "preis_vorstellung": "Preisvorstellung des Nutzers, kein Marktpreis",
    "preis_untergrenze": "Preisuntergrenze des Nutzers, kein Marktpreis",
    "preis": "Preis laut Inserat/Nutzer, kein Marktpreis",
}
_NUTZER_KEYS_SKIP = {"lauf_id", "bild_base64"}


def _kurz(text: Any, n: int) -> str:
    t = " ".join(str(text).split())
    return t if len(t) <= n else t[: n - 1].rstrip() + "…"


def _kurz_markiert(text: Any, n: int) -> str:
    """Wie `_kurz`, aber eine Kuerzung ist ausdruecklich benannt (keine scheinbar vollstaendige Teilregel)."""
    t = " ".join(str(text).split())
    return t if len(t) <= n else t[: n - 14].rstrip() + " […gekürzt]"


def _skalar(v: Any) -> bool:
    return isinstance(v, (str, int, float, bool))


def _block(zeilen: list[str], cap: int, name: str, nicht: list[str]) -> list[str]:
    """Nimmt Zeilen, solange `cap` reicht; Ausgelassenes wird gezaehlt und gemeldet."""
    out: list[str] = []
    used = 0
    for i, z in enumerate(zeilen):
        if used + len(z) + 1 > cap:
            rest = len(zeilen) - i
            nicht.append(f"{rest} weitere Einträge aus „{name}“")
            break
        out.append(z)
        used += len(z) + 1
    return out


# ── Blöcke ───────────────────────────────────────────────────────────────────

def _kopf(typ: str, e: dict) -> list[str]:
    z = [f"Art der Analyse: {'Kaufcheck' if typ == 'kauf' else 'Verkaufscheck'}"]
    for key, titel in _KURZFASSUNG:
        v = e.get(key)
        if v in (None, "", []) or not _skalar(v):
            continue
        if key == "empfehlung" and e.get("empfehlung_anzeige"):
            continue
        z.append(f"{titel}: {v}")
    status = e.get("research_status")
    if status:
        z.append(f"Recherchestatus: {_STATUS_TEXT.get(status, status)}")
    return z


def _vorbehalte(typ: str, e: dict) -> list[str]:
    z: list[str] = []
    if e.get("vertrauen") or e.get("quelle"):
        z.append(f"Herkunft der Analyse: {e.get('quelle') or 'unbekannt'}; "
                 f"Vertrauensstufe der Analyse: {e.get('vertrauen') or 'unbekannt'}.")
    for key in ("empfehlung_hinweis",):
        if e.get(key):
            z.append(_kurz(e[key], 400))
    luecke = e.get("technik_datenluecke")
    if luecke:
        z.append("Datenlücken Technik: " + _kurz("; ".join(map(str, luecke)) if isinstance(luecke, list) else luecke, 350))
    ident = e.get("identitaet_aufloesung")
    if isinstance(ident, dict):
        fehlt = ident.get("fehlend") or []
        z.append(f"Fahrzeugidentität: Stufe {ident.get('stufe')}"
                 + (f"; nicht bestätigt: {', '.join(map(str, fehlt))}" if fehlt else "") + ".")
    hu = e.get("hu_pruefung")
    if isinstance(hu, dict) and hu.get("hinweis"):
        z.append("HU: " + _kurz(hu["hinweis"], 250))
    if typ == "verkauf":
        plan = e.get("verkaufsplan") if isinstance(e.get("verkaufsplan"), dict) else {}
        fz = plan.get("fahrzeug") if isinstance(plan.get("fahrzeug"), dict) else {}
        for h in (fz.get("hinweise") or [])[:3]:
            z.append("Fahrzeug: " + _kurz(h, 250))
        if fz and fz.get("belastbar") is False:
            z.append("Fahrzeugzuordnung: nicht belastbar bestätigt.")
        markt = plan.get("markt") if isinstance(plan.get("markt"), dict) else {}
        for k in ("orientierung", "dauer"):
            m = markt.get(k)
            if isinstance(m, dict) and m.get("text"):
                z.append(f"Markt ({k}, Status {m.get('status')}): {_kurz(m['text'], 250)}"
                         + (f" {_kurz(m['hinweis'], 160)}" if m.get("hinweis") else ""))
    return z


def _nutzerangaben(eingabe: dict | None) -> list[str]:
    if not isinstance(eingabe, dict):
        return []
    z: list[str] = []
    for k, v in eingabe.items():
        if k in _NUTZER_KEYS_SKIP or v in (None, "", [], {}):
            continue
        if _skalar(v):
            zusatz = f" ({_NUTZER_PREIS_KEYS[k]})" if k in _NUTZER_PREIS_KEYS else ""
            z.append(f"{k}: {_kurz(v, 300 if k in _NUTZER_KEYS_LANG else 120)}{zusatz}")
        elif isinstance(v, list) and all(_skalar(x) for x in v):
            z.append(f"{k}: {_kurz(', '.join(map(str, v)), 200)}")
    return z


def _hinweise_kauf(e: dict) -> list[str]:
    """Wartungs-, Rückruf- und Risikohinweise MIT ihrer Beleglage (trust/applicability/confidence)."""
    z: list[str] = []
    rang = {"wartung": 0, "rueckruf": 1, "motorproblem": 2, "schwachstelle": 3}
    insights = [i for i in (e.get("insights") or []) if isinstance(i, dict) and i.get("kategorie") in rang]
    insights.sort(key=lambda i: rang[i["kategorie"]])
    for i in insights:
        meta = [f"Datenqualität {i.get('confidence')}"]
        if i.get("trust"):
            meta.append("Herkunft: " + {"verified": "geprüfter Datenbankeintrag",
                                         "unverified_db": "gemeldeter Hinweis der Datenbank, nicht geprüft",
                                         "web": "Webquelle"}.get(i["trust"], str(i["trust"])))
        if i.get("applicability"):
            meta.append(f"Anwendbarkeit {i['applicability']} (kein Nachweis für dieses einzelne Fahrzeug)")
        z.append(f"[{i['kategorie']}] {i.get('titel')}: {_kurz_markiert(i.get('beschreibung', ''), 700 if i['kategorie'] == 'wartung' else 300)} ({'; '.join(meta)})"
                 + (f" Einfluss: {_kurz(i['einfluss'], 140)}" if i.get("einfluss") else ""))
    return z


def _plan_verkauf(e: dict) -> list[str]:
    plan = e.get("verkaufsplan") if isinstance(e.get("verkaufsplan"), dict) else {}
    z: list[str] = []
    fz = plan.get("fahrzeug") if isinstance(plan.get("fahrzeug"), dict) else {}
    for zeile in (fz.get("zeilen") or []):
        if isinstance(zeile, dict) and zeile.get("label"):
            z.append(f"Fahrzeug – {zeile.get('label')}: {zeile.get('wert')} (Herkunft: {zeile.get('quelle')})")
    for item in ((plan.get("verhandlung") or {}).get("argumente") or []) if isinstance(plan.get("verhandlung"), dict) else []:
        if isinstance(item, dict) and item.get("einwand"):
            z.append(f"Verhandlung – {item['einwand']}: {_kurz(item.get('antwort', ''), 260)}")
    for d in plan.get("dokumente") or []:
        if isinstance(d, dict) and d.get("dokument"):
            z.append(f"Dokument: {d['dokument']}")
    ph = plan.get("pruefhinweise")
    if isinstance(ph, dict):
        for k in ("schwachstellen", "rueckrufe"):
            for item in ph.get(k) or []:
                if isinstance(item, dict) and item.get("titel"):
                    z.append(f"Prüfhinweis ({k}) – {item['titel']}: {_kurz(item.get('text', ''), 200)}")
        if ph.get("hinweis"):
            z.append("Prüfhinweis: " + _kurz(ph["hinweis"], 200))
    for k in ("wertminderer", "werttreiber"):
        for item in plan.get(k) or []:
            if isinstance(item, dict) and item.get("titel"):
                z.append(f"{k} – {item['titel']}: {_kurz(item.get('text', ''), 200)}")
    for item in plan.get("naechste_schritte") or []:
        if isinstance(item, str):
            z.append("Nächster Schritt: " + _kurz(item, 200))
        elif isinstance(item, dict) and (item.get("titel") or item.get("text")):
            z.append("Nächster Schritt: " + _kurz(f"{item.get('titel', '')} {item.get('text', '')}", 220))
    return z


def _keyfindings(e: dict) -> list[str]:
    z = []
    for k in e.get("key_findings") or []:
        if isinstance(k, dict) and k.get("titel"):
            z.append(f"{k['titel']}: {_kurz(k.get('beschreibung', ''), 220)}")
    return z


# ── Bericht, abschnittsweise ──────────────────────────────────────────────────

_PRIO = ("kaufempfehlung|verkauf|empfehlung", "risik|hinweis|wartung|rückruf", "preis|markt", "inserat", "")


def _bericht_abschnitte(bericht: str) -> tuple[str, list[tuple[str, str]]]:
    teile = re.split(r"(?m)^(?=## )", bericht)
    vorspann = ""
    abschnitte: list[tuple[str, str]] = []
    for t in teile:
        if t.startswith("## "):
            titel = t.splitlines()[0][3:].strip()
            abschnitte.append((titel, t.rstrip()))
        else:
            vorspann += t
    return vorspann.strip(), abschnitte


def _prio(titel: str) -> int:
    for i, muster in enumerate(_PRIO[:-1]):
        if re.search(muster, titel, re.IGNORECASE):
            return i
    return len(_PRIO) - 1


# Relevanz eines Eintrags fuer die Auswahl bei Platzmangel: Vorbehalte, Herkunftsangaben, Intervalle.
# ACHTUNG: Das entscheidet nur, WELCHE Eintraege bei Platzmangel Vorrang haben. Dass ein Eintrag vollstaendig
# bleibt (Taetigkeit, Erst-/Folgeregel, Bedingungen, Vorbehalte), garantiert die BLOCKSTRUKTUR, nicht die Liste.
_VORBEHALT_RE = re.compile(
    r"vorbehalt|nicht bestätigt|ungeklärt|unbekannt|voraussetzung|geltung|laut quelle|einsatzbedingung|"
    r"deine angabe|laut inserat|laut verkäufer|kein beleg|nicht belegt|ungeprüft|nicht geprüft|"
    r"intervall|prüfung|wechsel|bedingung|nur teilweise|teilweise vorhanden|unklar|danach|erstprüfung|folge|"
    r"\d[\d.]*\s*(?:km|jahr|monat)",
    re.IGNORECASE,
)
# Ein Eintrag (Gruppe) beginnt mit einem Listenpunkt mit fettem Titel ("- **Antriebsriemen** ...") oder einem
# fetten Absatzanfang. Alles danach bis zur Leerzeile (Folgezeilen, eingerueckte Zeilen, schlichte Unterpunkte)
# gehoert zum selben Eintrag: Erst-/Folgeregel und Vorbehalt duerfen nicht auf Zeilen verteilt werden.
_EINTRAG_START = re.compile(r"^\s*(?:[-*•]\s+|\d+\.\s+)?\*\*")


def _eintraege(zeilen: list[str]) -> list[list[str]]:
    bloecke: list[list[str]] = []
    cur: list[str] = []
    for z in zeilen:
        if not z.strip():
            if cur:
                bloecke.append(cur)
                cur = []
            continue
        if cur and _EINTRAG_START.match(z):
            bloecke.append(cur)
            cur = [z]
        else:
            cur.append(z)
    if cur:
        bloecke.append(cur)
    return bloecke


def _eintrag_titel(block: list[str]) -> str:
    m = re.search(r"\*\*(.+?)\*\*", block[0])
    return (m.group(1) if m else block[0].strip())[:50]


def _vorbehalts_auszug(text: str, platz: int, titel_abschnitt: str, nicht: list[str]) -> str | None:
    """Bei Platzmangel: Ueberschrift plus GANZE Eintraege mit Relevanz. Passt ein relevanter Eintrag nicht
    mehr vollstaendig hinein, wird er NICHT teilweise uebernommen, sondern als ausgelassen benannt — eine
    scheinbar vollstaendige Teilregel gibt es nicht."""
    zeilen = text.splitlines()
    kopf = zeilen[0]
    marke = "[… Abschnitt hier auf ganze Einträge mit Vorbehalten, Intervallen oder Herkunftsangaben gekürzt]"
    rest = platz - len(kopf) - len(marke) - 4
    gew: list[str] = []
    uebrig = 0
    for blk in _eintraege(zeilen[1:]):
        txt = "\n".join(blk)
        if not _VORBEHALT_RE.search(txt):
            uebrig += 1
            continue
        if len(txt) + 1 <= rest:
            gew.append(txt)
            rest -= len(txt) + 1
        else:
            nicht.append(f"Eintrag „{_eintrag_titel(blk)}“ in „{titel_abschnitt}“ (zu lang für den Rest, nicht teilweise übernommen)")
    if uebrig:
        nicht.append(f"{uebrig} Einträge ohne Vorbehalt/Intervall in „{titel_abschnitt}“")
    if not gew:
        return None
    return "\n".join([kopf, *gew, marke])


def _bericht_auswahl(bericht: str, platz: int, nicht: list[str]) -> list[str]:
    vorspann, abschnitte = _bericht_abschnitte(bericht)
    out: list[str] = []
    rest = platz
    if vorspann:
        # Absatzweise, nie mitten in einem Eintrag.
        absaetze = [a for a in re.split(r"\n\s*\n", vorspann) if a.strip()]
        gew: list[str] = []
        used = 0
        for ab in absaetze:
            if used + len(ab) + 2 > 900:
                nicht.append("Rest des Berichtskopfes (Titel, Datenbasis, Identitätszeilen)")
                break
            gew.append(ab)
            used += len(ab) + 2
        v = "\n\n".join(gew)
        if v:
            out.append(v)
            rest -= len(v) + 2
    gewaehlt: dict[int, str] = {}
    teilweise: set[int] = set()
    # Abschnitte mit Relevanz zuerst, dann nach Titelrang, dann in Berichtsreihenfolge.
    reihenfolge = sorted(range(len(abschnitte)),
                         key=lambda i: (0 if _VORBEHALT_RE.search(abschnitte[i][1]) else 1,
                                        _prio(abschnitte[i][0]), i))
    for idx in reihenfolge:
        titel, text = abschnitte[idx]
        if len(text) + 2 <= rest:
            gewaehlt[idx] = text
            rest -= len(text) + 2
        elif rest > 300:
            auszug = _vorbehalts_auszug(text, rest, titel, nicht)
            if auszug:
                gewaehlt[idx] = auszug
                teilweise.add(idx)
                rest -= len(auszug) + 2
    for idx in range(len(abschnitte)):
        if idx in gewaehlt:
            out.append(gewaehlt[idx])
        titel = abschnitte[idx][0]
        if idx not in gewaehlt:
            nicht.append(f"Berichtsabschnitt „{titel}“")
        elif idx in teilweise:
            nicht.append(f"übrige Einträge des Berichtsabschnitts „{titel}“")
    return out


# ── Zusammenbau ───────────────────────────────────────────────────────────────

def baue_analyse_kontext(typ: str, ergebnis_json: str | dict, eingabe_json: str | dict | None,
                         max_zeichen: int) -> str:
    """Baut den Kontext; leerer String, wenn nichts Verwertbares gespeichert ist."""
    def _laden(x: Any) -> dict:
        if isinstance(x, dict):
            return x
        try:
            d = json.loads(x) if x else {}
        except (json.JSONDecodeError, TypeError):
            return {}
        return d if isinstance(d, dict) else {}

    e = _laden(ergebnis_json)
    eingabe = _laden(eingabe_json)
    nicht: list[str] = []
    kopf = _kopf(typ, e)
    bericht = e.get("bericht") if isinstance(e.get("bericht"), str) else ""
    if not bericht.strip() and len(kopf) <= 1 and not eingabe:
        return ""

    teile: list[tuple[str, list[str]]] = [("KURZFASSUNG DER ANALYSE", kopf)]
    v = _block(_vorbehalte(typ, e), _CAP_VORBEHALTE, "Vorbehalte und Grenzen", nicht)
    if v:
        teile.append(("VORBEHALTE UND GRENZEN DER ANALYSE (bleiben bei jeder Wiedergabe erhalten)", v))
    n = _block(_nutzerangaben(eingabe), _CAP_NUTZER, "Angaben des Nutzers", nicht)
    if n:
        teile.append(("ANGABEN DES NUTZERS (vom Nutzer eingegeben oder aus seinem Inserat; KEIN Beleg)", n))
    if typ == "kauf":
        h = _block(_hinweise_kauf(e), _CAP_HINWEISE, "Wartungs-, Rückruf- und Risikohinweise", nicht)
        if h:
            teile.append(("WARTUNGS-, RÜCKRUF- UND RISIKOHINWEISE MIT BELEGLAGE", h))
    else:
        p = _block(_plan_verkauf(e), _CAP_PLAN, "Verkaufsplan", nicht)
        if p:
            teile.append(("AUSZUG AUS DEM VERKAUFSPLAN (Herkunft je Angabe)", p))
    k = _block(_keyfindings(e), _CAP_KEYFINDINGS, "Kernerkenntnisse", nicht)
    if k:
        teile.append(("KERNERKENNTNISSE", k))

    def _rendern(blk: list[tuple[str, list[str]]]) -> str:
        return "\n\n".join(f"### {t}\n" + "\n".join(zl) for t, zl in blk)

    fix = _rendern(teile)
    # Platz fuer den Bericht: Rest minus Reserve fuer die Auslassungsliste.
    platz = max(0, max_zeichen - len(fix) - 700)
    bericht_teile: list[str] = []
    if bericht.strip():
        if platz >= _MIN_BERICHT:
            bericht_teile = _bericht_auswahl(bericht, platz, nicht)
        else:
            nicht.append("der gesamte Bericht (Platz durch Vorbehalte und Angaben belegt)")
    out = fix
    if bericht_teile:
        out += "\n\n### BERICHT (Auswahl der Abschnitte)\n" + "\n\n".join(bericht_teile)
    if nicht:
        liste = "; ".join(nicht)
        out += ("\n\n### NICHT IM KONTEXT (hier nicht sichtbar, aber im vollständigen Bericht des Nutzers "
                "möglicherweise vorhanden)\n" + _kurz(liste, 600))
    if len(out) > max_zeichen:       # Sicherheitsnetz mit sichtbarer Marke, nie still
        out = out[: max_zeichen - 90].rstrip() + "\n[NICHT IM KONTEXT: Rest wegen Längenbegrenzung abgeschnitten.]"
    return out
