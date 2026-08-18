"""
Dauerhafte Ablage von Diagnose-Rohdaten (INTERNES WERKZEUG).

Warum es das gibt
-----------------
Der letzte BMW-320d-Audit konnte die verbleibenden Beobachtungen nicht endgültig
belegen: Tavilys Antworten liegen ausschließlich im Prozess-Cache von
`app.web_search` (Dict, 300 s TTL). Mit dem Ende des Diagnoseprozesses ist der
`raw_content` weg — und damit die einzige Grundlage, um Kartengrenzen, Detail-Links
und Anzeigen-IDs manuell nachzuprüfen. Dieses Modul schreibt einen Diagnoselauf
deshalb OPTIONAL vollständig als JSON auf die Platte.

Abgrenzung (bewusst eng)
------------------------
- NUR für manuelle Diagnoseläufe aus `scripts/`. Die Kauf-/Verkaufscheck-Pipeline
  ruft nichts davon auf und bleibt unverändert.
- KEIN Schreiben in die Kundendatenbank — ausschließlich lose JSON-Dateien in einem
  eigenen, per .gitignore ausgeschlossenen Ordner.
- KEINE Secrets: gespeichert werden nur Query, URL, Titel, Snippet und Seitentext.
  Der Tavily-Request-Body (mit API-Key) wird nie durchgereicht; zusätzlich läuft
  jeder Textwert durch `_ohne_secrets()` als Sicherheitsnetz.
- Die Dateien enthalten fremde Web-/Fahrzeugdaten und gehören NICHT ins Git.

Kartensegmentierung
-------------------
Seit `app/market_card_segmenter.py` wird JE KARTE festgehalten, wie sie abgegrenzt
wurde: `segmentation_method` ("detail_link" | "block_structure" | "title_anchor" |
"single_card" | "window_fallback"), `structural_confidence`, `start_offset`,
`end_offset` und `window_fallback_used`. Damit ist beim manuellen Prüfen sofort
unterscheidbar, ob eine Beobachtung aus einer echten strukturellen Karte stammt
oder nur aus einem Zeichenfenster (dann höchstens "bedingt", §1/§5).

Der Kopf der Datei nennt zusätzlich, ob der Lauf INSGESAMT strukturell segmentiert
werden konnte — `segmentierung.strukturell` ist true, sobald mindestens eine Karte
strukturell abgegrenzt wurde, und false, wenn alles im Fallback landete.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

# Ordner liegt im Repo-Wurzelverzeichnis und ist in .gitignore ausgeschlossen.
DIAGNOSE_ORDNER = Path(__file__).resolve().parent.parent / "diagnose_runs"

# Erlaeuterung der Segmentierung — wandert in jede Datei, damit spaeter
# nachvollziehbar bleibt, wie die Karten zustande kamen.
SEGMENTIERUNG_HINWEIS = (
    "Karten werden von app/market_card_segmenter.py abgegrenzt. Verfahren nach "
    "Prioritaet: detail_link (eigener Inserats-Detail-Link je Karte), "
    "block_structure (wiederholte Listen-/Blockstruktur), title_anchor "
    "(wiederkehrende Fahrzeugtitel), single_card (Seite mit genau einem Angebot). "
    "Ist keines nachweisbar, faellt die Extraktion auf das alte Zeichenfenster um "
    "den Preis zurueck (window_fallback) — solche Punkte sind hoechstens 'bedingt' "
    "und tragen die Preisstatistik nie als hochwertiger Vergleich."
)

# Sicherheitsnetz gegen versehentlich mitgeschriebene Zugangsdaten. Greift auf JEDEN
# gespeicherten Textwert — auch auf Seiteninhalte, die theoretisch fremde Schlüssel
# enthalten koennten.
_SECRET_MUSTER = (
    re.compile(r"tvly-[A-Za-z0-9_\-]{8,}"),                      # Tavily
    re.compile(r"AIza[A-Za-z0-9_\-]{20,}"),                      # Google/Gemini
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),                       # OpenAI-artig
    re.compile(r"(?i)\b(?:api[-_ ]?key|authorization|bearer)\b\s*[:=]\s*\S+"),
)


def _ohne_secrets(wert):
    """Ersetzt schlüsselartige Zeichenketten in Strings (rekursiv über Listen/Dicts)."""
    if isinstance(wert, str):
        for rx in _SECRET_MUSTER:
            wert = rx.sub("[REDACTED]", wert)
        return wert
    if isinstance(wert, dict):
        return {k: _ohne_secrets(v) for k, v in wert.items()}
    if isinstance(wert, list):
        return [_ohne_secrets(v) for v in wert]
    return wert


def _domain(url: str) -> str:
    try:
        netloc = urlparse(url or "").netloc.lower()
    except Exception:
        return ""
    return netloc.removeprefix("www.")


def _jetzt() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class DiagnoseRecorder:
    """Sammelt Suchergebnisse und daraus extrahierte Fahrzeugkarten eines Laufs.

    Nutzung im Diagnoseskript::

        rec = DiagnoseRecorder("bmw", lauf=1)
        rec.merke_suche(query="…", stufe="3", results=[…])   # je Tavily-Aufruf
        rec.merke_karte(result_url, card_index, card_text, beobachtung, status)
        pfad = rec.schreibe()

    `schreibe()` ist der einzige Schritt, der etwas auf die Platte legt — ohne
    Aufruf bleibt der Recorder folgenlos.
    """

    def __init__(self, testcase: str, lauf: int = 1, *, eingabe: dict | None = None,
                 ordner: Path | str | None = None):
        self.testcase = testcase
        self.lauf = lauf
        self.eingabe = eingabe or {}
        self.ordner = Path(ordner) if ordner else DIAGNOSE_ORDNER
        self.gestartet = _jetzt()
        self.suchergebnisse: list[dict] = []
        self.karten: list[dict] = []
        self.zusammenfassung: dict = {}
        # URLs, die schon einmal gespeichert wurden — dieselbe Seite taucht über
        # mehrere Query-Stufen hinweg auf, soll die Datei aber nicht aufblähen.
        self._gesehene_urls: set[str] = set()

    # ── Suchergebnisse ───────────────────────────────────────────────────────

    def merke_suche(self, query: str, stufe: str, results: list[dict]) -> None:
        """Speichert die rohen Treffer EINER Query-Stufe (inkl. raw_content)."""
        zeit = _jetzt()
        for r in results or []:
            url = r.get("url", "") or ""
            if url in self._gesehene_urls:
                continue
            self._gesehene_urls.add(url)
            self.suchergebnisse.append(_ohne_secrets({
                "timestamp": zeit,
                "testcase": self.testcase,
                "lauf": self.lauf,
                "query": query,
                "query_stage": stufe,
                "url": url,
                "domain": _domain(url),
                "title": r.get("title", "") or "",
                "content": r.get("content", "") or "",
                "raw_content": r.get("raw_content") or "",
            }))

    # ── Fahrzeugkarten ───────────────────────────────────────────────────────

    def merke_karte(self, source_result_url: str, card_index: int, card_text: str,
                    beobachtung, acceptance_status: str, card_hash: str | None = None) -> None:
        """Speichert EINE aus einem Treffer extrahierte Fahrzeugkarte.

        `beobachtung` ist eine bereits bewertete `models.Preisbeobachtung`;
        `card_text` der isolierte Kartentext, aus dem sie stammt.
        """
        b = beobachtung
        self.karten.append(_ohne_secrets({
            "source_result_url": source_result_url,
            "card_index": card_index,
            "card_text": card_text,
            "listing_id": b.listing_id,
            "detail_url": b.detail_url,
            "listing_key": b.listing_key,
            "card_hash": card_hash,
            "make": b.make,
            "model": b.model,
            "generation": b.generation,
            # §7: Woher der Generationscode stammt und — bei Ableitung — warum.
            "generation_evidence": b.generation_evidence,
            "generation_inference_reason": b.generation_inference_reason,
            "body": b.body,
            "body_evidence": b.body_evidence,
            "fuel": b.fuel,
            "engine_variant": b.engine_variant,
            "horsepower": b.horsepower,
            "transmission": b.transmission,
            "year": b.baujahr,
            "mileage": b.kilometerstand,
            "price": b.preis_eur,
            "similarity": b.similarity,
            "similarity_stufe": b.vergleichbarkeit,
            "extraction_source": b.extraction_source,
            "source_type": b.source_type,
            "acceptance_status": acceptance_status,
            "acceptance_reason": b.acceptance_reason,
            # ── Kartensegmentierung (§7) ─────────────────────────────────────
            # Damit beim späteren Live-Test auf einen Blick unterscheidbar ist:
            # "diese Karten stammen aus echten strukturellen Cards" vs. "diese
            # stammen nur aus unsicheren Textfenstern".
            "segmentation_method": b.segmentation_method,
            "structural_confidence": b.structural_confidence,
            "start_offset": b.start_offset,
            "end_offset": b.end_offset,
            "window_fallback_used": b.window_fallback_used,
            # Pro Karte wiederholt, damit ein einzeln herausgegriffener Datensatz
            # nicht ohne diesen Vorbehalt gelesen wird.
            "segmentierung_strukturell": not b.window_fallback_used,
        }))

    def merke_zusammenfassung(self, **werte) -> None:
        self.zusammenfassung.update(_ohne_secrets(werte))

    # ── Schreiben ────────────────────────────────────────────────────────────

    def dateiname(self) -> str:
        stempel = self.gestartet.replace(":", "").replace("-", "").replace("+0000", "Z")
        return f"{stempel}_{self.testcase}_lauf{self.lauf}.json"

    def segmentierung(self) -> dict:
        """Kopfangabe: wie wurden die Karten dieses Laufs abgegrenzt?

        Zwei GETRENNTE Zählungen, weil sie unterschiedliche Fragen beantworten:

          - `methoden_alle` zählt jede aufgezeichnete Fundstelle, also auch
            verworfene und mehrfach gefundene. Das ist der vollständige Audit-Trail.
          - `methoden_verwendet` zählt ausschließlich die Karten, die am Ende
            tatsächlich in Median/Quartile eingegangen sind (acceptance_status ==
            "verwendet"). NUR diese Zahlen dürfen mit der finalen Marktanalyse
            verglichen werden.

        Der Unterschied ist kein Schönheitsfehler: dieselbe Anzeige wird über
        mehrere Rechercheseiten gefunden, `analysiere_markt` führt sie zu EINER
        Beobachtung zusammen. Würde der Kopf beide Zahlen vermischen, sähe ein Lauf
        strukturell besser aus, als er ist.
        """
        def _zaehle(karten: list[dict]) -> dict[str, int]:
            out: dict[str, int] = {}
            for k in karten:
                m = k.get("segmentation_method", "unbekannt")
                out[m] = out.get(m, 0) + 1
            return out

        verwendet = [k for k in self.karten if k.get("acceptance_status") == "verwendet"]
        return {
            # "strukturell" bezieht sich bewusst auf die VERWENDETEN Karten — die
            # Frage lautet: trägt das Ergebnis echte Cards oder nur Textfenster?
            "strukturell": any(not k.get("window_fallback_used", True) for k in verwendet),
            "methoden_alle": _zaehle(self.karten),
            "methoden_verwendet": _zaehle(verwendet),
            "karten_gesamt": len(self.karten),
            "verwendet_gesamt": len(verwendet),
            "verwendet_strukturell": sum(1 for k in verwendet
                                         if not k.get("window_fallback_used", True)),
            "verwendet_window_fallback": sum(1 for k in verwendet
                                             if k.get("window_fallback_used", True)),
            "hinweis": SEGMENTIERUNG_HINWEIS,
        }

    def als_dict(self) -> dict:
        return {
            "schema": "vira-diagnose/2",
            "testcase": self.testcase,
            "lauf": self.lauf,
            "gestartet": self.gestartet,
            "geschrieben": _jetzt(),
            "eingabe": _ohne_secrets(self.eingabe),
            "segmentierung": self.segmentierung(),
            "zusammenfassung": self.zusammenfassung,
            "anzahl_suchergebnisse": len(self.suchergebnisse),
            "anzahl_karten": len(self.karten),
            "suchergebnisse": self.suchergebnisse,
            "karten": self.karten,
        }

    def schreibe(self) -> Path:
        """Schreibt den Lauf als JSON und gibt den Pfad zurück."""
        self.ordner.mkdir(parents=True, exist_ok=True)
        pfad = self.ordner / self.dateiname()
        pfad.write_text(json.dumps(self.als_dict(), ensure_ascii=False, indent=2),
                        encoding="utf-8")
        return pfad
