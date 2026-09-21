"""Regressionstests KI-Chat: Gespraechsgedaechtnis, Kontextbudget, Truncation.

Deterministisch — keine echten Gemini-/Tavily-Aufrufe. Alle Provider-Antworten
sind lokale Fakes, die Fahrzeug-DB wird nur lesend fuer die Erkennungstests
benutzt.

Hintergrund (der reproduzierte Produktionsdefekt):
  Turn 1 fragt nach drei Modellen, ENFAL empfiehlt Corolla / Mazda 3 / Focus.
  Turn 2 fragt "von den drei Autos ..." — und ENFAL antwortete "Welche drei
  Fahrzeuge meinst du?", waehrend unter derselben Antwort passende Quellenchips
  (ford.com, toyota.de, ADAC) standen.

  Ursache: das Zeichenbudget des Prompts wurde ZUERST vom System-Prompt
  verbraucht (der den DB-/Web-Kontext enthaelt) und der Verlauf bekam nur den
  Rest. Die Fahrzeugerkennung klebte den Verlauf zu EINEM Textblob zusammen und
  traf dadurch 17 Baureihen; deren Profile ergaben 114k Zeichen Kontext, der
  Rest wurde 0 — und der komplette Verlauf fiel lautlos aus dem Prompt. Die
  Websuche lief unabhaengig davon weiter auf demselben Verlauf, deshalb die
  passenden Quellen ohne passende Erinnerung.
"""
from __future__ import annotations

import asyncio
import io
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, ".")

import app.llm as llm
from app.models import ChatMessage
from app.config import NACHRICHT_MAX_ZEICHEN, GEMINI_CHAT_HISTORY_RESERVE_CHARS

FEHLER: list[str] = []
PASS = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS
    if ok:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FEHLER.append(f"{name}{(' — ' + detail) if detail else ''}")
        print(f"  FAIL  {name}{(' — ' + detail) if detail else ''}")


# ── Der Regressionsdialog aus dem Produktionsbefund ──────────────────────────

TURN1_USER = (
    "Ich suche einen zuverlässigen Benziner mit Automatik für maximal 20.000 €. "
    "Ich fahre ungefähr 15.000 km im Jahr, hauptsächlich Stadt und Autobahn. "
    "Welche drei Modelle würdest du mir empfehlen und warum?"
)
TURN1_KI = (
    "Für dein Budget und dein Fahrprofil passen diese drei:\n\n"
    "1. Toyota Corolla 1.8 Hybrid (ab 2019)\n"
    "2. Mazda 3 BP 2.0 Skyactiv-G (ab 2019)\n"
    "3. Ford Focus Mk4 1.0/1.5 EcoBoost (ab 2018)\n"
)
TURN2_USER = (
    "Von den drei Autos ist mir Zuverlässigkeit am wichtigsten. Welches würdest du "
    "deshalb an erste Stelle setzen? Gibt es bei den drei Motorisierungen bekannte "
    "Schwachstellen, auf die ich beim Gebrauchtwagenkauf besonders achten sollte?"
)
TURN2_KI = "An erste Stelle setze ich den Corolla, weil ..."
TURN3_USER = (
    "Erinnerst du dich an meine ursprüngliche Frage und an die drei Fahrzeuge, die du "
    "mir selbst empfohlen hast? Nenne nur die drei Fahrzeuge in einer Zeile."
)


# ── Provider-Fakes ───────────────────────────────────────────────────────────

class _FakeCandidate:
    def __init__(self, finish_reason):
        self.finish_reason = finish_reason


class _FakeChunk:
    def __init__(self, text, finish_reason=None):
        self.text = text
        self.candidates = [_FakeCandidate(finish_reason)] if finish_reason else []


class _FakeStream:
    def __init__(self, chunks):
        self._chunks = chunks

    def __aiter__(self):
        async def gen():
            for c in self._chunks:
                yield c
        return gen()


class _Spy:
    """Faengt ab, was tatsaechlich an Gemini geht."""

    def __init__(self, chunks=None):
        self.contents = None
        self.config = None
        self._chunks = chunks or [_FakeChunk("Antwort", "STOP")]

    def install(self):
        spy = self

        class _Models:
            async def generate_content_stream(self, model, contents, config):
                spy.contents = contents
                spy.config = config
                return _FakeStream(spy._chunks)

        class _Aio:
            def __init__(self):
                self.models = _Models()

        class _Client:
            def __init__(self):
                self.aio = _Aio()

        llm._get_client = lambda: _Client()
        return self

    # -- Auswertung -----------------------------------------------------------
    @property
    def rollen(self) -> list[str]:
        return [c["role"] for c in (self.contents or [])]

    @property
    def texte(self) -> list[str]:
        return [c["parts"][0]["text"] for c in (self.contents or [])]

    def enthaelt(self, schnipsel: str) -> bool:
        return any(schnipsel in t for t in self.texte)

    @property
    def system(self) -> str:
        return getattr(self.config, "system_instruction", "") or ""


