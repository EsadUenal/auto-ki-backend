"""CHAT-1b: Verlauf ist keine Quelle, Wartungsbloecke, Fachregeln, Karosserie/Getriebe getrennt.

Isoliert (Netzsperre + Temp-DB VOR den App-Imports). Provider sind Stubs: sie belegen KONTEXT und
VERARBEITUNG, keine Modelltreue. Originalstellen stammen aus den Nutzerexporten (Lexus-Antriebsriemen:
Erstpruefung 105.000 km/6 Jahre, danach 15.000 km/1 Jahr; Passat: Service Nutzerangabe, Nachweise teilweise);
Struktur und Fuellmaterial drumherum sind SYNTH.

Ausfuehren:  python test_chat1b_nachbesserung.py
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
_TMP = tempfile.mkdtemp(prefix="chat1b_")
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


print("\n[ISO] Isolation aktiv")
check("DB im Temp-Verzeichnis", os.environ["AUTO_KI_DB_PATH"].startswith(tempfile.gettempdir()))
try:
    socket.create_connection(("example.com", 80), timeout=1)
    sperre = False
except OSError as exc:
    sperre = "NETZSPERRE" in str(exc)
check("externe Verbindung wird blockiert", sperre)
BLOCKED.clear()


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
llm.TAVILY_API_KEY = "stub"
llm._heute = lambda: dt.date(2026, 11, 28)
GOLF = "volkswagen-golf-vii"


async def analyse(ctx, frage, verlauf):
    [e async for e in llm.analyse_frage_stream(ctx, frage, verlauf, "verkauf")]
    return CAPT["system"], CAPT["contents"]


async def chat(msg, verlauf=None):
    [e async for e in llm.chat_stream(msg, verlauf or [])]
    return CAPT["system"], CAPT["contents"]


# ═══ 1. Verlauf und Berichtstreue (CH-01/06) ═══════════════════════════════════════════════════
print("\n[1] Verlauf ist keine Quelle")
ergebnis = {"bericht": "## Verkaufsanalyse\n\nKeine ausreichend belastbare Marktpreisorientierung verfügbar.\n\n"
                       "Kleine technische Reparaturen: allgemeine Empfehlung, keine Beträge.",
            "research_status": "completed_no_market", "empfohlener_preis": 6500}
eingabe = {"marke": "Volkswagen", "modell": "Passat", "preis_vorstellung": 6900, "wartungsnachweise": "teilweise",
           "scheckheftgepflegt": True, "letzter_service_datum": "09/2026"}
ctx = baue_analyse_kontext("verkauf", json.dumps(ergebnis), json.dumps(eingabe), llm.ANALYSE_KONTEXT_MAX)
ALT = [{"rolle": "user", "text": "Was kostet die Vorbereitung?"},
       {"rolle": "ki", "text": "Schätzungen im Bericht: Klimaservice 80–150 €, Lackierung 400–800 €, neu 20–40 € Reinigung. "
                               "Dein Scheckheft ist nicht gepflegt."}]
s_leer, c_leer = asyncio.run(analyse(ctx, "Was soll ich vorbereiten?", []))
s_alt, c_alt = asyncio.run(analyse(ctx, "Was soll ich vorbereiten?", ALT))
check("System-Prompt mit alter Antwort = System-Prompt mit leerem Verlauf", s_leer == s_alt)
check("leerer Verlauf: contents ist nur die aktuelle Frage", len(c_leer) == 1)
modell = [c for c in c_alt if c["role"] == "model"]
check("Rückfrage: alte Assistant-Antwort trägt die Kennzeichnung 'keine Quelle und kein Berichtsinhalt'",
      len(modell) == 1 and modell[0]["parts"][0]["text"].startswith(llm.VERLAUF_KI_PRAEFIX))
check("Rückfrage: Nutzerfragen im Verlauf bleiben unverändert",
      c_alt[0]["parts"][0]["text"] == "Was kostet die Vorbereitung?" and c_alt[-1]["parts"][0]["text"] == "Was soll ich vorbereiten?")
check("Wortlaut der alten Antwort bleibt erhalten (keine Maskierung, nichts gelöscht)",
      "80–150 €" in modell[0]["parts"][0]["text"] and "400–800 €" in modell[0]["parts"][0]["text"])
check("nichtnumerische Altbehauptung ('Scheckheft nicht gepflegt') ist ebenfalls nur Gesprächsverlauf",
      "Scheckheft ist nicht gepflegt" in modell[0]["parts"][0]["text"] and modell[0]["parts"][0]["text"].startswith("[Frühere Antwort"))
s_c, c_c = asyncio.run(chat("Wie viele km sind normal?", ALT))
check("Chat: Assistant-Antworten ebenfalls als Gesprächsverlauf gekennzeichnet",
      [c["parts"][0]["text"].startswith(llm.VERLAUF_KI_PRAEFIX) for c in c_c if c["role"] == "model"] == [True])
for name, sp in (("Rückfrage", s_alt), ("Chat", s_c)):
    check(f"{name}: Regel 'Verlauf = Gesprächsverlauf, keine Quelle, Kontext hat Vorrang, korrigieren'",
          "keine Quelle und kein Berichtsinhalt" in sp and "hat Vorrang" in sp and "korrigiere sie ausdrücklich" in sp)
    check(f"{name}: Regel Geldbeträge trennen (Nutzer / allgemeine Schätzung / Analyse), Betrag im Kontext nicht automatisch belegt",
          "Wunsch oder Angabe des Nutzers" in sp and "allgemeine Schätzung" in sp and "nicht automatisch belegt" in sp)
    check(f"{name}: 'teilweise Nachweise' beweist nicht 'nicht scheckheftgepflegt'; Unbekanntes/Widersprüche offen",
          "beweist nicht \"nicht scheckheftgepflegt\"" in sp and "Unbekanntes und Widersprüche offen" in sp)
print("\n[1b] legitime Preisrückfrage und Nutzerbetrag bleiben verständlich")
s_p, c_p = asyncio.run(analyse(ctx, "Ich will 6900 € haben, ist das realistisch?",
                               [{"rolle": "user", "text": "Mein Wunschpreis sind 6900 €."}]))
check("Nutzerbetrag im Verlauf und in der Frage bleibt unverändert",
      c_p[0]["parts"][0]["text"] == "Mein Wunschpreis sind 6900 €." and "6900 €" in c_p[-1]["parts"][0]["text"])
check("Kontext: Preisvorstellung als Wunsch des Nutzers, kein Marktpreis",
      "preis_vorstellung: 6900 (Preisvorstellung des Nutzers, kein Marktpreis)" in ctx)
check("Kontext: Betrag der Analyse steht getrennt in der Kurzfassung", "Empfohlener Preis (EUR): 6500" in ctx.split("### ANGABEN")[0])
check("Kontext: 'Nachweise teilweise' und 'scheckheftgepflegt' stehen als getrennte Nutzerangaben",
      "wartungsnachweise: teilweise" in ctx and "scheckheftgepflegt: True" in ctx)
check("Kontext: keine bezifferten Reparaturkosten", not re.search(r"\d+\s*[–-]\s*\d+\s*(€|Euro)", ctx))

# ═══ 2. Wartungsblöcke (CH-03) ═════════════════════════════════════════════════════════════════
print("\n[2] Wartungsblöcke zusammenhängend")
FILL = "Füllzeile ohne weitere Aussage zum Fahrzeug. " * 6
V = "Marktvorbehalt der Quelle: Geltung in Deutschland nicht bestätigt. Ob die Arbeit gemacht wurde, ist unbekannt."
EINZEILIG = ["- **Antriebsriemen** Erstprüfung: 105.000 km oder 6 Jahre, danach alle 15.000 km oder 1 Jahr. " + V]
MEHRZEILIG = ["- **Antriebsriemen** Erstprüfung: 105.000 km oder 6 Jahre.", "  Danach alle 15.000 km oder 1 Jahr.",
              "  Bedingung: normale Einsatzbedingungen.", "  " + V]
GESCHWISTER = ["- **Antriebsriemen** Prüfung: Erstprüfung bei 105.000 km oder 6 Jahren.",
               "- Folgeprüfungen danach alle 15.000 km oder 1 Jahr.", "- " + V]


def bericht_lang(block):
    abschn = "\n\n".join(f"## Abschnitt {i}\n\n" + "\n".join([FILL] * 14) for i in range(5))
    return ("**Lexus RC F (2017)**\n\nDas Fahrzeug ist ca. neun Jahre alt.\n\n" + abschn + "\n\n## Verkäuferfragen\n\n" +
            "\n".join([FILL] * 20 + block + [FILL] * 20))


def kontext(b):
    return baue_analyse_kontext("kauf", json.dumps({"bericht": b}), "{}", llm.ANALYSE_KONTEXT_MAX)


for name, block in (("einzeilig", EINZEILIG), ("mehrzeilig", MEHRZEILIG), ("Folgeregel als Geschwister-Punkt", GESCHWISTER)):
    c = kontext(bericht_lang(block))
    check(f"{name}: Bericht länger als die Grenze, Kontext hält sie", len(bericht_lang(block)) > 2 * llm.ANALYSE_KONTEXT_MAX
          and len(c) <= llm.ANALYSE_KONTEXT_MAX)
    zeilen = c.splitlines()
    i = next((k for k, z in enumerate(zeilen) if "Antriebsriemen" in z), None)
    check(f"{name}: Eintrag kommt vor", i is not None)
    fenster = "\n".join(zeilen[i:i + len(block)]) if i is not None else ""
    check(f"{name}: Tätigkeit, Erst- und Folgeregel stehen zusammenhängend direkt untereinander",
          "105.000 km oder 6 Jahr" in fenster and "15.000 km oder 1 Jahr" in fenster)
    check(f"{name}: Vorbehalt steht im selben zusammenhängenden Block", "nicht bestätigt" in fenster and "unbekannt" in fenster)
    if name == "mehrzeilig":
        check("mehrzeilig: Bedingung bleibt im Block", "Bedingung: normale Einsatzbedingungen." in fenster)
    check(f"{name}: Auswahl ist als Auszug markiert und benennt Ausgelassenes", "NICHT IM KONTEXT" in c and "gekürzt" in c)
    check(f"{name}: nie Erstregel ohne Folgeregel", ("Erstprüfung" not in c) or ("danach" in c.lower() or "Folgeprüfungen" in c))

# Eintrag passt nicht mehr hinein: keine scheinbar vollständige Teilregel
GROSS = ["- **Großer Eintrag** Erstprüfung: 105.000 km oder 6 Jahre, danach alle 15.000 km oder 1 Jahr."] + \
        [f"  Bedingung {i}: Prüfung nach Intervall, Voraussetzung ungeklärt. " + "x" * 240 for i in range(40)]
b_gross = bericht_lang(EINZEILIG + [""] + GROSS)
c_gross = kontext(b_gross)
check("Fixture: der große Eintrag ist länger als der gesamte Kontext", len("\n".join(GROSS)) > llm.ANALYSE_KONTEXT_MAX)
check("zu großer Eintrag: keine seiner Zeilen steht teilweise im Kontext",
      "Großer Eintrag" not in c_gross.split("NICHT IM KONTEXT")[0] and "Bedingung 0:" not in c_gross.split("NICHT IM KONTEXT")[0])
check("zu großer Eintrag: Auslassung ist benannt und als 'nicht teilweise übernommen' gekennzeichnet",
      "Großer Eintrag" in c_gross.split("NICHT IM KONTEXT")[-1] and "nicht teilweise übernommen" in c_gross)
check("zu großer Eintrag: der kleine Nachbareintrag bleibt vollständig", "Antriebsriemen" in c_gross and "15.000 km oder 1 Jahr" in c_gross)
check("Kontext hält die Grenze auch hier", len(c_gross) <= llm.ANALYSE_KONTEXT_MAX)
# strukturierter Wartungshinweis: Kürzung benannt statt abgeschnittener Regel
lang_text = "Erstprüfung 105.000 km, danach 15.000 km. " * 40
ins = {"kategorie": "wartung", "titel": "Antriebsriemen", "beschreibung": lang_text, "confidence": "niedrig", "trust": "unverified_db"}
c_i = baue_analyse_kontext("kauf", json.dumps({"bericht": "## A\nx", "insights": [ins]}), "{}", 8000)
check("strukturierter Wartungshinweis: Kürzung ist ausdrücklich markiert", "[…gekürzt]" in c_i)

# ═══ 3. Fachregeln beider Pfade (CH-03) ════════════════════════════════════════════════════════
print("\n[3] Fachregeln in Chat und Rückfrage")
for name, sp in (("Chat", s_c), ("Rückfrage", s_alt)):
    check(f"{name}: erreichte Schwelle ist kein Beweis für Fälligkeit/Überfälligkeit/Versäumnis",
          "kein Beweis für aktuelle Fälligkeit, Überfälligkeit oder ein Versäumnis" in sp and "Durchführung" in sp.split("Schwelle überschritten")[1][:80] or "ob die Arbeit gemacht wurde, ist unbekannt" in sp)
    check(f"{name}: Erst- und Folgeintervall getrennt, nie verschmolzen", "Erst- und Folgeintervall getrennt" in sp)
    check(f"{name}: keine Herstellerschwelle aus Modellwissen als belegt", "Hersteller- oder Werkschwelle aus deinem Modellwissen" in sp)
    check(f"{name}: 'im Rechercheauszug nicht gefunden' ist nicht 'in der Quelle nicht vorhanden'",
          "nicht \"in der Quelle nicht vorhanden\"" in sp)
    check(f"{name}: digitale Nachweise nicht ausschließen, keine absoluten Aussagen",
          "digital" in sp and "Keine absoluten Aussagen" in sp and "nur ein physisches Dokument" in sp)
    check(f"{name}: Prüfung bleibt Prüfung", "Eine Prüfung ist kein Wechsel" in sp)

# ═══ 4. Karosserie und Getriebe getrennt (CH-02) ═══════════════════════════════════════════════
print("\n[4] Karosserie/Getriebe: angegeben, unbekannt, mehrdeutig")


def zeilen_fuer(text):
    sql = llm._sql_context([GOLF], "x", None, text)
    return sql.split("### Motorvarianten")[0]


k0 = zeilen_fuer("Golf VII 1.4 TSI, 2016, 120000 km")
check("keine Angabe: Karosserie UND Getriebe unbekannt, nicht angenommen",
      "Karosserie: vom Nutzer NICHT angegeben" in k0 and "Getriebe: vom Nutzer NICHT angegeben" in k0)
check("keine Angabe: Datenbank-Auswahl ist als Auswahl, nicht als Angabe zu diesem Fahrzeug gekennzeichnet",
      "keine Angabe zu diesem Fahrzeug" in k0)
kv = zeilen_fuer("Golf VII Variant 1.4 TSI")
check("'Variant' klärt die Karosserie, NICHT das Getriebe",
      "Karosserie laut Angabe des Nutzers: Kombi/Variant" in kv and "Getriebe: vom Nutzer NICHT angegeben" in kv)
kd = zeilen_fuer("Golf VII 1.4 TSI mit DSG")
check("'DSG' klärt das Getriebe, NICHT die Karosserie",
      "Getriebe laut Angabe des Nutzers: DSG/Doppelkupplung" in kd and "Karosserie: vom Nutzer NICHT angegeben" in kd)
kn = zeilen_fuer("Golf VII, ich will kein DSG")
check("Negation ('kein DSG') ist keine positive Angabe", "Getriebe: vom Nutzer NICHT angegeben" in kn)
kn2 = zeilen_fuer("Golf VII ohne Automatik")
check("Negation ('ohne Automatik') ist keine positive Angabe", "Getriebe: vom Nutzer NICHT angegeben" in kn2)
kn3 = zeilen_fuer("Ich fahre kein Variant, sondern ein Schrägheck")
check("Negation und Gegenangabe im selben Satz: nur das Schrägheck zählt",
      "Karosserie laut Angabe des Nutzers: Schrägheck" in kn3)
kc = zeilen_fuer("Ist der Variant oder das Schrägheck besser?")
check("Vergleich ('Variant oder Schrägheck') ist keine Angabe", "Karosserie: vom Nutzer NICHT angegeben" in kc)
km = zeilen_fuer("Golf VII Schrägheck. Später: Golf VII Variant.")
check("widersprüchliche Angaben werden als mehrdeutig geführt, keine wird gewählt",
      "Karosserie: Angaben des Nutzers mehrdeutig" in km)
check("ohne Nutzertext (mehrere Fahrzeuge) werden keine Ausführungszeilen erzeugt",
      "vom Nutzer NICHT angegeben" not in llm._sql_context([GOLF], "x"))

# Ende-zu-Ende über chat_stream: aktives Fahrzeug, Verlauf, Fahrzeugwechsel
verl_golf = [{"rolle": "user", "text": "Ich habe einen Golf VII Variant mit DSG."}, {"rolle": "ki", "text": "Alles klar."}]
sp_g, _ = asyncio.run(chat("Wann ist der Golf VII fällig für die Wartung?", verl_golf))
check("Chat: Angaben aus früheren Nutzernachrichten desselben Fahrzeugs gelten",
      "Karosserie laut Angabe des Nutzers: Kombi/Variant" in sp_g and "Getriebe laut Angabe des Nutzers: DSG/Doppelkupplung" in sp_g)
sp_p, _ = asyncio.run(chat("Und beim Passat B6?", verl_golf))
check("Chat: Fahrzeugwechsel überträgt Variant/DSG NICHT auf den Passat",
      "Kombi/Variant" not in sp_p.split("KONTEXT (getrennt")[1] and "DSG/Doppelkupplung" not in sp_p.split("KONTEXT (getrennt")[1])
sp_n, _ = asyncio.run(chat("Wann ist beim Golf VII die Wartung fällig?", []))
check("Chat: ohne Angabe bleibt beides unbekannt im Prompt", "Karosserie: vom Nutzer NICHT angegeben" in sp_n
      and "Getriebe: vom Nutzer NICHT angegeben" in sp_n)
check("Prompt: Karosserie/Getriebe getrennt, Unbekanntes nicht durch Quellenvariante ersetzen",
      "sind getrennte Angaben" in sp_n and "ersetze sie nicht durch eine in den Daten oder Quellen verfügbare Variante" in sp_n)

# ═══ 5. Chip-Metadaten (CH-01) ═════════════════════════════════════════════════════════════════
print("\n[5] Chip: Metadaten belegen bereitgestellten Kontext")


async def meta_fuer(frage, verlauf):
    ev = [e async for e in llm.chat_stream(frage, verlauf)]
    return next(e for e in ev if e["type"] == "meta")


m_b = asyncio.run(meta_fuer("Welche Preisklasse kann ich mir leisten, wenn ich 12.000 € gespart habe?", verl_golf))
m_f = asyncio.run(meta_fuer("Wie viel kW hat der Golf VII 1.4 TSI?", verl_golf))
check("Backend meldet bei Budgetantwort und Datenantwort dieselbe Quelle: der Kontext lag vor (nicht Antwortbeleg)",
      m_b["quelle"] == m_f["quelle"] == "datenbank")
check("internes 'vertrauen' bleibt unverändert (Grenze dokumentiert, nicht geändert)", m_b["vertrauen"] == "hoch")
src = open("app/llm.py", encoding="utf-8").read()
check("Quelle wird weiter aus 'Kontext vorhanden' bestimmt (Chip-Text trägt die Aussage)", "hat_db  = bool(sql_ctx or vec_ctx)" in src)

check("keine externe Verbindung", not BLOCKED, str(BLOCKED[:3]))
print(f"\n{PASS} PASS / {len(FEHLER)} FAIL")
for f in FEHLER:
    print("  -", f)
sys.exit(1 if FEHLER else 0)
