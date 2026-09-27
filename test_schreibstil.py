"""
Test: die gemeinsame Schreibstil-Regel erreicht JEDE generierende Oberflaeche.

Warum als eigener Test: die Regel lag vorher in zwei Prompts einzeln und in
vier weiteren gar nicht. Genau das soll nicht wieder auseinanderlaufen. Der
Test prueft nicht den Wortlaut einer Kopie, sondern dass die EINE Konstante aus
app/schreibstil.py tatsaechlich im fertigen Prompt steht.

Ausserdem wird die Gegenrichtung gesichert: die Regel darf Zahlenbereiche und
normale Bindestriche ausdruecklich erlauben, sonst formuliert das Modell
"2019 bis 2021" oder zerlegt "E-Mail".

Ohne Netzwerk, ohne Provider. Ausfuehren:  python test_schreibstil.py
"""
import os
import tempfile

os.environ["AUTO_KI_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="enfal_stil_"), "test.db")

import app.database as db          # noqa: E402
db.ensure_tables()

from app.schreibstil import STILREGEL_GEDANKENSTRICHE   # noqa: E402

FEHLER = []


def check(name, cond):
    print(f"[{'OK' if cond else 'FEHLER'}] {name}")
    if not cond:
        FEHLER.append(name)


# Jede Oberflaeche, die deutschen Fliesstext fuer Nutzer erzeugt.
import app.kaufcheck as kaufcheck            # noqa: E402
import app.verkaufscheck as verkaufscheck    # noqa: E402
import app.inserat as inserat                # noqa: E402
import app.llm as llm                        # noqa: E402
import app.autofinder_enrich as enrich       # noqa: E402

OBERFLAECHEN = [
    ("KaufCheck-Bericht", kaufcheck._SYSTEM),
    ("VerkaufsCheck-Bericht", verkaufscheck._SYSTEM),
    ("Inserats-Optimierung", inserat._OPT_SYSTEM),
    ("KI-Chat", llm.SYSTEM_PROMPT),
    ("Rueckfragen zur Analyse", llm._ANALYSE_SYSTEM),
    ("AutoFinder-Begruendungen", enrich._SYSTEM_PROMPT),
]

for name, prompt in OBERFLAECHEN:
    check(f"{name}: traegt die zentrale Stilregel",
          STILREGEL_GEDANKENSTRICHE in prompt)
    check(f"{name}: kein unaufgeloester Platzhalter",
          "[[STILREGEL]]" not in prompt)

# Die Regel muss die erlaubten Faelle ausdruecklich nennen, sonst entfernt das
# Modell auch korrekte Typografie.
check("Regel erlaubt Zahlenbereiche ausdruecklich",
      "Zahlenbereiche" in STILREGEL_GEDANKENSTRICHE)
check("Regel schuetzt normale Bindestriche ausdruecklich",
      "Bindestriche" in STILREGEL_GEDANKENSTRICHE and "E-Mail" in STILREGEL_GEDANKENSTRICHE)
check("Regel nennt die Alternativen konkret (sonst entstehen Komma-Ketten)",
      all(w in STILREGEL_GEDANKENSTRICHE for w in ("Punkt", "Komma", "Doppelpunkt", "Klammern")))
check("Regel benennt beide Striche",
      "–" in STILREGEL_GEDANKENSTRICHE and "—" in STILREGEL_GEDANKENSTRICHE)

# Es darf keine zweite, abweichende Fassung mehr geben.
#
# Geprueft wird der CODE, nicht der Rohtext: app/schreibstil.py beschreibt in
# seinem Docstring genau diesen alten Wortlaut als Begruendung. Eine naive
# Textsuche wuerde ihn dort finden und eine Regelverletzung melden, die es
# nicht gibt.
import ast          # noqa: E402
import io           # noqa: E402
import pathlib      # noqa: E402


def code_ohne_doku(pfad) -> str:
    baum = ast.parse(io.open(pfad, encoding="utf-8").read())
    doku = {id(k.value) for k in ast.walk(baum)
            if isinstance(k, ast.Expr) and isinstance(k.value, ast.Constant)
            and isinstance(k.value.value, str)}
    return " ".join(
        k.value for k in ast.walk(baum)
        if isinstance(k, ast.Constant) and isinstance(k.value, str) and id(k) not in doku
    )


alt_formulierung = "Gedankenstriche sparsam"
treffer = [p.name for p in pathlib.Path("app").glob("*.py")
           if alt_formulierung in code_ohne_doku(p)]
check("keine alte, abweichende Stilregel mehr im Code", not treffer)


# ── Die Regel gilt auch fuer ENFALS EIGENE Texte ────────────────────────────
#
# LIVE-RUN-BEFUND: im echten Bericht stand "... Rechnungen zeigen lassen — auch
# die der eigenen Aufbereitung ...". Dieser Satz kam NICHT vom Modell, sondern
# aus einem deterministischen Katalogtext. Dieser Test prueft bisher nur, dass
# die Regel in den PROMPTS steht. Genau deshalb ist die Verletzung durchgerutscht:
# fuer ENFALs eigene Saetze gab es keine Pruefung.
#
# Geprueft werden die Module, deren Strings woertlich im Bericht landen.
# Bewusst NICHT alle: Logmeldungen, Verifikationsnotizen der Datenmodule und
# Prompt-Bausteine (die die Regel selbst zitieren muessen) sind keine
# Nutzertexte und wuerden den Test zu einem Rauschmelder machen.
import re   # noqa: E402

NUTZERTEXT_MODULE = (
    "pruefplan_basis.py",      # Basis-Checklisten
    "kaufaktionen.py",         # fahrzeugspezifische Aktionen + Ersatztexte
    "key_findings.py",         # "Das solltest du wissen"
    "empfehlung_gruende.py",   # "Warum diese Empfehlung?"
    "servicehistorie.py",      # kanonische Saetze
    "getriebe.py",
    "verkaeuferart.py",
    "fin_hinweis.py",
    "wartungsangabe.py",
    "rueckruf_konsistenz.py",
)

# Rhetorisch = von Leerzeichen umgeben. "2019–2021" und "E-Mail" bleiben erlaubt,
# ein alleinstehender Strich als Platzhalter ("—" fuer "kein Wert") ebenso.
RE_RHETORISCH = re.compile(r"\s[—–]\s")
# Logmeldungen erkennt man zuverlaessig am Formatplatzhalter.
RE_LOGZEILE = re.compile(r"%[sdrif]")


def nutzertexte(pfad):
    baum = ast.parse(io.open(pfad, encoding="utf-8").read())
    doku = {id(k.value) for k in ast.walk(baum)
            if isinstance(k, ast.Expr) and isinstance(k.value, ast.Constant)
            and isinstance(k.value.value, str)}
    for k in ast.walk(baum):
        if isinstance(k, ast.Constant) and isinstance(k.value, str) and id(k) not in doku:
            if not RE_LOGZEILE.search(k.value):
                yield k.lineno, k.value


stil_treffer = []
for name in NUTZERTEXT_MODULE:
    pfad = pathlib.Path("app") / name
    if not pfad.exists():
        stil_treffer.append(f"{name}: Modul fehlt")
        continue
    for lineno, text in nutzertexte(pfad):
        if RE_RHETORISCH.search(text):
            stil_treffer.append(f"{name}:{lineno} {text[:90]}")

check("keine rhetorischen Gedankenstriche in ENFALs eigenen Nutzertexten",
      not stil_treffer)
for t in stil_treffer[:6]:
    print("        ", t)

# Gegenprobe: der Scanner darf nicht einfach immer gruen sein.
check("der Scanner erkennt einen rhetorischen Gedankenstrich",
      bool(RE_RHETORISCH.search("Rechnungen zeigen lassen — auch die der Aufbereitung.")))
check("der Scanner laesst Zahlenbereiche in Ruhe",
      not RE_RHETORISCH.search("Bauzeitraum 2019–2023, Motor B48."))
check("der Scanner laesst normale Bindestriche in Ruhe",
      not RE_RHETORISCH.search("KBA-Referenz 10009 und E-Mail-Adresse."))

print()
if FEHLER:
    print(f"{len(FEHLER)} FEHLER:")
    for f in FEHLER:
        print("  -", f)
    raise SystemExit(1)
print("Alle Schreibstil-Tests bestanden.")
