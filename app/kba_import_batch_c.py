from __future__ import annotations

"""
Batch C: Schliessen der durch den historischen Rueckruf-Import-Bug verlorenen,
fuer sich sicheren (Rueckruf, Baureihe)-Paare — GESCHLOSSENE Zielgenerationen.

HINTERGRUND
-----------
Das KaufCheck-Root-Cause-Closing (Befund M) hat zwei systemische Fehler der
alten Importlogik behoben:
  1. Die Entscheidung fiel PRO RUECKRUF statt pro (Rueckruf, Baureihe)-Paar
     (app/kba_import_kandidaten.py) — ein mehrdeutiges oder dubletten-
     verdaechtiges Ziel verwarf denselben Rueckruf auch bei allen anderen,
     fuer sich eindeutigen Zielen.
  2. Der Sicherheitsfilter erkannte Sicherheitsrelevanz nur an bestimmten
     Bauteilbegriffen, nicht an der im amtlichen Text genannten FOLGE
     (`_SICHERHEITSFOLGE`).
Beide Fehler haben SICHERE Paare verworfen, obwohl sie unter der jetzt
korrigierten, paarweisen Klassifikation SAFE_IMPORT waeren. Dieses Modul
schliesst GENAU diese Luecke — nichts sonst, keine neue Recherche.

EIN DRITTER FEHLER, BEIM REPRODUZIEREN GEFUNDEN
-------------------------------------------------
`_modelltokens()`/`_vira_modellkandidaten()` liefern `set[str]`. Wird
dieselbe Zielbaureihe ueber MEHRERE amtliche Modell-Token erreicht — z.B.
"audi-rs-3-sportback-8v" sowohl ueber den breiten Alias "A3" als auch ueber
den eigenen Token "RS3" (`MODELL_MAP[("AUDI","RS 3 SPORTBACK")]`) —, entschied
die zufaellige Set-Iterationsreihenfolge, je nach Prozessstart verschieden, ob
das Paar als SAFE_IMPORT oder als AMBIGUOUS_GENERATION galt: derselbe
Datenstand ergab je nach Lauf 927 bis 932 sichere Paare. Behoben in
`app/kba_import_kandidaten.py` — eine Baureihe gilt als eindeutig, wenn
WENIGSTENS EINER der sie erreichenden Token sie ALLEIN trifft; betroffen
waren 11 amtliche Datensaetze im Gesamtexport. Erst nach diesem Fix ist die
Kandidatenmenge ueberhaupt reproduzierbar genug fuer einen Import.

SCOPE DIESES BATCHES
----------------------
Nur Paare, die
  a) `verlorene_paare()` als fuer sich sicher, aber durch einen der beiden
     historischen Bugs verworfen ausweist, UND
  b) auf eine GESCHLOSSENE Zielgeneration zielen (`bauzeitraum_bis IS NOT
     NULL`).
Offene Zielgenerationen bleiben unangetastet: das darin liegende Restrisiko
(z.B. BMW iX3 G08, dokumentiert in app/kba_import_kandidaten.py) braucht eine
fachliche Pruefung gegen Herstellerquellen wie bei Batch B1 — das ist NICHT
Teil dieses Auftrags ("keine unnoetige externe Recherche"). Gemessen am
KBA-Gesamtexport vom 2026-08-27, nach dem Determinismus-Fix: 625 verlorene
sichere Paare, davon 339 mit geschlossener Zielgeneration (dieser Batch) und
286 mit offener Zielgeneration (unveraendert offen, siehe `offene_paare()`).

ZUSAETZLICHE TORE VOR DER MUTATION
------------------------------------
Dieselben vier wie bei Batch A (siehe app/kba_import_batch_a.py), hier auf
EBENE DES EINZELNEN PAARES angewandt statt auf den ganzen Rueckruf — ein
Rueckruf ueber "X5, X6" darf am X5-Paar nicht an einer X6-Besonderheit
scheitern:

C1  VERSTECKTE ZWEITE GENERATION FUER GENAU DIESES ZIEL
    Der Dry-Run-Klassifikator sieht nur Baureihen, die die Ueberdeckungs- und
    Ueberdehnungsregel bereits passiert haben. Eine Baureihe, die knapp
    darunter liegt, aber immer noch mindestens die HAELFTE der Ueberdeckung
    des Gewinners erreicht, verschwindet sonst lautlos aus der Betrachtung.

C2  VARIANTENBEDINGUNG IM FREITEXT
    Identisch zu Batch A / Gate A2: nur eine leere oder ausdruecklich
    verneinte Eingrenzung wird uebernommen.

C3  DUBLETTE — EXAKTER ABGLEICH GEGEN DEN AKTUELLEN BESTAND
    Identisch zu Batch A / Gate A3, zusaetzlich zur bereits im Dry-Run
    geprueften unscharfen Dublettenerkennung (Bauteilgruppe + Zeitraum +
    gemeinsame Begriffe).

C4  REFERENZFORMAT UND MARKENKOLLISION
    Identisch zu Batch A / Gate A4.

C5  PARALLELE AMTLICHE DATENSAETZE INNERHALB DES NEUEN BATCHES
    Identisch zu Batch A / Gate A5.

NUTZUNGSRECHTE UND DETERMINISMUS
-----------------------------------
Gleiche Quelle, gleiche Lizenz, gleicher Mechanismus wie Batch A (siehe dort).
IDs werden ab `ID_BASIS_C` fortlaufend vergeben, sortiert nach (normalisierte
Referenz, Baureihe) — reproduzierbar bei gleicher Eingabe.
"""

