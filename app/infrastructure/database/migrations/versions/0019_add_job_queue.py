"""Background jobs on PostgreSQL: Procrastinate 3.10.0's schema, attempt errors and job_overview."""

from pathlib import Path

from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels = None
depends_on = None

# A copy of procrastinate/sql/schema.sql at the pinned version; later versions ship migrations.
SCHEMA = Path(__file__).parents[1] / "procrastinate-3.10.0.sql"
TABLES = ["procrastinate_jobs", "procrastinate_events", "procrastinate_workers"]


def upgrade() -> None:
    op.execute(SCHEMA.read_text())
    # The app defers and reads jobs; workers (the same login) claim, finish and retry them.
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {', '.join(TABLES)} TO craft_app")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON procrastinate_periodic_defers TO craft_app")
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO craft_app")
    op.execute(
        """
        CREATE TABLE job_attempt_errors (
            job_id bigint NOT NULL,
            attempt integer NOT NULL,
            error text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_job_attempt_errors PRIMARY KEY (job_id, attempt)
        );
        GRANT SELECT, INSERT, UPDATE ON job_attempt_errors TO craft_app;
        """
    )
    # One row per job for TablePlus and the job routes: its API state, who asked, when it ran,
    # and its last error class.
    op.execute(
        """
        CREATE VIEW job_overview AS
        SELECT job.id AS job_id,
               job.task_name AS kind,
               job.queue_name AS queue,
               job.status::text AS status,
               CASE
                 WHEN job.status = 'todo' AND job.scheduled_at > now() THEN 'scheduled'
                 WHEN job.status = 'todo' THEN 'queued'
                 WHEN job.status = 'doing' AND job.abort_requested THEN 'stopping'
                 WHEN job.status = 'doing' THEN 'started'
                 WHEN job.status = 'succeeded' THEN 'finished'
                 WHEN job.status = 'cancelled' THEN 'canceled'
                 WHEN job.status = 'aborted' THEN 'stopped'
                 ELSE job.status::text
               END AS state,
               job.attempts,
               job.args ->> 'requested_by' AS requested_by,
               (SELECT min(at) FROM procrastinate_events e
                 WHERE e.job_id = job.id AND e.type = 'deferred') AS enqueued_at,
               (SELECT max(at) FROM procrastinate_events e
                 WHERE e.job_id = job.id AND e.type = 'started') AS started_at,
               (SELECT max(at) FROM procrastinate_events e
                 WHERE e.job_id = job.id
                   AND e.type IN ('succeeded', 'failed', 'cancelled', 'aborted')) AS finished_at,
               (SELECT error FROM job_attempt_errors e
                 WHERE e.job_id = job.id ORDER BY attempt DESC LIMIT 1) AS error,
               job.args
          FROM procrastinate_jobs job;
        GRANT SELECT ON job_overview TO craft_app;
        """
    )


def downgrade() -> None:
    op.execute("DROP VIEW job_overview")
    op.execute("DROP TABLE job_attempt_errors")
    for name in ["procrastinate_events", "procrastinate_periodic_defers", "procrastinate_jobs"]:
        op.execute(f"DROP TABLE {name} CASCADE")
    op.execute("DROP TABLE procrastinate_workers CASCADE")
    op.execute(
        """
        DO $$ DECLARE f record; BEGIN
          FOR f IN SELECT oid::regprocedure AS sig FROM pg_proc WHERE proname LIKE 'procrastinate_%'
          LOOP EXECUTE 'DROP FUNCTION ' || f.sig || ' CASCADE'; END LOOP;
        END $$;
        DROP TYPE procrastinate_job_to_defer_v1;
        DROP TYPE procrastinate_job_event_type;
        DROP TYPE procrastinate_job_status;
        """
    )
