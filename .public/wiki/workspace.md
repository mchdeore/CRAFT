# Workspace

CRAFT combines authenticated chat and a personal workspace for files and Markdown skills. The user and agent work with the same `.py` scripts, `.ipynb` notebooks, inputs and outputs beneath `USER_ROOT`, which is `/workspace`. JupyterLab provides Python editing, execution, terminals, uploads and downloads.

Use the top navigation to open **Chat** or **Workspace**. Workspace contains **Files** and **Skills**. Switching views retains the conversation, unsent message, visited skill editor and personal file navigation. In Files, choose **Open JupyterLab** to open the owner's workspace in a separate tab; it starts the workspace if it is stopped. Files and running kernels remain in JupyterLab while browsing CRAFT; agent file access goes through CRAFT's own operations.

Each owner has a workspace of their own: a JupyterLab server that JupyterHub starts on demand and stops after an hour idle, with their files on a volume that is kept. JupyterLab signs in with craft-id, the same account as CRAFT. The workspace runs as an unprivileged account and receives no source corpus mount or CRAFT model and database credentials.

## Files and skills

Configured workspace tools let the agent list and read the owner's files. The agent runs Python with `run_code` and changes files with `write_file` or `edit_notebook`; each call waits for the requester's approval, which shows the exact code or change, and runs once in the owner's own kernel with the owner's access. Approving a `run_code` or `edit_notebook` call for a notebook grants that notebook for the conversation: later runs and edits in it go through `run_in_granted_notebook` and `edit_granted_notebook` without asking again. Code the owner runs in Jupyter needs no approval. In Skills, write instructions in the document editor and choose **Save to workspace** to create a Markdown file under `/workspace/skills`. Load a file to edit its current contents, or choose New for another document. The sidebar shows actual registered tool metadata and inserts tool names or personal file paths into the document. These files are not registered as app skills or immutable revisions.

In chat, type `/` to select a personal `skills/*.md` file or `@` to select another personal file. The picker inserts the exact `/workspace` path into message text; it refers to current file contents. Personal skills can tell the agent to read or run owned paths such as `/workspace/scripts/analysis.py`. Import source originals first.

Select a `/workspace` file link in a chat response to open the current personal copy beside the conversation. The preview supports text, Python, notebook cells and saved text output, Word, Excel, PDF and PNG files. Refresh reloads saved contents; Download exports the copy. Word and PowerPoint page previews come from the workspace and refuse a file over 25 MiB. Edit scripts and notebooks in JupyterLab or ask the agent to update the owned file.

## Source copies

In **Chat**, describe the document in ordinary language. Source-result cards show its title, sentence preview and pinned version. Select **Import copy** to save the full original into the personal workspace and open its preview in chat. The browser performs this transfer with the existing CRAFT and Jupyter sign-ins; the agent does not receive those credentials. A previous saved copy can also be opened from the agent’s `/workspace` link.

In **Workspace → Files**, open JupyterLab and sign in there if needed, then choose **Search sources** in CRAFT and select **Import copy**. This saves the selected original version into the workspace root (`USER_ROOT`) with a readable name, such as `report.xlsx`, and reveals its containing folder in Personal files. If that name is taken, it tries numeric alternatives such as `report-2.xlsx`, up to 20 names, without overwriting existing files. The import limit is 16 MB.

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

`save` and `craft.excel` write a hidden file beside the target and then replace it, so a failed save never leaves half a file. `read` and `save` choose the format from the extension: CSV, TSV, Excel, Parquet, JSON, Markdown, Word, PowerPoint, PDF text, Matplotlib figures and plain text. `craft.docs` reads, edits and builds Word, PowerPoint, PDF and Excel files. Starter skills `documents.md`, `analysis.md` and `cube.md` are copied into `USER_ROOT/skills` on first start; an existing file with the same name is kept.

## CRAFT access points

The access points call the same `/api` routes as the browser, with the account's workspace token ([Workspace tokens](access.md#workspace-tokens)):

