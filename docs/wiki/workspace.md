# Workspace

CRAFT combines authenticated chat and a personal workspace for files and Markdown skills. The user and agent work with the same `.py` scripts, `.ipynb` notebooks, inputs and outputs beneath `USER_ROOT`, which is `/workspace`. JupyterLab provides Python editing, execution, terminals, uploads and downloads.

Use the top navigation to open **Chat** or **Workspace**. Workspace contains **Files** and **Skills**. Switching views retains the conversation, unsent message, visited skill editor and Jupyter workspace. Jupyter first loads when Files opens.

Each owner has dedicated workspace storage, a Jupyter service and a private MCP control connection. Jupyter uses its own password, separate from CRAFT sign-in. The workspace runs as an unprivileged account and receives no source corpus mount or CRAFT model and database credentials.

## Files and skills

Configured workspace tools let the agent read, create, edit and execute the owner's files. In Skills, write instructions in the document editor and choose **Save to workspace** to create a Markdown file under `/workspace/skills`. Load a file to edit its current contents, or choose New for another document. The sidebar shows actual registered tool metadata and inserts tool names or personal file paths into the document. These files are not registered as app skills or immutable revisions.

In chat, type `/` to select a personal `skills/*.md` file or `@` to select another personal file. The picker inserts the exact `/workspace` path into message text; it refers to current file contents. Personal skills can tell the agent to read or run owned paths such as `/workspace/scripts/analysis.py`. Import source originals first.

Select a `/workspace` file link in a chat response to open the current personal copy beside the conversation. The preview supports text, Python, notebook cells and saved text output, Word, Excel, PDF and PNG files. Refresh reloads saved contents; Download exports the copy. Edit scripts and notebooks in JupyterLab or ask the agent to update the owned file.

## Source copies

In **Workspace**, sign in to JupyterLab, search sources and select **Import**. Import copies the selected original version into `USER_ROOT/imports` with a readable name, such as `report.xlsx`. If that name is taken, it tries numeric alternatives such as `report-2.xlsx`, up to 20 names, without overwriting existing files. The import limit is 16 MB.

Source identity and the initial SHA-256 hash are recorded separately in `USER_ROOT/imports/source-metadata/<UUID>.json`. If metadata cannot be confirmed after the copy saves, the copy remains and Import prompts inspection before importing again.

Word and Excel copies open in the personal file preview after Import. Open copy reopens that preview; Download returns the personal bytes. The personal browser also previews supported files up to 10 MB. Use JupyterLab for Python and notebook editing, and for files beyond the preview limit. Source originals remain immutable. Personal copies and generated outputs stay outside ingestion and the engineering datacube.

## Python analysis

The workspace image includes Pandas for tables, DuckDB for local SQL, NumPy and SciPy for numerical analysis, Matplotlib for charts, and OpenPyXL for Excel files. Import them in scripts or notebooks and save exported tables and charts beneath `USER_ROOT/outputs`. Personal inputs, scripts, notebooks and results remain outside the source database.

## Native tools from Python

The `craft` package exposes native CRAFT read tools in the notebook kernel selected for an active chat run:

```python
from craft import native

async with native() as sdk:
    sources = await sdk.search("project requirements")
    skills = await sdk.skills()
```

SDK calls use that run's authenticated identity, tool-call budget, deadline, retrieval trace and audit records. The controller injects the binding into kernel memory without storing it in notebook history. Access ends on completion, pause, error or cancellation. Other kernels, standalone Python processes and notebooks outside an active run have no native binding.

The SDK refuses operations that require CRAFT approval before dispatch. Chat retains the existing approval and resume workflow for those operations.

See [agent runtime](runtime.md) and the separate [Python workspace prototype](python.md).