async def _fake_tavily(query, count=3, exclude_domains=None):
    return [{
        "title": "ADAC Testbericht",
        "url": "https://www.adac.de/beispiel",
        "content": "Beispielinhalt zur Zuverlässigkeit. " * 8,
        "score": 0.9,
    }]


async def _lauf(message: str, verlauf: list[dict], spy: _Spy) -> tuple[str, dict]:
    """Fuehrt chat_stream aus und liefert (Volltext, Meta)."""
    text, meta = [], {}
    async for ev in llm.chat_stream(message, verlauf):
        if ev["type"] == "text":
            text.append(ev["delta"])
        elif ev["type"] == "meta":
            meta = ev
    return "".join(text), meta


# ── A/B: Gespraechsgedaechtnis ueber Folgefragen ─────────────────────────────

async def test_folgefrage_traegt_verlauf():
    print("\n[A] Folgefrage im selben Chat traegt den Verlauf an den Provider")
    spy = _Spy().install()
    verlauf = [{"rolle": "user", "text": TURN1_USER}, {"rolle": "ki", "text": TURN1_KI}]
    await _lauf(TURN2_USER, verlauf, spy)

    check("Provider bekommt genau 3 Gespraechsbeitraege", len(spy.contents) == 3,
          f"waren {len(spy.contents)}")
    check("Reihenfolge user/model/user", spy.rollen == ["user", "model", "user"],
          str(spy.rollen))
    check("Turn-1-Frage des Nutzers ist enthalten", spy.enthaelt("maximal 20.000"))
    check("Turn-1-Antwort mit den drei Fahrzeugen ist enthalten",
          spy.enthaelt("Toyota Corolla") and spy.enthaelt("Ford Focus Mk4"))
    check("Aktuelle Frage steht zuletzt", spy.texte[-1] == TURN2_USER)


async def test_dritter_turn_behaelt_verlauf():
    print("\n[B] Expliziter Erinnerungstest (Turn 3) sieht weiterhin alles")
    spy = _Spy().install()
    verlauf = [
        {"rolle": "user", "text": TURN1_USER},
        {"rolle": "ki", "text": TURN1_KI},
        {"rolle": "user", "text": TURN2_USER},
        {"rolle": "ki", "text": TURN2_KI},
    ]
    await _lauf(TURN3_USER, verlauf, spy)
    check("Alle 4 Vorbeitraege + aktuelle Frage im Prompt", len(spy.contents) == 5,
          f"waren {len(spy.contents)}")
    check("Ursprungsfrage weiterhin vorhanden", spy.enthaelt("maximal 20.000"))
    check("Empfehlungen weiterhin vorhanden", spy.enthaelt("Mazda 3"))


async def test_neuer_chat_ist_isoliert():
    print("\n[C] Neuer Chat startet ohne Fremd-History")
    spy = _Spy().install()
    await _lauf(TURN2_USER, [], spy)
    check("Nur die aktuelle Frage im Prompt", len(spy.contents) == 1,
          f"waren {len(spy.contents)}")
    check("Keine Empfehlung aus einem anderen Chat", not spy.enthaelt("Toyota Corolla"))


# ── D: Der eigentliche Root Cause — grosser Kontext darf History nicht fressen

async def test_grosser_kontext_verdraengt_verlauf_nicht():
    print("\n[D] Root Cause: riesiger DB-Kontext verdraengt den Verlauf NICHT")
    spy = _Spy().install()
    original = llm._sql_context
    # Exakt der Produktionsfall: 17 erkannte Baureihen ergaben 114k Zeichen.
    llm._sql_context = lambda ids, fuel_hint_text=None: "X" * 200_000
    try:
        verlauf = [{"rolle": "user", "text": TURN1_USER}, {"rolle": "ki", "text": TURN1_KI}]
        await _lauf(TURN2_USER, verlauf, spy)
    finally:
        llm._sql_context = original

    check("Verlauf ueberlebt einen 200k-Zeichen-Kontext", len(spy.contents) == 3,
          f"waren {len(spy.contents)} — Verlauf wurde verdraengt")
    check("Die drei Fahrzeuge stehen weiterhin im Prompt",
          spy.enthaelt("Toyota Corolla") and spy.enthaelt("Ford Focus Mk4"))
    check("System-Prompt wurde auf das Budget gekuerzt",
          len(spy.system) <= llm.GEMINI_MAX_INPUT_CHARS - GEMINI_CHAT_HISTORY_RESERVE_CHARS,
          f"system={len(spy.system)}")