```python
hits = craft.search("radiator margin", missions=["msn-sim-01"])
light = craft.cube.find(("mass_dry", "le", 150, "kg"), stage="current")
table = craft.cube.compare(["msn-sim-01", "msn-sim-02"], ["mass_dry", "power_peak"]).df
evidence = craft.cube.evidence(light.data["missions"][0]["matched"][0]["id"])
```

Each result has `.data`, a `.df` table and `.citations`. Document access points are `search`, `documents`, `document`, `chunks`, `explore`, `check_citation` and `compare_versions`; cube access points are `craft.cube.parameters`, `missions`, `find`, `mission`, `compare`, `evidence`, `similar`, `explain` and `candidates`. They are read-only. `craft.whoami()` shows the signed-in account and `craft.login()` sets a token for the current kernel.

Kernels and terminal shells start with an outbound guard. Python connections, name lookups and program launches are refused except to the CRAFT server named by `CRAFT_API_URL` and, during a bound agent run, its bridge. The workspace network policy remains the boundary; the guard is a second layer.

## Native tools from Python

During an active chat run, the kernel of the owner's `analysis.ipynb` also has run-bound native access:

```python
import craft

async with craft.native() as sdk:
    sources = await sdk.search("project requirements", limit=5)
```

The SDK ([`workspace/src/craft/client.py`](https://github.com/mchdeore/CRAFT/blob/main/workspace/src/craft/client.py)) wraps CRAFT's existing read tools (search, documents and chunks, missions, parameters and the cube, catalogs) and returns their MCP results unchanged (`isError`, `content`, `structuredContent`); it adds no data API of its own. Calls use the run's identity, tool-call budget, deadline, retrieval trace and audit records, and the SDK refuses any operation that needs approval before dispatching it. How the agent uses it is in its instructions ([`app/agent/instructions.md`](https://github.com/mchdeore/CRAFT/blob/main/app/agent/instructions.md)).

- CRAFT opens `analysis.ipynb` when needed and injects the run's bridge capability into its kernel with a silent execution (`store_history=False`); it stays in kernel memory. The kernel reaches a per-run bridge on the API pod's own address with the run's bearer. A second run for the same owner is refused, and an existing binding is never overwritten.
- Finish, an approval pause, an error or cancellation withdraws the bridge and clears the binding. If the kernel connection is lost, the bridge is still withdrawn and the in-memory binding expires at the run's deadline.
- Another kernel or a separate Python process has no binding and gets an explicit missing-binding error. To run an owned script with native access, execute it in that notebook.
- `owned_path()` refuses absolute paths, traversal and existing symlinks: pass `outputs/result.txt`, not `/workspace/outputs/result.txt`. It is a convenience check; the pod is the boundary.
- `craft.client.Craft(session)` and `craft.client.connect(url, run_token=...)` use only authentication passed to them; they issue no credentials or runs.

## Build

Use uv 0.12.23 to regenerate this package's standalone release lock from the
repository root:

```sh
uv lock --project workspace
```

Commit `workspace/uv.lock` with the package metadata. It covers the
SDK dependencies, the `lab` and `analysis` extras, and the `build` group containing Setuptools
80.9.0. The backend's root lock does not replace this package lock.

Build from the public package directory:

```sh
docker build --file workspace/Dockerfile \
  --tag craft-user-workspace:0.1.6 workspace
```

The Dockerfile installs locked dependency wheels and Setuptools into
`/opt/notebook`, then builds the local package without build isolation. A missing
or stale lock fails the build; dependencies without compatible wheels also fail
rather than resolving unrecorded build dependencies. Python and uv image references include immutable manifest digests. Regenerating
the lock or updating either image requires a new build and verification. Record
the resulting image digest alongside each versioned release.

## Workspaces through JupyterHub

JupyterHub runs from its own unmodified chart beside CRAFT's on the same cluster (`deploy/jupyterhub/`, setup in its README). CRAFT reaches it only through its own operations and one Hub service token (`CRAFT_HUB_*` in [`.env.example`](https://github.com/mchdeore/CRAFT/blob/main/.env.example)):

- `GET /api/workspace`, `POST /api/workspace/start` and `POST /api/workspace/stop` report `stopped`, `starting` or `ready` and where JupyterLab opens ([api-contract](api-contract.md)). Start returns at once; the Hub tracks the spawn.
- Hub names are CRAFT user IDs. On a person's first start CRAFT creates their Hub user and links it to their craft-id account; only then can they sign in to JupyterLab.
- An agent run starts a stopped workspace and waits up to 90 seconds for it before its first workspace action.
- Only Jupyter's 404 means a notebook is missing. An outage or server error returns an error and never replaces a notebook with a new empty one.

Each workspace pod accepts traffic only from the Hub and its proxy, and reaches only CRAFT's API, the Hub and DNS. Installing Python packages or reaching external services requires a new reviewed image or a supplied CRAFT operation, rather than internet access from user code.

A notebook control server runs beside JupyterLab in the same pod, on its loopback only. It uses MCP SDK 2 (`jupyter-mcp-server==2.2.4`), so it has its own image (`workspace/mcp.Dockerfile`) rather than sharing the workspace package's MCP SDK 1.30.0. CRAFT reaches it through JupyterLab's proxy behind the Hub's sign-in.

The agent receives only the read tools `list_files`, `list_notebooks` and `read_notebook`, prefixed `workspace_`; they don't read plain files such as Markdown skills, which the agent reads with a small approved `run_code`. Running code and changing files use the operations `run_code`, `write_file` and `edit_notebook`, which wait for the requester's approval. Workspace calls share the run's 20-tool-call and 120-second limits and are dispatched once ([tools over MCP](agent.md#tools-over-mcp)). Personal files stay separate from the registered CRAFT skill catalog.

## Notebook runs

`run_notebook` (`POST /api/workspace/notebooks/runs`) runs a whole notebook unattended, after the requester's approval when the agent asks. It queues a job on the `interactive` queue and returns at once; the original notebook is never changed. The executed copy is saved beside it as `<name>.run-<time>.ipynb`, and new or changed files in its `outputs/` folder come back beside it too. An existing file is never overwritten: a clashing result gets a `.run-<time>` name. A run has 15 minutes, and each cell 2 minutes.

On the cluster a run is a Kubernetes Job on the person's own volume (`claim-<CRAFT user ID>`), from the workspace image, so the person's JupyterLab need not be running and no file passes through the worker:

- A trusted first step makes a run folder on the volume.
- The notebook runs with no network, a read-only root, and its folder read-only, with only the run's `outputs/` writable. A write anywhere else fails as read-only.
- A trusted last step moves the executed copy and outputs into place with hard links, never overwriting, then deletes the run folder.

Cancelling the job deletes the Job, which stops the run within seconds. The interactive worker creates these Jobs with its own service account, which may only create, read and delete Jobs; an admission policy holds every such Job to this shape, so it cannot mount another volume ([deploy/README.md](https://github.com/mchdeore/CRAFT/blob/main/deploy/README.md)). Under Docker Compose, until it is removed, the same operation runs the notebook's folder in a restricted Docker container instead.

## Activity log

A person's own work in JupyterLab never passes through CRAFT's operations, so the workspace logs it itself. Each line is JSON in CRAFT's log format on the pod's output, with `user_id` set to the CRAFT user ID, so `kubectl logs` lines join the call log:

- File saves, renames, copies and deletes, with path and size. Reads are not logged.
- Kernel starts, restarts, interrupts and shutdowns, with their outcome.
- Cell runs, with notebook path, execution count, duration and outcome. They are read from the kernel's messages by the server, so code in a kernel cannot write or fake them.
- Requests that change something, and failures, with method, path, status and duration, never the query string.

No line holds code, outputs or file content.

See [agent runtime](runtime.md).
