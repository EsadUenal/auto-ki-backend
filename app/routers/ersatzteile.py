"""
Ersatzteile-Router — intelligenter Preisvergleich für KFZ-Ersatzteile.

Ablauf:
  1. Gating prüfen (require_ersatzteil_access) — dekrementiert Kontingent
  2. Kurze, gezielte Tavily-Suche über Autodoc, kfzteile24, eBay, Amazon, TecDoc
  3. Gemini (JSON-Modus) strukturiert die Rohtreffer zu Vergleichskarten
     + liefert eine kurze KI-Einschätzung, welches Teil empfehlenswert ist
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel, field_validator

from app.car_lookup import call_gemini_json
from app.config import TAVILY_API_KEY
from app.ersatzteil_gate import require_ersatzteil_access
from app.ersatzteil_kompat import parse_fahrzeug, parse_bauteil, klassifiziere, HINWEIS_UNCERTAIN
from app.gemini_retry import RateLimitExhausted
from app.utf8 import UTF8JSONResponse
from app.web_search import results_to_belege, tavily_search

log = logging.getLogger(__name__)

router = APIRouter(
    prefix="/ersatzteile",
    tags=["ersatzteile"],
    default_response_class=UTF8JSONResponse,
)

_SHOP_DOMAINS = [
    "autodoc.de", "kfzteile24.de", "ebay.de", "kfzteile.com",
    "motointegrator.de", "teiledirekt.de", "daparto.de", "autoteile-markt.de",
]

# Allgemeine Positions-/Teil-Synonyme & Übersetzungen — KEIN fahrzeugspezifisches
# Hardcoding. Erweitert eine Bauteil-Bezeichnung um gängige Schreibweisen, damit die
# Suche nicht an einer einzelnen Formulierung scheitert (Root-Cause #1/#8).
_SYNONYME: dict[str, tuple[str, ...]] = {
    "vorne": ("vorderachse", "front", "vorn"),
    "vorderachse": ("vorne", "front"),
    "hinten": ("hinterachse", "rear", "hinterer"),
    "hinterachse": ("hinten", "rear"),
    "bremsscheiben": ("bremsscheibe", "brake discs", "brake rotors", "bremsscheiben satz"),
    "bremsscheibe": ("bremsscheiben", "brake disc"),
    "bremsbeläge": ("bremsbelag", "bremsklötze", "brake pads"),
    "bremsbelaege": ("bremsbeläge", "brake pads"),
    "stoßdämpfer": ("stossdaempfer", "shock absorber", "dämpfer"),
    "querlenker": ("control arm", "lenker"),
    "zündkerzen": ("zuendkerzen", "spark plugs"),
    "luftfilter": ("air filter",),
    "ölfilter": ("oelfilter", "oil filter"),
    "kupplung": ("clutch", "kupplungssatz"),
    "radlager": ("wheel bearing", "radnabe"),
    "keilrippenriemen": ("keilriemen", "serpentine belt"),
    "zahnriemen": ("timing belt", "zahnriemensatz"),
}

# Grobe Teile-Kategorie (für den ehrlichen Mindestnutzen-Fallback, §9) — allgemein.
_KATEGORIEN: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Bremsen", ("brems", "bremse", "sattel", "brake")),
    ("Fahrwerk", ("stoßdämpf", "stossdämpf", "querlenker", "feder", "domlager", "koppelstange")),
    ("Motor", ("zündkerze", "zahnriemen", "keilriemen", "steuerkette", "zylinder", "kolben", "turbolader")),
    ("Filter & Service", ("filter", "öl", "oel")),
    ("Kupplung/Getriebe", ("kupplung", "getriebe", "schwungrad")),
    ("Elektrik", ("batterie", "lichtmaschine", "anlasser", "sensor", "zündspule")),
    ("Abgas", ("auspuff", "katalysator", "partikelfilter", "lambda")),
    ("Kühlung", ("kühler", "kuehler", "wasserpumpe", "thermostat")),
)

_SYSTEM = """\
Du bist ein KFZ-Ersatzteil-Experte. Du erhältst rohe Web-Suchergebnisse zu einer \
Ersatzteilsuche (Fahrzeug + gesuchtes Bauteil) und wandelst sie in einen strukturierten \
Preisvergleich um — wie ein Vergleichsportal für Autoteile.

AUSGABE: Ausschließlich gültiges JSON, kein Text davor oder danach.

