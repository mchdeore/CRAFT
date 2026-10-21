# CRAFT frontend

The frontend uses React, TypeScript and Vite. Use Node.js 24 with npm. From a clean checkout, install both the shared API type dependencies and frontend dependencies:

```sh
npm ci --prefix api --ignore-scripts --no-audit --no-fund
npm ci --prefix web --no-audit --no-fund
cd web
```

Live mode is the default and requires an authenticated CRAFT backend. Start the backend separately, then configure the development proxy with a loopback origin:

```sh
CRAFT_BACKEND_URL=http://127.0.0.1:8000 npm run dev
```

For a local demo, seed the backend with demo data and sign-in passwords first, from the repository root (any value of 12 or more characters):

```sh
SEED_DEMO_PASSWORD='choose-a-local-password' uv run craft seed demo
```

Then sign in as `demo`, `restricted` or `admin`. Set the backend's `CRAFT_MCP_URL` to its own `/mcp` address when it does not run on port 8000.

Run every frontend test, lint the full frontend, and build:

```sh
npm test
npm run lint
npm run build
```

`npm test` loads TypeScript with the existing test loader and selects a unique retained production artifact directory outside the source tree. Its path is printed. To retain artifacts at a chosen location, set `CRAFT_RELEASE_TEST_OUTPUT` to a nonexistent absolute path outside the source tree; existing paths are rejected.

The production build writes `dist/`, ignores environment files, and includes only curated public assets. Serve it with the authenticated backend through the same origin and its `/api/` routes. The development proxy setting does not configure a production backend.
