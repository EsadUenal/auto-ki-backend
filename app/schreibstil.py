"""
Gemeinsame Schreibstil-Regel fuer alle generierten Nutzertexte.

WARUM ZENTRAL
-------------
KaufCheck, VerkaufsCheck, Inserats-Optimierung, KI-Chat und die
AutoFinder-Begruendungen schreiben alle deutschen Fliesstext fuer denselben
Nutzer. Stilregeln, die in fuenf Prompts einzeln formuliert sind, driften
auseinander: im KaufCheck stand bereits "Gedankenstriche sparsam", anderswo gar
nichts. Eine Konstante haelt die Regel an einer Stelle und macht sichtbar,
wenn eine Oberflaeche sie nicht verwendet.

WARUM PROMPT UND NICHT STRING-REPLACE
--------------------------------------
Der lange Gedankenstrich laesst sich nicht zuverlaessig nachtraeglich
ersetzen, ohne Text zu beschaedigen: derselbe Strich trennt mal einen
Einschub (dort passt ein Komma), mal zwei Saetze (dort passt ein Punkt), und in
einem Bereich wie "2019-2021" oder "15-30 Sekunden" ist er schlicht korrekte
deutsche Typografie. Ein pauschales Ersetzen wuerde aus "12.000 - 22.000 EUR"
einen Bindestrich machen und aus einem Einschub einen Satzbruch. Die Regel
gehoert deshalb in die Erzeugung, nicht in eine Nachbearbeitung.
"""
from __future__ import annotations

# Genau eine Zeile, damit sie sich in jede vorhandene STIL-Liste einfuegt, ohne
# deren Aufbau zu veraendern. Die Beispiele sind Absicht: die blosse Anweisung
# "keine Gedankenstriche" fuehrt sonst zu Saetzen, die mit Komma
# aneinandergehaengt werden, statt zu richtigem Deutsch.
STILREGEL_GEDANKENSTRICHE = (
    "Schreibe natürliches Deutsch und verwende KEINE langen Gedankenstriche "
    "(– oder —) als Stilmittel. Nutze stattdessen das Satzzeichen, das an der "
    "Stelle grammatisch richtig ist: Punkt für zwei eigenständige Aussagen, "
    "Komma oder Klammern für einen Einschub, Doppelpunkt vor einer Aufzählung "
    "oder Erklärung. Variiere dabei, statt jeden Satz nach demselben Muster zu "
    "bauen. Normale Bindestriche in Wörtern (E-Mail, KI-Chat, 2.0-TDI) bleiben "
    "selbstverständlich erhalten, ebenso Zahlenbereiche wie 2019-2021."
)
