"""List every model currently available on NVIDIA NIM.
Uses the public /v1/models endpoint, which does not require an API key.
Run:  py -m scripts.nvidia_models
"""

from __future__ import annotations
import json
import urllib.request


def main() -> None:
    url = "https://integrate.api.nvidia.com/v1/models"
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=15.0) as response:
            payload = json.loads(response.read())
    except Exception as exc:
        print(f"Failed to query NVIDIA catalog: {exc}")
        return

    models = payload.get("data", [])
    print(f"Found {len(models)} models on NVIDIA NIM:\n")

    # Group by the part before the slash so families are easy to scan.
    by_owner: dict[str, list[str]] = {}
    for entry in models:
        model_id = entry.get("id", "")
        if "/" not in model_id:
            continue
        owner, name = model_id.split("/", 1)
        by_owner.setdefault(owner, []).append(name)

    for owner in sorted(by_owner):
        print(f"  {owner}/")
        for name in sorted(by_owner[owner]):
            print(f"    {name}")
        print()


if __name__ == "__main__":
    main()

# py -m scripts.nvidia_models