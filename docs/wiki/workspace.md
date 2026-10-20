# Workspace

CRAFT combines authenticated chat and a personal workspace for files and Markdown skills. The user and agent work with the same `.py` scripts, `.ipynb` notebooks, inputs and outputs beneath `USER_ROOT`, which is `/workspace`. JupyterLab provides Python editing, execution, terminals, uploads and downloads.

Use the top navigation to open **Chat** or **Workspace**. Workspace contains **Files** and **Skills**. Switching views retains the conversation, unsent message, visited skill editor and personal file navigation. In Files, choose **Open JupyterLab** to launch the owner's configured workspace in a separate tab. Files and running kernels remain in JupyterLab while browsing CRAFT; agent file access continues through the existing backend workspace connection.

Each owner has dedicated workspace storage, a Jupyter service and a private MCP control connection. Jupyter uses its own password, separate from CRAFT sign-in. The workspace runs as an unprivileged account and receives no source corpus mount or CRAFT model and database credentials.

## Files and skills

Configured workspace tools let the agent list and read the owner's files. The agent runs Python with `run_code` and changes files with `write_file` or `edit_notebook`; each call waits for the requester's approval, which shows the exact code or change, and runs once in the owner's own kernel with the owner's access. Code the owner runs in Jupyter needs no approval. In Skills, write instructions in the document editor and choose **Save to workspace** to create a Markdown file under `/workspace/skills`. Load a file to edit its current contents, or choose New for another document. The sidebar shows actual registered tool metadata and inserts tool names or personal file paths into the document. These files are not registered as app skills or immutable revisions.

In chat, type `/` to select a personal `skills/*.md` file or `@` to select another personal file. The picker inserts the exact `/workspace` path into message text; it refers to current file contents. Personal skills can tell the agent to read or run owned paths such as `/workspace/scripts/analysis.py`. Import source originals first.

Select a `/workspace` file link in a chat response to open the current personal copy beside the conversation. The preview supports text, Python, notebook cells and saved text output, Word, Excel, PDF and PNG files. Refresh reloads saved contents; Download exports the copy. Edit scripts and notebooks in JupyterLab or ask the agent to update the owned file.

## Source copies

In **Workspace → Files**, open JupyterLab and sign in there if needed, then choose **Search sources** in CRAFT and select **Import copy**. This saves the selected original version into `USER_ROOT/imports` with a readable name, such as `report.xlsx`, and reveals its containing folder in Personal files. If that name is taken, it tries numeric alternatives such as `report-2.xlsx`, up to 20 names, without overwriting existing files. The import limit is 16 MB.

Source identity and the initial SHA-256 hash are recorded separately in `USER_ROOT/imports/source-metadata/<UUID>.json`. If metadata cannot be confirmed after the copy saves, search stays open with the warning, saved path and **Open copy** action. The personal browser still refreshes and reveals the saved copy.

Word and Excel copies open in the personal file preview after a fully successful import. **Open copy** opens that preview manually; Download returns the personal bytes. The personal browser also previews supported files up to 10 MB. Use JupyterLab for Python and notebook editing, and for files beyond the preview limit. Source originals remain immutable. Personal copies and generated outputs stay outside ingestion and the engineering datacube.

## Python analysis

The workspace image includes Pandas and DuckDB for tables and SQL, NumPy, SciPy and statsmodels for numerical and statistical analysis, scikit-learn for models, SymPy for symbolic mathematics, Pint for units, Matplotlib for charts, PyArrow for Parquet, OpenPyXL for Excel, python-docx and python-pptx for Word and PowerPoint, and pypdf for PDF text. There is no internet access or package installation inside the workspace.

The `craft` package names files relative to `USER_ROOT`; the path chooses the folder:

```python
import craft

sales = craft.read("data/sales.xlsx")
totals = craft.sql("select region, sum(amount) as total from sales group by region", sales=sales)
craft.save(totals, "results/totals.csv")
craft.docs.report("results/summary.docx", "Sales", "# Totals", totals)
```

`read` and `save` choose the format from the extension: CSV, TSV, Excel, Parquet, JSON, Markdown, Word, PowerPoint, PDF text, Matplotlib figures and plain text. `craft.docs` reads, edits and builds Word, PowerPoint, PDF and Excel files. Starter skills `documents.md`, `analysis.md` and `cube.md` are copied into `USER_ROOT/skills` on first start; an existing file with the same name is kept.

## CRAFT access points

The access points call the same `/api` routes as the browser, with the account's workspace token (section 9.7 of the specification):

```python
hits = craft.search("radiator margin", missions=["msn-sim-01"])
light = craft.cube.find(("mass_dry", "le", 150, "kg"), stage="current")
table = craft.cube.compare(["msn-sim-01", "msn-sim-02"], ["mass_dry", "power_peak"]).df
evidence = craft.cube.evidence(light.data["missions"][0]["matched"][0]["id"])
```

Each result has `.data`, a `.df` table and `.citations`. Document access points are `search`, `documents`, `document`, `chunks`, `explore`, `check_citation` and `compare_versions`; cube access points are `craft.cube.parameters`, `missions`, `find`, `mission`, `compare`, `evidence`, `similar`, `explain` and `candidates`. They are read-only. `craft.whoami()` shows the signed-in account and `craft.login()` sets a token for the current kernel.

Kernels and terminal shells start with an outbound guard. Python connections, name lookups and program launches are refused except to the CRAFT server named by `CRAFT_API_URL` and, during a bound agent run, its bridge. The workspace network policy remains the boundary; the guard is a second layer.

## Native tools from Python

During an active chat run, the selected notebook kernel also has run-bound native access:

```python
from craft import native

async with native() as sdk:
    sources = await sdk.search("project requirements")
```

These calls use that run's identity, tool-call budget, deadline, retrieval trace and audit records. Access ends on completion, pause, error or cancellation. The SDK refuses operations that require CRAFT approval before dispatch.

See [agent runtime](runtime.md) and the separate [Python workspace prototype](python.md).