async def test_retrieval_ersetzt_verlauf_nicht():
    print("\n[E] Web-/DB-Retrieval ergaenzt den Verlauf, ersetzt ihn nicht")
    spy = _Spy().install()
    original = llm.tavily_search
    llm.tavily_search = _fake_tavily
    try:
        verlauf = [{"rolle": "user", "text": TURN1_USER}, {"rolle": "ki", "text": TURN1_KI}]
        _, meta = await _lauf(TURN2_USER, verlauf, spy)
    finally:
        llm.tavily_search = original

    check("Verlauf weiterhin vollstaendig", len(spy.contents) == 3, f"waren {len(spy.contents)}")
    check("Kontext ist trotzdem im System-Prompt gelandet",
          "KONTEXT AUS GEPRÜFTER DATENBANK" in spy.system)
    check("Belege wurden ermittelt", isinstance(meta.get("belege"), list))


# ── F/G: Stiller Abbruch am Output-Limit ────────────────────────────────────

async def test_output_limit_wird_sichtbar():
    print("\n[F] finish_reason MAX_TOKENS endet nicht stumm mitten im Satz")
    chunks = [_FakeChunk("2.0-Liter-Vierzylinder-Saugmotor (122 PS / 150 PS", "MAX_TOKENS")]
    spy = _Spy(chunks).install()
    text, meta = await _lauf(TURN2_USER, [], spy)

    check("Meta meldet die Kuerzung", meta.get("abgeschnitten") is True, str(meta.get("abgeschnitten")))
    check("Nutzer sieht einen Hinweis statt eines stummen Endes",
          "gekürzt" in text, repr(text[-80:]))
    check("Der bereits erzeugte Text bleibt erhalten", "Vierzylinder" in text)


async def test_regulaerer_stop_ist_nicht_abgeschnitten():
    print("\n[G] finish_reason STOP gilt als vollstaendig")
    spy = _Spy([_FakeChunk("Vollstaendige Antwort.", "STOP")]).install()
    text, meta = await _lauf(TURN2_USER, [], spy)
    check("Meta meldet keine Kuerzung", meta.get("abgeschnitten") is False, str(meta.get("abgeschnitten")))
    check("Kein Kuerzungshinweis im Text", "gekürzt" not in text)


# ── H: Fahrzeugerkennung — Uebermatching aus dem Verlauf ────────────────────

def test_erkennung_ohne_blob_uebermatching():
    print("\n[H] Verlaufserkennung matcht nicht mehr quer ueber Nachrichten")
    verlauf = [{"rolle": "user", "text": TURN1_USER}, {"rolle": "ki", "text": TURN1_KI}]
    ids = [bid for bid, _ in llm._erkenne_fahrzeuge(TURN2_USER, verlauf)]

    fremd = [b for b in ids if any(x in b for x in ("supra", "hilux", "mustang", "kuga"))]
    check("Keine artfremden Baureihen aus dem Textblob", not fremd, str(fremd))
    check("Ford Focus Mk4 wird erkannt", any("ford-focus-mk4" == b for b in ids), str(ids))
    check("Toyota Corolla wird erkannt", any("corolla" in b for b in ids), str(ids))
    check("Ergebnis ist deterministisch",
          ids == [bid for bid, _ in llm._erkenne_fahrzeuge(TURN2_USER, verlauf)])


def test_deckel_bevorzugt_nicht_eine_einzige_nennung():
    print("\n[I] Deckel laesst jeder Nennung ihren besten Treffer")
    # Corolla matcht mehrere Generationen, Focus genau eine. Vor dem Fix
    # verbrauchte Corolla das komplette Budget und Focus fiel heraus.
    nennungen = [
        ("Toyota Corolla", ["corolla-ix", "corolla-x", "corolla-xi", "corolla-xii"]),
        ("Ford Focus Mk4", ["ford-focus-mk4"]),
    ]
    reihenfolge = [bid for bid, _ in llm._reihum(nennungen)]
    check("Focus steht vor der zweiten Corolla-Generation",
          reihenfolge.index("ford-focus-mk4") < reihenfolge.index("corolla-x"),
          str(reihenfolge))


