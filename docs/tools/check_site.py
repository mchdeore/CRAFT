"""Verify local files and anchors in a rendered documentation site."""

import json
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit


class Page(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = set()
        self.links = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get("id"):
            self.ids.add(attrs["id"])
        target = attrs.get("href") if tag == "a" else attrs.get("src")
        if target:
            self.links.append(target)


def check(root):
    root = Path(root).resolve()
    pages = {}
    for file in root.rglob("*.html"):
        page = Page()
        page.feed(file.read_text())
        pages[file] = page
    failures = []
    if not (root / "index.html").is_file():
        failures.append({"file": "index.html", "target": "missing home page"})
    count = 0
    for file, page in pages.items():
        for target in page.links:
            parsed = urlsplit(target)
            if parsed.scheme or parsed.netloc:
                continue
            if parsed.path.startswith("/"):
                path = root / unquote(parsed.path).lstrip("/")
            elif parsed.path:
                path = file.parent / unquote(parsed.path)
            else:
                path = file
            path = path.resolve()
            if path.is_dir():
                path /= "index.html"
            count += 1
            if not path.is_relative_to(root) or not path.exists():
                failures.append({"file": str(file.relative_to(root)), "target": target})
            elif parsed.fragment and path in pages:
                if unquote(parsed.fragment) not in pages[path].ids:
                    failures.append({"file": str(file.relative_to(root)), "target": target})
    return {"pages": len(pages), "local_links": count, "failures": failures}


if __name__ == "__main__":
    report = check(sys.argv[1])
    print(json.dumps(report))
    raise SystemExit(bool(report["failures"]))
