# Process lifecycle

## Ownership

`app/processes/` exposes `ProcessManager`, `ProcessSpec` and `ContainerSpec` for controller-owned services. Stable service IDs identify agent-runtime processes, tool workers and other services. The application attachment lives in `app/infrastructure/processes.py` and the FastAPI lifespan.

Set `CRAFT_PROCESS_ROOT` to a private runtime folder, such as `.local/runtime/processes`, to enable the attachment. An empty value leaves it disabled. Startup creates a new owned instance with no services. Only controller code adds service specifications; HTTP and MCP requests cannot create processes. Shutdown closes the instance even when the lifespan is cancelled, then disposes the database pool.

## Lifecycle

`add`, `start`, `stop`, `remove`, `update`, `status`, `start_all` and `close` form one serialized interface. At most 16 services exist in an instance. Startup uses ascending priority and service ID; shutdown reverses that order. Queue dispatch priority is a separate concern.

Process lifecycle uses supervisord. It owns startup retries, expected exit codes, restart policies and process-group termination. Commands retain their literal argument arrays through private configuration and `os.execv`; no shell is added. Provider credentials and the controller environment are not inherited. Child output and daemon logs are disabled. The control endpoint is a private Unix socket with mode 0600, bounded requests and no public listener.

Restart policies are `never`, `on-failure` and `always`. A normal exit has code zero. Startup retries range from zero to five; readiness waits range from one to 30 seconds. Each stop allows one to 15 seconds before the backend's kill fallback. Reverse shutdown is sequential and bounded by the sum of the per-service deadlines. Removal affects only the recorded service. A valid update stops and replaces that service; a failed replacement is reported explicitly.

## Readiness and limits

Process state and readiness are separate. A running process with no application probe has `unchecked` health. An optional bounded probe tests whether its configured localhost TCP port accepts a connection; it does not inspect application responses. A failed probe reports `failed` health, and startup timeout stops the service. Process memory, CPU and PID hard limits are refused because supervisord does not enforce them.

Container lifecycle uses the Docker SDK. A container requires a pinned digest or local image ID, numeric non-root user, memory/CPU/PID limits, isolated network, read-only root filesystem, dropped capabilities and no added privileges. It has a bounded writable temporary filesystem, no host mounts, no proxy configuration import and no captured output. Limits are inspected before starting it. Ownership labels are checked before stopping or removing it. A lost creation response is reconciled by the exact owned name and labels before retrying; cleanup verifies that no container with the instance's ownership label remains.

Backend refusal, failed startup, unsupported limits and incomplete cleanup return fixed error codes. They never report a healthy service or expose backend diagnostics. Original lifecycle ownership remains available when cleanup fails.

## Background jobs

Background jobs live in PostgreSQL, run by [Procrastinate](https://procrastinate.readthedocs.io/) ([`app/infrastructure/queue.py`](https://github.com/mchdeore/CRAFT/blob/main/app/infrastructure/queue.py)). There is no separate queue service. Three queues each have their own worker, so a long job never holds up a person who is waiting:

| Worker | Queue | Jobs at once | Automatic retries | Stop grace |
|---|---|---|---|---|
| `worker-agent` | `agent`: chat turns | 20 | None; a crash marks the run interrupted | 900 s |
| `worker-interactive` | `interactive`: someone is waiting | 4 | 5 s, 30 s, 2 min | 120 s |
| `worker-bulk` | `bulk`: long batch work | 1 | 1 min, 5 min, 30 min | 120 s |

All three run the same image and command, `craft worker`. `CRAFT_WORKER_QUEUES` and `CRAFT_WORKER_CONCURRENCY` choose the queues and the number of jobs at once; on a laptop one worker can take all three queues. Each worker is one process; more capacity means more containers. Inside, async jobs run on one event loop and blocking jobs in threads.

- **Claiming:** a worker claims a job with `FOR UPDATE SKIP LOCKED`, so two workers never take the same job. `LISTEN`/`NOTIFY` wakes a worker as soon as a job arrives.
- **Retries:** only transient failures are retried: timeouts, 408, 429, 5xx and connection errors. A `Retry-After` wins over the queue's wait. Kinds that change things or cost model money (chat turns, notebook runs) are never retried.
- **Crashes:** each worker writes a heartbeat every 10 s. Any worker retries or fails the jobs of a worker silent for 30 s, records `WorkerLost`, and for a chat turn marks the run `run_interrupted`.
- **Shutdown:** on SIGTERM a worker takes no new jobs and finishes its current ones within the grace period. A worker started before the migrations waits for them.
- **Arguments:** job arguments are JSON and hold IDs only, never document text, prompts or tokens. The worker logs one line per job outcome.

A job kind is one `register_job(kind, func, queue=…, retry=…, lock_key=…)` call. Effects must be idempotent: a job may run more than once. A `lock_key` makes a second request while one is queued or running return that job.

Signed-in users see and manage their own jobs; administrators see all of them:

- `GET /api/jobs` lists jobs, newest first (`status`, `offset`, `limit` 1–100)
- `GET /api/jobs/{jobId}` reads one job: kind, queue, status, attempts and the last error class
- `POST /api/jobs/{jobId}/retry` runs a failed, canceled or stopped job again as a new job
- `POST /api/jobs/{jobId}/cancel` cancels a queued job or asks a running one to stop (`stopping`, then `stopped`)
- `GET /health/queue` counts jobs per queue and state, with the oldest queued job's age and the dead (failed) count

There is no admin panel. The `job_overview` view gives one row per job for a database client; every state change is also a row in `procrastinate_events`. `craft worker --check` is the container health check: it passes while the worker has written a heartbeat in the last minute.

Related interfaces: [components](components.md), [runtime](runtime.md), [workspace](workspace.md) and [code layout](development.md).
