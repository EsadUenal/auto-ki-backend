"""CHAT-1 Abschlussnachweise mit echten Ausschnitten aus den Nutzerexporten (Lexus, Passat, Golf).

Die Ausschnitte sind ORIGINALTEXT, ersetzen aber weder vollstaendige gespeicherte ergebnis-Objekte noch
die damals abgerufenen Webinhalte. Alles, was um die Ausschnitte herum steht (Fuellabschnitte, Struktur),
ist als SYNTH gekennzeichnet nachgebaut. Gepruft wird der tatsaechliche Kontextbuilder und der tatsaechlich
gesendete Prompt; die Stubs belegen Kontext und Verarbeitung, KEINE Modelltreue.

Isolation: Netzsperre + Temp-DB VOR den App-Imports.   Ausfuehren: python test_chat1_originalfaelle.py
"""
from __future__ import annotations

import asyncio
import datetime as dt
import io
import json
import os
import re
import socket
import sys
import tempfile

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
_TMP = tempfile.mkdtemp(prefix="chat1o_")
os.environ["AUTO_KI_DB_PATH"] = os.path.join(_TMP, "t.db")
os.environ["AUTO_KI_CHROMA_PATH"] = os.path.join(_TMP, "chroma")
os.environ["AUTO_KI_ENV"] = "development"
BLOCKED: list[str] = []
_orig = socket.socket.connect


def _guard(self, addr):
    host = addr[0] if isinstance(addr, tuple) else addr
    if host not in ("127.0.0.1", "::1", "localhost"):
        BLOCKED.append(str(host))
        raise OSError(f"NETZSPERRE {host}")
    return _orig(self, addr)


socket.socket.connect = _guard
sys.path.insert(0, ".")
import app.database as db  # noqa: E402
db.ensure_tables()
import app.llm as llm  # noqa: E402
import app.routers.analyse_frage as r_frage  # noqa: E402
from app.analyse_kontext import baue_analyse_kontext  # noqa: E402

FEHLER: list[str] = []
PASS = 0


def check(name, ok, detail=""):
    global PASS
    if ok:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FEHLER.append(name)
        print(f"  FAIL  {name}{(' — ' + detail) if detail else ''}")


check("Isolation: Temp-DB", os.environ["AUTO_KI_DB_PATH"].startswith(tempfile.gettempdir()))


class _Cand:
    finish_reason = None


class _Chunk:
    def __init__(self, t): self.text = t; self.candidates = [_Cand()]


CAPT: dict = {}


class _Models:
    async def generate_content_stream(self, model, contents, config):
        CAPT["system"] = config.system_instruction
        CAPT["contents"] = contents

        async def gen():
            yield _Chunk("ok")
        return gen()


class _Aio: models = _Models()
class _Client: aio = _Aio()


llm._get_client = lambda: _Client()
llm._vector_search = lambda q, ids, n=3: []
TAV: list[str] = []


async def _tav(q, count=3, exclude_domains=None, **k):
    TAV.append(q)
    return []


llm.tavily_search = _tav
llm.curate_results = lambda r, kategorie=None, max_results=5: r[:max_results]
llm.TAVILY_API_KEY = "stub"

FILL = "Füllzeile ohne weitere Aussage zum Fahrzeug. " * 6   # SYNTH, ca. 260 Zeichen je Zeile


def abschnitt(titel, zeilen):
    return f"## {titel}\n\n" + "\n".join(zeilen)


def blocks(kontext):
    teile = re.split(r"(?m)^### ", kontext)
    return {t.splitlines()[0]: t for t in teile[1:]}


# ═══ Originalgegenprobe KaufCheck: Lexus RC F 2017, 82.000 km ════════════════════════════════════════
print("\n[Lexus] KaufCheck: Originalvorbehalte samt Tätigkeit im Prompt")
L_GETRIEBE = ("- **Automatikgetriebeöl** Prüfung: alle 60.000 km oder 4 Jahre. Marktvorbehalt der Quelle: "
              "Geltung in Deutschland nicht bestätigt. Angabe gilt laut Quelle für normale Einsatzbedingungen. "
              "Ob die Arbeit gemacht wurde, ist unbekannt.")
