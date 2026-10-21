"""The CRAFT workspace package: your files, document helpers and CRAFT access points.

Files are named relative to ``USER_ROOT``::

    import craft

    sales = craft.read("data/sales.xlsx")
    craft.save(sales.describe(), "results/summary.csv")

The access points are the only way user code reaches CRAFT, and they work
during an agent run that has bound this kernel::

    hits = craft.search("radiator margin", missions=["msn-sim-01"])
    light = craft.cube.find(("mass_dry", "le", 150, "kg"), stage="current")
    light.df, light.citations

Other network access and program launches are refused in the kernel.
"""

from craft import docs
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
from craft.client import Craft, native
from craft.files import excel, find, ls, path, read, save, sql, table

__all__ = [
    "USER_ROOT",
    "Craft",
    "CraftError",
    "Result",
    "capabilities",
    "check_citation",
    "chunks",
    "compare_versions",
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
    "path",
    "read",
    "save",
    "search",
    "sql",
    "table",
    "whoami",
]
