"""Envoie tous les scénarios de tests/scenarios vers POST /simulate/transaction."""
import json
import os
import sys
from pathlib import Path

import httpx
import os as _os

# Si la démo est protégée par ADMIN_TOKEN : export SENTINEL_ADMIN_TOKEN=...
ADMIN_HEADERS = {"X-Admin-Token": _os.getenv("SENTINEL_ADMIN_TOKEN", "")} if _os.getenv("SENTINEL_ADMIN_TOKEN") else {}

BASE_URL = os.getenv("SENTINEL_URL", "http://127.0.0.1:8000")
SCENARIOS = Path(__file__).resolve().parent.parent / "tests" / "scenarios"


def main() -> int:
    files = sorted(SCENARIOS.glob("*.json"))
    if not files:
        print(f"Aucun scénario dans {SCENARIOS}")
        return 1
    with httpx.Client(timeout=60, headers=ADMIN_HEADERS) as client:
        for f in files:
            payload = json.loads(f.read_text())
            print(f"\n=== {f.name} ===")
            try:
                r = client.post(f"{BASE_URL}/simulate/transaction", json=payload)
            except httpx.HTTPError as exc:
                print(f"Erreur réseau: {exc}")
                return 1
            print(f"HTTP {r.status_code}")
            print(json.dumps(r.json(), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
