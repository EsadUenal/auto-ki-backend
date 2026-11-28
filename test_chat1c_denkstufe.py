"""CHAT-1c: thinking_level=LOW fuer Gemini-3-Flash und inhaltsfreie Diagnose je Aufruf.

Isoliert (Netzsperre + Temp-DB VOR den App-Imports). Provider sind Stubs. Sie pruefen die ANFRAGE-
KONFIGURATION, die Streamverarbeitung und die Logzeile. Sie belegen NICHT, dass der Provider den Parameter
akzeptiert, wie viele Tokens real verbraucht werden oder wie sich die Antwortqualitaet aendert.

Ausfuehren:  python test_chat1c_denkstufe.py
"""
from __future__ import annotations

import asyncio
import datetime as dt
import io
import logging
import os
import re
import socket
import sys
import tempfile
from types import SimpleNamespace

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
_TMP = tempfile.mkdtemp(prefix="chat1c_")
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
from app.config import GEMINI_ANALYSE_MAX_OUTPUT_TOKENS, GEMINI_CHAT_MAX_OUTPUT_TOKENS  # noqa: E402
from app.gemini_retry import GeminiFehlgeschlagen  # noqa: E402
from google.genai import types as T  # noqa: E402

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


print("\n[ISO]")
check("DB im Temp-Verzeichnis", os.environ["AUTO_KI_DB_PATH"].startswith(tempfile.gettempdir()))
try:
    socket.create_connection(("example.com", 80), timeout=1)
    sperre = False
except OSError as exc:
    sperre = "NETZSPERRE" in str(exc)
check("externe Verbindung wird blockiert", sperre)
BLOCKED.clear()

# ── Stub ─────────────────────────────────────────────────────────────────────
GEHEIM = ["GEHEIME-FRAGE-4711", "GEHEIME-ANTWORT-4712", "GEHEIMER-VERLAUF-4713", "GEHEIMER-KONTEXT-4714"]


class _FR:
    def __init__(self, n): self.name = n


class _Cand:
    def __init__(self, fr): self.finish_reason = _FR(fr) if fr else None


class _Chunk:
    def __init__(self, text, fr=None, usage=None):
        self.text = text
        self.candidates = [_Cand(fr)]
        self.usage_metadata = usage


def U(**kw):
    return SimpleNamespace(**kw)


CAPT: dict = {}
MODE: dict = {}


class _Models:
    async def generate_content_stream(self, model, contents, config):
        if MODE.get("start_fehler"):
            raise GeminiFehlgeschlagen("stub 503")
        CAPT.update(model=model, config=config)

        async def gen():
            for i, (t, fr, u) in enumerate(MODE["chunks"]):
                if MODE.get("raise_at") == i:
                    raise ConnectionError("stub: Stream abgebrochen")
                yield _Chunk(t, fr, u)
        return gen()


class _Aio: models = _Models()
class _Client: aio = _Aio()


llm._get_client = lambda: _Client()
llm._vector_search = lambda q, ids, n=3: []
llm._heute = lambda: dt.date(2026, 11, 28)


async def _tav(q, count=3, exclude_domains=None, **k): return []


llm.tavily_search = _tav


class _Sammler(logging.Handler):
    def __init__(self): super().__init__(); self.zeilen: list[str] = []
    def emit(self, r): self.zeilen.append(r.getMessage())


H = _Sammler()
logging.getLogger("app.llm").addHandler(H)
logging.getLogger("app.llm").setLevel(logging.INFO)


def modus(chunks, raise_at=None, start_fehler=False):
    MODE.clear()
    MODE.update(chunks=chunks, raise_at=raise_at, start_fehler=start_fehler)
    H.zeilen.clear()


async def chat(msg="Wie viele km sind normal?"):
    return [e async for e in llm.chat_stream(msg, [{"rolle": "user", "text": GEHEIM[2]}, {"rolle": "ki", "text": GEHEIM[2]}])]


async def analyse():
    return [e async for e in llm.analyse_frage_stream(GEHEIM[3], GEHEIM[0], [{"rolle": "user", "text": GEHEIM[2]}], "kauf")]


def diag():
    return [z for z in H.zeilen if z.startswith("LLM-Diagnose")]


def feld(zeile, name):
    m = re.search(rf"\b{name}=(\S+)", zeile)
    return m.group(1) if m else None


# ═══ 1. Request-Konfiguration beider Pfade ═══════════════════════════════════════════════════════
print("\n[1] tatsächliche Request-Konfiguration")
print("   SDK google-genai 2.7.0 (requirements.txt), ThinkingConfig.thinking_level / ThinkingLevel.LOW vorhanden")
check("installiertes SDK kennt ThinkingConfig.thinking_level und ThinkingLevel.LOW",
      "thinking_level" in T.ThinkingConfig.model_fields and T.ThinkingLevel.LOW.name == "LOW")
check("konfiguriertes Modell: gemini-3.7-flash (Code-Default; NICHT als Railway-Konfiguration geprüft)",
      llm.LLM_MODEL == "gemini-3.7-flash")
