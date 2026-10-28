"""Queue measurements: job_metrics view, queue_samples table, agent run counters."""

from alembic import op

revision: str = "0026"
down_revision: str | None = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # One row per finished or still-open job: waits and runs derived from Procrastinate events.
    op.execute(
        """
        CREATE VIEW job_metrics AS
        SELECT job.id AS job_id,
               job.task_name AS kind,
               job.queue_name AS queue,
               job.args ->> 'requested_by' AS requested_by,
               job.attempts,
               CASE
                 WHEN job.status = 'todo' AND job.scheduled_at > now() THEN 'scheduled'
                 WHEN job.status = 'todo' THEN 'queued'
                 WHEN job.status = 'doing' AND job.abort_requested THEN 'stopping'
                 WHEN job.status = 'doing' THEN 'started'
                 WHEN job.status = 'succeeded' THEN 'finished'
                 WHEN job.status = 'cancelled' THEN 'canceled'
                 WHEN job.status = 'aborted' THEN 'stopped'
                 WHEN job.status = 'failed' THEN 'failed'
                 ELSE job.status::text
               END AS outcome,
               (SELECT min(at) FROM procrastinate_events e
                 WHERE e.job_id = job.id AND e.type = 'deferred') AS enqueued_at,
               (SELECT max(at) FROM procrastinate_events e
                 WHERE e.job_id = job.id AND e.type = 'started') AS started_at,
               (SELECT max(at) FROM procrastinate_events e
                 WHERE e.job_id = job.id
                   AND e.type IN ('succeeded', 'failed', 'cancelled', 'aborted')) AS finished_at,
               extract(epoch from (
                 (SELECT max(at) FROM procrastinate_events e
                   WHERE e.job_id = job.id AND e.type = 'started')
                 - (SELECT min(at) FROM procrastinate_events e
                   WHERE e.job_id = job.id AND e.type = 'deferred')
               )) AS wait_seconds,
               extract(epoch from (
                 (SELECT max(at) FROM procrastinate_events e
                   WHERE e.job_id = job.id
                     AND e.type IN ('succeeded', 'failed', 'cancelled', 'aborted'))
                 - (SELECT max(at) FROM procrastinate_events e
                   WHERE e.job_id = job.id AND e.type = 'started')
               )) AS run_seconds,
               (SELECT error FROM job_attempt_errors e
                 WHERE e.job_id = job.id ORDER BY attempt DESC LIMIT 1) AS error
          FROM procrastinate_jobs job;
        GRANT SELECT ON job_metrics TO craft_app;
        """
    )
    # Point-in-time samples written by workers; used to see saturation and peaks.
    op.execute(
        """
        CREATE TABLE queue_samples (
            id bigint GENERATED ALWAYS AS IDENTITY,
            sampled_at timestamptz NOT NULL DEFAULT now(),
            queue text NOT NULL,
            queued integer NOT NULL,
            running integer NOT NULL,
            workers_alive integer NOT NULL,
            concurrency integer NOT NULL,
            oldest_wait_seconds double precision,
            CONSTRAINT pk_queue_samples PRIMARY KEY (id),
            CONSTRAINT ck_queue_samples_counts CHECK (
                queued >= 0 AND running >= 0 AND workers_alive >= 0 AND concurrency >= 1
            )
        );
        CREATE INDEX ix_queue_samples_queue_sampled_at ON queue_samples (queue, sampled_at);
        GRANT SELECT, INSERT ON queue_samples TO craft_app;
        GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO craft_app;
        """
    )
    # Counters only: no prompts, answers or tool arguments.
    op.execute(
        """
        ALTER TABLE agent_runs
            ADD COLUMN tool_calls integer,
            ADD COLUMN active_seconds double precision,
            ADD COLUMN end_reason text;
        ALTER TABLE agent_runs
            ADD CONSTRAINT ck_agent_runs_end_reason CHECK (
                end_reason IS NULL OR end_reason IN (
                    'finished', 'paused', 'time_limit', 'tool_limit', 'failed', 'cancelled'
                )
            );
        ALTER TABLE agent_runs
            ADD CONSTRAINT ck_agent_runs_tool_calls CHECK (
                tool_calls IS NULL OR tool_calls >= 0
            );
        ALTER TABLE agent_runs
            ADD CONSTRAINT ck_agent_runs_active_seconds CHECK (
                active_seconds IS NULL OR active_seconds >= 0
            );
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE agent_runs
            DROP CONSTRAINT IF EXISTS ck_agent_runs_active_seconds,
            DROP CONSTRAINT IF EXISTS ck_agent_runs_tool_calls,
            DROP CONSTRAINT IF EXISTS ck_agent_runs_end_reason;
        ALTER TABLE agent_runs
            DROP COLUMN IF EXISTS end_reason,
            DROP COLUMN IF EXISTS active_seconds,
            DROP COLUMN IF EXISTS tool_calls;
        """
    )
    op.execute("DROP TABLE queue_samples")
    op.execute("DROP VIEW job_metrics")
