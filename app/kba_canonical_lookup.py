from __future__ import annotations

"""
RC-W6 — Canonical-only KBA-Lookup: die EINE Einstiegsfunktion, die der
KaufCheck-Pfad aufruft, wenn keine ENFAL-Baureihe vorliegt.

ABLAUF (Abschnitt 7 des Auftrags)
----------------------------------
 1. Trust-Gate (`app.kba_canonical_trust.kba_lookup_vertrauen`) — NUR
    `KBA_LOOKUP_ALLOWED` liest ueberhaupt etwas. `UNCERTAIN`/`DENIED`
    liefern `[]` an den Aufrufer (kein user-sichtbarer Insight), der
    Vertrauensstatus selbst bleibt aber im Rueckgabewert sichtbar fuer
    Diagnose/Logging.
 2. Canonical make/nameplate aus der IDENTITAET, mit der BESTEHENDEN
    Canonicalisierung (`app.kba_reconciliation.kba_marke`/
    `_vira_modellkandidaten`) — keine zweite Canonicalisierung.
 3. Lesen der canonical-only Kandidaten (`app.database.
    get_rueckrufe_canonical_fuer_identity`), bereits zeitlich gefiltert
    und durch dieselbe Verifikations-/Sperr-Pipeline wie jeder
    baureihe-gebundene Rueckruf gelaufen.
 4. KEINE zweite Applicability hier — `rueckruf_scope()`
   (app/recall_filter.py) entscheidet ueber Varianten-Zugehoerigkeit
   GENAUSO wie fuer jeden baureihe-gebundenen Rueckruf, erst in
   `app/evidence.py::build_insights()` (derselbe Aufrufer, dieselbe Stelle).
"""

from app.kba_canonical_trust import KBA_LOOKUP_ALLOWED, kba_lookup_vertrauen


def get_rueckrufe_fuer_identity(identity) -> tuple[list[dict], str]:
    """Rueckgabe: (canonical-only Rueckruf-Zeilen, Trust-Status).

    Die Zeilen sind IMMER `[]`, wenn der Trust-Status nicht
    `KBA_LOOKUP_ALLOWED` ist — der Status selbst wird trotzdem
    zurueckgegeben, damit der Aufrufer (Diagnose, `technical_coverage`,
    kuenftiges Logging) zwischen "kein Treffer" und "Identitaet zu
    unsicher fuer einen Versuch" unterscheiden kann, OHNE dass daraus
    je ein user-sichtbarer Unterschied im Bericht wird."""
    vertrauen = kba_lookup_vertrauen(identity)
    if vertrauen != KBA_LOOKUP_ALLOWED:
        return [], vertrauen

    from app.database import get_rueckrufe_canonical_fuer_identity
    from app.kba_reconciliation import _falte_modell, _vira_modellkandidaten, kba_marke

    canonical_make = kba_marke(identity.make)
    nameplate_kandidaten = set(_vira_modellkandidaten(identity.make, identity.model))
    # Root-Cause-Fix (RC-W6 B4): der amtliche KBA-Export schreibt Modellnamen
    # durchgaengig mit Bindestrich ("MX-5", "T-ROC", "C-KLASSE") -- `_falte_modell`
    # erhaelt genau deshalb den Bindestrich (s. dessen Docstring). Nutzer-/Web-
    # Freitext schreibt dieselbe Bezeichnung oft mit Leerzeichen ("MX 5", "T ROC").
    # Fuer eine katalogisierte Baureihe faengt die vorherige DB-Reconciliation
    # (Import-Kuratierung + `find_baureihe`) diesen Unterschied bereits ab; der
    # canonical-only Pfad hat keine solche Zwischenstufe und vergleicht den rohen
    # Identitaetstext zum ersten Mal direkt gegen den amtlichen Token. Ohne diese
    # Ergaenzung lieferte "MX 5" (identisch bestaetigte Identitaet, Trust=ALLOWED)
    # still `[]`, obwohl "MX-5" exakt in `rueckruf.canonical_nameplate` steht.
    # Generisch fuer jede Marke/jedes Modell, keine Sonderliste.
    gefaltet = _falte_modell(identity.model).upper()
    if gefaltet:
        nameplate_kandidaten.add(gefaltet.replace(" ", "-"))
        nameplate_kandidaten.add(gefaltet.replace("-", " "))
    zeilen = get_rueckrufe_canonical_fuer_identity(canonical_make, nameplate_kandidaten,
                                                   identity.year)
    return zeilen, vertrauen
