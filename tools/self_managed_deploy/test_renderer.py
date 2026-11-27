import json
import tempfile
from copy import deepcopy
from pathlib import Path

import unittest

from tools.self_managed_deploy.renderer import REQUIRED_ENDPOINTS, _normalize_repository_paths, render_multihost_compose

ENDPOINTS = {
    "postgres_url": "postgres://posthog:secret@pg.internal:5432/posthog",
    "postgres_hosts": "10.20.0.11:5433,10.20.0.12:5433,10.20.0.13:5433",
    "postgres_superuser_password": "superuser-secret",
    "postgres_replication_password": "replication-secret",
    "clickhouse_hosts": "ch-1.internal:9000,ch-2.internal:9000,ch-3.internal:9000",
    "clickhouse_user": "app",
    "clickhouse_password": "secret",
    "kafka_hosts": "kafka-1.internal:9092,kafka-2.internal:9092,kafka-3.internal:9092",
    "redis_hosts": "10.20.0.11:6379,10.20.0.12:6379,10.20.0.13:6379",
    "flags_redis_hosts": "10.20.0.11:6380,10.20.0.12:6380,10.20.0.13:6380",
    "redis_url": "rediss://:redis-secret@redis.internal:6380/0",
    "flags_redis_url": "rediss://:flags-redis-secret@flags-redis.internal:6380/0",
    "valkey_host": "valkey.internal",
    "valkey_port": 6381,
    "valkey_hosts": "10.20.0.11:6381,10.20.0.12:6381,10.20.0.13:6381",
    "seaweedfs_hosts": "10.20.0.11:8333,10.20.0.12:8333,10.20.0.13:8333",
    "seaweedfs_database_password": "seaweedfs-database-secret",
    "object_storage_endpoint": "https://s3.internal",
    "object_storage_access_key_id": "s3-access",
    "object_storage_secret_access_key": "s3-secret",
    "object_storage_region": "us-west-2",
    "object_storage_public_endpoint": "https://s3.public.internal",
    "session_replay_endpoint": "https://replay-s3.internal",
    "session_replay_access_key_id": "replay-access",
    "session_replay_secret_access_key": "replay-secret",
    "session_replay_region": "eu-west-1",
    "temporal_hosts": "10.20.0.11:7233,10.20.0.12:7233,10.20.0.13:7233",
    "temporal_database_password": "temporal-database-secret",
    "etcd_endpoints": "https://etcd.internal:2379",
    "domain": "posthog.example.com",
    "trusted_proxy_cidrs": ["10.0.0.0/8"],
}


