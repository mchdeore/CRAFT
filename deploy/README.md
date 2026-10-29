# Running CRAFT on the local cluster

CRAFT on Colima's built-in Kubernetes (k3s), with one Helm chart: the API, the workers, craft-id and PostgreSQL. Colima's Docker engine builds the image and k3s uses it directly, so there is no registry. Compose keeps working beside it.

- `craft/` the chart. Each setting comes from the `craft-env` Secret, made from `.env`; a container reads only the keys it needs, the same `CRAFT_*` variables compose passes.
- `jupyterhub/` the workspaces: JupyterHub's own chart beside this one (`jupyterhub/README.md`).
- `local.yaml` the local values: `worker-agent`, `worker-interactive` and `worker-bulk`, as compose has them, each with its queues, jobs at once per queue and memory limit.
- `worker-agent` runs inside the API's pod because chat turns call the API's MCP on `127.0.0.1`. The other workers have their own Deployments.
- PostgreSQL first boots with `app/infrastructure/database/initdb.sh` (`craft/initdb.sh` links to it).
- Probes: the API is live on `/health/live` and ready on `/health/ready`, so it's ready only once the database is at the latest migration (after `scripts/seed` or `alembic upgrade head`). The workers check their heartbeat file with `python -I -S app/infrastructure/heartbeat.py`, which never imports the app, and craft-id uses `/health/live`. Every probe sets its own timeout: on a busy machine the 1 s default killed a healthy database.

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

## Notebook runs

An unattended notebook run is a Kubernetes Job on the person's volume (`claim-<user ID>`), started by the interactive worker with its own service account (`deploy/craft/templates/notebook-runs.yaml`). The account may only create, read and delete Jobs, an admission policy holds them to the run's shape, and a network policy gives run pods no network. The run sees its folder read-only; a trusted last step moves the executed copy and new outputs beside the notebook without overwriting.

After building both images and upgrading both charts, the cluster tests run real Jobs through the worker:

```bash
CRAFT_DATABASE_HOST=127.0.0.1 CRAFT_DATABASE_PORT=55481 uv run pytest -m cluster app/workspace/tests/test_notebook_jobs_cluster.py
```

They need the check database (`scripts/check` starts it). Kubernetes has no per-pod process limit; kubelet's `podPidsLimit` bounds every pod on the node, which `K-vm` sets on the VM.

Volumes made before `claim-{unescaped_username}` are named `claim-x-…` and are no longer mounted; delete them with `kubectl delete pvc <name>` once nothing in them is needed.

## Backups

With `backup.enabled` (on in `local.yaml`), the `craft-backup` CronJob runs `pg_dump -Fc` of the whole database every night at 03:30 UTC into `/var/lib/craft-backup/db/<UTC date>.dump` on the node's own disk. A dump that fails or is under 1 KB fails the Job and deletes nothing; after a good one, only the newest 14 are kept. The folder is outside every volume claim, so `helm uninstall craft` and deleting the database's volume leave the dumps in place. They share the node's disk, so they don't survive losing it: on the VM, its weekly disk snapshot covers that.

Run one now:

```bash
kubectl create job --from=cronjob/craft-backup craft-backup-now
```

See the dumps:

```bash
colima ssh -p craft-k8s -- ls -l /var/lib/craft-backup/db
```

Check the newest one: a short-lived pod restores it into its own throwaway PostgreSQL and prints row counts, without touching the cluster's database. Run it monthly:

```bash
scripts/backup-check
```

Restore a dump over the database. Stop the API and workers first, then put them back:

```bash
kubectl scale deployment -l app.kubernetes.io/instance=craft --replicas=0
```

```bash
colima ssh -p craft-k8s -- cat /var/lib/craft-backup/db/<date>.dump | kubectl exec -i craft-postgres-0 -- pg_restore -U postgres --clean --if-exists --exit-on-error -d craft_dev
```

```bash
kubectl scale deployment -l app.kubernetes.io/instance=craft --replicas=1
```

## Stop

```bash
helm uninstall craft
```

The database's volume stays until `kubectl delete pvc data-craft-postgres-0`. `colima stop` stops everything, and `colima delete` removes the cluster.
