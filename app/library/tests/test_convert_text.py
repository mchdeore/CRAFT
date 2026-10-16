import pytest

from app.library.convert import convert


def test_markdown_chunks_and_locators() -> None:
    source = "# 2 Power\n\n## 2.3 Margins\n\n" + " ".join(["synthetic"] * 700)
    chunks = convert(source.encode(), "test.md")
    assert len(chunks) == 3 and all(c.heading_path == "2 Power > 2.3 Margins" for c in chunks)
    assert [c.locator for c in chunks] == [f"heading=2.3;block={n}" for n in [1, 2, 3]]
    assert [len(c.text.split()) for c in chunks] == [300, 300, 100]


def test_unsupported_format() -> None:
    with pytest.raises(ValueError, match="unsupported format"):
        convert(b"Synthetic", "test.docx")


def test_html_scripts_and_tables() -> None:
    html = (
        "<h1>1 Example</h1><script>never execute</script>"
        "<table><tr><th>Name</th><th>Value</th></tr>"
    )
    html += "".join(f"<tr><td>row{n}</td><td>{n}</td></tr>" for n in range(75)) + "</table>"
    chunks = convert(html.encode(), "test.html")
    assert len(chunks) == 3 and all(c.kind == "table" for c in chunks)
    assert all("Name" in c.text and "never execute" not in c.text for c in chunks)
    assert chunks[-1].locator.endswith(";rows=61-75")
