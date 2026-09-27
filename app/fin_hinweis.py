from __future__ import annotations

"""
Wo eine individuelle FIN-Abfrage tatsächlich möglich ist — EINE Quelle.

BEFUND AUS DEM ECHTEN PRODUCTION-RUN (BMW 330i G20)
---------------------------------------------------
Der Bericht forderte mehrfach, die Betroffenheit "per FIN beim Hersteller oder
KBA" zu prüfen. Das KBA ist die Quelle des RÜCKRUFDATENSATZES — daraus folgt
aber nicht, dass es für diesen Weg eine individuelle FIN-Auskunft an den
Fahrzeugkäufer gibt. ENFAL hat das nie geprüft und darf es deshalb nicht
zusichern: ein Nutzer, der es dort versucht, läuft ins Leere.

REGEL
-----
  Individuelle Betroffenheit  ->  Hersteller bzw. eine Vertragswerkstatt der
                                  Marke. Das ist der Weg, den ENFAL belegen kann.
  Herkunft des Datensatzes    ->  KBA. Die Referenz ("KBA-Referenz 10009") wird
                                  weiterhin genannt, sie ist amtlich und belegt.

Das KBA verschwindet also NICHT aus dem Bericht. Es hört nur auf, als Adresse
für eine Einzelabfrage aufzutreten.

Kein Herstellersonderfall: "einer Vertragswerkstatt der Marke" funktioniert für
BMW, VW, Opel und jede andere Marke gleichermaßen, ohne dass irgendwo ein
Markenname hartkodiert wird.
"""

# Der kanonische Satz. Punkt am Ende, damit er sich an einen Vorsatz anhängen
# lässt, ohne dass an sieben Stellen eine eigene Interpunktion entsteht.
HINWEIS_FIN = ("Betroffenheit anhand der FIN beim Hersteller oder einer "
               "Vertragswerkstatt der Marke prüfen lassen.")

# Kurzform für Stellen, an denen der Satz mitten in einer Aufzählung steht.
STELLE_FIN = "beim Hersteller oder einer Vertragswerkstatt der Marke"