L_MOTOROEL = ("- **Motoröl** Wechselintervall laut Quelle. Voraussetzung der bedingten Regel ungeklärt. "
              "Ob die Arbeit gemacht wurde, ist unbekannt.")  # SYNTH-Wortlaut zum Originalbefund
L_KOPF = "**Lexus RC F (2017)**\n\nDatenbasis: Webrecherche. Das Fahrzeug ist ca. neun Jahre alt, 82.000 km.\n\n"
kurz = (L_KOPF + abschnitt("Kaufempfehlung", ["**VORSICHT**"]) + "\n\n" +
        abschnitt("Verkäuferfragen", [L_GETRIEBE, L_MOTOROEL]))
lang = (L_KOPF + abschnitt("Kaufempfehlung", ["**VORSICHT**"]) + "\n\n" +
        abschnitt("Relevante Risiken und Hinweise", [FILL] * 12) + "\n\n" +
        abschnitt("Inserat im Vergleich", [FILL] * 12) + "\n\n" +
        abschnitt("Besichtigung", [FILL] * 12) + "\n\n" +
        abschnitt("Probefahrt", [FILL] * 12) + "\n\n" +
        # Wartungszeilen stehen mitten in einem GROSSEN Abschnitt, hinter Fuellmaterial (SYNTH-Struktur)
        abschnitt("Verkäuferfragen", [FILL] * 20 + [L_GETRIEBE, L_MOTOROEL] + [FILL] * 20) + "\n\n" +
        abschnitt("Dokumente und finale Checkliste", [FILL] * 12))
ORIG = ["Prüfung: alle 60.000 km oder 4 Jahre.",
        "Marktvorbehalt der Quelle: Geltung in Deutschland nicht bestätigt",
        "Angabe gilt laut Quelle für normale Einsatzbedingungen",
        "Ob die Arbeit gemacht wurde, ist unbekannt.",
        "Voraussetzung der bedingten Regel ungeklärt"]
for name, bericht in (("kurzer Bericht", kurz), ("langer Bericht", lang)):
    ctx = baue_analyse_kontext("kauf", json.dumps({"bericht": bericht, "research_status": "completed_medium"}),
                               "{}", llm.ANALYSE_KONTEXT_MAX)
    if name == "langer Bericht":
        check("Fixture: Bericht deutlich länger als die Grenze", len(bericht) > 2 * llm.ANALYSE_KONTEXT_MAX, str(len(bericht)))
    for o in ORIG:
        check(f"Lexus/{name}: Originalstelle im Kontext: {o[:48]}", o in ctx)
    zeile = next((z for z in ctx.splitlines() if "Automatikgetriebeöl" in z), "")
    check(f"Lexus/{name}: Tätigkeit 'Prüfung' und Vorbehalte stehen in DERSELBEN Zeile wie das Bauteil",
          "Prüfung: alle 60.000 km" in zeile and "nicht bestätigt" in zeile and "unbekannt" in zeile)
    check(f"Lexus/{name}: aus 'Prüfung' wird kein 'Wechsel' (Getriebeöl-Zeile ohne Wechsel)", "Wechsel" not in zeile)
    check(f"Lexus/{name}: Berichtsalter 'ca. neun Jahre' bleibt stehen", "ca. neun Jahre" in ctx)
    check(f"Lexus/{name}: Grenze eingehalten", len(ctx) <= llm.ANALYSE_KONTEXT_MAX, str(len(ctx)))
    check(f"Lexus/{name}: Builder ergänzt keine Beträge", not re.search(r"\d+\s*[–-]\s*\d+\s*(€|Euro)", ctx))
    if name == "langer Bericht":
        check("Lexus/lang: Kürzung wird benannt (NICHT IM KONTEXT)", "NICHT IM KONTEXT" in ctx)
        check("Lexus/lang: Zeilenauszug ist als solcher markiert", "auf Zeilen mit Vorbehalten gekürzt" in ctx)
        check("Lexus/lang: Füllzeilen sind nicht vollständig übernommen", ctx.count(FILL[:40]) < lang.count(FILL[:40]))
    # Router liefert denselben Kontext
    check(f"Lexus/{name}: Router = Builder",
          r_frage._kontext_aus_check("kauf", json.dumps({"bericht": bericht, "research_status": "completed_medium"}), "{}") == ctx)
    h = blocks(ctx)
    check(f"Lexus/{name}: keine Kontextüberschrift nennt etwas 'geprüft' oder 'dokumentarisch'",
          not any(re.search(r"geprüft|dokumentarisch|belegt\b", k, re.I) for k in h), str(list(h)))

