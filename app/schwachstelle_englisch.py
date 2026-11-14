"""Englische Aussageverarbeitung der Schwachstellen-Recherche (Paket "Englisch").

Reine Textfunktionen, kein Netzwerk, keine DB, keine Fahrzeug-/Marken-/Modellkenntnis. Die deutsche
Verarbeitung in `app/technical_research.py` bleibt unveraendert; dieses Modul liefert die englischen
Gegenstuecke, die DORT eingebunden werden:

  * Sprache je Aussage (`sprache`): "en" nur bei eindeutig englischem Satz; "de"; "gemischt"; "unklar".
    Positive Regeln (Problem, Bauteil, Reichweite) gelten NUR fuer "en". Schutzregeln (Verneinung,
    Relativierung, Einzelbericht, Frage, Bedingung) gelten fuer alles, was nicht eindeutig deutsch ist --
    kurze oder gemischte Aussagen umgehen sie dadurch nicht.
  * Problem-, Verneinungs-, Einzelfahrzeug-, Wiederholungs- und Populationsregeln gemeinsam
    (`hat_problem`, `ist_benign`, `reichweite`, `reichweiten_kontext`).
  * Englische Bauteilmuster mit Wortgrenzen, abgebildet auf die BESTEHENDEN deutschen Schluessel
    (`bauteil`). Kein Teilstring-Abgleich: "agree", "bladder", "describe" erzeugen kein Bauteil.
  * Markt- und Variantenbezug aussagenbezogen (`markt`, `variante`): positiver Beleg verlangt, fehlender
    Widerspruch ist keiner. Ein fremder Marktmarker sperrt nur die Aussage, in deren Satz oder Rahmen er
    steht, nie die ganze Seite.
  * Leistungsangaben in hp/bhp/PS/kW werden NICHT verglichen und nicht gleichgesetzt: sie gelten nur als
    Beschreibung der Variante, nie als Beleg der Uebertragbarkeit.
"""
from __future__ import annotations

import re
from typing import Callable

# ── Sprache ───────────────────────────────────────────────────────────────────────────────────────

# Nur Funktionswoerter, die im jeweils anderen Deutsch/Englisch (praktisch) nicht vorkommen.
_EN_WOERTER = frozenset({
    "the", "and", "with", "that", "this", "these", "those", "is", "are", "were", "been", "has", "have", "had",
    "it", "its", "they", "their", "which", "who", "many", "most", "some", "other", "owners", "owner", "cars",
    "known", "common", "often", "but", "not", "from", "for", "on", "of", "to", "can", "may", "would", "should",
    "there", "than", "very", "more", "about", "after", "before", "when", "if", "into", "over", "only", "my",
    "our", "we", "you", "your", "no", "does", "did", "do", "been", "being", "while", "also", "all"})
_EN_WOERTER = _EN_WOERTER - {"also", "all", "do"}   # "also"/"all"/"do" kommen in deutschem Text vor
_DE_WOERTER = frozenset({
    "der", "die", "das", "den", "dem", "des", "ein", "eine", "einer", "eines", "einem", "einen", "und", "oder",
    "nicht", "mit", "bei", "von", "zu", "zum", "zur", "ist", "sind", "wird", "werden", "wurde", "hat", "haben",
    "auch", "sich", "auf", "für", "fuer", "als", "wie", "nach", "aus", "über", "ueber", "kein", "keine", "dass",
    "wenn", "aber", "noch", "nur", "sehr", "oft", "häufig", "haeufig", "viele", "vielen", "bekannt", "bekannte",
    "bekanntes", "ich", "wir", "mein", "meine", "meinem", "unser", "unsere"})
_RE_WORT = re.compile(r"[a-zäöüß]+")


def sprache(text: str | None) -> str:
    """"en" | "de" | "gemischt" | "unklar". "en" verlangt mindestens zwei englische Funktionswoerter und
    KEIN deutsches Signal (Umlaut/ß zaehlen als deutsches Signal)."""
    t = (text or "").lower()
    woerter = _RE_WORT.findall(t)
    en = sum(1 for w in woerter if w in _EN_WOERTER)
    de = sum(1 for w in woerter if w in _DE_WOERTER) + 2 * len(re.findall(r"[äöüß]", t))
    if en >= 2 and de == 0:
        return "en"
    if de > en:
        return "de"
    if en and de:
        return "gemischt"
    return "unklar"


def _glatt(text: str | None) -> str:
    """Typografische Apostrophe vereinheitlichen und Kontraktionen mit "n't" ausschreiben."""
    t = (text or "").replace("’", "'").replace("‘", "'")
    t = re.sub(r"\bcan't\b", "can not", t, flags=re.I)
    t = re.sub(r"\bwon't\b", "will not", t, flags=re.I)
    return re.sub(r"(?i)n't\b", " not", t)


# ── Claim-Segmentierung ───────────────────────────────────────────────────────────────────────────

_RE_KONTRAST = re.compile(
    r"(?:[,;]\s*)?\b(?:but|however|although|though|whereas|nevertheless|nonetheless|except)\b[,\s]+"
    r"|[,;]\s*while\b[,\s]*|\bwhilst\b[,\s]*", re.IGNORECASE)
_RE_UND_NEUER_CLAIM = re.compile(
    r"\band\s+(?=(?:the|a|an|its|their|his|her|this|that|these|those|some|many|most|all|your|our|my)\s+\w+)",
    re.IGNORECASE)
_RE_FINITES_VERB = re.compile(
    r"\b(?:is|are|was|were|be|been|has|have|had|can|could|may|might|will|would|should|must|does|do|did|"
    r"fail\w*|leak\w*|crack\w*|break\w*|broke\w*|show\w*|develop\w*|suffer\w*|need\w*|require\w*|tend\w*|"
    r"seem\w*|wear\w*|worn|report\w*|experience\w*|cause\w*|goes|go)\b", re.IGNORECASE)