from app.kba_import_batch_a import (
    ALTERNATIV_ANTEIL, SAMMELSTEMPEL, _baujahre, _KEINE_EINGRENZUNG, _norm_text,
    _referenz_marken, ziel_index,
)
from app.kba_import_kandidaten import (
    MEDIAN_GENERATIONSDAUER, _ueberdeckung, import_kandidaten, verlorene_paare,
)
from app.kba_reconciliation import (
    _modelltokens, _ueberlappt, normalisiere_referenz,
)

# Oberhalb von Batch A (2001+), Batch B1 (3001+) und Mixed Target (4001+35).
ID_BASIS_C = 5001


def alternative_fuer_ziel(kand, idx: dict, ziel_id: str):
    """Gibt es fuer GENAU DIESES Ziel-Paar eine vom Dry-Run bereits
    ausgeschlossene, aber fast ebenso plausible ANDERE Baureihe (Gate C1)?

    Der Dry-Run-Klassifikator markiert eine Baureihe schon dann als
    "mehrdeutig", wenn ein ERREICHTES Ziel mehrfach vorkommt (siehe
    `kba_import_kandidaten.klassifiziere_kandidat`). Diese Funktion sucht
    zusaetzlich nach einer Baureihe, die NICHT erreicht wurde — durch die
    Ueberdeckungs- oder Ueberdehnungsregel ausgeschlossen —, aber immer noch
    eine relevante Ueberdeckung des amtlichen Fensters traegt. Baureihen, die
    der Kandidat selbst schon als Ziel fuehrt (`kand.ziel_ids`), zaehlen
    NICHT als Alternative: die Mehrdeutigkeit zwischen zwei akzeptierten
    Zielen behandelt bereits `mehrdeutige_ids` im Dry-Run selbst.
    """
    akzeptiert = set(kand.ziel_ids)
    bester = None
    for tok in _modelltokens(kand.modell):
        kandidaten_tok = idx.get((kand.marke.upper(), tok), [])
        b_ziel = next((b for b in kandidaten_tok if b["id"] == ziel_id), None)
        if b_ziel is None:
            continue
        u_ziel = _ueberdeckung(kand.prod_von, kand.prod_bis,
                               b_ziel.get("bauzeitraum_von"), b_ziel.get("bauzeitraum_bis"))
        for b2 in kandidaten_tok:
            if b2["id"] in akzeptiert:
                continue
            von, bis = b2.get("bauzeitraum_von"), b2.get("bauzeitraum_bis")
            if not _ueberlappt(von, bis, kand.prod_von, kand.prod_bis):
                continue
            if (bis is None and von and kand.prod_von
                    and kand.prod_von > von + MEDIAN_GENERATIONSDAUER):
                continue
            u2 = _ueberdeckung(kand.prod_von, kand.prod_bis, von, bis)
            if u2 >= u_ziel * ALTERNATIV_ANTEIL and (bester is None or u2 > bester[2]):
                bester = (b2["id"], tok, u2, u_ziel)
    return bester


def geschlossene_verlorene_paare(kba, recalls, baureihen):
    """`verlorene_paare()`, eingegrenzt auf GESCHLOSSENE Zielgenerationen.

    Rueckgabe: (paare, kand_je_referenz) — `paare` wie `verlorene_paare()`,
    `kand_je_referenz` fuer den Zugriff auf die vollen Kandidatenfelder in
    der finalen Pruefung.
    """
    bis_je_id = {b["id"]: b.get("bauzeitraum_bis") for b in baureihen}
    kand = import_kandidaten(kba, recalls, baureihen)
    kand_je_ref = {k.referenz: k for k in kand}
    alle = verlorene_paare(kand)
    geschlossen = [v for v in alle if bis_je_id.get(v["baureihe_id"]) is not None]
    offen = [v for v in alle if bis_je_id.get(v["baureihe_id"]) is None]
    return geschlossen, offen, kand_je_ref