{
  "ergebnisse": [
    {
      "teilename": "<konkreter Produktname>",
      "anbieter": "<Shop-Name, z.B. Autodoc, kfzteile24, eBay>",
      "preis_eur": <Zahl in EUR ohne Währungszeichen, oder null wenn unbekannt>,
      "marke_typ": "oem" | "original" | "nachbau" | "unbekannt",
      "qualitaetsstufe": "<kurzer Begriff, z.B. 'Erstausrüster-Qualität', 'Premium-Nachbau', 'Budget-Nachbau'>",
      "url": "<direkte Produkt-URL aus den Quellen>",
      "passt_fahrzeug": "<für welches Fahrzeug/Variante das Teil laut Quelle ist — so genau wie belegt, z.B. 'BMW M3 E92' oder 'BMW 3er E90/E92 318i-330i'; leer wenn die Quelle es nicht sagt>",
      "hinweis": "<1 kurzer Satz: Besonderheit, Lieferzeit o.ä.>"
    }
  ],
  "empfehlung": "<2-4 Sätze: welches Teil ist empfehlenswert und warum — konkret, ehrlich, ohne Marketing-Sprache>",
  "empfohlener_index": <0-basierter Index des empfohlenen Eintrags in "ergebnisse", oder null>
}

REGELN:
1. Nutze NUR Informationen aus den bereitgestellten Web-Ergebnissen. Erfinde keine Preise oder Produkte.
2. Wenn ein Preis im Text nicht eindeutig erkennbar ist, setze preis_eur auf null — niemals schätzen.
3. "oem" = vom Fahrzeughersteller selbst (z.B. "BMW Original"), "original" = Erstausrüster-Marke (z.B. Brembo, Bosch, ATE), "nachbau" = günstige Nachbau-Marke ohne Erstausrüster-Bezug.
4. Maximal 8 Einträge, sortiert nach Preis aufsteigend.
5. Wenn KEINE brauchbaren Treffer in den Web-Ergebnissen stehen, gib "ergebnisse": [] zurück und erkläre in "empfehlung" ehrlich, dass keine Angebote gefunden wurden.
6. Die Empfehlung soll dem Nutzer Sicherheit geben: worauf er beim gewählten Teil achten sollte (Qualität vs. Preis), nicht nur "das billigste".
7. Schreibe ausschließlich auf Deutsch, sachlich, ohne Übertreibung.
8. Fülle "passt_fahrzeug" so exakt wie die Quelle es hergibt (Marke, Modell, Performance-Variante wie M3/AMG/RS/GTI, Karosseriecode, Achse). Erfinde KEINE Passgenauigkeit. Ist das Zielfahrzeug ein Performance-Modell (z.B. M3), bestätige NUR dann Kompatibilität, wenn die Quelle genau dieses Performance-Modell nennt — ein Teil für den normalen E92 (z.B. 320i/320d) ist NICHT kompatibel mit dem M3.\
"""


class SucheBody(BaseModel):
    fahrzeug: str
    bauteil: str

    @field_validator("fahrzeug", "bauteil")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("darf nicht leer sein")
        return v[:120]


def _teil_varianten(bauteil: str) -> list[str]:
    """Bauteil-Bezeichnung + gängige Synonyme/Übersetzungen (allgemein, kein
    Fahrzeug-Hardcoding). Erste Variante = Originaltext."""
    b = bauteil.strip()
    low = b.lower()
    varianten = [b]
    # Längste (spezifischste) Bezeichnung zuerst — das Bauteil-Nomen ('bremsscheiben')
    # hat Vorrang vor Positionswörtern ('vorne'), damit die Nomen-Synonyme/-Übersetzungen
    # nicht von den Positions-Synonymen verdrängt werden. Überlappende kürzere Keys
    # ('bremsscheibe' in 'bremsscheiben') werden übersprungen (bereits abgedeckt).
    used_keys: list[str] = []
    for key in sorted((k for k in _SYNONYME if k in low), key=len, reverse=True):
        if any(key in longer for longer in used_keys):
            continue
        used_keys.append(key)
        for s in _SYNONYME[key]:
            v = low.replace(key, s)
            if v not in [x.lower() for x in varianten]:
                varianten.append(v)
    return varianten[:6]


def _kategorie(bauteil: str) -> str | None:
    low = bauteil.lower()
    for name, keys in _KATEGORIEN:
        if any(k in low for k in keys):
            return name
    return None


async def _mehrstufige_suche(fahrzeug: str, bauteil: str) -> list[dict]:
    """Mehrstufige, akkumulierende Ersatzteilsuche (Root-Cause #1/#8).

    Kein `site:`-Filter im Query-TEXT mehr (das war zusammen mit include_domains eine
    Doppelrestriktion, die enge Anfragen auf 0 Treffer zog). Stufen: (1) Shops exakt,
    (2) Shops mit Synonym, (3) breiter Web-Fallback, (4) breiter Web mit Synonym.
    Es wird akkumuliert & dedupliziert, bis genug Treffer vorliegen ODER alle Stufen
    ausgeschöpft sind.
    """
    if not TAVILY_API_KEY:
        return []
    varianten = _teil_varianten(bauteil)
    haupt = varianten[0]
    syn = varianten[1] if len(varianten) > 1 else haupt

    stufen: list[tuple[str, list[str] | None]] = [
        (f"{fahrzeug} {haupt} kaufen Preis", _SHOP_DOMAINS),      # 1: Shops, exakt
        (f"{fahrzeug} {syn} kaufen", _SHOP_DOMAINS),              # 2: Shops, Synonym
        (f"{fahrzeug} {haupt} Ersatzteil Preis Deutschland", None),  # 3: breiter Web-Fallback
        (f"{fahrzeug} {syn} Ersatzteil", None),                  # 4: breiter Web, Synonym
    ]

    acc: list[dict] = []
    seen: set[str] = set()
    for query, domains in stufen:
        results = await tavily_search(query[:350], count=8, include_domains=domains)
        for r in results:
            u = (r.get("url") or "").rstrip("/").lower()
            if u and u not in seen:
                seen.add(u)
                acc.append(r)
        # Genug brauchbare Treffer für die LLM-Strukturierung -> Stufen sparen.
        if len(acc) >= 5:
            break
    return acc


def _mindestnutzen(fahrzeug: str, bauteil: str) -> str:
    """Ehrlicher Mindestnutzen, wenn keine kaufbare Produktseite gefunden wurde (§9):
    erkanntes Fahrzeug + Teil + Kategorie + konkreter nächster Schritt. Erfindet nichts."""
    kat = _kategorie(bauteil)
    kat_satz = f" Kategorie: {kat}." if kat else ""
    return (
        f"Für **{fahrzeug}** – **{bauteil}** konnten aktuell keine eindeutig kaufbaren "
        f"Angebote verifiziert werden.{kat_satz} Für eine exakte Zuordnung hilft eine "
        f"genauere Angabe: die **FIN/Fahrgestellnummer** oder ein konkretes Maß "
        f"(z. B. Bremsscheiben-Durchmesser, Ø in mm). Nächster sinnvoller Schritt: die "
        f"Suche mit exakter Motor-/Ausstattungsvariante oder einer OE-/OEM-Nummer "
        f"wiederholen. Es wurden bewusst keine unbestätigten Teilenummern angezeigt."
    )


def _bewerte_kompatibilitaet(fahrzeug: str, bauteil: str, ergebnisse: list[dict]) -> tuple[list[dict], int | None]:
    """§5: jedes Produkt strukturiert gegen das Zielfahrzeug einstufen.

    - "rejected" (klarer Widerspruch, z.B. normale E92-Bremse für einen M3, falsche
      Achse, anderer Hersteller) -> vollständig ausblenden.
    - "uncertain" -> bleibt sichtbar, aber NIE empfohlen; klarer FIN/OE-Hinweis.
    - "confirmed" -> darf empfohlen werden.

    Gibt die gefilterten Ergebnisse und den Index des empfohlenen (nur "confirmed",
    günstigstes) zurück — unabhängig davon, was das LLM als empfohlen markiert hätte.
    """
    fz = parse_fahrzeug(fahrzeug)
    teil = parse_bauteil(bauteil)
    sichtbar: list[dict] = []
    for e in ergebnisse:
        if not isinstance(e, dict):
            continue
        produkt_text = " ".join(str(e.get(k, "")) for k in ("teilename", "passt_fahrzeug", "hinweis", "url"))
        kompat, grund = klassifiziere(fz, teil, produkt_text)
        if kompat == "rejected":
            # Sicherheitsrelevant falsch -> gar nicht anzeigen.
            continue
        e["kompatibilitaet"] = kompat
        e["kompat_grund"] = grund
        if kompat == "uncertain":
            e["kompat_hinweis"] = HINWEIS_UNCERTAIN
        sichtbar.append(e)

    # Empfohlen darf NUR ein "confirmed"-Treffer sein; günstigsten wählen (Preis
    # aufsteigend, None-Preise ans Ende).
    confirmed = [(i, e) for i, e in enumerate(sichtbar) if e.get("kompatibilitaet") == "confirmed"]
    if not confirmed:
        return sichtbar, None
    confirmed.sort(key=lambda t: (t[1].get("preis_eur") is None, t[1].get("preis_eur") or 0))
    return sichtbar, confirmed[0][0]


@router.post("/suche")
async def ersatzteil_suche(
    body: SucheBody,
    _user_id: int = Depends(require_ersatzteil_access),
):
    web_results = await _mehrstufige_suche(body.fahrzeug, body.bauteil)

    if not web_results:
        # Kein leeres "nichts gefunden": ehrlicher Mindestnutzen (§9) statt Sackgasse.
        return {
            "suchanfrage": {"fahrzeug": body.fahrzeug, "bauteil": body.bauteil},
            "ergebnisse": [],
            "empfehlung": _mindestnutzen(body.fahrzeug, body.bauteil),
            "empfohlener_index": None,
            "quelle": "web",
            "belege": [],
        }

    belege = results_to_belege(web_results)
    web_ctx_lines = ["=== WEB-TREFFER ===", ""]
    for i, r in enumerate(web_results, 1):
        web_ctx_lines.append(f"[{i}] {r.get('title', '')}")
        web_ctx_lines.append(f"    URL: {r.get('url', '')}")
        content = (r.get("content") or "").strip()
        if content:
            web_ctx_lines.append(f"    Inhalt: {content}")
        web_ctx_lines.append("")
    web_ctx = "\n".join(web_ctx_lines)

    user_msg = (
        f"GESUCHTES FAHRZEUG: {body.fahrzeug}\n"
        f"GESUCHTES BAUTEIL: {body.bauteil}\n\n"
        f"{web_ctx}"
    )

    try:
        result = await call_gemini_json(_SYSTEM, user_msg)
    except RateLimitExhausted as exc:
        return {
            "suchanfrage": {"fahrzeug": body.fahrzeug, "bauteil": body.bauteil},
            "ergebnisse": [],
            "empfehlung": f"KI-Auswertung momentan nicht verfügbar: {exc}",
            "empfohlener_index": None,
            "quelle": "web",
            "belege": belege,
        }

    ergebnisse = result.get("ergebnisse", [])
    if not isinstance(ergebnisse, list):
        ergebnisse = []

    # §5: Kompatibilität strukturiert einstufen — "rejected" ausblenden, "uncertain"
    # nie empfehlen, nur "confirmed" darf empfohlen werden (deterministisch, nicht LLM).
    ergebnisse, empfohlener_index = _bewerte_kompatibilitaet(body.fahrzeug, body.bauteil, ergebnisse)

    # Treffer vorhanden, aber das LLM konnte keine kaufbaren Angebote strukturieren
    # (oder alle wurden als inkompatibel ausgeblendet): ehrlicher Mindestnutzen (§9).
    empfehlung = result.get("empfehlung", "")
    if not ergebnisse:
        empfehlung = _mindestnutzen(body.fahrzeug, body.bauteil)
    elif empfohlener_index is None:
        # Es gibt sichtbare Treffer, aber keiner ist bestätigt kompatibel -> ehrlich
        # darauf hinweisen (§5: uncertain nie als "empfohlen" darstellen).
        empfehlung = (
            "Für dieses Fahrzeug konnte keine Kompatibilität eindeutig bestätigt werden. "
            "Die angezeigten Angebote sind unverbindlich — prüfe die Passung vor der "
            "Bestellung anhand der FIN/Fahrgestellnummer oder der OE-/OEM-Nummer. "
        ) + (empfehlung or "")

    return {
        "suchanfrage": {"fahrzeug": body.fahrzeug, "bauteil": body.bauteil},
        "ergebnisse": ergebnisse,
        "empfehlung": empfehlung,
        "empfohlener_index": empfohlener_index,
        "quelle": "web",
        "belege": belege,
    }