# ── J: Kontext-Kuerzung schneidet an Blockgrenzen ───────────────────────────

def test_kontext_kuerzung():
    print("\n[J] Kontext wird an Blockgrenzen gekuerzt, nicht mitten im Satz")
    bloecke = [f"Block {i}: " + "y" * 500 for i in range(20)]
    kontext = "\n\n---\n\n".join(bloecke)
    gekuerzt = llm._kuerze_kontext(kontext, 2_000)
    check("Budget eingehalten", len(gekuerzt) <= 2_000, f"{len(gekuerzt)}")
    # Der Schnitt darf nur zwischen Bloecken liegen: jeder uebrig gebliebene Block
    # muss ein VOLLSTAENDIGER Originalblock sein, kein angeschnittener.
    behalten = gekuerzt.split("\n\n---\n\n")
    check("Nur vollstaendige Bloecke uebrig",
          all(b in bloecke for b in behalten), repr(behalten[-1][-30:]))
    check("Mindestens ein Block bleibt erhalten", len(behalten) >= 1 and behalten[0] == bloecke[0])
    check("Leeres Budget ergibt leeren Kontext", llm._kuerze_kontext(kontext, 0) == "")
    check("Passender Kontext bleibt unveraendert", llm._kuerze_kontext("kurz", 100) == "kurz")


# ── K: Pseudo-Quellen ───────────────────────────────────────────────────────

def test_pseudoquellen_werden_entfernt():
    print("\n[K] 'Allgemeines Kfz-Wissen' erscheint nicht als geprüfte Quelle")
    faelle = [
        "Der Zahnriemen läuft im Öl (Quelle: Allgemeines Kfz-Wissen).",
        "Typisch sind 15.000 km [Quelle: allgemeines Kfz-Wissen]",
        "Faustregel (Quelle: Erfahrungswerte) gilt weiterhin.",
        "Das ist so (Quelle: Allgemeinwissen).",
    ]
    for f in faelle:
        gesaeubert = llm._scrub_jargon(f)
        check(f"entfernt: {f[:42]}…", "Quelle" not in gesaeubert, repr(gesaeubert))
    check("Echte Quellenhinweise bleiben unangetastet",
          "ADAC" in llm._scrub_jargon("Laut ADAC-Pannenstatistik ist das so."))


# ── L: Verlaufsgrenze passt zur Persistenzgrenze ────────────────────────────

def test_verlaufsgrenze_passt_zur_persistenz():
    print("\n[L] Eine gespeicherte Langantwort wird als Verlauf akzeptiert")
    lang = "x" * NACHRICHT_MAX_ZEICHEN
    try:
        ChatMessage(rolle="ki", text=lang)
        ok = True
    except Exception as exc:  # noqa: BLE001
        ok = False
        print(f"        {exc}")
    check(f"ChatMessage akzeptiert {NACHRICHT_MAX_ZEICHEN} Zeichen "
          "(Persistenzlimit der gespeicherten Nachricht)", ok)


# ── M: Inhaltsregeln stehen im System-Prompt ────────────────────────────────

def test_content_regeln_im_prompt():
    print("\n[M] Beratungsregeln sind Teil des System-Prompts")
    sp = llm.SYSTEM_PROMPT
    check("Pseudo-Quellen sind ausdrücklich verboten",
          "Allgemeines Kfz-Wissen" in sp and "NIEMALS eine Quellenangabe" in sp)
    check("Absolute Zuverlässigkeits-Urteile sind untersagt",
          "zuverlässigsten Autos überhaupt" in sp)
    check("Zuverlässigkeit muss variantenabhängig beantwortet werden",
          "Zuverlässigkeit ist IMMER variantenabhängig" in sp)
    check("Bekannte variantenspezifische Risiken dürfen nicht weggelassen werden",
          "NICHT weglassen" in sp)
    check("Keine Überladung mit Warnlisten",
          "NICHT mit Warnlisten" in sp)
    check("Abweichende Antriebsart muss gekennzeichnet werden",
          "Ein Vollhybrid ist kein reiner Benziner" in sp)
    check("Hybrid-Terminologie ist geregelt",
          "Hilfsbatterie" in sp and "Starterbatterie" in sp)


# ── N: Kostenverhalten ──────────────────────────────────────────────────────

