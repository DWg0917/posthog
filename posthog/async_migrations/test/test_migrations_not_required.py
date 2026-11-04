from uuid import uuid4

from django.test import override_settings

from posthog.async_migrations.setup import ALL_ASYNC_MIGRATIONS
from posthog.async_migrations.test.util import AsyncMigrationBaseTest
from posthog.clickhouse.client import sync_execute
from posthog.models.event.util import create_event
from posthog.models.person.sql import COMMENT_DISTINCT_ID_COLUMN_SQL

# No pytest.mark.async_migrations here on purpose: the Core shards filter uses
# -m "not async_migrations", so this guard must run there. Do not re-add the marker.


# Async migrations are data migrations aimed at getting users from an old schema to a new schema
# Fresh installs should have the new schema, however. So check that async migrations are being
# written correctly such that this is the case
#
# Note that 0004_replicated_schema is currently an exception for this
class TestAsyncMigrationsNotRequired(AsyncMigrationBaseTest):
    def setUp(self):
        super().setUp()
        sync_execute(COMMENT_DISTINCT_ID_COLUMN_SQL())
        sync_execute("TRUNCATE TABLE sharded_events")

    def test_async_migrations_not_required_on_fresh_instances(self):
        for name, migration in ALL_ASYNC_MIGRATIONS.items():
            self.assertFalse(
                migration.is_required(),
                f"Async migration {name} is_required returned True",
            )

    def test_0007_does_not_require_backfill_for_event_without_groups(self):
        create_event(
            event_uuid=uuid4(),
            event="test",
            team=self.team,
            distinct_id="test",
            person_id=uuid4(),
            person_properties={},
            person_created_at="2024-01-01T00:00:00Z",
        )
        self.assertFalse(ALL_ASYNC_MIGRATIONS["0007_persons_and_groups_on_events_backfill"].is_required())

    def test_0007_requires_backfill_for_missing_person(self):
        create_event(event_uuid=uuid4(), event="test", team=self.team, distinct_id="test")
        self.assertTrue(ALL_ASYNC_MIGRATIONS["0007_persons_and_groups_on_events_backfill"].is_required())

    def test_0007_does_not_require_person_data_for_propertyless_event(self):
        create_event(event_uuid=uuid4(), event="test", team=self.team, distinct_id="test", person_mode="propertyless")
        self.assertFalse(ALL_ASYNC_MIGRATIONS["0007_persons_and_groups_on_events_backfill"].is_required())

    def test_0007_requires_review_for_missing_group_data(self):
        create_event(
            event_uuid=uuid4(),
            event="test",
            team=self.team,
            distinct_id="test",
            properties={"$group_0": "example"},
            person_id=uuid4(),
            person_properties={},
            person_created_at="2024-01-01T00:00:00Z",
        )
        self.assertTrue(ALL_ASYNC_MIGRATIONS["0007_persons_and_groups_on_events_backfill"].is_required())

    @override_settings(CLOUD_DEPLOYMENT=None, DEBUG=False)
    def test_0010_is_not_required_for_self_hosted(self):
        self.assertFalse(ALL_ASYNC_MIGRATIONS["0010_move_old_partitions"].is_required())
