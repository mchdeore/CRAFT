"""The CRAFT workspace package: your files, document helpers and CRAFT access points.

``import craft`` is the only import a notebook needs. Files are named relative to
``USER_ROOT``::

    import craft

    sales = craft.read("data/sales.xlsx")
    craft.save(sales.describe(), "results/summary.csv")
    craft.preview("outputs/report.docx")   # the first pages, shown in the notebook
    craft.check()                          # what works here and what is missing

The access points are the only way user code reaches CRAFT, and they work
during an agent run that has bound this kernel::

    hits = craft.search("radiator margin", missions=["msn-sim-01"])
    light = craft.cube.find(("mass_dry", "le", 150, "kg"), stage="current")
    light.df, light.citations

Other network access and program launches are refused in the kernel. pandas,
Matplotlib, the Office libraries and the MCP client load on first use, so this
import stays fast.
"""

from typing import TYPE_CHECKING, Any

from craft import analysis, docs, system
from craft._root import USER_ROOT, owned_path
from craft.access import (
    CraftError,
    Result,
    capabilities,
    check_citation,
    chunks,
    compare_versions,
    cube,
    document,
    documents,
    explore,
    login,
    search,
    whoami,
)
from craft.analysis import chart, vega
from craft.docs import pages, preview
from craft.files import copy, excel, find, ls, path, read, save, show, sql, table
from craft.system import MissingSoftware, check

if TYPE_CHECKING:
    from craft.client import Craft, native

# The MCP client is heavy to import and only needed for agent-bound native reads.
_LAZY = {"Craft": "craft.client", "native": "craft.client"}


def __getattr__(name: str) -> Any:
    if name in _LAZY:
        from importlib import import_module

        value = getattr(import_module(_LAZY[name]), name)
        globals()[name] = value
        return value
    raise AttributeError(f"module 'craft' has no attribute {name!r}")


__all__ = [
    "USER_ROOT",
    "Craft",
    "CraftError",
    "MissingSoftware",
    "Result",
    "analysis",
    "capabilities",
    "chart",
    "check",
    "check_citation",
    "chunks",
    "compare_versions",
    "copy",
    "cube",
    "docs",
    "document",
    "documents",
    "excel",
    "explore",
    "find",
    "login",
    "ls",
    "native",
    "owned_path",
    "pages",
    "path",
    "preview",
    "read",
    "save",
    "search",
    "show",
    "sql",
    "system",
    "table",
    "vega",
    "whoami",
]
