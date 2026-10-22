# Workspace

CRAFT combines authenticated chat and a personal workspace for files and Markdown skills. The user and agent work with the same `.py` scripts, `.ipynb` notebooks, inputs and outputs beneath `USER_ROOT`, which is `/workspace`. JupyterLab provides Python editing, execution, terminals, uploads and downloads.

Use the top navigation to open **Chat** or **Workspace**. Workspace contains **Files** and **Skills**. Switching views retains the conversation, unsent message, visited skill editor and personal file navigation. In Files, choose **Open JupyterLab** to launch the owner's configured workspace in a separate tab. Files and running kernels remain in JupyterLab while browsing CRAFT; agent file access continues through the existing backend workspace connection.

Each owner has dedicated workspace storage, a Jupyter service and a private MCP control connection. Jupyter uses its own password, separate from CRAFT sign-in. The workspace runs as an unprivileged account and receives no source corpus mount or CRAFT model and database credentials.

## Files and skills

Configured workspace tools let the agent list and read the owner's files. The agent runs Python with `run_code` and changes files with `write_file` or `edit_notebook`; each call waits for the requester's approval, which shows the exact code or change, and runs once in the owner's own kernel with the owner's access. Code the owner runs in Jupyter needs no approval. In Skills, write instructions in the document editor and choose **Save to workspace** to create a Markdown file under `/workspace/skills`. Load a file to edit its current contents, or choose New for another document. The sidebar shows actual registered tool metadata and inserts tool names or personal file paths into the document. These files are not registered as app skills or immutable revisions.

In chat, type `/` to select a personal `skills/*.md` file or `@` to select another personal file. The picker inserts the exact `/workspace` path into message text; it refers to current file contents. Personal skills can tell the agent to read or run owned paths such as `/workspace/scripts/analysis.py`. Import source originals first.

Select a `/workspace` file link in a chat response to open the current personal copy beside the conversation. The preview supports text, Python, notebook cells and saved text output, Word, Excel, PDF and PNG files. Refresh reloads saved contents; Download exports the copy. Edit scripts and notebooks in JupyterLab or ask the agent to update the owned file.

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

`read` and `save` choose the format from the extension: CSV, TSV, Excel, Parquet, JSON, Markdown, Word, PowerPoint, PDF text, Matplotlib figures and plain text. `craft.docs` reads, edits and builds Word, PowerPoint, PDF and Excel files. Starter skills `documents.md`, `analysis.md` and `cube.md` are copied into `USER_ROOT/skills` on first start; an existing file with the same name is kept.

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

During an active chat run, the selected notebook kernel also has run-bound native access:

```python
from craft import native

async with native() as sdk:
    sources = await sdk.search("project requirements")
```

These calls use that run's identity, tool-call budget, deadline, retrieval trace and audit records. Access ends on completion, pause, error or cancellation. The SDK refuses operations that require CRAFT approval before dispatch.

## Build and owner provisioning

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

Provision one dedicated volume, Jupyter server, and external MCP connection per
CRAFT owner. Keep service ports private or on host loopback and enforce container
resource and network limits. Kernels and terminals rely on the container boundary;
the Python path helper is a convenience check. Workspace containers receive no
source corpus mount, CRAFT model/database keys, or global service session cookie.

The workspace package uses MCP SDK 1.30.0. Run `jupyter-mcp-server==2.2.4`, which
uses MCP SDK 2, in a separate control environment. Supply each control process
with its owner's `JUPYTER_URL`, `JUPYTER_TOKEN`, and unique `MCP_TOKEN` through
private configuration. Start it after the owned Jupyter server is ready:

```sh
jupyter-mcp-server \
  --transport streamable-http --host 127.0.0.1 --port 4040 \
  --start-new-code-sandbox False --document-provider jupyter \
  --jupyterlab False --reconnect-interval 0 \
  --execution-timeout 120 --max-execution-timeout 120
```

