# Demo finance skill: cost review

Use this skill when I reference a cost model, such as @cost-model.csv, and ask for a cost review. It gives finance a reserve-adjusted view and a reserve what-if from my own imported copy, in one approval, saved as a labelled analysis folder.

## Run

Do not list or inspect files first. Propose one run_code call shaped like this, changing only the folder's mission key and the file name:

```python
import shutil
import craft
folder, source = "analyses/msn-sim-02-cost-review", "cost-model.csv"
for part in ("inputs", "outputs"):
    craft.path(f"{folder}/{part}").mkdir(parents=True, exist_ok=True)
shutil.copy(craft.path(source), craft.path(f"{folder}/inputs/{source}"))
code = r"""
import craft
folder = "analyses/msn-sim-02-cost-review"
df = craft.table(folder + "/inputs/cost-model.csv")
item = df.select_dtypes(exclude="number").columns[0]
est = next(c for c in df.columns if "estimate" in c.lower())
pct = next(c for c in df.columns if "reserve" in c.lower())
df = df[~df[item].astype(str).str.contains("total", case=False)]
out = df[[item, est, pct]].copy()
out["Reserve (credits)"] = out[est] * out[pct] / 100
out["Adjusted (credits)"] = out[est] + out["Reserve (credits)"]
out["What-if +5 pts"] = out[est] * (out[pct] + 5) / 100 + out[est]
out["Flag"] = out[pct].lt(12).map({True: "review: thin reserve", False: ""})
print(out.round(2).to_string(index=False))
base, adj, whatif = out[est].sum(), out["Adjusted (credits)"].sum(), out["What-if +5 pts"].sum()
print(f"Estimate {base:,.2f} | Reserve {adj - base:,.2f} ({(adj - base) / base:.1%}) | Adjusted {adj:,.2f}")
print(f"What-if +5 pts: {whatif:,.2f} (+{whatif - adj:,.2f})")
craft.save(out.round(2), folder + "/outputs/reserve-by-item.csv")
"""
notes = "# Cost review: reserves and a +5 point what-if\n\nReserve-adjusted cost per item from inputs/cost-model.csv, saved to outputs/reserve-by-item.csv.\n"
exec(code, {})
cell = {"cell_type": "code", "metadata": {}, "execution_count": None, "outputs": [], "source": code.strip()}
kernel = {"kernelspec": {"name": "python3", "display_name": "Python 3", "language": "python"}}
craft.save({"cells": [cell], "metadata": kernel, "nbformat": 4, "nbformat_minor": 5}, f"{folder}/analysis.ipynb")
craft.save(code.strip() + "\n", f"{folder}/analysis.py")
craft.save(notes, f"{folder}/notes.md")
for name in sorted(craft.path(folder).rglob("*")):
    if name.is_file():
        print("Saved", name.relative_to(craft.USER_ROOT), name.stat().st_size, "bytes")
```

## Answer

- A table of each item: estimate, reserve %, reserve, adjusted cost and flag, in credits.
- One line each for the totals and the +5 point what-if.
- The item carrying the most reserve, and any item flagged for a thin reserve (under 12%).
- Three short bullets I could send the finance team.
- Links to the folder's analysis.ipynb and outputs/reserve-by-item.csv. The printed Saved lines verify every file; do not list files to check them.

Label every figure as calculated from the simulated cost model. If the file came from a search, cite the source document.