def segmentiere(satz: str) -> list[str]:
    """Zerlegt einen englischen Satz an Kontrastwoertern und an "and" + neuem Teilsatz (nur wenn der Teil
    davor ein eigenes finites Verb traegt). Ein benigner Teil verwirft nur sich selbst."""
    segmente: list[str] = []
    for grob in _RE_KONTRAST.split(satz or ""):
        pos = 0
        for m in _RE_UND_NEUER_CLAIM.finditer(grob):
            if _RE_FINITES_VERB.search(grob[pos:m.start()]):
                segmente.append(grob[pos:m.start()])
                pos = m.end()
        segmente.append(grob[pos:])
    segmente = [s.strip(" ,;.") for s in segmente if s and s.strip(" ,;.")]
    return segmente or [satz]


# ── Problemsignal ─────────────────────────────────────────────────────────────────────────────────

_RE_MATERIAL_KEIN_SCHADEN = re.compile(
    r"\banti[- ]?(?:rust|corrosion)\w*|\b(?:rust|corrosion)[- ]?(?:proof\w*|protect\w*|resist\w*|inhibit\w*|"
    r"prevent\w*|free|perforation|warranty)\b|\bcorrosion\s+(?:protection|resistance)\b", re.IGNORECASE)
_RE_PROBLEM = re.compile(
    r"\b(?:fail(?:s|ed|ing|ure|ures)?|fault(?:s|y)?|defect(?:s|ive)?|issues?|problems?|problematic|"
    r"leak(?:s|ed|ing|y|age)?|crack(?:s|ed|ing)?|worn|wear(?:s)?\s+out|wearing\s+out|"
    r"break(?:s|ing|down)?|broke|broken|malfunction\w*|weak(?:ness(?:es)?|\s+points?|\s+spots?)?|premature(?:ly)?|"
    r"overheat\w*|seiz(?:e|es|ed|ing)|rattl\w+|corrod\w+|corrosion|rust(?:s|ed|ing|y)?|"
    r"burn(?:s|ing)?\s+oil|prone\s+to|susceptible|notorious\w*|trouble(?:some)?|unreliab\w+|"
    r"gives?\s+out|go(?:es)?\s+bad|goes\s+wrong|disintegrat\w+|collapse\w*|snap(?:s|ped)?)\b", re.IGNORECASE)


def hat_problem(satz: str) -> bool:
    return bool(_RE_PROBLEM.search(_RE_MATERIAL_KEIN_SCHADEN.sub(" ", satz or "")))


# ── Verneinung, Relativierung, Frage, Bedingung, Garantie, Rueckrufdatensatz (Schutzregeln) ──────

_DEF_N = (r"(?:problems?|issues?|faults?|defects?|failures?|failings?|complaints?|weakness(?:es)?|"
          r"weak\s+(?:points?|spots?)|concerns?|troubles?|leaks?|reports?|cases?|incidents?|gremlins?)")
_RE_BENIGN = re.compile(
    r"\bno\s+(?:[\w-]+\s+){0,3}?" + _DEF_N + r"\b"
    r"|\bnot\s+(?:(?:a|an|really|particularly|generally|typically|usually|commonly|known|considered|seen|"
    r"regarded|widely|often|especially|very|that|too|so|an?\s+issue\s+or)\s+){0,4}?"
    r"(?:common|known|widespread|typical|frequent|prone|problematic|reported|issues?|problems?|concerns?|"
    r"faults?|defects?|failures?|weakness(?:es)?|worry|worrying)\b"
    r"|\b(?:does|do|did|has|have|had|will|would|can|could|is|are|was|were)\s+not\s+(?:[\w-]+\s+){0,2}?"
    r"(?:fail|leak|crack|break|rust|corrode|wear|burn|overheat|seize|rattle|suffer|show|develop|give|"
    r"experience|have)\w*"
    r"|\bnever\s+(?:[\w-]+\s+){0,3}?(?:fail|leak|crack|break|had|gave|give|suffer|show|need|develop)\w*"
    r"|\b(?:rarely|seldom|hardly|barely|scarcely)\b"
    r"|\b(?:free\s+(?:of|from)|without)\s+(?:[\w-]+\s+){0,1}?(?:problems?|issues?|faults?|defects?|failures?|"
    r"leaks?|trouble|rust|corrosion)\b"
    r"|\b(?:problem|trouble|issue|fault|leak|rust|maintenance|worry)[- ]free\b"
    r"|\bunproblematic\b|\bbulletproof\b|\bbombproof\b|\bindestructible\b|\brock[- ]solid\b"
    r"|\bnothing\s+(?:to\s+worry|wrong|unusual|alarming)\b|\bno\s+cause\s+for\b"
    r"|\bperfectly\s+(?:normal|fine|ok|okay)\b|\bharmless\b"
    r"|\b(?:is|are|completely|totally)\s+normal\b|\bnormal\s+(?:behaviou?r|wear|operation|characteristic\w*)\b"
    r"|\bby\s+design\b|\bto\s+be\s+expected\b"
    r"|\b(?:does|do)\s+not\s+(?:necessarily\s+)?(?:mean|indicate|imply)\b|\bnot\s+necessarily\b"
    r"|\bno\s+(?:signs?|evidence|indications?)\s+of\b|\bunlikely\b",
    re.IGNORECASE)
_RE_BEDINGT = re.compile(r"^\s*(?:if|should|unless|in\s+case|whether|provided\s+that|assuming)\b", re.IGNORECASE)
_RE_FRAGE = re.compile(r"\?\s*$|^\s*(?:is|are|does|do|did|has|have|can|could|will|would|should|why|how|what|which)\b",
                       re.IGNORECASE)
