"""One locator-to-quote rule serves cached and freshly converted source evidence."""

import re


def quote(text: str, locator: str, block_locator: str) -> str | None:
    base, marker, number = locator.rpartition(";row=")
    if not marker:
        return text if locator == block_locator else None
    if base != block_locator or not number.isdigit() or not text.lstrip().startswith("|"):
        return None
    window = re.search(r";rows=(\d+)-(\d+)", base)
    region = re.search(r";range=[A-Z]+(\d+):", base)
    ordinal = int(number) - (int(region[1]) if region else 0)
    first = int(window[1]) if window else 1
    index = 2 + ordinal - first
    lines = text.splitlines()
    if index < 2 or index >= len(lines):
        return None
    return lines[index]
