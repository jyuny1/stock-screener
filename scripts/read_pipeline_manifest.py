#!/usr/bin/env python3
"""Read a field from a pipeline-run-manifest artifact entry.

This tiny helper keeps GitHub Actions shell snippets simple and avoids jq
availability assumptions. It intentionally has no project dependencies.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _lookup(payload: dict[str, Any], key: str, field: str) -> Any:
    artifacts = payload.get("artifacts") or {}
    entry = artifacts.get(key)
    if not isinstance(entry, dict):
        raise SystemExit(f"artifact key not found in pipeline manifest: {key}")
    current: Any = entry
    for part in field.split("."):
        if not isinstance(current, dict) or part not in current:
            raise SystemExit(f"field not found for {key}: {field}")
        current = current[part]
    return current


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("key")
    parser.add_argument("field")
    args = parser.parse_args()

    payload = json.loads(args.manifest.read_text(encoding="utf-8"))
    value = _lookup(payload, args.key, args.field)
    if isinstance(value, (dict, list)):
        print(json.dumps(value, sort_keys=True))
    else:
        print(value)


if __name__ == "__main__":
    main()
