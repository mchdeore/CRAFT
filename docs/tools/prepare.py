"""Prepare a print copy and resolve specification index page numbers.

Usage: prepare.py SOURCE.md_OR_SPEC_FOLDER OUT.md UPDATED [SPEC.pdf | --section N]
"""

import re
import subprocess
import sys
from pathlib import Path

source, out, updated = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
section = int(sys.argv[5]) if len(sys.argv) > 5 and sys.argv[4] == "--section" else None
pdf = sys.argv[4] if len(sys.argv) > 4 and section is None else None
is_spec = source.is_dir() or source.name == "SPEC.md"


def join_sections(folder: Path) -> str:
    """Join numbered sections without changing their body text."""
    files = sorted(folder.glob("*.md"))
    if len(files) != 15:
        raise SystemExit(f"{folder}: expected fifteen section files")
    chunks, headings = [], []
    for number, path in enumerate(files, 1):
        text = path.read_text(encoding="utf-8")
        if not path.name.startswith(f"{number:02}-") or not re.match(rf"^# {number}\. .+\n", text):
            raise SystemExit(f"{path}: unexpected section number")
        rows, fenced = [], False
        for line in text.splitlines(keepends=True):
            if re.match(r"^\s*(```|~~~)", line):
                fenced = not fenced
            heading = re.match(r"^(#{1,2}) (\d+(?:\.\d+)?)\.? (.+)\n?$", line)
            if heading and not fenced:
                if heading[1] == "#":
                    headings.append((heading[2], heading[3]))
                line = "#" + line
            rows.append(line)
        chunks.append("".join(rows))
    body = "\n".join(chunks)
    metadata = re.findall(r'^<p class="meta">.*</p>$', body, re.M)
    if len(metadata) != 1:
        raise SystemExit(f"{folder}: expected one metadata line")
    body = body.replace(metadata[0], "", 1)
    contents = ["| § | Section |", "|---|---|"]
    for number, title in headings:
        anchor = re.sub(r"[^\w\s-]", "", f"{number} {title}".lower()).replace(" ", "-")
        contents.append(f"| {number} | [{title}](#{anchor}) |")
    return (
        "# CRAFT specification\n\n"
        + metadata[0]
        + "\n\n## Index\n\n"
        + "\n".join(contents)
        + "\n\n"
        + body
    )


text = join_sections(source) if source.is_dir() else source.read_text(encoding="utf-8")

if section is not None:
    if not 1 <= section <= 15 or not text.startswith(f"# {section}. "):
        raise SystemExit(f"{source}: unexpected section number")
    text = re.sub(r'^<p class="meta">.*</p>\n?', "", text, flags=re.M)
    title, body = text.split("\n", 1)
    text = (
        f'{title}\n\n<p class="meta">CRAFT Specification | Section {section} of 15 | '
        f"{updated}</p>\n{body}"
    )

meta = re.search(r'^<p class="meta">.*</p>$', text, re.M)
date = r"\b\d{1,2} [A-Z][a-z]+ \d{4}\b"
if not meta or not re.search(date, meta[0]):
    raise SystemExit(f"{source}: no dated meta line under the title")
line = re.sub(date, updated, meta[0], count=1)
text = text[: meta.start()] + line + text[meta.end() :]


def page_numbers(pdf: str, headings: dict[str, str]) -> dict[str, int]:
    """Page of each section heading in the built PDF (the index is page 1)."""
    info = subprocess.run(["pdfinfo", pdf], capture_output=True, text=True, check=True).stdout
    pages: dict[str, int] = {}
    for page in range(2, int(re.search(r"Pages:\s+(\d+)", info)[1]) + 1):
        lines = subprocess.run(
            ["pdftotext", "-f", str(page), "-l", str(page), pdf, "-"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.splitlines()
        found = {line.strip() for line in lines}
        for number, heading in headings.items():
            if number not in pages and heading in found:
                pages[number] = page
    missing = sorted(set(headings) - set(pages))
    if missing:
        raise SystemExit(f"headings not found in {pdf}: {missing}")
    return pages


if is_spec:
    headings = {m[1]: f"{m[1]}. {m[2]}" for m in re.finditer(r"^## (\d+)\. (.+)$", text, re.M)}
    pages = page_numbers(pdf, headings) if pdf else {}
    rows, in_index = [], False
    for line in text.splitlines():
        if line == "## Index":
            in_index = True
        elif in_index and line.startswith("## "):
            in_index = False
            rows.append('<div style="break-before: page"></div>\n')
        if in_index and line.startswith("|"):
            number = line.split("|")[1].strip().strip("*")
            if number == "§":
                line += " Page |"
            elif set(number) <= {"-"}:
                line += "---|"
            else:
                line += f" {pages.get(number, '')} |"
        if line.startswith("|") and not in_index:  # keep section lists like "3.1, 3.8" on one line
            line = "|".join(
                c.replace(", ", ",\u00a0") if re.fullmatch(r"[\d., ]+", c.strip()) else c
                for c in line.split("|")
            )
        rows.append(line)
    text = "\n".join(rows) + "\n"

out.write_text(text, encoding="utf-8")
