"""CHAT-1: Beleglage, Anwendbarkeit, Fachregeln, Datum, Produktregel, Berichtskontext, Stream.

Isoliert: Netzsperre und Temp-DB werden VOR den App-Imports gesetzt und aktiv nachgewiesen.
Gemini und Tavily sind lokale Stubs. Diese Stubs belegen TRANSPORT und VERARBEITUNG (welcher Kontext
im Prompt landet, welche Ereignisse der Router sendet) — nicht, ob ein Modell sich an die Regeln haelt.

Originalexporte (Golf-Verlauf, Lexus- und Passat-Bericht) lagen bei der Umsetzung NICHT vor. Die
Faelle unten sind deshalb als SYNTH gekennzeichnet nachgebaut: sie bilden die beschriebenen
Mechanismen nach (Vorbehalt in Bericht/Plan, Nutzerangabe, Intervall "Pruefung"), nicht den Wortlaut.

Ausfuehren:  python test_chat1_chatpaket.py   (aus dem Repo-Root, venv)
"""
from __future__ import annotations

import asyncio
import datetime as dt
import io
import json
import os
import socket
import sys
import tempfile

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# ── Isolation VOR App-Imports ────────────────────────────────────────────────
_TMP = tempfile.mkdtemp(prefix="chat1_")
os.environ["AUTO_KI_DB_PATH"] = os.path.join(_TMP, "t.db")
os.environ["AUTO_KI_CHROMA_PATH"] = os.path.join(_TMP, "chroma")
os.environ["AUTO_KI_ENV"] = "development"
BLOCKED: list[str] = []
DIAG: list[str] = []
_orig_connect = socket.socket.connect


def _guard(self, addr):
    host = addr[0] if isinstance(addr, tuple) else addr
    if host not in ("127.0.0.1", "::1", "localhost"):
        BLOCKED.append(str(host))
        import traceback
        if len(BLOCKED) == 1:
            DIAG.append("".join(traceback.format_stack(limit=12)))
        raise OSError(f"NETZSPERRE {host}")
    return _orig_connect(self, addr)


socket.socket.connect = _guard
sys.path.insert(0, ".")

import app.database as db  # noqa: E402
db.ensure_tables()

import app.llm as llm  # noqa: E402
import app.routers.analyse_frage as r_frage  # noqa: E402
import app.routers.chat as r_chat  # noqa: E402
from app.analyse_kontext import baue_analyse_kontext  # noqa: E402
from app.gemini_retry import GeminiFehlgeschlagen, KI_UEBERLASTET_NACHRICHT  # noqa: E402
from app.provider_control import ProviderCapacityExceeded  # noqa: E402

FEHLER: list[str] = []
PASS = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS
    if ok:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FEHLER.append(name)
        print(f"  FAIL  {name}{(' — ' + detail) if detail else ''}")


print("\n[ISO] Isolation aktiv")
check("DB liegt im Temp-Verzeichnis, nicht unter LOCALAPPDATA",
      os.environ["AUTO_KI_DB_PATH"].startswith(tempfile.gettempdir())
      and "LOCALAPPDATA" not in os.environ["AUTO_KI_DB_PATH"].upper())
try:
    socket.create_connection(("example.com", 80), timeout=1)
    sperre = False
except OSError as exc:
    sperre = "NETZSPERRE" in str(exc)
check("externe Verbindung wird blockiert", sperre)
BLOCKED.clear()   # der Sonden-Versuch oben zaehlt nicht; ab hier waere jeder Versuch ein Fehler
DIAG.clear()

# ── Stubs ────────────────────────────────────────────────────────────────────
class _FR:
    def __init__(self, n): self.name = n


class _Cand:
    def __init__(self, fr): self.finish_reason = _FR(fr) if fr else None


class _Chunk:
    def __init__(self, text, fr=None):
        self.text = text
        self.candidates = [_Cand(fr)]


MODE = {"chunks": ["Antwort."], "finish": "STOP", "raise_at": None, "start_fehler": False}
CAPT: dict = {}


class _Models:
    async def generate_content_stream(self, model, contents, config):
        if MODE["start_fehler"]:
            raise GeminiFehlgeschlagen("stub 503")
        CAPT["system"] = config.system_instruction
        CAPT["contents"] = contents

        async def gen():
            n = len(MODE["chunks"])
            for i, t in enumerate(MODE["chunks"]):
                if MODE["raise_at"] is not None and i == MODE["raise_at"]:
                    raise ConnectionError("stub: Stream abgebrochen")
                yield _Chunk(t, MODE["finish"] if i == n - 1 else None)
        return gen()