async def test_eine_antwort_ein_providerlauf():
    print("\n[N] Eine gekürzte Antwort löst KEINEN zweiten Modellaufruf aus")
    aufrufe = {"n": 0}
    spy = _Spy([_FakeChunk("Teiltext", "MAX_TOKENS")])

    class _Models:
        async def generate_content_stream(self, model, contents, config):
            aufrufe["n"] += 1
            spy.contents, spy.config = contents, config
            return _FakeStream(spy._chunks)

    class _Aio:
        def __init__(self):
            self.models = _Models()

    class _Client:
        def __init__(self):
            self.aio = _Aio()

    llm._get_client = lambda: _Client()
    _, meta = await _lauf(TURN2_USER, [], spy)
    check("Genau ein Providerlauf pro Nutzerfrage", aufrufe["n"] == 1, str(aufrufe["n"]))
    check("Kürzung wird gemeldet statt automatisch fortgesetzt",
          meta.get("abgeschnitten") is True)


# ── O: Absolute Zuverlässigkeitsclaims (Live-Befund RC1) ────────────────────

async def test_bewertungsregeln_stehen_direkt_vor_dem_kontext():
    print("\n[O] Bewertungsregel steht mit höchster Recency direkt vor dem Kontext")
    spy = _Spy().install()
    await _lauf(TURN2_USER, [{"rolle": "user", "text": TURN1_USER},
                             {"rolle": "ki", "text": TURN1_KI}], spy)
    sp = spy.system
    pos_hinweis = sp.find("— VOR JEDER BEWERTUNG —")
    pos_kontext = sp.find("KONTEXT AUS GEPRÜFTER DATENBANK")
    check("Bewertungshinweis ist im tatsächlich gesendeten System-Prompt", pos_hinweis >= 0)
    check("… und steht unmittelbar vor dem Kontext",
          0 <= pos_hinweis < pos_kontext and pos_kontext - pos_hinweis < 800,
          f"hinweis={pos_hinweis} kontext={pos_kontext}")
    for verboten in ("langlebigste", "wartungsärmste", "ganz klar", "unanfällig"):
        check(f"Superlativ/Verstärker ausdrücklich untersagt: {verboten}", verboten in sp)
    check("Rangfolge als Einschätzung formuliert", "tendenziell zuerst prüfen" in sp)
    check("Einschätzung vs. belegte Daten getrennt",
          "belegten Daten" in llm.SYSTEM_PROMPT and "technischen Einschätzung" in llm.SYSTEM_PROMPT)


def test_verstaerker_filter():
    print("\n[P] Verstärker werden nur in Adverbstellung entfernt")
    faelle = {
        "An erster Stelle steht ganz klar der Toyota Corolla.":
            "An erster Stelle steht der Toyota Corolla.",
        "Der Mazda ist zweifellos solide.": "Der Mazda ist solide.",
        "Er ist ohne jeden Zweifel solide.": "Er ist solide.",
        # Prädikativ am Satzende: Entfernen würde den Satz zerstören → bleibt.
        "Das ist ganz klar.": "Das ist ganz klar.",
        # Satzanfang: kein führendes Leerzeichen → bleibt unangetastet.
        "Ganz klar: der Corolla.": "Ganz klar: der Corolla.",
    }
    for eingabe, erwartet in faelle.items():
        ist = llm._scrub_jargon(eingabe)
        check(f"{eingabe[:40]!r}", ist == erwartet, repr(ist))


async def main() -> None:
    await test_folgefrage_traegt_verlauf()
    await test_dritter_turn_behaelt_verlauf()
    await test_neuer_chat_ist_isoliert()
    await test_grosser_kontext_verdraengt_verlauf_nicht()
    await test_retrieval_ersetzt_verlauf_nicht()
    await test_output_limit_wird_sichtbar()
    await test_regulaerer_stop_ist_nicht_abgeschnitten()
    test_erkennung_ohne_blob_uebermatching()
    test_deckel_bevorzugt_nicht_eine_einzige_nennung()
    test_kontext_kuerzung()
    test_pseudoquellen_werden_entfernt()
    test_verlaufsgrenze_passt_zur_persistenz()
    test_content_regeln_im_prompt()
    await test_eine_antwort_ein_providerlauf()
    await test_bewertungsregeln_stehen_direkt_vor_dem_kontext()
    test_verstaerker_filter()


asyncio.run(main())
print(f"\n{PASS} PASS / {len(FEHLER)} FAIL")
if FEHLER:
    for f in FEHLER:
        print(" -", f)
    raise SystemExit(1)
print("ALLE CHAT-VERLAUF-TESTS GRUEN")
