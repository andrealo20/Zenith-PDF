"""Failing tests of one xcresult as plain text.

    python3 ipad/tools/test_summary.py <tests.json>

Reads the JSON of `xcrun xcresulttool get test-results tests --path X.xcresult` and
prints one line per failed test with its failure messages, or "all passed".
"""

from __future__ import annotations

import json
import sys


def walk(node: dict, trail: list[str], counts: dict[str, int], failures: list[str]) -> None:
    kind = node.get("nodeType", "")
    name = node.get("name", "")
    children = node.get("children", [])
    if kind == "Test Case":
        result = node.get("result", "")
        counts[result] = counts.get(result, 0) + 1
        if result == "Failed":
            messages = [child.get("name", "") for child in children if child.get("nodeType") == "Failure Message"]
            failures.append(f"FAILED {'/'.join(trail + [name])}: {' | '.join(messages) or 'no message'}")
        return
    if kind in ("Unit test bundle", "UI test bundle", "Test Suite"):
        trail = trail + [name]
    for child in children:
        walk(child, trail, counts, failures)


def summarize(data: dict) -> list[str]:
    counts: dict[str, int] = {}
    failures: list[str] = []
    for node in data.get("testNodes", []):
        walk(node, [], counts, failures)
    total = ", ".join(f"{n} {result.lower()}" for result, n in sorted(counts.items())) or "no tests ran"
    if not counts:
        return [total]
    return [total] + (failures or ["all passed"])


def main() -> None:
    with open(sys.argv[1], encoding="utf-8") as handle:
        data = json.load(handle)
    print("\n".join(summarize(data)))


if __name__ == "__main__":
    main()
