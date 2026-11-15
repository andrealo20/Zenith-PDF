"""Print the UDID of the iPad simulator the CI uses.

    python3 ipad/tools/pick_simulator.py [devices.json]

Reads `xcrun simctl list devices available --json` (or the given file), takes the
newest iOS runtime first, and prefers iPad Air 13-inch (M4), then any iPad Air,
then any iPad. Exits 1 when there is no iPad simulator.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys

PREFERENCES = (
    lambda name: name == "iPad Air 13-inch (M4)",
    lambda name: name.startswith("iPad Air"),
    lambda name: name.startswith("iPad"),
)


def runtime_version(key: str) -> tuple[int, ...]:
    # com.apple.CoreSimulator.SimRuntime.iOS-26-5 -> (26, 5)
    match = re.search(r"iOS-([\d-]+)$", key)
    return tuple(int(part) for part in match.group(1).split("-") if part) if match else ()


def pick(listing: dict) -> str | None:
    runtimes = sorted((key for key in listing.get("devices", {}) if "iOS" in key), key=runtime_version, reverse=True)
    for wanted in PREFERENCES:
        for key in runtimes:
            for device in listing["devices"][key]:
                if device.get("isAvailable", True) and wanted(device.get("name", "")):
                    return device["udid"]
    return None


def main() -> None:
    if len(sys.argv) > 1:
        with open(sys.argv[1], encoding="utf-8") as handle:
            listing = json.load(handle)
    else:
        listing = json.loads(subprocess.run(["xcrun", "simctl", "list", "devices", "available", "--json"],
                                            check=True, capture_output=True, text=True).stdout)
    udid = pick(listing)
    if udid is None:
        sys.exit("no iPad simulator found; available runtimes: " + ", ".join(listing.get("devices", {})))
    print(udid)


if __name__ == "__main__":
    main()