modus([("Antwort.", "STOP", None)])
asyncio.run(chat())
cc = CAPT["config"]
check("Chat: thinking_level=LOW", cc.thinking_config is not None and cc.thinking_config.thinking_level == T.ThinkingLevel.LOW)
check("Chat: kein thinking_budget gleichzeitig", cc.thinking_config.thinking_budget is None)
check("Chat: max_output_tokens unverändert", cc.max_output_tokens == GEMINI_CHAT_MAX_OUTPUT_TOKENS == 4096)
check("Chat: Modell im Request ist das konfigurierte", CAPT["model"] == llm.LLM_MODEL)
modus([("Antwort.", "STOP", None)])
asyncio.run(analyse())
ca = CAPT["config"]
check("Rückfrage: thinking_level=LOW", ca.thinking_config is not None and ca.thinking_config.thinking_level == T.ThinkingLevel.LOW)
check("Rückfrage: kein thinking_budget gleichzeitig", ca.thinking_config.thinking_budget is None)
check("Rückfrage: max_output_tokens unverändert", ca.max_output_tokens == GEMINI_ANALYSE_MAX_OUTPUT_TOKENS == 2048)
check("Config lässt sich serialisieren (keine widersprüchlichen Felder)",
      ca.thinking_config.model_dump(exclude_none=True) == {"thinking_level": T.ThinkingLevel.LOW})

print("\n[1b] nur die ausdrücklich belegte Modell-ID bekommt thinking_level; keine künftigen Versionen")
for m, soll in (("gemini-3.7-flash", True),
                ("gemini-3.8-flash", False), ("gemini-3-flash-preview", False), ("gemini-3.9-flash", False),
                ("gemini-3.7-flash-lite", False), ("gemini-3.7-flash-preview", False), ("Gemini-3.7-Flash", False),
                ("gemini-2.5-flash", False), ("gemini-flash-lite-latest", False), ("gemini-3.1-flash-lite", False),
                ("gemini-3.1-pro-preview", False), ("", False)):
    check(f"_thinking_config({m!r}) {'= LOW' if soll else '= None (Modellstandard)'}",
          (llm._thinking_config(m) is not None) == soll)
orig_modell = llm.LLM_MODEL
llm.LLM_MODEL = "gemini-2.5-flash"
modus([("Antwort.", "STOP", None)])
asyncio.run(chat())
check("Chat mit anderem Modell: thinking_config bleibt None", CAPT["config"].thinking_config is None and CAPT["model"] == "gemini-2.5-flash")
modus([("Antwort.", "STOP", None)])
asyncio.run(analyse())
check("Rückfrage mit anderem Modell: thinking_config bleibt None", CAPT["config"].thinking_config is None)
H.zeilen.clear()
modus([("Antwort.", "STOP", None)])
asyncio.run(chat())
check("Diagnose nennt bei anderem Modell 'Modellstandard' als Denkstufe", feld(diag()[0], "denkstufe") == "Modellstandard")
llm.LLM_MODEL = orig_modell

# ═══ 2. Diagnose je Aufruf ═══════════════════════════════════════════════════════════════════════
print("\n[2] inhaltsfreie Diagnose")
modus([("Teil eins ", None, U(prompt_token_count=100, response_token_count=5, thoughts_token_count=900, total_token_count=1005)),
       ("Teil zwei", "STOP", U(prompt_token_count=100, response_token_count=12, thoughts_token_count=900, total_token_count=1012))])
asyncio.run(analyse())
d = diag()
check("genau EINE Diagnosezeile je Aufruf", len(d) == 1)
z = d[0]
check("Pfad, Modell, Denkstufe, Grenze, finish_reason stehen drin",
      feld(z, "pfad") == "analyse" and feld(z, "modell") == "gemini-3.7-flash" and feld(z, "denkstufe") == "LOW"
      and feld(z, "max_output") == "2048" and feld(z, "finish_reason") == "STOP" and feld(z, "status") == "ok")
check("kumulative usage_metadata wird NICHT aufsummiert (letzter Wert gilt)",
      feld(z, "thoughts_tokens") == "900" and feld(z, "antwort_tokens") == "12" and feld(z, "prompt_tokens") == "100"
      and feld(z, "gesamt_tokens") == "1012")
modus([("Antwort.", "STOP", None)])
asyncio.run(chat())
z = diag()[0]
check("fehlende usage_metadata: alle Token-Zahlen 'unbekannt', nicht 0",
      all(feld(z, n) == "unbekannt" for n in ("prompt_tokens", "antwort_tokens", "thoughts_tokens", "gesamt_tokens")))
modus([("Antwort.", "STOP", U(prompt_token_count=50, response_token_count=7, total_token_count=57))])
asyncio.run(chat())
z = diag()[0]
check("teilweise usage_metadata: fehlendes thoughts_token_count bleibt 'unbekannt', vorhandene Werte stehen",
      feld(z, "thoughts_tokens") == "unbekannt" and feld(z, "prompt_tokens") == "50")
