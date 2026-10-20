# Analysis

Use this skill for tables, statistics, models, units and charts in the workspace.

## Data in and out

```python
import craft

sales = craft.read("data/sales.csv")            # CSV, TSV, Excel, Parquet, JSON
totals = craft.sql("select region, sum(amount) as total from sales group by region", sales=sales)
craft.save(totals, "results/totals.xlsx")
craft.ls("results"), craft.find("*.xlsx")
```

Names are relative to USER_ROOT; the path chooses the folder.

## Libraries

| Need | Library |
|---|---|
| Tables | pandas, DuckDB (`craft.sql`) |
| Numbers and statistics | NumPy, SciPy, statsmodels |
| Models | scikit-learn |
| Symbols and equations | SymPy |
| Units | Pint |
| Charts | Matplotlib, saved with `craft.save(figure, "charts/name.png")` |

There is no internet access and no package installation. Use what is in the image.

## CRAFT data

```python
light = craft.cube.find(("mass_dry", "le", 150, "kg"), stage="current")
light.df            # one row per matched value
light.citations     # the handles behind every value
```

Keep citations with any number taken from CRAFT, and keep unknown values and gaps visible. A computed result is not source evidence; label it as calculated.