_RE_GARANTIE = re.compile(r"\bwarrant(?:y|ies)\b|\bguarantee\w*|\bcovered\s+(?:by|under)\b|\bgoodwill\b",
                          re.IGNORECASE)
_RE_RUECKRUFDATENSATZ = re.compile(
    r"\brecall\s+(?:date|number|no\b|code|campaign|id|reference|status|notice)|\bnumber\s+of\s+affected\b"
    r"|\baffected\s+vehicles\b|\bcampaign\s+(?:number|no\b|code|id)\b", re.IGNORECASE)
_RE_RUECKVERWEIS_START = re.compile(r"^\s*(?:it|this|that|these|those|they|its|their|such|the\s+same)\b",
                                    re.IGNORECASE)


def ist_benign(satz: str) -> bool:
    """Verneinung/Relativierung ("no known issues", "not a common problem", "free of ...")."""
    return bool(_RE_BENIGN.search(_glatt(satz)))


def ist_frage(satz: str) -> bool:
    return bool(_RE_FRAGE.search((satz or "").strip()))


def ist_bedingt(satz: str) -> bool:
    return bool(_RE_BEDINGT.search(satz or ""))


def ist_garantie_oder_spezifikation(satz: str) -> bool:
    return bool(_RE_GARANTIE.search(satz or ""))


def ist_rueckrufdatensatz(satz: str) -> bool:
    return bool(_RE_RUECKRUFDATENSATZ.search(satz or ""))


def ist_rueckverweis(satz: str) -> bool:
    """Satz beginnt mit einem Rueckbezug ("It also fails ...") -- ohne Vorgaenger kein eigener Claim."""
    return bool(_RE_RUECKVERWEIS_START.search(satz or ""))


# ── Reichweite: Einzelfahrzeug, Wiederholung, Population ─────────────────────────────────────────

_RE_EINZEL = re.compile(
    r"(?-i:\bI\b)|\bI['’](?:ve|d|m|ll)\b|\bwe\b|\b(?:my|our|mine|ours)\b"
    r"|\b(?:long[- ]term|test|press|review|company|demo)\s+(?:car|vehicle|example|loan)\b"
    r"|\b(?:one|a|an|this|the\s+previous|the\s+current)\s+(?:owner|reader|customer|buyer|driver)\b"
    r"|\bthis\s+(?:car|vehicle|example|unit|one)\b|\b(?:a|the)\s+friend\b|\bmy\s+neighbou?r\b"
    r"|\b(?:forum\s+post|owner\s+report|reader\s+report|reader\s+review|user\s+review|anecdot\w+)\b",
    re.IGNORECASE)
_RE_WIEDERHOLUNG = re.compile(
    r"\b(?:once|twice|thrice)\b|\b(?:two|three|four|five|six|seven|eight|nine|ten|\d+)\s+times\b"
    r"|\b(?:again|repeatedly|multiple\s+times|several\s+times|over\s+and\s+over)\b", re.IGNORECASE)
_RE_POPULATION = re.compile(
    r"\b(?:many|most|several|numerous|countless|lots\s+of|a\s+lot\s+of|a\s+number\s+of|plenty\s+of|other|"
    r"all|some|multiple|various|several\s+other)\s+(?:[\w-]+\s+){0,2}(?:owners?|drivers?|cars?|vehicles?|"
    r"examples?|models?|units?|customers?|buyers?|users?)\b"
    r"|\b(?:owners?|drivers?|buyers?|customers?|users?)\s+(?:often\s+|frequently\s+|commonly\s+)?"
    r"(?:report|reported|complain|complained|say|said|mention|mentioned|note|noted|find|found|experience|"
    r"experienced|have\s+reported|have\s+had|have\s+found|agree)\b"
    r"|\b(?:on|in|with|across)\s+(?:this|these|the|that)\s+(?:model|models|engine|engines|generation|series|"
    r"variant|variants|platform|range|powertrain|motor)\b"
    r"|\b(?:on|in|with)\s+(?:early|earlier|older|late|later|pre[- ]?facelift|post[- ]?facelift)\s+(?:[\w-]+\s+){0,1}"
    r"(?:cars?|models?|examples?|vehicles?|builds?|production|units?)\b"
    r"|\bmodel[- ]specific\b|\bserial\s+(?:problem|issue|defect)\b"
    r"|\btypical\s+(?:for|of)\s+(?:this|the)\s+(?:model|car|engine|series|generation)\b",
    re.IGNORECASE)
_RE_ALLGEMEIN = re.compile(
    r"\b(?:common(?:ly)?|frequent(?:ly)?|often|typical(?:ly)?|prone\s+to|tends?\s+to|tend\s+to|"
    r"known\s+(?:for|issues?|problems?|faults?|weakness(?:es)?|failures?|to)|widespread|recurring|recurrent|"
    r"regular(?:ly)?|usually|generally|notorious\w*|weak\s+(?:points?|spots?)|weakness(?:es)?|achilles|"
    r"inherent|systemic|endemic|well[- ](?:documented|known)|chronic\w*|plagued|susceptible|"
    r"history\s+of|reputation\s+for|numerous\s+reports|many\s+reports)\b", re.IGNORECASE)
_RE_PROBLEMRAHMEN = re.compile(
    r"\b(?:problems?|issues?|weakness(?:es)?|weak\s+(?:points?|spots?)|faults?|defects?|failures?|troubles?|"
    r"concerns?|gremlins?|achilles)\b", re.IGNORECASE)


def hat_einzelfahrzeug(text: str) -> bool:
    return bool(_RE_EINZEL.search(_glatt(text)))


