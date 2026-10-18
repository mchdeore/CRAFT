"""Export public topic sources and verify their download and edit links."""

import argparse
from html import escape
from html.parser import HTMLParser
from pathlib import Path
from posixpath import relpath
from urllib.parse import quote

DOCUMENTS = Path(__file__).resolve().parents[1]
EDIT_BASE = "https://github.com/mchdeore/CRAFT/edit/main/docs/"


def stage_public_sources(repository, staged, paths):
    """Copy declared versioned docs, excluding tools and local build material."""
    repository, staged = Path(repository).resolve(), Path(staged)
    staged.mkdir(parents=True)
    count = 0
    for name in paths:
        relative = Path(name)
        if relative.is_absolute() or len(relative.parts) < 2 or relative.parts[0] != "docs":
            raise ValueError("Documentation path must be inside docs")
        if ".." in relative.parts:
            raise ValueError("Documentation path must be inside docs")
        topic = Path(*relative.parts[1:])
        if any(part in {"tools", "build", "site"} or part.startswith(".") for part in topic.parts):
            continue
        source = repository / relative
        if source.is_symlink() or any(
            parent.is_symlink() for parent in source.parents if parent.is_relative_to(repository)
        ):
            raise ValueError("Documentation sources must not be symlinks")
        target = staged / topic
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
        count += 1
    return count


def topic_paths(documents=DOCUMENTS):
    documents = Path(documents)
    topics = {}
    for file in sorted(documents.rglob("*.md")):
        relative = file.relative_to(documents)
        if any(
            part in {"tools", "build", "site"} or part.startswith(".") for part in relative.parts
        ):
            continue
        if file.is_symlink():
            raise ValueError("Documentation sources must not be symlinks")
        topics[relative.as_posix()] = file
    return topics


def source_urls(name):
    download = quote(relpath("markdown/" + name, str(Path(name).parent)), safe="/")
    return download, EDIT_BASE + quote(name, safe="/")


def prepare(documents, staged, downloads):
    staged, downloads = Path(staged), Path(downloads)
    topics = topic_paths(documents)
    for name, original in topics.items():
        target = downloads / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(original.read_bytes())
        download, edit = source_urls(name)
        footer = (
            '<nav class="source-links" aria-label="Page source">'
            f'<a href="{escape(download, quote=True)}" download>Download Markdown</a> · '
            f'<a href="{escape(edit, quote=True)}">Edit this page</a></nav>'
        )
        page = staged / name
        page.write_text(page.read_text().rstrip() + "\n\n" + footer + "\n")
    return len(topics)


class SourcePage(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = set()
        self.downloads = set()

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "a" and attributes.get("href"):
            self.links.add(attributes["href"])
            if "download" in attributes:
                self.downloads.add(attributes["href"])


def source_failures(root, documents=DOCUMENTS):
    root = Path(root)
    failures = []
    for name, original in topic_paths(documents).items():
        html = root / Path(name).with_suffix(".html")
        markdown = root / "markdown" / name
        if not html.is_file() or html.is_symlink():
            failures.append({"file": name, "target": "missing rendered topic"})
            continue
        if not markdown.is_file() or markdown.is_symlink():
            failures.append({"file": name, "target": "missing Markdown download"})
        elif markdown.read_bytes() != original.read_bytes():
            failures.append({"file": name, "target": "Markdown differs from its public source"})
        page = SourcePage()
        page.feed(html.read_text())
        download, edit = source_urls(name)
        if download not in page.downloads:
            failures.append({"file": name, "target": "missing Markdown download link"})
        if edit not in page.links:
            failures.append({"file": name, "target": "missing GitHub edit link"})
    return failures


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("documents")
    parser.add_argument("staged")
    parser.add_argument("downloads")
    args = parser.parse_args()
    print(f"Markdown topics: {prepare(args.documents, args.staged, args.downloads)}")
