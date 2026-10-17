"""Verify local files and anchors in a rendered documentation site."""

import argparse
import json
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


def public_failures(root):
    """Allow rendered pages and static assets, without source, data or hidden files."""
    root = Path(root).resolve()
    suffixes = {
        ".html",
        ".pdf",
        ".css",
        ".js",
        ".svg",
        ".ico",
        ".woff",
        ".woff2",
        ".ttf",
        ".eot",
        ".mmd",
    }
    generated = {"sitemap.xml", "sitemap.xml.gz", "search/search_index.json"}
    failures = []
    for file in root.rglob("*"):
        relative = file.relative_to(root)
        if file.is_symlink() or any(
            part.startswith(".") or part == "__pycache__" for part in relative.parts
        ):
            failures.append({"file": str(relative), "target": "hidden or symlinked artifact"})
        elif (
            file.is_file()
            and file.suffix.lower() not in suffixes
            and relative.as_posix() not in generated
        ):
            failures.append({"file": str(relative), "target": "unsupported public artifact"})
    return failures


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root")
    parser.add_argument("--public", action="store_true", help="also refuse unsafe public artifacts")
    arguments = parser.parse_args()
    failures = public_failures(arguments.root) if arguments.public else []
    report = (
        {"pages": 0, "local_links": 0, "failures": failures} if failures else check(arguments.root)
    )
    print(json.dumps(report))
    raise SystemExit(bool(report["failures"]))
