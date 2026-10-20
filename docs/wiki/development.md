# Development

## 1. Environment

The root uv workspace resolves the application and local component packages into one development environment. Each package retains its own import namespace and dependency declaration. `uv.lock` records the complete resolved set.

```sh
uv sync --locked
uv run ruff check .
uv run ruff format --check .
uv run pyright
```

The application image installs production dependencies, including the lifecycle package. The runtime, Python workspace, document and fleet prototypes are development dependencies. The Python workspace prototype image builds separately from its own lock.

The shared [workspace](workspace.md) uses `packages/user_workspace/` and its separate JupyterLab image. Each owner has private storage and MCP controls, with Jupyter sign-in separate from CRAFT. The control service uses a separate environment for MCP SDK 2; the workspace package uses MCP SDK 1. Native SDK reads require an existing notebook kernel selected by the chat run's controller. The [dependency reference](../Dependencies-and-Integration.md) owns exact pins and environment requirements.

## 2. Checks

Configure a disposable PostgreSQL database using the application settings, then run migrations and tests.

```sh
uv run alembic upgrade head
uv run alembic check
uv run pytest
```

Pytest uses isolated module imports so components can use natural test filenames. Runtime test fixtures have an explicit test-support path. Live service cases require their declared settings; Docker workspace cases require the restricted image and daemon. These prerequisites determine which checks can run.

## 3. Documentation

Topic pages link to the canonical contract and specification sections. Build the navigation site through the documentation dependency group. Formatted downloads use the existing document build path and print styles.

```sh
docs/tools/build.sh
```

The build stages versioned public documents and adds a Markdown download and a GitHub edit link to each topic. Downloads contain the public source file. The site check verifies those links and refuses hidden files, scripts and data artifacts in the published output.

See [components](components.md), [connections](../spec/02-connections.md), [API contract](../API-Contract.md) and [document downloads](../downloads.md).