Use distinct ports and credentials for additional owners. Jupyter receives its
own `JUPYTER_TOKEN` and `JUPYTER_PASSWORD_HASH`. `CRAFT_UI_ORIGIN` is the plain
loopback frontend origin, such as `http://127.0.0.1:5173`. Keep credentials out of
URLs, command arguments, model arguments, and frontend configuration.

Set the CRAFT server's `USER_WORKSPACES_FILE` to a private JSON file. Keys must
match trusted CRAFT owner UUIDs exactly. Replace these example IDs and secret
placeholders with each owner's configuration:

```json
{
  "00000000-0000-0000-0000-000000000001": {
    "url": "http://127.0.0.1:4040/mcp",
    "headers": {"Authorization": "Bearer <owner-one-MCP_TOKEN>"},
    "tools": ["list_files", "read_notebook"],
    "jupyter": {
      "url": "http://127.0.0.1:18881/",
      "token": "<owner-one-JUPYTER_TOKEN>",
      "kernel_id": "00000000-0000-4000-8000-000000000101",
      "bridge_host": "host.docker.internal"
    }
  },
  "00000000-0000-0000-0000-000000000002": {
    "url": "http://127.0.0.1:4041/mcp",
    "headers": {"Authorization": "Bearer <owner-two-MCP_TOKEN>"},
    "tools": ["list_files", "read_notebook"],
    "jupyter": {
      "url": "http://127.0.0.1:18882/",
      "token": "<owner-two-JUPYTER_TOKEN>",
      "kernel_id": "00000000-0000-4000-8000-000000000102",
      "bridge_host": "host.docker.internal"
    }
  }
}
```

The optional `jupyter` entry enables native Python SDK reads during an existing
CRAFT agent run. Root selects an already-running Python kernel with exactly one
attached notebook session. Its ID comes from that owner's authenticated Jupyter
inventory. The backend needs Jupyter Client in its control environment. Omit this
entry to retain workspace tools without a native kernel binding.

The controller serves a private bridge on a prebound `127.0.0.1` socket.
`bridge_host` selects how the kernel reaches that socket: `127.0.0.1` (the default),
`localhost`, or `host.docker.internal`. Root must verify reachability for its
container/network arrangement. Optional private `bridge_port` selects a fixed
integer port; the default `0` retains ephemeral allocation. Configure distinct
fixed ports per owner for a network allowlist. An occupied port fails without
falling back. No native bearer belongs in this configuration.

The demo network boundary is managed outside the user image. Give each owner a
dedicated bridge network, disable IPv6 and external DNS, and permit only that
owner's fixed CRAFT bridge destination and established Jupyter responses. Block
other outbound traffic and host access. Enforce both routed traffic and traffic
to the container host; a shared bridge can leave peer traffic outside routed
firewall rules. Start a workspace only after its policy is installed and checked.
Keep automatic restart disabled when the policy is not restored by a supervisor.
Installing Python packages or reaching external services requires a new reviewed
image or a supplied CRAFT operation, rather than internet access from user code.

Set the frontend's `VITE_USER_WORKSPACES` to a nonsecret JSON map of the same
owner UUIDs to plain loopback Jupyter server roots. The app opens `/lab`:

```dotenv
VITE_USER_WORKSPACES='{"00000000-0000-0000-0000-000000000001":"http://127.0.0.1:18881/","00000000-0000-0000-0000-000000000002":"http://127.0.0.1:18882/"}'
```

The signed-in owner selects the matching entry; an absent entry has no global
fallback. Open the selected workspace and enter that owner's Jupyter password.
Jupyter login is separate from CRAFT login and is not SSO. XSRF protection stays
enabled. The private Jupyter token serves the control connection.

The agent receives only the configured read tools (`list_files`, `list_notebooks`,
`list_kernels`, `read_notebook`, `read_cell`), prefixed `workspace_`. Running code
and changing files use the CRAFT operations `run_code`, `write_file` and
`edit_notebook`, which wait for the requester's approval and dispatch once. Workspace calls
share the CRAFT run's 20-tool-call and 120-second limits. The transport dispatches
once; a lost response is an unknown outcome. Keep Markdown skill files under
`/workspace/skills` and refer to exact owned paths. Read selected or relevant skill
contents through a small approved `run_code` call; the direct list/notebook tools
do not read plain Markdown files. Personal files remain separate from the registered
CRAFT skill catalog. Without a configured Jupyter kernel, only the configured
list/notebook reads are available.

