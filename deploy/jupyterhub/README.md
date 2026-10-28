# Workspaces through JupyterHub on the local cluster

Each person's JupyterLab workspace is a pod the Hub starts on demand and the idle culler stops, with
their files on a volume of their own. It runs on the same cluster as CRAFT (`deploy/README.md`),
through the [Zero to JupyterHub](https://z2jh.jupyter.org) chart, unmodified, configured by
`values.yaml`:

- Sign-in with craft-id. Hub names are CRAFT user IDs: CRAFT creates a person's Hub user on their
  first start and links it to their craft-id account, and only then can they sign in to the Hub.
- CRAFT reaches the Hub with one service token, `CRAFT_HUB_TOKEN`: start, stop and status
  (`GET /api/workspace`, `POST /api/workspace/start`, `POST /api/workspace/stop`), the agent's
  workspace actions, and the notebook control server for its read tools. The token has no expiry;
  rotate it by hand (below).
- Each workspace pod accepts traffic only from the Hub and its proxy, and reaches only CRAFT's API
  pod, the Hub and DNS.

## Start

CRAFT's chart must be installed first (`deploy/README.md`). Run these from the repository root.

Build the workspace image and its notebook control server in Colima's Docker engine:

```bash
docker build -t craft-user-workspace:local workspace
```

```bash
docker build -t craft-workspace-control:local -f workspace/mcp.Dockerfile workspace
```

Register the Hub with craft-id. It prints the client ID and secret once:

```bash
kubectl exec deploy/craft-id -- craft id clients add craft-hub --redirect-uri http://localhost:8200/hub/oauth_callback
```

Make a service token:

```bash
openssl rand -hex 32
```

Put the client and the token in `.local/runtime/jupyterhub/secrets.yaml` (never committed):

```yaml
hub:
  config:
    GenericOAuthenticator:
      client_id: <clientId>
      client_secret: <clientSecret>
  services:
    craft:
      api_token: <token>
```

Add the Hub to `.env`, with the same token, then recreate the `craft-env` Secret and restart CRAFT
(`deploy/README.md`, Change):

```
CRAFT_HUB_URL=http://proxy-public
CRAFT_HUB_PUBLIC_URL=http://localhost:8200
CRAFT_HUB_TOKEN=<token>
```

Install the chart:

```bash
helm repo add jupyterhub https://hub.jupyter.org/helm-chart/
```

```bash
helm install jupyterhub jupyterhub/jupyterhub --version 4.4.4 -f deploy/jupyterhub/values.yaml -f .local/runtime/jupyterhub/secrets.yaml
```

The browser reaches the Hub on `localhost:8200` and craft-id on `localhost:8100`; keep both
forwards open:

```bash
kubectl port-forward svc/proxy-public 8200:80
```

```bash
kubectl port-forward svc/craft-id 8100:8100
```

## Change

After a change to `values.yaml` or the token:

```bash
helm upgrade jupyterhub jupyterhub/jupyterhub --version 4.4.4 -f deploy/jupyterhub/values.yaml -f .local/runtime/jupyterhub/secrets.yaml
```

A rebuilt workspace image is used by each workspace the next time it starts.

## Stop

```bash
helm uninstall jupyterhub
```

Each person's volume stays until `kubectl delete pvc claim-<their user ID>`.
