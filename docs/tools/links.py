"""Resolve download sources and revision dates for the document index.

Usage: links.py INDEX.md MANIFEST.tsv RESOLVED.md REVISION
"""

import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path


def repository_slug() -> str:
    """Use the build repository so source links follow the public source root."""
    configured = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if configured:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", configured):
            raise SystemExit("Invalid build repository slug")
        return configured
    root = Path(__file__).resolve().parents[2]
    remote = (
        subprocess.run(
            ["git", "-C", str(root), "config", "--get", "remote.origin.url"],
            capture_output=True,
            text=True,
            check=True,
        )
        .stdout.strip()
        .removesuffix("/")
        .removesuffix(".git")
    )
    match = re.fullmatch(
        r"(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)",
        remote,
    )
    if not match:
        raise SystemExit("Cannot derive the build repository from origin")
    return match[1]


source_mode = os.environ.get("DOCS_SOURCE_LINKS", "local")
if source_mode not in {"local", "github"}:
    raise SystemExit("Invalid source-link mode")
repository = repository_slug() if source_mode == "github" else ""
index, manifest, resolved = (Path(a) for a in sys.argv[1:4])
revision = sys.argv[4]
if not re.fullmatch(r"[0-9a-f]{40}", revision):
    raise SystemExit("Expected a full repository revision")
records = {}
for line in manifest.read_text(encoding="utf-8").splitlines():
    name, pages, date, source = line.split("\t")
    if name in records or not int(pages) > 0 or not source.startswith("docs/"):
        raise SystemExit("Invalid document manifest")
    records[name] = (date, source)
newest = max((date for date, _ in records.values()), key=lambda d: datetime.strptime(d, "%d %B %Y"))
text = index.read_text(encoding="utf-8")
linked = set()
rows = []
for line in text.splitlines():
    if line.startswith("| ") and "[PDF]" in line:
        pdf = re.search(r"\[PDF\]\(\./([^/]+\.pdf)\)", line)
        source = re.search(r"\[Source\]\(\./([^()]+)\)", line)
        if not pdf or not source or pdf[1] not in records or pdf[1] in linked:
            raise SystemExit("Invalid or repeated download row")
        date, path = records[pdf[1]]
        if "docs/" + source[1].rstrip("/") != path:
            raise SystemExit("Download/source mismatch")
        kind = "tree" if source[1].endswith("/") else "blob"
        if source_mode == "github":
            url = f"https://github.com/{repository}/{kind}/{revision}/{path}"
        else:
            url = (
                "./index.md#2-application-concepts"
                if kind == "tree"
                else "./" + path.removeprefix("docs/")
            )
        line = line.replace(source[0], f"[Source]({url})")
        line = line.rstrip().removesuffix("|").rstrip() + f"<br>Updated {date} |"
        linked.add(pdf[1])
    rows.append(line)
if linked != set(records) or len(records) != 18:
    raise SystemExit("Download rows do not match eighteen built documents")
out = "\n".join(rows) + "\n"
out = re.sub(r'(<p class="meta">[^|]+\|).*(</p>)', rf"\1 Updated {newest}\2", out, count=1)
out = re.sub(r"\[([^\]]+)\]\((\S+\.pdf)\)", r'<a href="\2" download>\1</a>', out)
resolved.parent.mkdir(parents=True, exist_ok=True)
resolved.write_text(out, encoding="utf-8")
