# Process lifecycle

## 1. Ownership

`packages/process_manager/` exposes `ProcessManager`, `ProcessSpec` and `ContainerSpec` for controller-owned services. Stable service IDs identify agent-runtime processes, tool workers and other services. The application attachment lives in `app/infrastructure/processes.py` and the FastAPI lifespan.

Set `CRAFT_PROCESS_ROOT` to a private runtime folder, such as `var/processes`, to enable the attachment. An empty value leaves it disabled. Startup creates a new owned instance with no services. Only controller code adds service specifications; HTTP and MCP requests cannot create processes. Shutdown closes the instance even when the lifespan is cancelled, then disposes the database pool.

## 2. Lifecycle

`add`, `start`, `stop`, `remove`, `update`, `status`, `start_all` and `close` form one serialized interface. At most 16 services exist in an instance. Startup uses ascending priority and service ID; shutdown reverses that order. Queue dispatch priority is a separate concern.

Process lifecycle uses supervisord. It owns startup retries, expected exit codes, restart policies and process-group termination. Commands retain their literal argument arrays through private configuration and `os.execv`; no shell is added. Provider credentials and the controller environment are not inherited. Child output and daemon logs are disabled. The control endpoint is a private Unix socket with mode 0600, bounded requests and no public listener.

Restart policies are `never`, `on-failure` and `always`. A normal exit has code zero. Startup retries range from zero to five; readiness waits range from one to 30 seconds. Each stop allows one to 15 seconds before the backend's kill fallback. Reverse shutdown is sequential and bounded by the sum of the per-service deadlines. Removal affects only the recorded service. A valid update stops and replaces that service; a failed replacement is reported explicitly.

## 3. Readiness and limits

Process state and readiness are separate. A running process with no application probe has `unchecked` health. An optional bounded probe tests whether its configured localhost TCP port accepts a connection; it does not inspect application responses. A failed probe reports `failed` health, and startup timeout stops the service. Process memory, CPU and PID hard limits are refused because supervisord does not enforce them.

Container lifecycle uses the Docker SDK. A container requires a pinned digest or local image ID, numeric non-root user, memory/CPU/PID limits, isolated network, read-only root filesystem, dropped capabilities and no added privileges. It has a bounded writable temporary filesystem, no host mounts, no proxy configuration import and no captured output. Limits are inspected before starting it. Ownership labels are checked before stopping or removing it. A lost creation response is reconciled by the exact owned name and labels before retrying; cleanup verifies that no container with the instance's ownership label remains.

Backend refusal, failed startup, unsupported limits and incomplete cleanup return fixed error codes. They never report a healthy service or expose backend diagnostics. Original lifecycle ownership remains available when cleanup fails.

Related interfaces: [components](components.md), [runtime](runtime.md), [Python workspace](python.md), [model jobs](jobs.md) and [code layout](../spec/06-code-layout.md).