# ═══ Originalgegenprobe VerkaufCheck: Passat B6 2009 ═════════════════════════════════════════════════
print("\n[Passat] VerkaufCheck: Nutzerangaben, Schlüssel/ZB II, keine Kostenbeträge")
from app.models import VerkaufsCheckRequest  # noqa: E402
from app.verkaufsplan import baue_verkaufsplan  # noqa: E402

req = VerkaufsCheckRequest(marke="Volkswagen", modell="Passat", baujahr=2009, kilometerstand=180000,
                           preis_vorstellung=6900, letzter_service_datum="09/2026", letzter_service_km=177000,
                           wartungsnachweise="teilweise", schluessel_anzahl=2, scheckheftgepflegt=True,
                           tuev_bis="06/2027", vorbesitzer=2)
plan = baue_verkaufsplan(req, None, None, None, [], None, None, None)
plan_txt = json.dumps(plan, ensure_ascii=False)
P_SERVICE = "Der letzte Service (09/2026, 177.000 km) ist deine Angabe, kein Beleg; ein Eintrag oder eine Rechnung dazu stützt sie."
check("Fixture: der ECHTE Verkaufsplan erzeugt den Original-Servicesatz wörtlich", P_SERVICE in plan_txt)
check("Fixture: der echte Plan enthält keine Klimaservice-/Lackierbeträge",
      "Klimaservice" not in plan_txt and not re.search(r"\b(80|400)\s*[–-]\s*(150|800)\b", plan_txt))
P_MARKT = "Keine ausreichend belastbare Marktpreisorientierung verfügbar."
eingabe = {"marke": "Volkswagen", "modell": "Passat", "baujahr": 2009, "kilometerstand": 180000,
           "preis_vorstellung": 6900, "letzter_service_datum": "09/2026", "letzter_service_km": 177000,
           "wartungsnachweise": "teilweise", "schluessel_anzahl": 2, "tuev_bis": "06/2027", "vorbesitzer": 2}
bericht_v = f"## Verkaufsanalyse\n\n{P_MARKT}\n\nPreisvorstellung 6.900 EUR."
bericht_v_lang = bericht_v + "\n\n" + "\n\n".join(abschnitt(f"Füllabschnitt {i}", [FILL] * 14) for i in range(6)) + \
    "\n\n## Hinweise zu Angaben\n\nWartungsnachweise nur teilweise vorhanden. Zwei Schlüssel sind eine Angabe des Verkäufers."