class _Aio: models = _Models()
class _Client: aio = _Aio()


llm._get_client = lambda: _Client()
llm._vector_search = lambda q, ids, n=3: []
TAVILY_AUFRUFE: list[str] = []
WEB_STUB = [{"title": "Golf VII Variant Facelift 2017 DSG", "url": "https://example-test.de/golf",
             "content": "Golf Variant ab 2017 mit 7-Gang-DSG, DSG-Ölwechsel alle 60.000 km.", "score": 0.9}]


async def _tavily(query, count=3, exclude_domains=None, **kw):
    TAVILY_AUFRUFE.append(query)
    return [dict(r) for r in WEB_STUB]


llm.tavily_search = _tavily
llm.curate_results = lambda r, kategorie=None, max_results=5: r[:max_results]
llm.TAVILY_API_KEY = "stub"
llm._heute = lambda: dt.date(2026, 11, 28)


def modus(chunks=("Antwort.",), finish="STOP", raise_at=None, start_fehler=False):
    MODE.update(chunks=list(chunks), finish=finish, raise_at=raise_at, start_fehler=start_fehler)


async def chat(msg, verlauf=None, fk=None):
    ev = [e async for e in llm.chat_stream(msg, verlauf or [], fahrzeug_kontext=fk)]
    return ev


GOLF = "volkswagen-golf-vii"
PASSAT = "volkswagen-passat-b6"