## Notebook SDK reads

`owned_path()` rejects absolute paths, traversal, and existing symlinks. Pass
`outputs/result.txt`, not `/workspace/outputs/result.txt`. It is a convenience
check; the container boundary confines the process.

The agent can run an inspected owned script through its existing workspace
execution tool. When native SDK reads are needed, execute in the active run's
selected notebook and kernel. Starting a separate Python process loses that
binding. For example, execute an already-read `scripts/report.py` in the notebook:

```python
from craft import owned_path

script = owned_path("scripts/report.py")
code = script.read_text(encoding="utf-8")
exec(compile(code, str(script), "exec"), {"__name__": "__main__", "__file__": str(script)})
```

During a native CRAFT run with `jupyter` configured, use top-level `await` in
that selected notebook:

```python
from craft import native

async with native() as sdk:
    result = await sdk.search("authorized source", limit=5, kinds=["chunk"])
    if result.isError:
        raise RuntimeError("CRAFT search failed; inspect the tool result.")
    print(result.structuredContent)
```

The SDK exposes existing tools and preserves MCP results (`isError`, `content`
and `structuredContent`); it does not add a separate data API:

| Call | Purpose |
| --- | --- |
| `await sdk.tools()` | Read allowed native tool names and input schemas when needed |
| `await sdk.missions(query="sim", limit=5)` | Resolve readable mission names and aliases to returned keys |
| `await sdk.parameters()` | Read parameter keys and registered units before numeric filters |
| `await sdk.search(query, **filters)` | Search readable source text |
| `await sdk.document(item, version=version)` | Read the selected exact item/version metadata and ordered outline |
| `await sdk.chunks(item, version, from_block=1, to_block=20)` | Read an inclusive block range from the selected exact version |
| `await sdk.skills(limit=10)` | List the legacy saved-skill registry only when explicitly requested |

`search` takes a 1–500 character `query`, optional `missions`, `silos`, `stages`
and `kinds` lists, and `limit` from 1 to 8. `kinds` accepts `chunk` or `document`.
Group hits by their handle's item/version, show the returned title and a sentence
preview, and retain those exact identities when the user selects a document.
Report bounded results and ambiguous choices. Scores are ranking values, not
confidence. Use the existing citation/source preview flow, not an owned-file
link, for sources that have not been imported.

Document metadata and search hits do not establish a complete summary. Read all
outline blocks with the existing `read_chunks` tool's inclusive ranges of at most
20 blocks, keeping the selected item/version and checking for missing blocks.
Report partial coverage when the run budget or available text prevents completion.
For numeric datacube questions, use declared `find_missions`, `get_mission` or
`compare_missions` schemas after resolving dictionary keys and units. Preserve
`silo`, `stage`, `at`, `knownAt`, gaps and value citations; text search does not
execute numeric conditions. Exact owned paths and known tool schemas do not
require repeated inventory calls.

Root injects the ephemeral bridge capability through authenticated, silent
Jupyter kernel execution with `store_history=False`. It stays in kernel memory.
Native SDK calls borrow the agent's exact middleware, deadline, call budget,
retrieval trace and trusted run identity. Approved `run_code` calls execute in
the configured kernel. An overlapping run for the same
owner is rejected, and an existing kernel binding cannot be overwritten.

Finish, approval pause, error and cancellation withdraw the bridge before the
native transport closes and clear the same run's kernel binding. A lost kernel
connection still leaves the bridge withdrawn; the memory binding expires at the
run's original deadline. Unprovisioned kernels and standalone Python processes
raise an explicit missing-binding error. Native SDK mutations remain unavailable
until the CRAFT approval/resume path is integrated. `craft.client.Craft(session)` and
`craft.client.connect(url, run_token=...)` still consume explicitly injected authentication;
they issue no credentials or persistent runs.

See [agent runtime](runtime.md).