def hat_wiederholung(text: str) -> bool:
    return bool(_RE_WIEDERHOLUNG.search(text or ""))


def hat_population(text: str) -> bool:
    return bool(_RE_POPULATION.search(text or ""))


def traegt_reichweite(text: str) -> bool:
    return bool(_RE_POPULATION.search(text or "") or _RE_ALLGEMEIN.search(text or ""))


def reichweite(claim: str, kontext: tuple[bool, bool] = (False, False)) -> str:
    """"einzel" | "allgemein" | "unbelegt" -- dieselbe Rangfolge wie die deutsche Regel: ausdrueckliche
    Population vor Einzelfahrzeug; Wiederholung am eigenen Auto ("three times") ist KEINE Population."""
    c = claim or ""
    kontext_einzel, kontext_allgemein = kontext
    if _RE_POPULATION.search(c):
        return "allgemein"
    if kontext_einzel or hat_einzelfahrzeug(c) or hat_wiederholung(c):
        return "einzel"
    if _RE_ALLGEMEIN.search(c) or kontext_allgemein:
        return "allgemein"
    return "unbelegt"


def rahmende_zeile(satz: str, hat_bauteil: Callable[[str], bool]) -> bool:
    """Rahmensatz, der die folgenden Punkte als allgemeine Befunde einleitet ("Common problems of the RC F
    include:"): traegt Reichweite, ist problembezogen, nennt KEIN eigenes Bauteil und ist nicht benign."""
    s = (satz or "").strip()
    if not traegt_reichweite(s) or hat_einzelfahrzeug(s) or hat_wiederholung(s):
        return False
    if not _RE_PROBLEMRAHMEN.search(s) or ist_benign(s):
        return False
    return not hat_bauteil(s)


def reichweiten_kontext(titel: str | None, satz_liste: list[str], satz: str, *,
                        fremd: Callable[[str], bool], koepfe: frozenset[str],
                        hat_bauteil: Callable[[str], bool]) -> tuple[bool, bool, list[str]]:
    """(einzelfahrzeug, allgemein, rahmen) aus dem Kontext DESSELBEN Treffers -- Gegenstueck zu
    `_reichweiten_kontext`. `rahmen` sind die Saetze/Ueberschriften, die diese Aussage rahmen (fuer die
    aussagenbezogene Markt-/Variantenpruefung); der Treffer-Titel zaehlt nur als Einzelfahrzeug-Hinweis."""
    einzel = hat_einzelfahrzeug(titel or "")
    try:
        i = satz_liste.index(satz)
    except ValueError:
        return einzel, False, []
    nachbarn = satz_liste[max(0, i - 1):i] + satz_liste[i + 1:i + 2]
    if any(hat_einzelfahrzeug(t) for t in nachbarn):
        einzel = True
    titel_norm = re.sub(r"\W+", " ", (titel or "").lower()).strip()
    allgemein = False
    rahmen: list[str] = []
    for j in range(i - 1, max(-1, i - 6), -1):
        vorher = satz_liste[j]
        if titel_norm and re.sub(r"\W+", " ", vorher.lower()).strip() == titel_norm:
            continue
        if fremd(vorher) or hat_einzelfahrzeug(vorher):
            break
        if vorher in koepfe:
            rahmen.append(vorher)          # kurze Ueberschrift: nie Reichweiten-Rahmen, aber Markt-/Variantenrahmen
            continue
        if rahmende_zeile(vorher, hat_bauteil):
            allgemein = True
            rahmen.append(vorher)
            break
    if not allgemein and i + 1 < len(satz_liste):
        folge = satz_liste[i + 1]
        if (re.match(r"^\s*(?:this|that|these|those|it|the\s+(?:problem|issue|failure|fault|defect))\b", folge,
                     re.IGNORECASE) and traegt_reichweite(folge)
                and not hat_einzelfahrzeug(folge) and not fremd(folge)):
            allgemein = True
            rahmen.append(folge)
    return einzel, allgemein, rahmen


# ── Bauteile (englisch -> bestehende deutsche Schluessel) ────────────────────────────────────────

