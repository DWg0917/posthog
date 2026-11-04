# Self-hosted async migration checks

The startup checks for historical async migrations distinguish events without a
group from events whose associated group data may need backfilling. Events configured
without person profiles are not required to have person data. Modern events can
also have an associated group with empty group properties, so this check alone
cannot distinguish them from historical rows needing a backfill.

The self-hosted deployment runs `run_async_migrations --complete-noop-migrations`
after the ClickHouse schema migrations and before starting event ingestion. On a
fresh database, this records migrations that are not needed as completed, so later
events do not cause historical migrations to be re-evaluated at startup. Existing
installations with uncompleted migrations must inspect their data and migration
state; do not mark a required migration completed without verifying it.
Completing no-op migrations does not send completion emails. Notifications for
migrations that actually run retain their existing behavior.

## Existing installations

Before deciding whether to run 0007, preserve a restorable backup of the event,
person, distinct-ID, group, and migration-state data. Record the migration state
and inspect the event data and its available source records. A missing group property
alone is ambiguous: current ingestion can leave it empty, while an older event may
also need backfilling. Do not treat either an empty field or a newer event timestamp
as proof that the migration is safe to skip.

The historical 0007 operation fills empty event properties from a snapshot of the
current person and group tables, not necessarily their values at event time. It can
therefore change the results of historical property-based queries, including for
modern events. If a source person no longer exists, the migration cannot recreate
that person from the event alone. Its existing postcheck tolerates a small fraction
of incomplete rows, so a completed status is not proof that every row was repaired.
Compare pre- and post-migration counts and representative rows independently.

An isolated single-node test on ClickHouse 26.6 rejected the original dictionary
mutation unless `allow_nondeterministic_mutations` was enabled for that operation.
Do not enable it globally or apply this workaround to replicated clusters without
proving dictionary consistency on every replica. The original postcheck also
misclassifies propertyless events; the candidate adjustment has only been tested in
the isolated environment and has not been included in the release code. Do not run
the original migration on a mixed dataset assuming it preserves those events.

The historical partition migration 0010 is intended for cloud deployments and
is not required for Hobby installations. Do not run it manually as a generic
repair: its partition bounds were set for a historical cleanup.

The SES account reputation poll checks AWS SES, not the SMTP settings used for
outgoing instance email. On a Hobby instance without AWS credentials, the poll
returns without logging an exception. Other SES failures continue to be logged.
