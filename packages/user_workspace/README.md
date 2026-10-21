# User workspace

The user workspace image provides an unprivileged JupyterLab workspace with
the `craft` Python package. Edit and run `.py` scripts and `.ipynb` notebooks,
keep Markdown skills and input files, and save outputs beneath `USER_ROOT`,
which is `/workspace`. Jupyter provides terminals, uploads, and downloads.

## Files and skills in chat

Use `@` to select an owned file and `/` to select a Markdown skill under
`/workspace/skills`. The message retains the exact path to its current contents.
The agent reads that path through the configured `workspace_` tools, using their
actual schemas. An exact path needs no discovery call; a missing file or an
ambiguous name needs an honest response. Files and personal Markdown skills stay
user-owned and need no registration in the saved-skill catalog.

Ask the agent to read before editing or executing, preserve unrelated work, and
verify a save by reading the resulting bytes. Selected skill instructions remain
subject to system rules, the tool allowlist, approval gates and owner/source
boundaries. Source originals are read-only; edit an imported workspace copy.

Saved files can be returned as clickable Markdown links, for example
`[sales summary.csv](/workspace/outputs/sales%20summary.csv)`. Encode spaces in
the href. Chat previews show the current owned file, so refresh after edits.
Source citations retain their existing source preview links and exact handles;
an unimported source has no invented `/workspace` path.

## Local analysis

Use Python's standard library for simple local inputs and outputs. The shipped
image installs the locked `analysis` extra for these additional imports:

| Package | Use |
| --- | --- |
| Pandas | Clean, join and summarize CSV and Excel tables |
| DuckDB | Query local tables with SQL |
| NumPy and SciPy | Numerical analysis, statistics and optimization |
| Matplotlib | Save charts for preview and export |
| OpenPyXL | Read and write Excel workbooks |
| python-docx | Edit personal Word copies and write Word outputs |

Other deployments may have fewer packages. Check imports before specialized
handling: Pandas XLSX I/O needs an installed engine such as OpenPyXL.
`python-docx` provides the installed image's high-level DOCX API. It does not
guarantee lossless editing of every Word feature; verify changes to an owned copy.
Simple bounded DOCX text inspection can use the standard library's ZIP/XML
tools; state their coverage limits and verify edits to an owned copy. External
network access and source writes are not granted by the Python package.

Run analysis on uploaded or imported owned files. This standard-library example
reads `sales.csv` with `region` and `amount` columns, saves a new output, verifies
its contents and prints a chat link. Exclusive creation preserves existing
outputs; choose a new output name if it already exists.

```python
import csv
from collections import defaultdict
from decimal import Decimal
from urllib.parse import quote

from craft import USER_ROOT, owned_path

totals = defaultdict(Decimal)
with owned_path("sales.csv").open(encoding="utf-8", newline="") as stream:
    for row in csv.DictReader(stream):
        totals[row["region"]] += Decimal(row["amount"])

output = owned_path("outputs/sales summary.csv")
output.parent.mkdir(parents=True, exist_ok=True)
rows = sorted(totals.items())
with output.open("x", encoding="utf-8", newline="") as stream:
    writer = csv.writer(stream)
    writer.writerow(["region", "total"])
    writer.writerows(rows)
with output.open(encoding="utf-8", newline="") as stream:
    saved = list(csv.DictReader(stream))
assert saved == [{"region": region, "total": str(total)} for region, total in rows]
href = "/workspace/" + quote(output.relative_to(USER_ROOT).as_posix(), safe="/")
print(f"[{output.name}]({href})")
```

With Pandas and OpenPyXL installed, convert that saved CSV to a new XLSX file
and verify the round trip:

```python
from urllib.parse import quote

import pandas as pd

from craft import USER_ROOT, owned_path

summary = pd.read_csv(owned_path("outputs/sales summary.csv"))
workbook = owned_path("outputs/sales summary.xlsx")
with workbook.open("xb") as stream:
    summary.to_excel(stream, index=False, engine="openpyxl")
saved = pd.read_excel(workbook, engine="openpyxl")
pd.testing.assert_frame_equal(saved, summary, check_dtype=False)
href = "/workspace/" + quote(workbook.relative_to(USER_ROOT).as_posix(), safe="/")
print(f"[{workbook.name}]({href})")
```

## Build and owner provisioning

Use uv 0.12.23 to regenerate this package's standalone release lock from the
repository root:

```sh
uv lock --project packages/user_workspace
```

Commit `packages/user_workspace/uv.lock` with the package metadata. It covers the
SDK dependencies, the `lab` and `analysis` extras, and the `build` group containing Setuptools
80.9.0. The backend's root lock does not replace this package lock.

Build from the public package directory:

```sh
docker build --file packages/user_workspace/Dockerfile \
  --tag craft-user-workspace:0.1.6 packages/user_workspace
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
    "tools": ["list_files", "use_notebook", "read_notebook", "execute_code"],
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
    "tools": ["list_files", "use_notebook", "read_notebook", "execute_code"],
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

The agent receives only the configured tools, prefixed `workspace_`. The
configured allowlist determines which read, create, edit and execute operations
are available. Workspace calls
share the CRAFT run's 20-tool-call and 120-second limits. The transport dispatches
once; a lost response is an unknown outcome. Keep Markdown skill files under
`/workspace/skills` and refer to exact owned paths. The agent reads these files
through workspace tools; they are separate from the registered CRAFT skill catalog.

## SDK reads in an active notebook

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
| `await sdk.call("list_missions", {"query": "sim", "limit": 5})` | Resolve readable mission names and aliases to returned keys |
| `await sdk.call("list_parameters", {})` | Read parameter keys and registered units before numeric filters |
| `await sdk.search(query, **filters)` | Search readable source text |
| `await sdk.document(item, version=version)` | Read the selected exact item/version metadata and ordered outline |
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
retrieval trace and trusted run identity. Workspace `execute_code` and
`use_notebook` dispatches are pinned to the selected kernel; switching the active
notebook path is rejected during the lease. An overlapping run for the same
owner is rejected, and an existing kernel binding cannot be overwritten.

Finish, approval pause, error and cancellation withdraw the bridge before the
native transport closes and clear the same run's kernel binding. A lost kernel
connection still leaves the bridge withdrawn; the memory binding expires at the
run's original deadline. Unprovisioned kernels and standalone Python processes
raise an explicit missing-binding error. Native SDK mutations remain unavailable
until the CRAFT approval/resume path is integrated. `Craft(session)` and
`connect(url, run_token=...)` still consume explicitly injected authentication;
they issue no credentials or persistent runs.

## Import source copies

In Workspace, sign in to JupyterLab, search sources and select Import to copy an
original version into `USER_ROOT/imports`. Files up to 16 MB receive readable names
such as `report.xlsx`. If a name is taken, Import tries numeric alternatives such
as `report-2.xlsx`, up to 20 names, without overwriting existing files.

Source identity and the initial SHA-256 hash are recorded separately in
`USER_ROOT/imports/source-metadata/<UUID>.json`. If metadata cannot be confirmed
after the copy saves, the copy remains and Import prompts inspection in
JupyterLab before importing again. Open the copy to edit and export it. Originals
remain read-only; personal copies stay outside ingestion. Update external
originals manually.