modus([("Antwort.", "STOP", U(prompt_token_count=50, response_token_count=7, thoughts_token_count=0, total_token_count=57))])
asyncio.run(chat())
check("ausdrücklich gemeldete 0 bleibt 0", feld(diag()[0], "thoughts_tokens") == "0")
modus([("Antwort.", "STOP", U(prompt_token_count=50, response_token_count=7, thoughts_token_count=900, total_token_count=957)),
       ("", None, U(prompt_token_count=50, response_token_count=7, thoughts_token_count=900, total_token_count=957))])
asyncio.run(chat())
check("wiederholte identische Metadaten verdoppeln nichts", feld(diag()[0], "thoughts_tokens") == "900")

print("\n[2b] Status: STOP, MAX_TOKENS, Abbruch, Startfehler, leer — beide Pfade")
for pfad, fn in (("chat", chat), ("analyse", analyse)):
    modus([("Antwort.", "STOP", None)])
    ev = asyncio.run(fn())
    check(f"{pfad}/STOP: status=ok, kein Meta/Hinweis", feld(diag()[0], "status") == "ok" and not any(e["type"] == "meta" and e.get("abgeschnitten") for e in ev))
    modus([("Kurz ###", "MAX_TOKENS", U(thoughts_token_count=1900, response_token_count=140))])
    ev = asyncio.run(fn())
    z = diag()[0]
    check(f"{pfad}/MAX_TOKENS: status=abgeschnitten, finish_reason und Denk-Token sichtbar",
          feld(z, "status") == "abgeschnitten" and feld(z, "finish_reason") == "MAX_TOKENS" and feld(z, "thoughts_tokens") == "1900")
    check(f"{pfad}/MAX_TOKENS: Kürzungshinweis bleibt erhalten",
          any(e["type"] == "text" and "gekürzt" in e["delta"] for e in ev))
    modus([("Teil", None, None), ("x", None, None)], raise_at=1)
    ev = asyncio.run(fn())
    check(f"{pfad}/Abbruch nach Teiltext: status=fehler_nach_teiltext, Fehlerereignis bleibt",
          feld(diag()[0], "status") == "fehler_nach_teiltext" and ev[-1] == {"type": "error", "teilantwort": True})
    modus([("x", None, None)], raise_at=0)
    ev = asyncio.run(fn())
    check(f"{pfad}/Abbruch ohne Text: status=fehler_ohne_text", feld(diag()[0], "status") == "fehler_ohne_text"
          and ev[-1] == {"type": "error", "teilantwort": False})
    modus([], start_fehler=True)
    ev = asyncio.run(fn())
    check(f"{pfad}/Startfehler: status=start_fehler, finish_reason unbekannt, Fehlerereignis bleibt",
          feld(diag()[0], "status") == "start_fehler" and feld(diag()[0], "finish_reason") == "unbekannt"
          and ev[-1] == {"type": "error", "teilantwort": False})
    modus([("", "STOP", U(thoughts_token_count=2048, response_token_count=0))])
    asyncio.run(fn())
    check(f"{pfad}/Leerantwort (nur Denken): status=leer, Denk-Token sichtbar",
          feld(diag()[0], "status") == "leer" and feld(diag()[0], "thoughts_tokens") == "2048")
    modus([("Antwort.", None, None)])
    asyncio.run(fn())
    check(f"{pfad}/fehlendes finish_reason: 'unbekannt' statt erfundenem Wert", feld(diag()[0], "finish_reason") == "unbekannt")

print("\n[3] Logs enthalten nur erlaubte Metadaten")
modus([(GEHEIM[1], "STOP", U(prompt_token_count=10, response_token_count=3, thoughts_token_count=5, total_token_count=18))])
asyncio.run(chat(GEHEIM[0]))
asyncio.run(analyse())
alles = "\n".join(H.zeilen)
check("kein Nachrichten-, Antwort-, Verlaufs- oder Kontexttext in irgendeiner app.llm-Logzeile",
      not any(g in alles for g in GEHEIM), alles[:200])
erlaubt = {"pfad", "modell", "denkstufe", "max_output", "finish_reason", "status", "text_chunks",
           "prompt_tokens", "antwort_tokens", "thoughts_tokens", "gesamt_tokens"}
check("Diagnosezeile enthält ausschließlich die erlaubten Felder",
      all(set(re.findall(r"(\w+)=", z)) <= erlaubt for z in diag()), str([set(re.findall(r"(\w+)=", z)) for z in diag()]))
werte = [v for z in diag() for v in re.findall(r"\w+=(\S+)", z)]
check("alle Werte sind kurze Kennwörter/Zahlen (keine Nutzerkennung, kein Token/Secret, keine FIN)",
      all(re.fullmatch(r"[A-Za-z0-9_.\-]{1,40}", v) for v in werte) and not re.search(r"bearer|secret|api[_-]?key|@", " ".join(diag()), re.I))

check("keine externe Verbindung", not BLOCKED, str(BLOCKED[:3]))
print(f"\n{PASS} PASS / {len(FEHLER)} FAIL")
for f in FEHLER:
    print("  -", f)
sys.exit(1 if FEHLER else 0)