# (Muster, deutsches Anzeigelabel, bestehender Schluessel in app/kaufaktionen._KOMPONENTEN, generisch).
# Das Label enthaelt das deutsche Muster seines Schluessels, damit Pruefplan/Fragen es wiederfinden.
_B = re.IGNORECASE
_BAUTEILE: tuple[tuple[re.Pattern, str, str, bool], ...] = tuple(
    (re.compile(r"(?<![\w-])(?:" + muster + r")(?![\w-])", _B), label, schluessel, generisch)
    for muster, label, schluessel, generisch in (
        (r"air\s+suspension|air\s+springs?|air\s+struts?", "Luftfederung", "luftfederung", False),
        (r"adaptive\s+(?:suspension|dampers?|damping)|magnetic\s+ride|variable\s+dampers?",
         "Adaptive Dämpfer", "adaptive_daempfer", False),
        (r"control\s+arms?|wishbones?|shock\s+absorbers?|dampers?|struts?|ball\s+joints?|anti[- ]roll\s+bars?|"
         r"sway\s+bars?|bushings?|suspension", "Fahrwerk", "fahrwerk", False),
        (r"wheel\s+bearings?", "Radlager", "raeder", False),
        (r"tyres?|tires?", "Reifen", "raeder", False),
        (r"brake\s+(?:pads?|discs?|disks?|rotors?|calipers?|lines?|fluid|boosters?)|brakes?(?!\s+horse)",
         "Bremsen", "bremsen", False),
        (r"power\s+steering|steering\s+(?:rack|column|pump|gear)|steering", "Lenkung", "lenkung", False),
        (r"dual[- ]mass\s+fly\s*wheels?|DMF", "Zweimassenschwungrad", "zweimassenschwungrad", False),
        (r"automatic\s+(?:transmission|gearbox)|torque\s+converter|dual[- ]clutch|DCT|DSG|CVT|mechatronics?",
         "Automatikgetriebe", "automatikgetriebe", False),
        (r"clutch(?:es)?", "Kupplung", "kupplung", False),
        (r"transmission|gearbox|manual\s+gearbox", "Getriebe", "getriebe", False),
        (r"differentials?|prop\s*shafts?|drive\s*shafts?|cv\s+joints?|transfer\s+case", "Differenzial",
         "allradantrieb", False),
        (r"turbo(?:charger)?s?|superchargers?|wastegates?|charge\s+pipes?|intercoolers?", "Turbolader",
         "turbolader", False),
        (r"particulate\s+filters?|DPF|GPF|OPF", "Partikelfilter", "partikelfilter", False),
        (r"EGR|exhaust\s+gas\s+recirculation", "Abgasrückführung", "agr", False),
        (r"adblue", "AdBlue", "adblue", False),
        (r"fuel\s+injectors?|injectors?", "Injektor", "einspritzung", False),
        (r"high[- ]pressure\s+fuel\s+pumps?|fuel\s+pumps?", "Kraftstoffpumpe", "einspritzung", False),
        (r"timing\s+chains?|chain\s+tensioners?|cam\s+chains?", "Steuerkette", "steuerkette", False),
        (r"timing\s+belts?|cam\s*belts?", "Zahnriemen", "zahnriemen", False),
        (r"serpentine\s+belts?|drive\s+belts?", "Keilrippenriemen", "zahnriemen", False),
        (r"VVT|variable\s+valve\s+timing|VANOS|valvetronic|cam\s+phasers?", "Nockenwellenversteller",
         "nockenwellenverstellung", False),
        (r"ignition\s+coils?|coil\s+packs?", "Zündspule", "zuendung", False),
        (r"spark\s+plugs?", "Zündkerze", "zuendung", False),
        (r"oil\s+consumption|burn(?:s|ing)?\s+oil|oil\s+loss|valve\s+stem\s+seals?|piston\s+rings?",
         "Ölverbrauch", "oelverlust", False),
        (r"oil\s+leaks?|leaking\s+oil", "Ölverlust", "oelverlust", False),
        (r"head\s+gaskets?|cylinder\s+heads?", "Zylinderkopfdichtung", "zylinderkopf", False),
        (r"connecting\s+rods?|rod\s+bearings?|big[- ]end\s+bearings?|main\s+bearings?", "Pleuel",
         "motor_innen", False),
        (r"crankshafts?", "Kurbelwelle", "motor_innen", False),
        (r"water\s+pumps?|coolant\s+pumps?", "Wasserpumpe", "kuehlung", False),
        (r"thermostats?", "Thermostat", "kuehlung", False),
        (r"radiators?|cooling\s+system|coolant\s+(?:leaks?|hoses?)", "Kühlsystem", "kuehlung", False),
        (r"mass\s+air\s*flow(?:\s+sensors?)?|throttle\s+bod(?:y|ies)|sensors?", "Sensor", "sensorik", False),
        (r"catalytic\s+converters?", "Katalysator", "abgasanlage", False),
        (r"exhaust(?:\s+system|\s+manifolds?)?|manifolds?", "Auspuff", "abgasanlage", False),
        (r"hybrid\s+batter(?:y|ies)|traction\s+batter(?:y|ies)|high[- ]voltage\s+batter(?:y|ies)|"
         r"hv\s+batter(?:y|ies)", "Hochvoltbatterie", "hochvoltbatterie", False),
        (r"alternators?", "Lichtmaschine", "starterbatterie", False),
        (r"12\s?v\s+batter(?:y|ies)|starter\s+batter(?:y|ies)", "Starterbatterie", "starterbatterie", False),
        (r"infotainment|head\s+units?|touch\s*pads?|touch\s*screens?|navigation\s+systems?|sat[- ]?nav",
         "Infotainment", "infotainment", False),
        (r"air[- ]?con(?:ditioning)?|a/c|climate\s+control|ac\s+compressors?", "Klimaanlage", "klimaanlage", False),
        (r"sun\s*roofs?", "Schiebedach", "dach_fenster", False),
        (r"panoramic\s+roofs?", "Panoramadach", "dach_fenster", False),
        (r"window\s+regulators?|power\s+windows?", "Fensterheber", "dach_fenster", False),
        (r"head\s*lights?|head\s*lamps?|xenon|reversing\s+cameras?|rear[- ]view\s+cameras?", "Scheinwerfer",
         "beleuchtung", False),
        (r"clear\s*coat|lacquer|peeling\s+paint", "Klarlack", "oberflaeche", False),
        (r"dashboards?|interior\s+(?:trim|plastics?)", "Armaturenbrett", "innenraum", False),
        (r"rust(?:ing|ed)?|corrosion|corroded", "Rost", "rost", False),
        (r"engines?|motors?", "Motor", "motor", True),
    ))


def bauteil(satz: str) -> tuple[str, str] | None:
    """(deutsches Label, bestehender Schluessel) des ERSTEN spezifischen Bauteils im Satz; ein generisches
    Wort ("engine") nur, wenn kein spezifisches Bauteil vorkommt. Wortgrenzen, kein Teilstring."""
    bestes: tuple[int, str, str] | None = None
    generisch: tuple[int, str, str] | None = None
    for muster, label, schluessel, ist_generisch in _BAUTEILE:
        m = muster.search(satz or "")
        if not m:
            continue
        kandidat = (m.start(), label, schluessel)
        if ist_generisch:
            generisch = generisch or kandidat
        elif bestes is None or kandidat[0] < bestes[0]:
            bestes = kandidat
    treffer = bestes or generisch
    return (treffer[1], treffer[2]) if treffer else None