def pruefe_batch_c(kba, recalls, baureihen):
    """Finale Vor-Mutations-Pruefung fuer die geschlossenen, verlorenen Paare.

    Rueckgabe: (zeilen, ausschluesse, offene_paare).
    `zeilen`: dicts mit den Spalten der Tabelle `rueckruf` plus
    `herstellercode`/`amtlicher_zeitraum`/`amtliches_datum` fuer den
    Verifikationsvermerk. `ausschluesse`: (referenz, baureihe_id, grund).
    `offene_paare`: unveraendert gelassene Paare mit offener Zielgeneration.
    """
    from app.recall_filter import kba_referenz_format_plausibel

    idx = ziel_index(baureihen)
    baureihen_je_id = {b["id"]: b for b in baureihen}
    ref_marken = _referenz_marken(recalls, baureihen)
    leer_normalisiert = {_norm_text(x).replace(" ", "") for x in _KEINE_EINGRENZUNG}
    mangel_je_baureihe: dict = {}
    ref_je_baureihe: dict = {}
    for r in recalls:
        mangel_je_baureihe.setdefault(r["baureihe_id"], set()).add(_norm_text(r["mangel"]))
        ref = normalisiere_referenz(r.get("kba_referenz"))
        if ref:
            ref_je_baureihe.setdefault(r["baureihe_id"], set()).add(ref)

    geschlossen, offen, kand_je_ref = geschlossene_verlorene_paare(kba, recalls, baureihen)

    zeilen, ausschluesse = [], []
    for v in sorted(geschlossen, key=lambda x: (normalisiere_referenz(x["referenz"]),
                                                x["baureihe_id"])):
        kand = kand_je_ref.get(v["referenz"])
        bid = v["baureihe_id"]
        kennung = (v["referenz"], bid)

        if kand is None or bid not in baureihen_je_id:
            ausschluesse.append((*kennung, "C0 Kandidat oder Baureihe nicht mehr auffindbar"))
            continue
        if kand.prod_von is None or kand.prod_bis is None or not kand.datum:
            ausschluesse.append((*kennung, "C0 Produktionszeitraum oder Datum fehlt"))
            continue

        alt = alternative_fuer_ziel(kand, idx, bid)
        if alt:
            aid, tok, u2, uw = alt
            ausschluesse.append((*kennung, f"C1 versteckte zweite Generation: {aid} "
                                           f"({tok}, {u2:.0%}) gegen Gewinner {uw:.0%}"))
            continue

        eingr = kand.eingrenzung.strip()
        if _norm_text(eingr).replace(" ", "") not in leer_normalisiert:
            ausschluesse.append((*kennung, f"C2 amtliche Eingrenzung nicht abbildbar: "
                                           f"{eingr[:70]!r}"))
            continue

        ref = kand.referenz.strip()
        if not kba_referenz_format_plausibel(ref):
            ausschluesse.append((*kennung, f"C4 Referenzformat unplausibel: {ref!r}"))
            continue
        fremde = ref_marken.get(normalisiere_referenz(ref), set()) - {kand.marke.upper()}
        if fremde:
            ausschluesse.append((*kennung, f"C4 Referenz steht im Bestand bereits bei "
                                           f"{sorted(fremde)}"))
            continue

        if (_norm_text(kand.mangel) in mangel_je_baureihe.get(bid, set())
                or normalisiere_referenz(ref) in ref_je_baureihe.get(bid, set())):
            ausschluesse.append((*kennung, "C3 Dublette gegen den aktuellen Bestand"))
            continue

        code = kand.herstellercode.strip()
        datum = None if kand.datum.startswith(SAMMELSTEMPEL) else kand.datum
        zeilen.append({
            "baureihe_id": bid,
            "datum": datum,
            "betroffene_baujahre": _baujahre(kand, baureihen_je_id[bid]),
            "mangel": kand.mangel,
            "abhilfe": kand.massnahme or None,
            "kba_referenz": ref,
            "herstellercode": "" if code.upper() in {"", "N/A"} else code,
            "amtlicher_zeitraum": f"{kand.prod_von}-{kand.prod_bis}",
            "amtliches_datum": kand.datum,
            "amtliche_modelle": kand.modell,
            "verlust_grund": v["grund"],
        })

    zeilen.sort(key=lambda z: (normalisiere_referenz(z["kba_referenz"]), z["baureihe_id"]))

    # ── C5: PARALLELE AMTLICHE DATENSAETZE (identisch zu Batch A / A5) ──────
    gesehen: dict = {}
    behalten = []
    for z in zeilen:
        schluessel = (z["baureihe_id"], _norm_text(z["mangel"]),
                      z["betroffene_baujahre"], z["datum"])
        if schluessel in gesehen:
            ausschluesse.append((
                z["kba_referenz"], z["baureihe_id"],
                f"C5 paralleler amtlicher Datensatz zu KBA {gesehen[schluessel]} — "
                f"wortgleich, gleicher Zeitraum, gleiches Datum"))
            continue
        gesehen[schluessel] = z["kba_referenz"]
        behalten.append(z)

    for i, z in enumerate(behalten):
        z["id"] = ID_BASIS_C + i
    return behalten, ausschluesse, offen
