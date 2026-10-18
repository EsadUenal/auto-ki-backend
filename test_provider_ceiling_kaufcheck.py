"""
Adaptive-Evidence-Research (Folgeauftrag §1): Hard Ceiling für
"kaufcheck"/"tavily" wurde von 16 auf 22 angehoben -- Grundlage ist die
Worst-Case-Modellierung des adaptiven Mehrrunden-Pfads (app/technical_research.py:
Identity-Runde-1 2 + Identity-Spec-Gap 2 + Rückruf 2 + Technik-Runde-1 4 +
Technik-Runde-2 bis 4 + Technik-Runde-3 bis 4 = 18) PLUS Marktpreis (bis 3,
kaskadierend, app/kaufcheck.py) = 21 + 1 Sicherheitspuffer = 22.

KEIN Netzwerk -- reiner Unit-Test gegen app.provider_control (claim_call/
ProviderCallLimitExceeded/provider_scope), 0 Live Calls.

    python test_provider_ceiling_kaufcheck.py
"""
import asyncio

from app.config import PROVIDER_FEATURE_LIMITS
from app.provider_control import ProviderCallLimitExceeded, claim_call, provider_scope

_FEHLER: list[str] = []


def check(name: str, bedingung: bool, detail: str = "") -> None:
    print(f"[{'OK  ' if bedingung else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))
    if not bedingung:
        _FEHLER.append(name)


print("=== 1) Konfiguriertes Ceiling ist 22 (Default, kein Env-Override in diesem Lauf) ===")
check("1a PROVIDER_FEATURE_LIMITS['kaufcheck']['tavily'] == 22",
      PROVIDER_FEATURE_LIMITS["kaufcheck"]["tavily"] == 22,
      str(PROVIDER_FEATURE_LIMITS["kaufcheck"]))
check("1b andere Feature-Limits UNVERAENDERT (§3 Auftrag: keine anderen Providerlimits)",
      PROVIDER_FEATURE_LIMITS["kaufcheck"]["gemini"] == 4
      and PROVIDER_FEATURE_LIMITS["chat"]["tavily"] == 9
      and PROVIDER_FEATURE_LIMITS["autofinder"]["tavily"] == 6
      and PROVIDER_FEATURE_LIMITS["verkaufscheck"]["tavily"] == 16,
      str(PROVIDER_FEATURE_LIMITS))


async def _innerhalb_scope():
    async with provider_scope("kaufcheck", key="test-ceiling") as scope:
        ergebnisse = []
        for i in range(22):
            feature, used, limit = claim_call("tavily")
            ergebnisse.append((feature, used, limit))
        # Call Nr. 23 -- ueber dem Ceiling.
        exception_bei_23 = None
        try:
            claim_call("tavily")
        except ProviderCallLimitExceeded as exc:
            exception_bei_23 = exc
        return scope, ergebnisse, exception_bei_23


async def main():
    scope, ergebnisse, exc23 = await _innerhalb_scope()

    print("\n=== 2) Call 1..22 werden erlaubt (<=22 erlaubt) ===")
    check("2a genau 22 Calls erfolgreich gezaehlt", len(ergebnisse) == 22, str(len(ergebnisse)))
    check("2b used/limit korrekt bis zum Ceiling (letzter Call: used=22, limit=22)",
          ergebnisse[-1] == ("kaufcheck", 22, 22), str(ergebnisse[-1]))
    check("2c scope.calls['tavily'] steht nach 22 Calls auf 22", scope.calls["tavily"] == 22)

    print("\n=== 3) Call 23 wird sauber blockiert (>22 blockiert) ===")
    check("3a ProviderCallLimitExceeded wurde bei Call 23 ausgeloest",
          isinstance(exc23, ProviderCallLimitExceeded), repr(exc23))
    check("3b Fehlermeldung nennt Feature und Provider, keine internen Details/Secrets",
          exc23 is not None and "kaufcheck" in str(exc23) and "tavily" in str(exc23)
          and "key" not in str(exc23).lower() and "token" not in str(exc23).lower())
    check("3c scope.calls['tavily'] bleibt bei 22 (Call 23 wurde NICHT mitgezaehlt)",
          scope.calls["tavily"] == 22)

    print("\n=== 4) ProviderCallLimitExceeded bleibt eine normale, fangbare Exception "
          "(kein Sonderverhalten eingefuehrt) ===")
    check("4a ist eine RuntimeError-Subklasse (unveraendert)",
          issubclass(ProviderCallLimitExceeded, RuntimeError))


asyncio.run(main())

print()
if _FEHLER:
    print(f"{len(_FEHLER)} FEHLER:")
    for f in _FEHLER:
        print("  -", f)
    raise SystemExit(1)
print("ALLE PROVIDER-CEILING-KAUFCHECK-TESTS GRUEN")