def compose_config() -> dict[str, object]:
    services: dict[str, object] = {
        "db": {"image": "postgres", "volumes": ["postgres-data:/data"]},
        "clickhouse": {"image": "clickhouse", "volumes": ["clickhouse-data:/data"]},
        "kafka": {"image": "redpanda"},
        "proxy": {
            "image": "caddy",
            "ports": ["80:80", "443:443"],
            "environment": {
                "CADDY_HOST": "https://posthog.example.com",
                "CADDYFILE": "\n    @objectstorage {\n        path /posthog\n        path /posthog/\n        path /posthog/*\n    }\n\n    handle @objectstorage {\n        reverse_proxy objectstorage:19000\n    }\n",
            },
            "depends_on": ["web"],
        },
        "web": {
            "image": "posthog",
            "command": "/compose/start",
            "volumes": [{"type": "bind", "source": "/checkout/compose", "target": "/compose"}],
            "environment": {
                "DATABASE_URL": "postgres://posthog:posthog@db:5432/posthog",
                "PERSONS_DB_READER_URL": "postgres://posthog:posthog@db:5432/posthog_persons",
                "CYCLOTRON_NODE_DATABASE_URL": "postgres://posthog:posthog@db:5432/cyclotron_node",
                "PGHOST": "db",
                "PGPORT": "5432",
                "PGUSER": "posthog",
                "PGPASSWORD": "posthog",
                "CLICKHOUSE_HOST": "clickhouse",
                "CLICKHOUSE_LOGS_CLUSTER_HOST": "clickhouse",
                "CLICKHOUSE_SECURE": "true",
                "KAFKA_HOSTS": "kafka:9092",
                "REDIS_URL": "redis://redis7:6379/",
                "LOGS_REDIS_HOST": "redis7",
                "LOGS_REDIS_PORT": "6379",
                "LOGS_REDIS_TLS": "false",
                "CDP_VALKEY_HOST": "valkey",
                "CDP_VALKEY_PORT": "6379",
                "OBJECT_STORAGE_ENDPOINT": "http://objectstorage:19000",
                "OBJECT_STORAGE_ACCESS_KEY_ID": "local-access",
                "OBJECT_STORAGE_SECRET_ACCESS_KEY": "local-secret",
                "OBJECT_STORAGE_PUBLIC_ENDPOINT": "https://posthog.example.com",
                "TEMPORAL_HOST": "temporal",
                "TEMPORAL_CLI_ADDRESS": "temporal:7233",
                "ETCD_ENDPOINTS": "http://etcd:2379",
            },
            "depends_on": ["db", "clickhouse", "kafka"],
        },
        "worker": {
            "image": "posthog",
            "command": "./bin/docker-worker-celery --with-embedded-scheduler",
            "environment": {"DATABASE_URL": "postgres://posthog:posthog@db:5432/posthog"},
        },
        "feature-flags": {
            "image": "ghcr.io/posthog/posthog/feature-flags:master",
            "build": {"context": "rust/", "args": {"BIN": "feature-flags"}},
            "environment": {"KAFKA_HOSTS": "kafka:9092"},
        },
        "temporal-django-worker": {
            "image": "posthog",
            "command": ["/compose/temporal-django-worker"],
            "volumes": [{"type": "bind", "source": "/checkout/compose", "target": "/compose"}],
            "environment": {"TEMPORAL_HOST": "temporal"},
        },
        "cymbal": {
            "image": "cymbal",
            "environment": {
                "PERSONS_URL": "postgres://posthog:posthog@db:5432/posthog",
                "ISSUE_BUCKETS_REDIS_URL": "redis://redis7:6379/",
            },
        },
        "livestream": {"image": "ghcr.io/posthog/posthog/livestream:master"},
        "streaming-consumer": {"image": "posthog", "depends_on": ["kafka"]},
        "ingestion-logs": {"image": "posthog-node", "environment": {"PLUGIN_SERVER_MODE": "ingestion-logs"}},
        "ingestion-sessionreplay": {
            "image": "posthog-node",
            "environment": {
                "SESSION_RECORDING_V2_S3_ENDPOINT": "http://seaweedfs:8333",
                "SESSION_RECORDING_V2_S3_ACCESS_KEY_ID": "any",
                "SESSION_RECORDING_V2_S3_SECRET_ACCESS_KEY": "any",
            },
        },
        "kafka-init": {"image": "redpanda", "command": ["-c", "rpk topic list --brokers kafka:9092"]},
        "personhog-etcd-init": {
            "image": "etcd",
            "entrypoint": ["etcdctl", "--endpoints=http://etcd:2379", "put", "/personhog/config/changelog", "{}"],
        },
        "personhog-tables-ready": {
            "image": "postgres",
            "environment": {"PGPASSWORD": "posthog"},
            "entrypoint": ["sh", "-c", "until psql -h db -U posthog -d posthog -tAc 'SELECT true'"],
        },
        "asyncmigrationscheck": {"image": "posthog", "command": "python manage.py run_async_migrations --check"},
    }
    return {"services": services, "volumes": {"postgres-data": {}, "clickhouse-data": {}}}


