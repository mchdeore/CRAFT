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

Development mocks require an explicit opt-in:

```sh
CRAFT_BACKEND_URL=http://127.0.0.1:8000 VITE_MOCK=true npm run dev:mock
```

The mock document download fixture is intentionally absent; that development download requires a separately supplied fixture.

Run every frontend test, lint the full frontend, and build:

```sh
npm test
npm run lint
npm run build
```

`npm test` loads TypeScript with the existing test loader and selects a unique retained production artifact directory outside the source tree. Its path is printed. To retain artifacts at a chosen location, set `CRAFT_RELEASE_TEST_OUTPUT` to a nonexistent absolute path outside the source tree; existing paths are rejected.

The production build writes `dist/`, disables all mocks, ignores environment files, and includes only curated public assets. Serve it with the authenticated backend through the same origin and its `/api/` routes. The development proxy setting does not configure a production backend.