def hat_bauteil(satz: str) -> bool:
    return bauteil(satz) is not None


# ── Zeitraum ──────────────────────────────────────────────────────────────────────────────────────

_J = r"((?:19|20)\d{2})"
_RE_Z_BEREICH = re.compile(_J + r"\s*(?:-|–|to|through|thru|until|till)\s*" + _J
                           + r"|\bbetween\s+" + _J + r"\s+and\s+" + _J, re.IGNORECASE)
_RE_Z_AB = re.compile(r"\b(?:from|since|starting(?:\s+(?:in|from))?|beginning(?:\s+in)?)\s+" + _J
                      + r"|\b" + _J + r"\s*(?:onwards?|and\s+(?:later|newer|up)|\+)", re.IGNORECASE)
_RE_Z_BIS = re.compile(r"\b(?:before|prior\s+to|up\s+to|until|through|pre[- ]?)\s*" + _J, re.IGNORECASE)
_RE_Z_EINZEL = re.compile(r"\b(?:model\s+years?\s+|MY\s?)" + _J + r"|\b" + _J
                          + r"\s+(?:models?|model\s+year|cars?|examples?|builds?|production)\b", re.IGNORECASE)


def zeitraum(text: str) -> tuple[int | None, int | None] | None:
    """(von, bis) aus englischen Jahresangaben, sonst None."""
    t = text or ""
    m = _RE_Z_BEREICH.search(t)
    if m:
        jahre = [int(g) for g in m.groups() if g]
        return jahre[0], jahre[1]
    ab, bis = _RE_Z_AB.search(t), _RE_Z_BIS.search(t)
    if ab or bis:
        von = int(next(g for g in ab.groups() if g)) if ab else None
        b = int(bis.group(1)) - (1 if re.match(r"(?i)before|prior|pre", bis.group(0)) else 0) if bis else None
        return von, b
    m = _RE_Z_EINZEL.search(t)
    if m:
        j = int(next(g for g in m.groups() if g))
        return j, j
    return None


# ── Markt ─────────────────────────────────────────────────────────────────────────────────────────

_RE_MARKT_FREMD = re.compile(
    r"(?-i:(?<![\w.])(?:US|USA|U\.S\.A?\.?|USDM|JDM|CARB|EPA|NHTSA|GCC)(?!\w))"
    r"|\bUnited\s+States\b|\bNorth\s+Americ\w*|\bAmerican\s+(?:market|models?|cars?|spec)\b|\bCanad\w*"
    r"|\bAustrali\w*|\bNew\s+Zealand\b|\bin\s+Japan\b|\bJapan(?:ese)?[- ](?:market|spec|domestic|models?)\b"
    r"|\bMiddle\s+East\b|\bChina\b|\bChinese\s+market\b|\bSouth\s+Africa\b|\bMexic\w*|\bBrazil\w*"
    r"|\bCalifornia\w*|\bfederal\s+(?:spec|emissions?|models?)\b|\bUS[- ]spec\b", re.IGNORECASE)
_RE_MARKT_EU = re.compile(
    r"\b(?:in|across|throughout|for|within|from)\s+(?:the\s+)?(?:Europe\w*|EU|UK|U\.K\.|Britain|Germany|"
    r"Deutschland|England)\b"
    r"|\b(?:Europe\w*|EU|UK|U\.K\.|Euro)[- ](?:market|spec|specification|models?|versions?|cars?|"
    r"variants?|vehicles?|owners?|buyers?|customers?|dealers?|sold|built)\b"
    r"|\b(?:British|German)[- ]market\b"
    r"|\bsold\s+in\s+(?:Europe|the\s+UK|Germany)\b|\bworldwide\b|\bglobally\b|\ball\s+markets\b|\ball\s+regions\b"
    r"|\bglobal\s+(?:market|models?|issue|problem)\b|\b(?:Germany|Deutschland)\b", re.IGNORECASE)
_MARKT_NAMEN = (("Deutschland", re.compile(r"Germany|Deutschland|German", re.I)),
                ("UK", re.compile(r"\bUK\b|U\.K\.|Britain|British|England")),
                ("Europa", re.compile(r"Europe\w*|\bEU\b|Euro[- ]", re.I)),
                ("weltweit", re.compile(r"worldwide|globally|global|all\s+(?:markets|regions)", re.I)))


def markt(claim: str, rahmen: list[str] | None = None) -> tuple[str, str | None]:
    """("ok", Markt) | ("fremd", None) | ("unklar", None) | ("unbelegt", None). NUR Aussage und ihr Rahmen --
    nie die ganze Seite. Positiver Beleg verlangt; ein fremder Marker allein sperrt die Aussage; beide
    zugleich bleiben ungeklaert; kein Marker ist kein Beleg."""
    text = " ".join([claim or "", *(rahmen or [])])
    fremd, eu = _RE_MARKT_FREMD.search(text), _RE_MARKT_EU.search(text)
    if fremd and eu:
        return "unklar", None
    if fremd:
        return "fremd", None
    if eu:
        namen = [n for n, rx in _MARKT_NAMEN if rx.search(text)]
        return "ok", "/".join(namen) if namen else "Europa"
    return "unbelegt", None


# ── Variante / technische Passung ────────────────────────────────────────────────────────────────

_RE_HUBRAUM = re.compile(r"(?<![\d.,])([1-9][.,]\d)\s*-?\s*(?:l\b|ltr\b|litre|liter|L\b)"
                         r"|(?<![\d.,])([1-9][.,]\d)\s+(?=V\s?\d|I\d|inline|turbo|diesel|petrol|hybrid|TDI|TSI)",
                         re.IGNORECASE)
