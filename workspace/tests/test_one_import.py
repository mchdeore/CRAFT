"""`import craft` is the only import a notebook needs: its surface, speed and native tools."""

import subprocess
import sys
from pathlib import Path

import pytest

SOURCE = str(Path(__file__).parents[1] / "src")

IMPORT_PROBE = """
import sys, time
started = time.perf_counter()
import craft
elapsed = time.perf_counter() - started
heavy = [m for m in ("pandas", "matplotlib", "docx", "pptx", "openpyxl", "vl_convert", "mcp")
         if m in sys.modules]
assert not heavy, heavy
missing = [name for name in craft.__all__ if not hasattr(craft, name)]
assert not missing, missing
from craft import native
assert native is craft.native
print(round(elapsed, 3))
"""


def test_import_is_fast_loads_nothing_heavy_and_exposes_every_name():
    result = subprocess.run(
        [sys.executable, "-c", IMPORT_PROBE],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=SOURCE,
    )
    assert result.returncode == 0, result.stderr
    assert float(result.stdout) < 1.0


GUARDED_LAUNCH = """
import subprocess
import craft_guard
from craft import system
assert craft_guard.installed()
system._found["pdftoppm"] = "/bin/echo"
print(system.run("pdftoppm", "launched", timeout=10).stdout.decode().strip())
try:
    subprocess.run(["/bin/echo", "direct"])
except craft_guard.OutboundBlocked:
    print("still guarded")
"""


def test_found_tools_launch_in_the_guarded_kernel_and_nothing_else_does():
    result = subprocess.run(
        [sys.executable, "-c", GUARDED_LAUNCH],
        env={"CRAFT_GUARD": "1", "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        timeout=60,
        cwd=SOURCE,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.split("\n")[:2] == ["launched", "still guarded"]


@pytest.fixture
def no_office(tmp_path, monkeypatch):
    """A workspace with a Word file, on a machine without LibreOffice."""
    from craft import _root, system
    from docx import Document

    monkeypatch.setattr(_root, "USER_ROOT", tmp_path)
    monkeypatch.setattr(system, "_found", {"pdftoppm": "/usr/bin/pdftoppm"})
    monkeypatch.setitem(system._TOOLS["soffice"], "paths", ())
    monkeypatch.setattr(system.shutil, "which", lambda name: None)
    document = Document()
    document.add_paragraph("Generated memo.")
    document.save(str(tmp_path / "memo.docx"))
    return tmp_path


def test_office_rendering_without_libreoffice_says_what_to_install(no_office):
    import craft

    for render in (craft.pages, craft.preview, craft.docs.to_pdf):
        with pytest.raises(craft.MissingSoftware, match=r"LibreOffice .*install"):
            render("memo.docx")
    # Everything else still works.
    assert "Generated memo." in craft.read("memo.docx")
    assert craft.copy("memo.docx", "copies/memo.docx") == "copies/memo.docx"
    copied = no_office / "copies" / "memo.docx"
    assert copied.read_bytes() == (no_office / "memo.docx").read_bytes()


def test_check_reports_each_missing_tool_with_its_install_hint(no_office, monkeypatch, capsys):
    import craft
    from craft import system

    monkeypatch.setattr(system, "_found", {})
    monkeypatch.setitem(system._TOOLS["pdftoppm"], "paths", ())
    monkeypatch.delenv("CRAFT_API_URL", raising=False)
    report = craft.check()
    assert report["LibreOffice"].startswith("missing: LibreOffice")
    assert "brew install poppler" in report["Poppler"]
    assert report["Fonts"].startswith("missing")
    assert report["Access points"].startswith("not working: CRAFT_API_URL")
    assert "LibreOffice" in capsys.readouterr().out
