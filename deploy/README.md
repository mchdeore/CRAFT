# Running CRAFT on the local cluster

CRAFT on Colima's built-in Kubernetes (k3s), with one Helm chart: the API, the workers, craft-id and PostgreSQL. Colima's Docker engine builds the image and k3s uses it directly, so there is no registry. Compose keeps working beside it.

- `craft/` the chart. Each setting comes from the `craft-env` Secret, made from `.env`; a container reads only the keys it needs, the same `CRAFT_*` variables compose passes.
- `jupyterhub/` the workspaces: JupyterHub's own chart beside this one (`jupyterhub/README.md`).
- `local.yaml` the local values: `worker-agent`, `worker-interactive` and `worker-bulk`, as compose has them, each with its queues, jobs at once per queue and memory limit.
- `worker-agent` runs inside the API's pod because chat turns call the API's MCP on `127.0.0.1`. The other workers have their own Deployments.
- PostgreSQL first boots with `app/infrastructure/database/initdb.sh` (`craft/initdb.sh` links to it).
- Probes: the API is live on `/health/live` and ready on `/health/ready`, so it's ready only once the database is at the latest migration (after `scripts/seed` or `alembic upgrade head`). The workers use `craft worker --check`, and craft-id uses `/health/live`.

## Start

Run these from the repository root. `.env` needs `CRAFT_ID_DB_PASSWORD` for craft-id.

```bash
colima start --kubernetes
```

```bash
docker build -t craft-app:local -f app/Dockerfile .
```

```bash
kubectl create secret generic craft-env --from-env-file=.env
```

```bash
mkdir -p .local/runtime/provider
```

```bash
helm install craft deploy/craft -f deploy/local.yaml --set provider.hostPath="$PWD/.local/runtime/provider"
```

```bash
kubectl rollout status statefulset/craft-postgres
```

Then load the frozen set through a port forward. Run the forward in a second terminal and keep it open. It uses port 55432, not 5432, so nothing meant for compose's database reaches the cluster's:

```bash
kubectl port-forward svc/craft-postgres 55432:5432
```

```bash
CRAFT_DATABASE_PORT=55432 scripts/seed cluster
```

Then reach the API on `localhost` (in another terminal):

```bash
kubectl port-forward svc/craft-api 8000:8000
```

```bash
curl -s localhost:8000/health/ready
```

craft-id answers on `localhost:8100` through `kubectl port-forward svc/craft-id 8100:8100`.

## Change

After a code change, rebuild the image and restart the pods:

```bash
docker build -t craft-app:local -f app/Dockerfile . && kubectl rollout restart deployment
```

After a change to `.env`, recreate the Secret and restart:

```bash
kubectl create secret generic craft-env --from-env-file=.env --dry-run=client -o yaml | kubectl apply -f - && kubectl rollout restart deployment
```

## Checks against the cluster's database

`scripts/check` can use the cluster's PostgreSQL (its `craft_test` database) instead of its own container. Keep the same forward open while the checks run:

```bash
kubectl port-forward svc/craft-postgres 55491:5432
```

```bash
CHECK_DB_PORT=55491 scripts/check
```

## Network policies are enforced

Colima's k3s enforces NetworkPolicy. This test serves a page in a scratch namespace, reads it from another pod, applies `deny-all-test.yaml` and reads it again. The second read should fail (the connection is refused).

```bash
kubectl create namespace netpol-test
```

```bash
kubectl -n netpol-test run web --image=craft-app:local --image-pull-policy=Never --port=8080 --expose -- python -m http.server 8080 -d /app
```

```bash
kubectl -n netpol-test wait --for=condition=Ready pod/web
```

```bash
kubectl -n netpol-test run probe --rm -i --restart=Never --image=craft-app:local --image-pull-policy=Never -- python -c "import urllib.request as r; print(r.urlopen('http://web:8080', timeout=5).status)"
```

```bash
kubectl -n netpol-test apply -f deploy/deny-all-test.yaml
```

Run the probe again: it now fails with a timeout. Then remove the test:

```bash
kubectl delete namespace netpol-test
```

## Stop

```bash
helm uninstall craft
```

The database's volume stays until `kubectl delete pvc data-craft-postgres-0`. `colima stop` stops everything, and `colima delete` removes the cluster.