for name, bericht in (("kurzer Bericht", bericht_v), ("langer Bericht", bericht_v_lang)):
    erg = {"bericht": bericht, "research_status": "completed_no_market", "vertrauen": "niedrig", "quelle": "web",
           "verkaufsplan": plan}
    ctx = baue_analyse_kontext("verkauf", json.dumps(erg), json.dumps(eingabe), llm.ANALYSE_KONTEXT_MAX)
    check(f"Passat/{name}: Originalsatz 'Keine ausreichend belastbare Marktpreisorientierung' im Kontext", P_MARKT in ctx)
    check(f"Passat/{name}: Original-Servicevorbehalt wörtlich im Kontext (Datum, km, 'deine Angabe, kein Beleg')",
          "(09/2026, 177.000 km) ist deine Angabe, kein Beleg" in ctx)
    check(f"Passat/{name}: Wartungsnachweise 'nur teilweise' bleiben sichtbar",
          "teilweise" in ctx.lower())
    h = blocks(ctx)
    nutzer = next((v for k, v in h.items() if k.startswith("ANGABEN DES NUTZERS")), "")
    check(f"Passat/{name}: Service, Schlüssel, Vorbesitzer, HU stehen im Block der Nutzerangaben",
          all(x in nutzer for x in ("letzter_service_datum: 09/2026", "schluessel_anzahl: 2", "vorbesitzer: 2", "tuev_bis: 06/2027")))
    check(f"Passat/{name}: dieser Block heißt 'KEIN Beleg' und nicht 'geprüft/dokumentarisch'",
          "KEIN Beleg" in next(k for k in h if k.startswith("ANGABEN DES NUTZERS")))
    check(f"Passat/{name}: keine Kontextüberschrift behauptet Prüfung oder dokumentarischen Beleg",
          not any(re.search(r"geprüft|dokumentarisch|belegt\b", k, re.I) for k in h), str(list(h)))
    check(f"Passat/{name}: Schlüssel und ZB II sind nie in derselben Zeile",
          not any(("Schlüssel" in z and "Teil II" in z) for z in ctx.splitlines()))
    check(f"Passat/{name}: ZB II steht als eigenes Dokument, Schlüssel als eigenes Dokument",
          "Dokument: Zulassungsbescheinigung Teil II" in ctx and "Dokument: Alle Schlüssel" in ctx)
    check(f"Passat/{name}: Builder ergänzt keine Klimaservice-/Lackierbeträge (80–150 / 400–800)",
          "Klimaservice" not in ctx and not re.search(r"\d+\s*[–-]\s*\d+\s*(€|Euro)", ctx))
    check(f"Passat/{name}: Grenze eingehalten", len(ctx) <= llm.ANALYSE_KONTEXT_MAX, str(len(ctx)))
    if name == "langer Bericht":
        check("Passat/lang: Vorbehalt am Berichtsende bleibt erhalten",
              "Zwei Schlüssel sind eine Angabe des Verkäufers" in ctx)
check("Prompt-Regel gegen Ausgabe fremder Beträge als 'Schätzung im Bericht'",
      'Nie als Schätzung "im Bericht" ausgeben' in llm._ANALYSE_SYSTEM)
check("Prompt-Regel gegen 'dokumentarisch belegt' für Nutzerangaben",
      "Nie als belegt, dokumentiert oder dokumentarisch belegt" in llm._ANALYSE_SYSTEM)

# ═══ Kompatibilität alter gespeicherter Checks ════════════════════════════════════════════════════
print("\n[Alt] ältere gespeicherte Checks bleiben nutzbar")
alt_kauf = {"bericht": "## Kaufempfehlung\nVORSICHT\n", "empfehlung": "VORSICHT", "preis_bewertung": "nicht bewertbar"}
alt_verkauf = {"bericht": "## Analyse\nText", "schnellverkaufs_preis": 5000, "maximal_preis": 7000, "empfohlener_preis": 6000}
for nm, typ, e in (("kauf ohne Insights/Plan", "kauf", alt_kauf), ("verkauf ohne verkaufsplan", "verkauf", alt_verkauf)):
    for ein in (None, "", "kein json", "[]", "{}"):
        try:
            c = baue_analyse_kontext(typ, json.dumps(e), ein, llm.ANALYSE_KONTEXT_MAX)
            ok = "## " in c or "Analyse" in c
        except Exception as exc:  # noqa: BLE001
            ok, c = False, repr(exc)
        check(f"Alt/{nm} mit eingabe={ein!r}: Kontext ohne Fehler", ok, c[:80])
check("Alt: Preisfelder alter Verkaufschecks bleiben im Kontext",
      "Maximalpreis (EUR): 7000" in baue_analyse_kontext("verkauf", json.dumps(alt_verkauf), None, 8000))
check("Alt: ergebnis ohne jeden Inhalt ergibt weiterhin leeren Kontext (422 im Router)",
      baue_analyse_kontext("kauf", "{}", None, 8000) == "" and baue_analyse_kontext("kauf", "kaputt", None, 8000) == "")