_RE_ZYLINDER = re.compile(r"\b(?:V\s?(\d{1,2})|inline[- ]?(\d)|straight[- ]?(\d)|flat[- ]?(\d)|I(\d))\b")
_RE_MOTORCODE = re.compile(r"\b(\d[A-Z]{1,2}-[A-Z]{1,3}(?:\s?[A-Z])?)\b|\bengine\s+code\s+([A-Z0-9-]{3,8})\b")
_RE_LEISTUNG = re.compile(r"\b\d{2,4}\s*(?:hp|bhp|ps|kw|cv|horse\s*power|horsepower)\b", re.IGNORECASE)
_RE_KRAFTSTOFF = re.compile(
    r"\b(diesel|petrol|gasoline|plug[- ]in\s+hybrid|PHEV|mild[- ]hybrid|MHEV|hybrid|HEV|electric|BEV|EV)\b",
    re.IGNORECASE)
_RE_GENERATION = re.compile(
    r"\b(?:first|second|third|fourth|fifth|\d(?:st|nd|rd|th))[- ]gen(?:eration)?\b|\bgen\s?\d\b|\bmk\s?\d\b"
    r"|\bgenerations?\b|\bplatforms?\b|\bchassis\b", re.IGNORECASE)
_RE_FACELIFT = re.compile(r"\b(?:pre|post)[- ]?facelift\b|\bfacelift(?:ed)?\b|\bLCI\b|\bmid[- ]cycle\b",
                          re.IGNORECASE)


def hat_technikbezug(text: str) -> bool:
    """Die Aussage ist ueber Motor/Leistung/Generation eingegrenzt (Motorcode, Hubraum, Zylinder, Leistung,
    Generation/Plattform)."""
    t = text or ""
    return bool(_RE_HUBRAUM.search(t) or _RE_MOTORCODE.search(t) or _RE_ZYLINDER.search(t)
                or _RE_LEISTUNG.search(t) or _RE_GENERATION.search(t))


def _kraftstoff_klasse(wort: str) -> tuple[str | None, str | None]:
    w = wort.lower()
    if w == "diesel":
        return "diesel", None
    if w in ("petrol", "gasoline"):
        return "benzin", None
    if "plug" in w or w == "phev":
        return None, "PHEV"
    if "mild" in w or w == "mhev":
        return None, "MHEV"
    if w in ("hybrid", "hev"):
        return None, "HYBRID"
    return None, "BEV"       # electric / BEV / EV


def variante_detail(text: str, ziel: dict | None) -> tuple[str, str | None, list[str]]:
    """("ok"|"abweichend"|"unbelegt", Grund, uebereinstimmende Achsen). `ziel`: hubraum (str "5.0"), fuel ("benzin"/"diesel"/"elektro"),
    powertrains (frozenset), motor (Freitext). Unbekanntes Ziel -> "unbelegt", nie "ok". Leistungsangaben
    (hp/bhp/PS/kW) werden NICHT verglichen und nie als PS gelesen: sie belegen nichts."""
    ziel = ziel or {}
    t = text or ""
    motor = (ziel.get("motor") or "")
    abweichend: list[str] = []
    unbelegt: list[str] = []
    passend: list[str] = []      # Achsen, die die Aussage NENNT und die zum Ziel passen

    for m in _RE_HUBRAUM.finditer(t):
        wert = float((m.group(1) or m.group(2)).replace(",", "."))
        try:
            ziel_h = float(str(ziel.get("hubraum")).replace(",", ".")) if ziel.get("hubraum") else None
        except ValueError:
            ziel_h = None
        if ziel_h is None:
            unbelegt.append("hubraum")
        elif abs(wert - ziel_h) > 0.06:
            abweichend.append("hubraum")
        else:
            passend.append("hubraum")

    for m in _RE_ZYLINDER.finditer(t):
        zahl = next(g for g in m.groups() if g)
        konfig = re.search(r"\b(?:V\s?(\d{1,2})|inline[- ]?(\d)|straight[- ]?(\d)|flat[- ]?(\d)|I(\d))\b", motor)
        if not motor or not konfig:
            unbelegt.append("zylinder")
        elif next(g for g in konfig.groups() if g) != zahl:
            abweichend.append("zylinder")
        else:
            passend.append("zylinder")

    for m in _RE_MOTORCODE.finditer(t):
        code = re.sub(r"\s", "", next(g for g in m.groups() if g)).lower()
        if code not in re.sub(r"\s", "", motor.lower()):
            unbelegt.append("motorcode")
        else:
            passend.append("motorcode")

    fuel_ziel, pts = ziel.get("fuel"), ziel.get("powertrains")
    for m in _RE_KRAFTSTOFF.finditer(t):
        fuel, pt = _kraftstoff_klasse(m.group(1))
        if fuel:
            if not fuel_ziel:
                unbelegt.append("kraftstoff")
            elif fuel_ziel != fuel:
                abweichend.append("kraftstoff")
            else:
                passend.append("kraftstoff")
        else:
            if not pts:
                unbelegt.append("antrieb")
            elif pt == "HYBRID":
                if not (set(pts) & {"MHEV", "PHEV", "HEV"}):
                    abweichend.append("antrieb")
            elif pt not in pts:
                abweichend.append("antrieb")
            else:
                passend.append("antrieb")
        if fuel is None and pt == "HYBRID" and pts and (set(pts) & {"MHEV", "PHEV", "HEV"}):
            passend.append("antrieb")

    if _RE_FACELIFT.search(t):
        unbelegt.append("modellpflege")
    if _RE_LEISTUNG.search(t):
        unbelegt.append("leistung")          # offen: hp/bhp/PS/kW werden nicht verglichen

    if abweichend:
        return "abweichend", "+".join(sorted(set(abweichend))), []
    if unbelegt:
        return "unbelegt", "+".join(sorted(set(unbelegt))), []
    return "ok", None, sorted(set(passend))