async def main():
    # ═════ CH-01 Herkunft und Beleglage ════════════════════════════════════
    print("\n[CH-01] Herkunft getrennt, Beleglage je Fakt")
    check("_beleglage: verified -> geprüft", llm._beleglage({"_trust": "verified"}) == " [Beleglage: geprüft]")
    check("_beleglage: unverified_db -> gemeldeter Hinweis",
          "gemeldeter Hinweis, nicht geprüft" in llm._beleglage({"_trust": "unverified_db"}))
    check("_beleglage: ohne Trust wird nichts behauptet", llm._beleglage({}) == "")
    golf = db.get_baureihe("Volkswagen", "Golf", "VII")
    trust_sw = {s.get("_trust") for s in golf["schwachstellen_baureihe"]}
    trust_rr = {s.get("_trust") for s in golf["rueckrufe"]}
    check("Fixture-Voraussetzung: Golf-Schwachstellen sind unverified_db, Rückrufe verified",
          trust_sw == {"unverified_db"} and "verified" in trust_rr, f"{trust_sw} {trust_rr}")
    sql = llm._sql_context([GOLF], "Golf VII")
    sw_block = sql.split("### Schwachstellen Baureihe:")[1].split("###")[0]
    check("Baureihen-Schwachstellen tragen 'gemeldeter Hinweis, nicht geprüft'",
          all("gemeldeter Hinweis, nicht geprüft" in z for z in sw_block.splitlines() if z.strip().startswith("[")))
    rr_block = sql.split("### KBA-Rückrufe")[1].split("###")[0]
    check("Rückrufzeilen tragen ihre Beleglage", "[Beleglage: geprüft]" in rr_block)
    check("Rückrufblock sagt: Betroffenheit/Durchführung nicht belegt, nur per FIN prüfbar",
          "NICHT belegt" in sql and "FIN" in sql)
    check("kein Fahrzeugfakt im Kontext gilt pauschal als 'geprüfte Datenbank'",
          "GEPRÜFTER DATENBANK" not in sql and "geprüfte Datenbank" not in sql.lower())

    modus()
    await chat("Wie viel PS hat der Golf VII 1.4 TSI und was kostet er gebraucht?")
    sp = CAPT["system"]
    i_db = sp.find("=== FAHRZEUGDATENBANK")
    i_web = sp.rfind("=== AKTUELLE WEB-ERGEBNISSE (Tavily")  # rfind: Regeltext nennt den Block ebenfalls
    check("DB-Abschnitt und Web-Abschnitt stehen unter eigenen Überschriften, DB zuerst",
          0 <= i_db < i_web)
    check("keine gemeinsame 'GEPRÜFTER DATENBANK'-Überschrift mehr", "GEPRÜFTER DATENBANK" not in sp)
    check("Prompt: nur Quellen nennen, die im Kontext stehen", "NUR, was im Kontext tatsächlich steht" in sp)
    check("Prompt: Datenbank heißt nicht geprüft", '"Aus der Datenbank" heißt NICHT "geprüft"' in sp)
    check("Prompt enthält keinen erfundenen Quellennamen (erWin)", "erwin" not in sp.lower())

    # ═════ CH-02 Anwendbarkeit ═════════════════════════════════════════════
    print("\n[CH-02] Baujahr/Variante je Aussage, Fahrzeugwechsel")
    steuerkette = "TSI-Motoren (Steuerkette)"
    check("ohne Jahr bleibt die Steuerketten-Zeile (Baujahre 2012-2015)", steuerkette in llm._sql_context([GOLF], "x"))
    check("Baujahr 2014 behält sie", steuerkette in llm._sql_context([GOLF], "x", 2014))
    check("Baujahr 2016 lässt die nachweislich nicht passende Zeile weg",
          steuerkette not in llm._sql_context([GOLF], "x", 2016))
    check("Baujahr 2016 ist als Angabe, nicht als Beleg gekennzeichnet",
          "Angabe, kein Beleg" in llm._sql_context([GOLF], "x", 2016))
    check("Baureihendaten bleiben nutzbar (Motorvarianten weiterhin im Kontext)",
          "### Motorvarianten" in llm._sql_context([GOLF], "x", 2016))
    j = llm._baujahr_aus_gespraech
    check("Jahr aus der Nachricht, ein Fahrzeug", j("Golf VII 1.4 TSI, Baujahr 2016, 120000 km", [], [GOLF]) == 2016)
    check("Kilometerstand wird nicht als Jahr gelesen", j("Golf VII mit 2000 km", [], [GOLF]) is None)
    check("Spanne ist nicht eindeutig", j("Golf VII 2012-2015", [], [GOLF]) is None)
    check("zwei Fahrzeuge in der Nachricht: keine Zuordnung", j("Golf VII 2016 oder Passat B6 2009", [], [GOLF, PASSAT]) is None)
    v_golf = [{"rolle": "user", "text": "Ich habe einen Golf VII Baujahr 2016"},
              {"rolle": "ki", "text": "Dein Golf VII von 2011 ..."}]
    check("Jahr aus dem Verlauf (Nutzernachricht, gleiches Fahrzeug)", j("Und die Steuerkette beim Golf VII?", v_golf, [GOLF]) == 2016)
    check("KI-Text im Verlauf liefert kein Jahr",
          j("Und beim Golf VII?", [{"rolle": "ki", "text": "Golf VII 2011"}], [GOLF]) is None)
    check("Fahrzeugwechsel: altes Jahr wird nicht auf den Passat übertragen",
          j("Und wie ist das beim Passat B6?", v_golf, [PASSAT]) is None)
    check("Jahr ohne erkennbaren Fahrzeugbezug im Verlauf wird nicht zugeordnet",
          j("Golf VII Steuerkette?", [{"rolle": "user", "text": "Baujahr 2016"}], [GOLF]) is None)
    h125 = llm._sql_context([GOLF], "x", None, "Golf VII 1.4 TSI 125 PS")
    check("125 PS nicht hinterlegt: 'exakte Variante nicht hinterlegt' statt mehrerer passender Motoren",
          "nicht hinterlegt" in h125 and "125 PS" in h125 and "mehrere" not in h125.split("### Motorvarianten")[0])
    check("hinterlegte Leistung (122 PS) erzeugt keinen Hinweis",
          "Hinweis zur Variante" not in llm._sql_context([GOLF], "x", None, "Golf VII 1.4 TSI 122 PS"))
    check("Prompt: Web-Wert je Aussage prüfen, Abweichung nennen, nicht alles verwerfen",
          "PASSEND heißt je Aussage" in llm.SYSTEM_PROMPT and "nicht automatisch alle Angaben der Quelle" in llm.SYSTEM_PROMPT)
    check("Prompt: kein 'MUSST du ihn verwenden — auch wenn nur ungefähr' mehr",
          "MUSST du ihn verwenden" not in llm.SYSTEM_PROMPT and "auch wenn er nur ungefähr" not in llm.SYSTEM_PROMPT)

    # ═════ CH-03/04 Fachregeln und Datum ═══════════════════════════════════
    print("\n[CH-03/04] Fachregeln und Datum in BEIDEN Chatpfaden")
    modus()
    await chat("Wann ist beim Golf VII Baujahr 2016 die Wartung fällig?")
    chat_sp = CAPT["system"]
    modus()
    list([e async for e in llm.analyse_frage_stream("Kontext", "Frage?", [], "kauf")])
    ana_sp = CAPT["system"]
    for name, sp_ in (("Chat", chat_sp), ("Rückfrage", ana_sp)):
        check(f"{name}: aktuelles Datum 28.11.2026 im Prompt", "Heutiges Datum: 28.11.2026" in sp_)
        check(f"{name}: keine offenen Platzhalter", "[[" not in sp_)
        check(f"{name}: Prüfung ist kein Wechsel", "Eine Prüfung ist kein Wechsel" in sp_)
        check(f"{name}: Intervall, Bedingung, Geltung bleiben erhalten", "Erst- und Folgeintervall" in sp_ and "Geltung" in sp_)
        check(f"{name}: fehlende Historie beweist keine Fälligkeit", "beweist keine Fälligkeit" in sp_)
        check(f"{name}: Faustregel ist kein Fahrzeugintervall", "Faustregel" in sp_ and "kein Intervall dieses Fahrzeugs" in sp_)
        check(f"{name}: Rückruf: Existenz, Betroffenheit, Durchführung getrennt", "Durchgeführt" in sp_ or "durchgeführt" in sp_)
        check(f"{name}: Alter nur aus Baujahr und Datum, kein erfundener Monat", "erfinde keinen Monat" in sp_)
        check(f"{name}: alte Altersangaben werden nicht übernommen", "nicht, sondern rechnest neu" in sp_)
    modus()
    await chat("Und wie alt ist er?", [{"rolle": "user", "text": "Golf VII 2016"},
                                       {"rolle": "ki", "text": "Dein achtjähriger Golf"}])
    check("Verlauf mit falscher Altersangabe wird weitergereicht, aber das Datum steht daneben im Prompt",
          any("achtjährig" in p["parts"][0]["text"] for p in CAPT["contents"]) and "28.11.2026" in CAPT["system"])

    # ═════ CH-05 Produktregel ══════════════════════════════════════════════
    print("\n[CH-05] Kredit/Finanzierung/Leasing")
    for name, sp_ in (("Chat", chat_sp), ("Rückfrage", ana_sp)):
        check(f"{name}: keine Empfehlung von Kredit/Finanzierung/Zins/Leasing",
              "empfiehlt und vermittelt keine Kredite" in sp_)
        check(f"{name}: Alternativen genannt", "günstigeres Fahrzeug" in sp_ and "Ansparen" in sp_ and "Kauf verschieben" in sp_)
        check(f"{name}: sachliche Eigentums-/Belastungsprüfung bleibt zulässig",
              "noch finanziert oder belastet" in sp_ and "keine Empfehlung zur Kreditaufnahme" in sp_)
    check("'finanzierung' ist kein Websuche-Trigger mehr", "finanzierung" not in llm._PREIS_KEYWORDS)
    check("Finanzierungsfrage mit erkanntem Fahrzeug löst keine Websuche aus",
          llm._needs_web_search("Soll ich den Golf mit Kredit oder Leasing finanzieren?", [GOLF], [], "") is False)
    check("Preisfrage ohne Finanzierung löst weiterhin Websuche aus",
          llm._needs_web_search("Was kostet ein Golf gebraucht?", [GOLF], [], "") is True)
    check("Rückruffrage bleibt auch mit Finanzierungswort websuchfähig",
          llm._needs_web_search("Gibt es Rückrufe, ist der Golf finanziert?", [GOLF], [], "") is True)
    TAVILY_AUFRUFE.clear()
    modus()
    await chat("Soll ich den Golf VII mit Kredit oder Leasing finanzieren?")
    check("Finanzierungsfrage: kein Tavily-Aufruf", not TAVILY_AUFRUFE)

    # ═════ CH-05/06/07 Berichtskontext ═════════════════════════════════════
    print("\n[CH-06/07] Rückfragekontext aus gespeicherten Feldern")
    from app.models import VerkaufsCheckRequest
    from app.verkaufsplan import baue_verkaufsplan
    req = VerkaufsCheckRequest(marke="Volkswagen", modell="Passat", baujahr=2009, kilometerstand=180000,
                               preis_vorstellung=6900, letzter_service_datum="09/2026",
                               letzter_service_km=177000, wartungsnachweise="teilweise",
                               schluessel_anzahl=2, scheckheftgepflegt=True)
    plan = baue_verkaufsplan(req, None, None, None, [], None, None, None)
    check("Fixture-Voraussetzung: der echte Verkaufsplan trägt den Servicevorbehalt",
          "deine Angabe, kein Beleg" in json.dumps(plan, ensure_ascii=False))
    ergebnis_v = {"bericht": "## Verkaufsanalyse\nPreiswunsch 6.900 EUR. Keine belastbare Marktorientierung.",
                  "research_status": "completed_no_market", "vertrauen": "niedrig", "quelle": "web",
                  "verkaufsplan": plan}
    eingabe_v = {"marke": "Volkswagen", "modell": "Passat", "letzter_service_datum": "09/2026",
                 "letzter_service_km": 177000, "schluessel_anzahl": 2, "bild_base64": "XXXX"}
    ctx_v = baue_analyse_kontext("verkauf", json.dumps(ergebnis_v), json.dumps(eingabe_v), llm.ANALYSE_KONTEXT_MAX)
    check("Servicevorbehalt 'deine Angabe, kein Beleg' erreicht den Prompt", "deine Angabe, kein Beleg" in ctx_v)
    check("Nutzerangaben stehen unter eigener Überschrift 'KEIN Beleg'",
          "ANGABEN DES NUTZERS" in ctx_v and "KEIN Beleg" in ctx_v and "letzter_service_datum: 09/2026" in ctx_v)
    check("Bilddaten werden nicht in den Prompt übernommen", "XXXX" not in ctx_v)
    check("Dokumente (ZB I/II, Schlüssel) stehen getrennt im Plan-Auszug",
          "Dokument: Zulassungsbescheinigung Teil II" in ctx_v and "Dokument: Alle Schlüssel" in ctx_v)
    check("Recherchestatus ohne Marktpreis ist als solcher benannt", "OHNE belastbaren Marktpreis" in ctx_v)
    check("keine erfundenen Kostenbeträge im Kontext (80–150 / 400–800)",
          "80–150" not in ctx_v and "400–800" not in ctx_v and "80-150" not in ctx_v)
    check("Kontext hält die gemeinsame Grenze", len(ctx_v) <= llm.ANALYSE_KONTEXT_MAX)

    ergebnis_k = {"bericht": "## Kaufempfehlung\nVORSICHT\n\n## Relevante Risiken und Hinweise\nSiehe unten.",
                  "empfehlung_hinweis": "Geltung für den deutschen Markt unbestätigt, Bedingungen ungeklärt.",
                  "research_status": "completed_medium",
                  "insights": [
                      {"kategorie": "wartung", "titel": "Automatikgetriebeöl",
                       "beschreibung": "Prüfung 60.000 km / 4 Jahre; Wechsel nur nach Befund.",
                       "confidence": "niedrig", "trust": "unverified_db", "applicability": None},
                      {"kategorie": "rueckruf", "titel": "Bremsscheiben", "beschreibung": "Baureihenrückruf",
                       "confidence": "mittel", "trust": "verified", "applicability": "series_only"}]}
    ctx_k = baue_analyse_kontext("kauf", json.dumps(ergebnis_k), "{}", llm.ANALYSE_KONTEXT_MAX)
    check("Wartungstätigkeit und Intervall bleiben unverändert (Prüfung, 60.000 km / 4 Jahre)",
          "Prüfung 60.000 km / 4 Jahre" in ctx_k)
    check("Marktgeltung/Bedingungsvorbehalt steht im Kontext", "Geltung für den deutschen Markt unbestätigt" in ctx_k)
    check("ungeprüfter Wartungshinweis ist als gemeldeter Hinweis gekennzeichnet",
          "gemeldeter Hinweis der Datenbank, nicht geprüft" in ctx_k)
    check("Rückruf: Anwendbarkeit 'series_only' mit Zusatz 'kein Nachweis für dieses einzelne Fahrzeug'",
          "series_only" in ctx_k and "kein Nachweis für dieses einzelne Fahrzeug" in ctx_k)

    # Lange Berichte: wichtige Vorbehalte am ENDE bleiben erhalten
    filler = "Füllzeile ohne Aussage. " * 140
    bericht_lang = ("**VW Passat**\n\nDatenbasis: Test.\n\n## Kaufempfehlung\nVORSICHT.\n\n"
                    "## Füllabschnitt A\n" + filler + "\n\n## Füllabschnitt B\n" + filler +
                    "\n\n## Füllabschnitt C\n" + filler + "\n\n## Relevante Risiken und Hinweise\n"
                    "Servicehistorie nur laut Inserat, nicht belegt. ENDMARKER_VORBEHALT_7731\n")
    ctx_l = baue_analyse_kontext("kauf", json.dumps({"bericht": bericht_lang}), "{}", llm.ANALYSE_KONTEXT_MAX)
    check("Bericht länger als die Grenze", len(bericht_lang) > llm.ANALYSE_KONTEXT_MAX)
    check("Vorbehalt am Berichtsende bleibt erhalten (Abschnittsauswahl statt Kopfkürzung)",
          "ENDMARKER_VORBEHALT_7731" in ctx_l)
    check("Ausgelassene Abschnitte werden benannt", "NICHT IM KONTEXT" in ctx_l and "Füllabschnitt" in ctx_l.split("NICHT IM KONTEXT")[1])
    check("Auslassung nennt nur Ausgelassenes, nicht den erhaltenen Abschnitt",
          "Relevante Risiken" not in ctx_l.split("NICHT IM KONTEXT")[1])
    check("Kontext hält die gemeinsame Grenze auch bei langem Bericht", len(ctx_l) <= llm.ANALYSE_KONTEXT_MAX, str(len(ctx_l)))
    # Router liefert denselben Kontext, LLM schneidet nicht ein zweites Mal still
    ctx_r = r_frage._kontext_aus_check("kauf", json.dumps({"bericht": bericht_lang}), "{}")
    check("Router und Builder liefern denselben, grenzgerechten Kontext", ctx_r == ctx_l)
    modus()
    list([e async for e in llm.analyse_frage_stream(ctx_l, "Was steht zum Service?", [], "kauf")])
    check("LLM-Aufruf übernimmt den Kontext unverändert (keine zweite stille Kopfkürzung)",
          ctx_l in CAPT["system"] and "ENDMARKER_VORBEHALT_7731" in CAPT["system"])
    list([e async for e in llm.analyse_frage_stream("A" * (llm.ANALYSE_KONTEXT_MAX + 500), "?", [], "kauf")])
    check("Überlänge aus fremder Quelle wird sichtbar markiert, nicht still gekürzt",
          "NICHT IM KONTEXT" in CAPT["system"])
    check("Prompt: 'laut Bericht' nur für tatsächlich vorhandenen Inhalt; Beträge sonst als allgemein kennzeichnen",
          "Nie als Schätzung \"im Bericht\" ausgeben" in ana_sp)
    check("Prompt: Nutzerangaben nie als dokumentarisch belegt", "dokumentarisch belegt" in ana_sp and "laut deiner Angabe" in ana_sp)
    check("Prompt: ausgelassener Abschnitt heißt nicht 'Bericht enthält es nicht'",
          "Sag nie, der Bericht enthalte etwas nicht" in ana_sp)
    check("leere Checks ergeben keinen Kontext", baue_analyse_kontext("kauf", "{}", "{}", 8000) == "")

    # Echte Router-Funktion gegen gespeicherte Zeile (Eigentum, eingabe wird mitgelesen)
    with db.get_conn() as conn:
        uid = conn.execute("INSERT INTO users (email, password_hash) VALUES ('c1@t.de','x')").lastrowid
        cid = conn.execute(
            "INSERT INTO checks (user_id, typ, titel, eingabe, ergebnis, lauf_id) VALUES (?,?,?,?,?,?)",
            (uid, "verkauf", "Passat", json.dumps(eingabe_v), json.dumps(ergebnis_v), "lauf-1")).lastrowid
        conn.commit()
    typ, erg, ein = r_frage._eigener_bezahlter_check(cid, uid)
    check("Router liest typ, ergebnis UND eingabe des eigenen bezahlten Checks", typ == "verkauf" and "letzter_service" in ein)
    check("Router baut daraus den Kontext mit Nutzerangaben", "ANGABEN DES NUTZERS" in r_frage._kontext_aus_check(typ, erg, ein))

    # ═════ CH-07 Stream, Fehler, Speicherung ═══════════════════════════════
    print("\n[CH-06/07] Stream: Erfolg, MAX_TOKENS, Totalausfall, Abbruch nach Teiltext")

    def teile(sse):
        out = []
        for s in sse:
            if s.startswith("data: ") and s.strip() != "data: [DONE]":
                out.append(json.loads(s[6:]))
        return out

    async def sse_analyse():
        return [s async for s in r_frage._sse_generator("Kontext", "Frage?", [], "kauf", "t")]

    async def sse_chat(msg="Wie viele km sind normal?"):
        return [s async for s in r_chat._sse_generator(msg, [], None, "t")]

    for name, fn in (("Rückfrage", sse_analyse), ("Chat", sse_chat)):
        modus(chunks=["Teil eins. ", "Teil zwei."], finish="STOP")
        sse = await fn()
        ev = teile(sse)
        check(f"{name}/Erfolg: Text, kein Fehlerereignis, Ende mit [DONE]",
              any("delta" in e for e in ev) and not any("fehler" in e for e in ev) and sse[-1].strip() == "data: [DONE]")
        modus(chunks=["Teil eins. ", "Teil zwei, am Limit abgeschnit"], finish="MAX_TOKENS")
        ev = teile(await fn())
        text = "".join(e.get("delta", "") for e in ev)
        meta_abg = any(e.get("meta", {}).get("abgeschnitten") for e in ev)
        check(f"{name}/MAX_TOKENS: sichtbarer Hinweis im Text UND abgeschnitten-Meta", "gekürzt" in text and meta_abg)
        modus(start_fehler=True)
        sse = await fn()
        ev = [e for e in teile(sse) if "status" not in e]   # Fortschrittsanzeigen sind keine Antwort
        check(f"{name}/Totalausfall: genau ein Fehlerereignis, kein Antworttext",
              len(ev) == 1 and "fehler" in ev[0] and ev[0]["teilantwort"] is False)
        check(f"{name}/Totalausfall: Fehlertext steht nicht als delta", not any("delta" in e for e in ev))
        modus(chunks=["Erster Teil der Antwort. ", "x", "y"], finish="STOP", raise_at=1)
        ev = teile(await fn())
        text = "".join(e.get("delta", "") for e in ev)
        fehl = [e for e in ev if "fehler" in e]
        check(f"{name}/Abbruch nach Teiltext: Teiltext kommt an, Fehler meldet teilantwort=true",
              "Erster Teil" in text and len(fehl) == 1 and fehl[0]["teilantwort"] is True)
        check(f"{name}/Abbruch: Fehlertext ist kein Teil des Antworttextes", KI_UEBERLASTET_NACHRICHT not in text)
        check(f"{name}/Abbruch: kein Meta (wird nicht als vollständig abgeschlossen)",
              not any(e.get("meta", {}).get("quelle") for e in ev))
    modus()
    orig = llm.analyse_frage_stream

    async def kapazitaet(*a, **k):
        raise ProviderCapacityExceeded("voll")
        yield  # pragma: no cover

    r_frage.analyse_frage_stream = kapazitaet
    ev = teile(await sse_analyse())
    check("Rückfrage/Kapazitätsgrenze: Fehlerereignis statt Antworttext",
          len(ev) == 1 and "fehler" in ev[0] and not any("delta" in e for e in ev))
    r_frage.analyse_frage_stream = orig

    # Nicht-Stream-Pfad des Chats: kein Fehlertext als normale Antwort
    from fastapi.testclient import TestClient
    import app.main as main
    from app.config import API_KEY
    H = {"Authorization": f"Bearer {API_KEY}"}
    modus(start_fehler=True)
    c = TestClient(main.app, raise_server_exceptions=False)
    r = c.post("/api/v1/chat", json={"message": "Wie viele km sind normal?", "stream": False}, headers=H)
    check("Chat ohne Stream/Totalausfall: HTTP 503 statt Fehlertext als Antwort",
          r.status_code == 503 and r.json()["fehler"]["code"] == "ki_ueberlastet", f"{r.status_code} {r.text[:120]}")

    check("keine externe Verbindung wurde aufgebaut", not BLOCKED, str(BLOCKED[:3]) + (DIAG[0][-1500:] if DIAG else ""))


asyncio.run(main())
print(f"\n{PASS} PASS / {len(FEHLER)} FAIL")
if FEHLER:
    print("FEHLGESCHLAGEN:")
    for f in FEHLER:
        print("  -", f)
sys.exit(1 if FEHLER else 0)