# ═══ Datum dynamisch ═════════════════════════════════════════════════════════════════════════════
print("\n[Datum] dynamisch, mehrere festgesetzte Daten, nichts fest codiert")
quelltext = open("app/llm.py", encoding="utf-8").read()
check("kein fest codiertes Datum 28.11.2026 im Produktcode", "28.11.2026" not in quelltext and "2026-11-28" not in quelltext)
_echt = llm._heute
check("ohne Festsetzen liefert _heute das echte Tagesdatum", _echt() == dt.date.today())


async def prompts():
    out = {}
    llm._vector_search = lambda q, ids, n=3: []
    ev = [e async for e in llm.chat_stream("Wie viele km sind normal?", [])]
    out["chat"] = CAPT["system"]
    [e async for e in llm.analyse_frage_stream("ctx", "Frage?", [], "kauf")]
    out["analyse"] = CAPT["system"]
    return out


for tag, d, erwartet, nicht in (("28.11.2026", dt.date(2026, 11, 28), "Heutiges Datum: 28.11.2026", "03.02.2027"),
                                ("03.02.2027", dt.date(2027, 2, 3), "Heutiges Datum: 03.02.2027", "28.11.2026"),
                                ("31.12.2030", dt.date(2030, 12, 31), "Heutiges Datum: 31.12.2030", "28.11.2026")):
    llm._heute = lambda d=d: d
    pr = asyncio.run(prompts())
    for pfad in ("chat", "analyse"):
        check(f"{pfad} mit festgesetztem Datum {tag}: '{erwartet}' im Prompt", erwartet in pr[pfad])
        check(f"{pfad} mit {tag}: kein anderes festes Datum", nicht not in pr[pfad])
check("Regel: Baujahr statt Altersmonat (kein erfundener Monat)", "erfinde keinen Monat" in llm.SYSTEM_PROMPT
      and "erfinde keinen Monat" in llm._ANALYSE_SYSTEM)
llm._heute = lambda: dt.date(2026, 11, 28)

# ═══ Golf VII: allgemeiner Chat ═══════════════════════════════════════════════════════════════════
print("\n[Golf] Chat: Datum, Baujahr/Variante, Kreditregel")
TAV.clear()
verlauf = [{"rolle": "user", "text": "Golf VII 1.4 TSI 125 PS, 2016, 120000 km."},
           {"rolle": "ki", "text": "Dein achtjähriger Golf VII ..."}]
asyncio.run(_collect := (lambda: None)() or asyncio.sleep(0))


async def golf():
    [e async for e in llm.chat_stream("Welche Preisklasse passt zu mir, ich zahle bar oder mit Kredit?", verlauf)]
    return CAPT["system"], CAPT["contents"]


sp, cont = asyncio.run(golf())
check("Golf: Datum 28.11.2026 im Prompt", "Heutiges Datum: 28.11.2026" in sp)
check("Golf: Jahr 2016 aus dem Nutzerverlauf als Angabe (kein Beleg) im Kontext", "Baujahr laut Angabe des Nutzers: 2016" in sp)
check("Golf: 125 PS als 'nicht hinterlegt' gekennzeichnet", "nicht hinterlegt" in sp and "125 PS" in sp)
check("Golf: falsche Altersangabe der KI steht im Verlauf, aber Regel verlangt Neuberechnung",
      any("achtjährig" in p["parts"][0]["text"] for p in cont) and "nicht, sondern rechnest neu" in sp)
check("Golf: Kreditregel steht im Prompt (Alternativen, Belastungsprüfung erlaubt)",
      "empfiehlt und vermittelt keine Kredite" in sp and "Ansparen" in sp and "keine Empfehlung zur Kreditaufnahme" in sp)
check("Golf: Finanzierungsfrage löst keine Websuche aus", not TAV)
check("Golf: das Wort 'Kreditsumme' wird nirgends empfohlen (Prompt nennt Kredit nur als verbotenes Beispiel)",
      "bar oder mit einer sehr geringen" not in sp)

check("keine externe Verbindung", not BLOCKED, str(BLOCKED[:3]))
print(f"\n{PASS} PASS / {len(FEHLER)} FAIL")
for f in FEHLER:
    print("  -", f)
sys.exit(1 if FEHLER else 0)