def variante(text: str, ziel: dict | None) -> tuple[str, str | None]:
    """Wie `variante_detail`, ohne die uebereinstimmenden Achsen."""
    status, grund, _passend = variante_detail(text, ziel)
    return status, grund


# ── Fehlertyp (Gegenstueck zu `_schwachstelle_failure_mode`, gleiche Namen/Reihenfolge) ──────────

_FEHLERTYPEN: tuple[tuple[str, re.Pattern], ...] = (
    ("korrosion", re.compile(r"\b(?:rust(?:s|ed|ing|y)?|corrod\w+|corrosion)\b", re.IGNORECASE)),
    ("undicht", re.compile(r"\bleak(?:s|ed|ing|y|age)?\b", re.IGNORECASE)),
    ("geraeusch", re.compile(r"\b(?:noise|noisy|rattl\w+|knock\w*|whin\w+|squeal\w*|squeak\w*|grind\w*|clunk\w*|"
                             r"whistl\w+|hum(?:s|ming)?)\b", re.IGNORECASE)),
    ("vibration", re.compile(r"\b(?:vibrat\w+|judder\w*|shudder\w*|jerk\w*|stutter\w*|hesitat\w+)\b",
                             re.IGNORECASE)),
    ("elektrisch", re.compile(r"\b(?:fault\s+codes?|electrical\s+(?:fault|failure|problem)s?|ecu\s+(?:fault|failure))\b",
                              re.IGNORECASE)),
    ("verschleiss", re.compile(r"\b(?:worn|wears?\s+out|wearing\s+out|excessive\s+wear|premature\s+wear)\b",
                               re.IGNORECASE)),
    ("ausfall", re.compile(r"\b(?:fail(?:s|ed|ing|ure|ures)?|break(?:s|ing|down)?|broke|broken|dies|died|"
                           r"seiz(?:e|es|ed|ing)|gives?\s+out|malfunction\w*)\b", re.IGNORECASE)),
)


def failure_mode(claim: str) -> str | None:
    """Gleiche Fehlertyp-Namen wie die deutsche Klassifikation; `None`, wenn nicht sicher bestimmbar."""
    for name, rx in _FEHLERTYPEN:
        if rx.search(claim or ""):
            return name
    return None


# ── Uebertragung auf das Zielfahrzeug (Beziehung Bauteil/Ausfuehrung/Zeitraum/Ziel) ──────────────

# Ausdrueckliche Aussage, dass DASSELBE Teil/Design in anderen Maerkten/Varianten verbaut ist. Nur sie macht
# eine Marktangabe (z. B. "UK") fuer ein anderes Zielgebiet relevant; eine Marktangabe allein ist nur die
# Reichweite der QUELLE, kein Beleg fuer die konkrete Geltung des Mangels.
_RE_UEBERTRAGUNG = re.compile(
    r"\b(?:same|identical)\s+(?:[\w-]+\s+){0,3}?(?:parts?|pumps?|engines?|units?|designs?|components?|assembl(?:y|ies)|"
    r"platforms?|hardware|specifications?|specs?|items?|chains?|turbos?|gearbox(?:es)?|transmissions?)\b"
    r"|\bshared\s+(?:with|across|between)\b"
    r"|\b(?:all|every|both)\s+(?:markets?|regions?)\b|\bregardless\s+of\s+(?:the\s+)?(?:market|region|country)\b"
    r"|\bnot\s+(?:market|region)[- ]specific\b"
    r"|\b(?:also|equally)\s+(?:affects?|applies\s+to|found\s+(?:on|in))\s+(?:the\s+)?(?:european|german|eu)\b",
    re.IGNORECASE)
_RE_UEBERTRAGUNG_NEG = re.compile(
    r"\bnot\s+(?:the\s+)?(?:same|identical|shared)\b|\b(?:different|differing)\s+(?:[\w-]+\s+){0,2}"
    r"(?:parts?|pumps?|engines?|units?|designs?|specs?|specification)\b|\bunique\s+to\b", re.IGNORECASE)


def hat_uebertragungsbeleg(text: str) -> bool:
    """Die Aussage selbst belegt ausdruecklich, dass dasselbe Teil/Design auch bei anderen Maerkten/Varianten
    verbaut ist. Fehlender Widerspruch ist kein Beleg; eine Verneinung ("not the same") hebt ihn auf."""
    t = _glatt(text)
    return bool(_RE_UEBERTRAGUNG.search(t)) and not _RE_UEBERTRAGUNG_NEG.search(t)


def generation_passt(text: str, codes: list[str] | None) -> bool:
    """Die Aussage nennt ausdruecklich eine BELEGTE Generation des Zielfahrzeugs (Wortgrenze, kein Teilstring)."""
    for c in codes or []:
        if c and re.search(r"(?<![\w-])" + re.escape(str(c)) + r"(?![\w-])", text or "", re.IGNORECASE):
            return True
    return False


# ── Unabhaengigkeit (Kopie-Erkennung) ────────────────────────────────────────────────────────────


def fast_gleich(a: str, b: str, schwelle: float = 0.8) -> bool:
    """Nahezu identische Aussage (abgeschrieben/syndiziert): zwei Quellen mit demselben Satz sind keine
    unabhaengigen Belege."""
    ta = set(re.findall(r"[a-z0-9]{3,}", (a or "").lower()))
    tb = set(re.findall(r"[a-z0-9]{3,}", (b or "").lower()))
    if not ta or not tb:
        return False
    return len(ta & tb) / len(ta | tb) >= schwelle
