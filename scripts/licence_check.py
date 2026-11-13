"""Fail if any installed Python distribution declares an AGPL or GPL licence (LGPL is fine)."""

import re
import sys
from importlib.metadata import distributions

BANNED = re.compile(
    r"AGPL|AFFERO|(?<!L)GPL|(?<!LESSER )(?<!LIBRARY )GENERAL PUBLIC", re.IGNORECASE)


def is_banned(text: str) -> bool:
    return bool(BANNED.search(text))


def declared_licences(dist) -> list[str]:
    md = dist.metadata
    found = [v for k in ("License-Expression", "License") if (v := md.get(k)) and len(v) < 120]
    found += [c for c in md.get_all("Classifier", []) if c.startswith("License ::")]
    return found


def main() -> int:
    bad = [(d.metadata["Name"], lic) for d in distributions()
           for lic in declared_licences(d) if is_banned(lic)]
    for name, lic in sorted(bad):
        print(f"BANNED LICENCE: {name}: {lic}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
