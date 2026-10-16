"""Text converters preserve block order, headings and stable source locators."""

import re
from dataclasses import dataclass
from html.parser import HTMLParser


@dataclass(frozen=True)
class Block:
    block: int
    kind: str
    heading_path: str
    locator: str
    text: str


class VisibleHTML(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts = []
        self.hidden = 0
        self.first_row = False
        self.row_cells = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1
        elif not self.hidden:
            if re.fullmatch(r"h[1-6]", tag):
                self.parts.append("\n" + "#" * int(tag[1]) + " ")
            elif tag == "table":
                self.parts.append("\n")
                self.first_row = True
            elif tag == "tr":
                if self.parts and not self.parts[-1].endswith("\n"):
                    self.parts.append("\n")
                self.row_cells = 0
            elif tag in {"p", "div", "br"}:
                self.parts.append("\n")
            elif tag in {"td", "th"}:
                self.parts.append(" | ")
                self.row_cells += 1

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)
        elif not self.hidden and tag == "tr":
            self.parts.append(" |\n")
            if self.first_row:
                self.parts.append("|" + "---|" * self.row_cells + "\n")
                self.first_row = False
        elif not self.hidden and (tag in {"p", "div"} or re.fullmatch(r"h[1-6]", tag)):
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def text_parts(text: str, size: int = 300):
    words = list(re.finditer(r"\S+", text))
    for start in range(0, len(words), size):
        end = min(start + size, len(words))
        yield text[words[start].start() : words[end - 1].end()]


def convert(content: bytes, filename: str) -> list[Block]:
    suffix = filename.rsplit(".", 1)[-1].lower()
    if suffix not in {"md", "txt", "html", "htm"}:
        raise ValueError("unsupported format")
    text = content.decode("utf-8")
    if suffix in {"html", "htm"}:
        parser = VisibleHTML()
        parser.feed(text)
        text = "".join(parser.parts)
    blocks = []
    headings = []
    buffer = []
    heading_id = "0"

    def add(value, kind="text", rows=None):
        number = len(blocks) + 1
        locator = f"heading={heading_id};block={number}" + (f";rows={rows}" if rows else "")
        blocks.append(Block(number, kind, " > ".join(t for _, t in headings), locator, value))

    def flush():
        nonlocal buffer
        for part in text_parts("\n".join(buffer)):
            add(part)
        buffer = []

    lines = text.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        heading = re.match(r"^(#{1,6})\s+(.+)$", line) if suffix != "txt" else None
        if heading:
            flush()
            level = len(heading[1])
            headings[:] = [(depth, title) for depth, title in headings if depth < level]
            headings.append((level, heading[2]))
            heading_id = heading[2].split()[0]
        elif (
            "|" in line and index + 1 < len(lines) and re.fullmatch(r"[\s|:\-]+", lines[index + 1])
        ):
            flush()
            header = line
            separator = lines[index + 1]
            rows = []
            index += 2
            while index < len(lines) and "|" in lines[index]:
                rows.append(lines[index])
                index += 1
            for start in range(0, len(rows), 30):
                end = min(start + 30, len(rows))
                add("\n".join([header, separator, *rows[start:end]]), "table", f"{start + 1}-{end}")
            index -= 1
        else:
            buffer.append(line)
        index += 1
    flush()
    return blocks