class RenderMultihostComposeTest(unittest.TestCase):
    def test_rejects_fewer_than_three_kafka_brokers_for_replicated_topics(self) -> None:
        endpoints = deepcopy(ENDPOINTS)
        endpoints["kafka_hosts"] = "kafka-1.internal:9092,kafka-2.internal:9092"

        with self.assertRaisesRegex(ValueError, "at least three unique Kafka brokers"):
            render_multihost_compose(deepcopy(compose_config()), endpoints, "direct")

    def test_rejects_fewer_than_three_clickhouse_nodes(self) -> None:
        endpoints = deepcopy(ENDPOINTS)
        endpoints["clickhouse_hosts"] = "ch-1.internal:9000,ch-2.internal:9000"

        with self.assertRaisesRegex(ValueError, "at least three unique ClickHouse nodes"):
            render_multihost_compose(deepcopy(compose_config()), endpoints, "direct")

    def test_rejects_public_postgres_hosts(self) -> None:
        endpoints = deepcopy(ENDPOINTS)
        endpoints["postgres_hosts"] = "8.8.8.8:5433,10.20.0.12:5433,10.20.0.13:5433"

        with self.assertRaisesRegex(ValueError, "private PostgreSQL data-node IPs"):
            render_multihost_compose(deepcopy(compose_config()), endpoints, "direct")

    def test_rejects_duplicate_clickhouse_nodes(self) -> None:
        endpoints = deepcopy(ENDPOINTS)
        endpoints["clickhouse_hosts"] = "ch-1.internal:9000,ch-2.internal:9000,ch-1.internal:9000"

        with self.assertRaisesRegex(ValueError, "at least three unique ClickHouse nodes"):
            render_multihost_compose(deepcopy(compose_config()), endpoints, "direct")

    def test_requires_clickhouse_user_created_by_data_node_configuration(self) -> None:
        endpoints = deepcopy(ENDPOINTS)
        endpoints["clickhouse_user"] = "posthog"

        with self.assertRaisesRegex(ValueError, "clickhouse_user must be app"):
            render_multihost_compose(deepcopy(compose_config()), endpoints, "direct")

    def test_normalizes_installer_paths_to_checkout_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "rust").mkdir()
            (root / "docker/kafka").mkdir(parents=True)
            (root / "docker/kafka/topics.txt").touch()
            (root / "docker/kafka/init-topics.sh").touch()
            services = {
                "capture": {"build": {"context": str(root / "posthog/rust"), "dockerfile": "Dockerfile"}},
                "kafka-init": {
                    "volumes": [
                        {
                            "type": "bind",
                            "source": str(root / "posthog/docker/kafka/topics.txt"),
                            "target": "/topics.txt",
                        },
                        {
                            "type": "bind",
                            "source": str(root / "posthog/docker/kafka/init-topics.sh"),
                            "target": "/init-topics.sh",
                        },
                    ]
                },
            }

            _normalize_repository_paths(services, str(root))

        self.assertEqual(services["capture"]["build"]["context"], str((root / "rust").resolve()))
        self.assertEqual(
            services["kafka-init"]["volumes"][0]["source"], str((root / "docker/kafka/topics.txt").resolve())
        )
        self.assertEqual(
            services["kafka-init"]["volumes"][1]["source"], str((root / "docker/kafka/init-topics.sh").resolve())
        )

    def test_endpoint_example_contains_every_required_setting(self) -> None:
        example = json.loads(Path(__file__).with_name("endpoints.example.json").read_text())

        self.assertEqual(REQUIRED_ENDPOINTS - example.keys(), set())

    def test_removes_state_services_and_rewrites_app_connections(self) -> None:
        rendered = render_multihost_compose(deepcopy(compose_config()), ENDPOINTS, "direct")
        services = rendered["services"]

        for service_name in ("db", "clickhouse", "kafka"):
            with self.subTest(service=service_name):
                self.assertNotIn(service_name, services)
        self.assertEqual(services["asyncmigrationscheck"]["profiles"], ["init"])
        self.assertEqual(rendered["volumes"], {})
        environment = services["web"]["environment"]
        self.assertEqual(environment["DATABASE_URL"], "postgres://posthog:secret@postgres-router:5432/posthog")
        self.assertEqual(
            environment["PERSONS_DB_READER_URL"], "postgres://posthog:secret@postgres-router:5432/posthog_persons"
        )
        self.assertEqual(
            environment["CYCLOTRON_NODE_DATABASE_URL"],
            "postgres://posthog:secret@postgres-router:5432/cyclotron_node",
        )
        self.assertEqual(environment["PGHOST"], "postgres-router")
        self.assertEqual(environment["PGPORT"], "5432")
        self.assertEqual(environment["PGUSER"], "posthog")
        self.assertEqual(environment["PGPASSWORD"], "secret")
        self.assertEqual(environment["CLICKHOUSE_HOST"], "clickhouse-router")
        self.assertEqual(environment["CLICKHOUSE_USER"], "app")
        self.assertEqual(environment["CLICKHOUSE_PASSWORD"], ENDPOINTS["clickhouse_password"])
        self.assertEqual(environment["CLICKHOUSE_LOGS_CLUSTER_HOST"], "clickhouse-router")
        self.assertEqual(environment["CLICKHOUSE_SECURE"], "false")
        router = services["clickhouse-router"]
        self.assertEqual(router["image"], "haproxy:3.2.27-alpine")
        self.assertIn("ch-1.internal:9000", rendered["configs"]["clickhouse-haproxy"]["content"])
        self.assertIn("ch-2.internal:9000", rendered["configs"]["clickhouse-haproxy"]["content"])
        self.assertIn("ch-3.internal:9000", rendered["configs"]["clickhouse-haproxy"]["content"])
        self.assertEqual(rendered["services"]["seaweedfs-router"]["image"], "haproxy:3.2.27-alpine")
        seaweedfs_config = rendered["configs"]["seaweedfs-haproxy"]["content"]
        self.assertIn("10.20.0.11:8333", seaweedfs_config)
        self.assertIn("10.20.0.12:8333", seaweedfs_config)
        self.assertIn("10.20.0.13:8333", seaweedfs_config)
        temporal_config = rendered["configs"]["temporal-haproxy"]["content"]
        self.assertIn("10.20.0.11:7233", temporal_config)
        self.assertIn("10.20.0.12:7233", temporal_config)
        self.assertIn("10.20.0.13:7233", temporal_config)
        self.assertEqual(rendered["services"]["temporal-router"]["image"], "haproxy:3.2.27-alpine")
        postgres_router = services["postgres-router"]
        self.assertEqual(postgres_router["image"], "haproxy:3.2.27-alpine")
        self.assertIn("bind :5432", rendered["configs"]["postgres-haproxy"]["content"])
        self.assertIn("server pg1 10.20.0.11:5433 check port 8008", rendered["configs"]["postgres-haproxy"]["content"])
        self.assertIn(
            "http-check send meth GET uri /primary ver HTTP/1.1", rendered["configs"]["postgres-haproxy"]["content"]
        )
        self.assertEqual(environment["KAFKA_HOSTS"], ENDPOINTS["kafka_hosts"])
        self.assertEqual(environment["REDIS_URL"], "redis://:redis-secret@redis-router:6379/0")
        self.assertEqual(environment["LOGS_REDIS_HOST"], "redis-router")
        self.assertEqual(environment["LOGS_REDIS_PORT"], "6379")
        self.assertEqual(environment["LOGS_REDIS_TLS"], "false")
        redis_router = services["redis-router"]
        self.assertEqual(redis_router["image"], "haproxy:3.2.27-alpine")
        redis_config = rendered["configs"]["redis-haproxy"]["content"]
        self.assertIn("server redis1 10.20.0.11:6379 check", redis_config)
        self.assertIn(
            "tcp-check send-binary 2a320d0a24340d0a415554480d0a2431320d0a72656469732d736563726574", redis_config
        )
        self.assertIn("tcp-check expect string role:master", redis_config)
        flags_redis_config = rendered["configs"]["flags-redis-haproxy"]["content"]
        self.assertIn("bind :6380", flags_redis_config)
        self.assertIn("server redis1 10.20.0.11:6380 check", flags_redis_config)
        self.assertIn(b"flags-redis-secret".hex(), flags_redis_config)
        valkey_config = rendered["configs"]["valkey-haproxy"]["content"]
        self.assertIn("bind :6381", valkey_config)
        self.assertIn("server redis1 10.20.0.11:6381 check", valkey_config)
        self.assertNotIn("2a320d0a24340d0a41555448", valkey_config)
        self.assertEqual(environment["OBJECT_STORAGE_ENDPOINT"], "http://seaweedfs-router:8333")
        self.assertEqual(environment["OBJECT_STORAGE_ACCESS_KEY_ID"], ENDPOINTS["object_storage_access_key_id"])
        self.assertEqual(environment["OBJECT_STORAGE_SECRET_ACCESS_KEY"], ENDPOINTS["object_storage_secret_access_key"])
        self.assertEqual(environment["OBJECT_STORAGE_PUBLIC_ENDPOINT"], ENDPOINTS["object_storage_public_endpoint"])
        self.assertEqual(environment["BATCH_EXPORT_OBJECT_STORAGE_ENDPOINT"], "http://seaweedfs-router:8333")
        self.assertEqual(environment["OBJECT_STORAGE_REGION"], ENDPOINTS["object_storage_region"])
        for service_name in ("web", "worker", "temporal-django-worker", "feature-flags"):
            with self.subTest(service=service_name):
                self.assertEqual(
                    services[service_name]["environment"]["FLAGS_REDIS_URL"],
                    "redis://:flags-redis-secret@flags-redis-router:6380/0",
                )
        replay_environment = services["ingestion-sessionreplay"]["environment"]
        self.assertEqual(replay_environment["SESSION_RECORDING_V2_S3_ENDPOINT"], "http://seaweedfs-router:8333")
        self.assertEqual(
            replay_environment["SESSION_RECORDING_V2_S3_ACCESS_KEY_ID"], ENDPOINTS["session_replay_access_key_id"]
        )
        self.assertEqual(
            replay_environment["SESSION_RECORDING_V2_S3_SECRET_ACCESS_KEY"],
            ENDPOINTS["session_replay_secret_access_key"],
        )
        self.assertEqual(environment["TEMPORAL_HOST"], "temporal-router")
        self.assertEqual(environment["TEMPORAL_CLI_ADDRESS"], "temporal-router:7233")
        self.assertEqual(services["worker"]["environment"]["TEMPORAL_HOST"], "temporal-router")
        self.assertEqual(services["asyncmigrationscheck"]["environment"]["TEMPORAL_HOST"], "temporal-router")
        self.assertEqual(environment["ETCD_ENDPOINTS"], ENDPOINTS["etcd_endpoints"])
        self.assertEqual(environment["CDP_VALKEY_HOST"], "valkey-router")
        self.assertEqual(environment["CDP_VALKEY_PORT"], str(ENDPOINTS["valkey_port"]))
        self.assertEqual(
            services["cymbal"]["environment"]["PERSONS_URL"],
            "postgres://posthog:secret@postgres-router:5432/posthog",
        )
        self.assertEqual(
            services["cymbal"]["environment"]["ISSUE_BUCKETS_REDIS_URL"],
            "redis://:redis-secret@redis-router:6379/0",
        )
        self.assertEqual(services["kafka-init"]["profiles"], ["init"])
        self.assertEqual(services["kafka-init"]["environment"]["KAFKA_HOSTS"], ENDPOINTS["kafka_hosts"])
        self.assertEqual(services["kafka-init"]["environment"]["KAFKA_TOPIC_REPLICATION_FACTOR"], "3")
        self.assertIn(ENDPOINTS["etcd_endpoints"], services["personhog-etcd-init"]["entrypoint"][1])
        self.assertIn("postgres-router", services["personhog-tables-ready"]["entrypoint"][2])
        self.assertEqual(services["personhog-tables-ready"]["environment"]["PGPASSWORD"], "secret")
        self.assertNotIn("depends_on", services["streaming-consumer"])
        self.assertIn("reverse_proxy seaweedfs-router:8333", services["proxy"]["environment"]["CADDYFILE"])
        self.assertEqual(
            services["proxy"]["depends_on"],
            {
                "web": {"condition": "service_started"},
                "seaweedfs-router": {"condition": "service_healthy"},
            },
        )
        web_dependencies = services["web"]["depends_on"]
        self.assertEqual(
            set(web_dependencies),
            {
                "clickhouse-router",
                "flags-redis-router",
                "postgres-router",
                "redis-router",
                "seaweedfs-router",
                "temporal-router",
                "valkey-router",
            },
        )
        self.assertTrue(all(dependency["condition"] == "service_healthy" for dependency in web_dependencies.values()))
        self.assertTrue(all(set(service.get("depends_on", {})) <= services.keys() for service in services.values()))

    def test_separates_migrations_and_the_celery_scheduler(self) -> None:
        services = render_multihost_compose(deepcopy(compose_config()), ENDPOINTS, "direct")["services"]

        self.assertEqual(services["web"]["command"], ["./bin/docker-server"])
        self.assertNotIn("volumes", services["web"])
        self.assertEqual(services["worker"]["command"], ["./bin/docker-worker-celery"])
        self.assertEqual(services["celery-beat"]["command"], ["./bin/docker-worker-beat"])
        self.assertNotIn("profiles", services["celery-beat"])
        self.assertEqual(services["temporal-django-worker"]["command"], ["./bin/temporal-django-worker"])
        self.assertNotIn("volumes", services["temporal-django-worker"])

    def test_app_node_rendering_supports_a_manual_scheduler_failover(self) -> None:
        app_1 = render_multihost_compose(deepcopy(compose_config()), ENDPOINTS, "direct", node_name="app-1")
        app_2 = render_multihost_compose(deepcopy(compose_config()), ENDPOINTS, "direct", node_name="app-2")

        self.assertIn("celery-beat", app_1["services"])
        self.assertNotIn("profiles", app_1["services"]["celery-beat"])
        self.assertEqual(app_2["services"]["celery-beat"]["profiles"], ["scheduler"])

    def test_one_shot_initialization_services_only_exist_on_primary_app_node(self) -> None:
        app_1 = render_multihost_compose(deepcopy(compose_config()), ENDPOINTS, "direct", node_name="app-1")
        app_2 = render_multihost_compose(deepcopy(compose_config()), ENDPOINTS, "direct", node_name="app-2")

        for service_name in ("asyncmigrationscheck", "kafka-init", "personhog-etcd-init", "personhog-tables-ready"):
            with self.subTest(service=service_name):
                self.assertIn(service_name, app_1["services"])
                self.assertNotIn(service_name, app_2["services"])

    def test_app_two_drops_dependencies_on_one_time_init_services(self) -> None:
        config = deepcopy(compose_config())
        config["services"]["streaming-consumer"] = {"image": "posthog", "depends_on": ["kafka-init"]}

        services = render_multihost_compose(config, ENDPOINTS, "direct", node_name="app-2")["services"]

        self.assertNotIn("kafka-init", services)
        self.assertNotIn("depends_on", services["streaming-consumer"])

    def test_rejects_unknown_application_node(self) -> None:
        with self.assertRaisesRegex(ValueError, "app-1 or app-2"):
            render_multihost_compose(deepcopy(compose_config()), ENDPOINTS, "direct", node_name="app-3")

    def test_builds_application_and_node_images_from_the_checkout(self) -> None:
        services = render_multihost_compose(deepcopy(compose_config()), ENDPOINTS, "direct", build_context="/checkout")
        services = services["services"]

        self.assertEqual(services["web"]["build"], {"context": "/checkout", "dockerfile": "Dockerfile"})
        self.assertEqual(services["ingestion-logs"]["build"], {"context": "/checkout", "dockerfile": "Dockerfile.node"})
        self.assertEqual(
            services["livestream"]["build"], {"context": "/checkout/livestream", "dockerfile": "Dockerfile"}
        )

    def test_adds_metrics_consumer_for_the_metrics_topic(self) -> None:
        services = render_multihost_compose(deepcopy(compose_config()), ENDPOINTS, "direct")["services"]

        self.assertEqual(services["ingestion-metrics"]["environment"]["PLUGIN_SERVER_MODE"], "ingestion-metrics")

    def test_adds_dedicated_feature_flag_cache_consumer(self) -> None:
        services = render_multihost_compose(deepcopy(compose_config()), ENDPOINTS, "direct")["services"]
        consumer = services["flags-cache-builder"]

        self.assertEqual(
            consumer["build"],
            {
                "context": "./rust",
                "args": {"BIN": "flags-cache-builder", "BUILD_FEATURES": "feature-flags/flags-cache-builder"},
            },
        )
        self.assertEqual(consumer["restart"], "unless-stopped")
        self.assertEqual(consumer["healthcheck"]["test"], ["CMD", "curl", "-f", "http://localhost:9090/_readiness"])
        self.assertEqual(consumer["environment"]["KAFKA_HOSTS"], ENDPOINTS["kafka_hosts"])
        self.assertEqual(
            consumer["environment"]["READ_DATABASE_URL"],
            "postgres://posthog:secret@postgres-router:5432/posthog",
        )
        self.assertEqual(
            consumer["environment"]["FLAGS_REDIS_URL"],
            "redis://:flags-redis-secret@flags-redis-router:6380/0",
        )
        self.assertEqual(consumer["environment"]["OBJECT_STORAGE_ENDPOINT"], "http://seaweedfs-router:8333")
        self.assertEqual(consumer["environment"]["AWS_ACCESS_KEY_ID"], ENDPOINTS["object_storage_access_key_id"])
        self.assertEqual(
            consumer["environment"]["AWS_SECRET_ACCESS_KEY"], ENDPOINTS["object_storage_secret_access_key"]
        )
        self.assertEqual(consumer["environment"]["OBJECT_STORAGE_REGION"], ENDPOINTS["object_storage_region"])
        self.assertEqual(consumer["environment"]["KAFKA_CONSUMER_TOPIC"], "flags_cache_invalidation")

    def test_requires_a_dedicated_flags_redis_endpoint(self) -> None:
        endpoints = dict(ENDPOINTS)
        endpoints.pop("flags_redis_url")

        with self.assertRaisesRegex(ValueError, "flags_redis_url"):
            render_multihost_compose(compose_config(), endpoints, "direct")

    def test_rejects_reusing_the_general_redis_endpoint_for_flags(self) -> None:
        endpoints = dict(ENDPOINTS, flags_redis_url=ENDPOINTS["redis_url"])

        with self.assertRaisesRegex(ValueError, "must use a dedicated Redis endpoint"):
            render_multihost_compose(compose_config(), endpoints, "direct")

    def test_requires_a_password_safe_for_the_redis_sentinel_configuration(self) -> None:
        endpoints = dict(ENDPOINTS, redis_url="redis://:password%20with%20spaces@redis.internal:6379/0")

        with self.assertRaisesRegex(ValueError, "URL-safe password"):
            render_multihost_compose(compose_config(), endpoints, "direct")

    def test_renders_both_ingress_modes(self) -> None:
        cases = (
            ("direct", "posthog.example.com, http://, https://"),
            ("lb", "http://posthog.example.com"),
        )
        for mode, expected_host in cases:
            with self.subTest(mode=mode):
                proxy_env = render_multihost_compose(deepcopy(compose_config()), ENDPOINTS, mode)["services"]["proxy"][
                    "environment"
                ]
                self.assertEqual(proxy_env["CADDY_HOST"], expected_host)
                expected_tls = "" if mode == "direct" else "servers { trusted_proxies static 10.0.0.0/8 }"
                self.assertEqual(proxy_env["CADDY_TLS_BLOCK"], expected_tls)
                ports = render_multihost_compose(deepcopy(compose_config()), ENDPOINTS, mode)["services"]["proxy"][
                    "ports"
                ]
                self.assertEqual(ports, ["80:80"] if mode == "lb" else ["80:80", "443:443"])

    def test_rejects_unknown_ingress_mode(self) -> None:
        with self.assertRaisesRegex(ValueError, "ingress mode"):
            render_multihost_compose(compose_config(), ENDPOINTS, "guess")

    def test_requires_complete_external_endpoint_configuration(self) -> None:
        endpoints = dict(ENDPOINTS)
        endpoints.pop("session_replay_secret_access_key")

        with self.assertRaisesRegex(ValueError, "session_replay_secret_access_key"):
            render_multihost_compose(compose_config(), endpoints, "direct")

    def test_applies_service_specific_connection_settings(self) -> None:
        endpoints = dict(
            ENDPOINTS,
            service_environment_overrides={
                "web": {"POSTHOG_POSTGRES_DIRECT_HOST": "pg-direct.internal"},
                "ingestion-sessionreplay": {"KAFKA_PRODUCER_SECURITY_PROTOCOL": "SSL"},
            },
        )

        services = render_multihost_compose(compose_config(), endpoints, "direct")["services"]

        self.assertEqual(services["web"]["environment"]["POSTHOG_POSTGRES_DIRECT_HOST"], "pg-direct.internal")
        self.assertEqual(services["ingestion-sessionreplay"]["environment"]["KAFKA_PRODUCER_SECURITY_PROTOCOL"], "SSL")

    def test_propagates_redis_url_password_to_host_based_redis_clients(self) -> None:
        endpoints = dict(ENDPOINTS, redis_url="rediss://:redis-secret@redis.internal:6380/0")
        config = compose_config()
        config["services"]["ingestion-metrics"] = {
            "environment": {
                "METRICS_REDIS_HOST": "redis7",
                "METRICS_REDIS_PORT": "6379",
                "METRICS_REDIS_PASSWORD": "",
                "METRICS_REDIS_TLS": "false",
            }
        }
        config["services"]["plugins"] = {
            "environment": {"CDP_REDIS_HOST": "redis7", "CDP_REDIS_PORT": "6379", "CDP_REDIS_PASSWORD": ""}
        }

        rendered = render_multihost_compose(config, endpoints, "direct")["services"]
        environment = rendered["ingestion-metrics"]["environment"]

        self.assertEqual(environment["METRICS_REDIS_HOST"], "redis-router")
        self.assertEqual(environment["METRICS_REDIS_PORT"], "6379")
        self.assertEqual(environment["METRICS_REDIS_PASSWORD"], "redis-secret")
        self.assertEqual(environment["METRICS_REDIS_TLS"], "false")
        self.assertEqual(rendered["plugins"]["environment"]["CDP_REDIS_PASSWORD"], "redis-secret")

    def test_rejects_overrides_for_unknown_services(self) -> None:
        endpoints = dict(
            ENDPOINTS,
            service_environment_overrides={"unknown-service": {"KAFKA_PRODUCER_SECURITY_PROTOCOL": "SSL"}},
        )

        with self.assertRaisesRegex(ValueError, "unknown service"):
            render_multihost_compose(compose_config(), endpoints, "direct")

    def test_rejects_invalid_load_balancer_proxy_cidr(self) -> None:
        endpoints = dict(ENDPOINTS, trusted_proxy_cidrs=["not-a-cidr"])

        with self.assertRaisesRegex(ValueError, "trusted proxy CIDR"):
            render_multihost_compose(compose_config(), endpoints, "lb")

    def test_rejects_domain_values_that_are_not_hostnames(self) -> None:
        for value in ("https://posthog.example.com", "posthog.example.com/path", "posthog.example.com\n{}"):
            with self.subTest(domain=value):
                endpoints = dict(ENDPOINTS, domain=value)
                with self.assertRaisesRegex(ValueError, "domain must be a hostname or IP address"):
                    render_multihost_compose(compose_config(), endpoints, "direct")


if __name__ == "__main__":
    unittest.main()
