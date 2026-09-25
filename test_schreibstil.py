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

print()
if FEHLER:
    print(f"{len(FEHLER)} FEHLER:")
    for f in FEHLER:
        print("  -", f)
    raise SystemExit(1)
print("Alle Schreibstil-Tests bestanden.")
