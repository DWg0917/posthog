import os
import json
import hashlib
import tempfile
import subprocess
from pathlib import Path
from xml.etree import ElementTree as ET

import unittest

from tools.self_managed_deploy.data_services import render_data_node_compose

CLICKHOUSE_ENDPOINTS = {
    "clickhouse_user": "app",
    "clickhouse_password": "a-generated-test-password",
    "redis_url": "redis://:a-generated-redis-password@redis-router:6379/0",
    "flags_redis_url": "redis://:a-generated-flags-password@flags-redis-router:6380/0",
    "postgres_hosts": "10.20.0.11:5433,10.20.0.12:5433,10.20.0.13:5433",
    "clickhouse_hosts": "10.20.0.11:9000,10.20.0.12:9000,10.20.0.13:9000",
    "kafka_hosts": "10.20.0.11:9092,10.20.0.12:9092,10.20.0.13:9092",
    "redis_hosts": "10.20.0.11:6379,10.20.0.12:6379,10.20.0.13:6379",
    "flags_redis_hosts": "10.20.0.11:6380,10.20.0.12:6380,10.20.0.13:6380",
    "valkey_hosts": "10.20.0.11:6381,10.20.0.12:6381,10.20.0.13:6381",
    "seaweedfs_hosts": "10.20.0.11:8333,10.20.0.12:8333,10.20.0.13:8333",
    "seaweedfs_database_password": "a-generated-seaweedfs-password",
    "object_storage_access_key_id": "object-storage-key",
    "object_storage_secret_access_key": "object-storage-secret",
    "session_replay_access_key_id": "session-replay-key",
    "session_replay_secret_access_key": "session-replay-secret",
    "temporal_hosts": "10.20.0.11:7233,10.20.0.12:7233,10.20.0.13:7233",
    "temporal_database_password": "a-generated-temporal-password",
    "postgres_url": "postgresql://posthog:app-pass@postgres-router:5432/posthog",
    "postgres_superuser_password": "a-generated-superuser-password",
    "postgres_replication_password": "a-generated-replication-password",
}
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]

CLUSTER = {
    "client_cidrs": ["10.20.0.0/16"],
    "nodes": [
        {"name": "data-1", "address": "10.20.0.11", "id": 1},
        {"name": "data-2", "address": "10.20.0.12", "id": 2},
        {"name": "data-3", "address": "10.20.0.13", "id": 3},
    ],
}


class RenderDataNodeComposeTest(unittest.TestCase):
    def test_patroni_bootstrap_creates_all_hobby_databases(self) -> None:
        repository_root = REPOSITORY_ROOT
        post_init = repository_root / "docker/postgres-ha/post-init.sh"
        with tempfile.TemporaryDirectory() as temp_directory:
            temp_path = Path(temp_directory)
            psql_log = temp_path / "psql.jsonl"
            psql_stub = temp_path / "psql"
            psql_stub.write_text(
                "#!/usr/bin/env python3\n"
                "import json, os, sys\n"
                "with open(os.environ['PSQL_LOG'], 'a') as log:\n"
                "    log.write(json.dumps({'args': sys.argv[1:], 'stdin': sys.stdin.read()}) + '\\n')\n"
            )
            psql_stub.chmod(0o755)
            environment = {
                **os.environ,
                "PATH": f"{temp_path}:{os.environ['PATH']}",
                "PSQL_LOG": str(psql_log),
                "POSTHOG_APP_USER": "posthog",
                "POSTHOG_APP_PASSWORD": "app-secret",
                "SEAWEEDFS_DATABASE_PASSWORD": "seaweedfs-secret",
                "TEMPORAL_DATABASE_PASSWORD": "temporal-secret",
                "POSTHOG_POSTGRES_INIT_SCRIPTS": str(repository_root / "docker/postgres-init-scripts"),
                "PRODUCTS_DIR": str(repository_root / "products"),
            }

            subprocess.run(
                ["sh", str(post_init), "postgresql://postgres@127.0.0.1:5433/postgres"],
                check=True,
                env=environment,
                capture_output=True,
                text=True,
            )

            calls = [json.loads(line) for line in psql_log.read_text().splitlines()]
            statements = "\n".join(call["stdin"] + "\n" + " ".join(call["args"]) for call in calls)
            for database in (
                "posthog",
                "posthog_persons",
                "cyclotron",
                "cyclotron_node",
                "flags_read_store",
                "behavioral_cohorts",
                "agent_runtime_queue",
                "ducklake",
                "seaweedfs",
                "temporal",
                "temporal_visibility",
            ):
                self.assertIn(database, statements)
            self.assertIn("SEAWEEDFS_DATABASE_PASSWORD", post_init.read_text())
            self.assertIn("seaweedfs_user", statements)

    def test_redis_node_bootstrap_rejoins_the_master_reported_by_sentinel(self) -> None:
        with tempfile.TemporaryDirectory() as temp_directory:
            temp_path = Path(temp_directory)
            arguments_file = temp_path / "redis-server-args"
            redis_cli = temp_path / "redis-cli"
            redis_cli.write_text('#!/bin/sh\nprintf \'%s\\n%s\\n\' "$MASTER_ADDRESS" "$MASTER_PORT"\n')
            redis_cli.chmod(0o755)
            redis_server = temp_path / "redis-server"
            redis_server.write_text('#!/bin/sh\nprintf \'%s\\n\' "$@" > "$REDIS_SERVER_ARGS"\n')
            redis_server.chmod(0o755)
            environment = {
                **os.environ,
                "PATH": f"{temp_path}:{os.environ['PATH']}",
                "REDIS_NODE_ADDRESS": "10.20.0.12",
                "REDIS_INITIAL_PRIMARY": "10.20.0.11",
                "REDIS_SENTINEL_HOST": "10.20.0.12",
                "REDIS_SENTINEL_PORT": "26379",
                "REDIS_PASSWORD": "redis-secret",
                "MASTER_ADDRESS": "10.20.0.13",
                "MASTER_PORT": "6379",
                "REDIS_SERVER_ARGS": str(arguments_file),
            }

            subprocess.run(
                ["sh", str(REPOSITORY_ROOT / "docker/redis-ha/redis-node.sh")],
                check=True,
                env=environment,
                capture_output=True,
                text=True,
            )

            arguments = arguments_file.read_text().splitlines()
            self.assertEqual(
                arguments[arguments.index("--replicaof") + 1 : arguments.index("--replicaof") + 3],
                ["10.20.0.13", "6379"],
            )

    def test_valkey_node_uses_its_own_tools_and_does_not_enable_auth(self) -> None:
        with tempfile.TemporaryDirectory() as temp_directory:
            temp_path = Path(temp_directory)
            arguments_file = temp_path / "valkey-server-args"
            valkey_cli = temp_path / "valkey-cli"
            valkey_cli.write_text('#!/bin/sh\nprintf \'%s\\n%s\\n\' "$MASTER_ADDRESS" "$MASTER_PORT"\n')
            valkey_cli.chmod(0o755)
            valkey_server = temp_path / "valkey-server"
            valkey_server.write_text('#!/bin/sh\nprintf \'%s\\n\' "$@" > "$REDIS_SERVER_ARGS"\n')
            valkey_server.chmod(0o755)
            environment = {
                **os.environ,
                "PATH": f"{temp_path}:{os.environ['PATH']}",
                "REDIS_NODE_ADDRESS": "10.20.0.12",
                "REDIS_INITIAL_PRIMARY": "10.20.0.11",
                "REDIS_SENTINEL_HOST": "10.20.0.12",
                "REDIS_SENTINEL_PORT": "26381",
                "REDIS_SENTINEL_NAME": "posthog-cdp",
                "REDIS_PORT": "6381",
                "REDIS_PASSWORD": "",
                "REDIS_CLI_BIN": "valkey-cli",
                "REDIS_SERVER_BIN": "valkey-server",
                "REDIS_RUNTIME_USER": "valkey",
                "MASTER_ADDRESS": "10.20.0.13",
                "MASTER_PORT": "6381",
                "REDIS_SERVER_ARGS": str(arguments_file),
            }

            subprocess.run(
                ["sh", str(REPOSITORY_ROOT / "docker/redis-ha/redis-node.sh")],
                check=True,
                env=environment,
                capture_output=True,
                text=True,
            )

            arguments = arguments_file.read_text().splitlines()
            self.assertEqual(arguments[arguments.index("--port") + 1], "6381")
            self.assertEqual(
                arguments[arguments.index("--replicaof") + 1 : arguments.index("--replicaof") + 3],
                ["10.20.0.13", "6381"],
            )
            self.assertNotIn("--requirepass", arguments)

    def test_redis_sentinel_persists_a_quorum_two_monitor_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as temp_directory:
            temp_path = Path(temp_directory)
            arguments_file = temp_path / "redis-server-args"
            redis_server = temp_path / "redis-server"
            redis_server.write_text('#!/bin/sh\nprintf \'%s\\n\' "$@" > "$REDIS_SERVER_ARGS"\n')
            redis_server.chmod(0o755)
            environment = {
                **os.environ,
                "PATH": f"{temp_path}:{os.environ['PATH']}",
                "REDIS_NODE_ADDRESS": "10.20.0.12",
                "REDIS_INITIAL_PRIMARY": "10.20.0.11",
                "REDIS_PASSWORD": "redis-secret",
                "REDIS_DATA_DIR": str(temp_path),
                "REDIS_SERVER_ARGS": str(arguments_file),
            }

            subprocess.run(
                ["sh", str(REPOSITORY_ROOT / "docker/redis-ha/redis-sentinel.sh")],
                check=True,
                env=environment,
                capture_output=True,
                text=True,
            )

            sentinel_config = (temp_path / "sentinel.conf").read_text()
            self.assertIn("sentinel monitor posthog 10.20.0.11 6379 2", sentinel_config)
            self.assertIn("sentinel auth-pass posthog redis-secret", sentinel_config)
            self.assertIn("sentinel failover-timeout posthog 30000", sentinel_config)
            self.assertEqual(arguments_file.read_text().splitlines()[-1], "--sentinel")

    def test_generates_persistent_three_member_coordination_and_broker_services(self) -> None:
        compose = render_data_node_compose(CLUSTER, "data-2", CLICKHOUSE_ENDPOINTS)

        self.assertEqual(
            set(compose["services"]),
            {
                "clickhouse",
                "etcd",
                "zookeeper",
                "redpanda",
                "postgresql",
                "redis",
                "redis-sentinel",
                "flags-redis",
                "flags-redis-sentinel",
                "valkey",
                "valkey-sentinel",
                "seaweedfs-postgres-router",
                "seaweedfs",
                "temporal",
            },
        )
        for service in compose["services"].values():
            self.assertEqual(service["network_mode"], "host")
            self.assertEqual(service["restart"], "unless-stopped")
        self.assertIn(
            "--initial-cluster=data-1=http://10.20.0.11:2380,data-2=http://10.20.0.12:2380,data-3=http://10.20.0.13:2380",
            compose["services"]["etcd"]["command"],
        )
        self.assertIn(
            "server.2=10.20.0.12:2888:3888;2181", compose["services"]["zookeeper"]["environment"]["ZOO_SERVERS"]
        )
        self.assertEqual(
            compose["services"]["zookeeper"]["environment"]["ZOO_CFG_EXTRA"],
            "clientPortAddress=10.20.0.12 quorumListenOnAllIPs=false",
        )
        self.assertIn("--advertise-kafka-addr=internal://10.20.0.12:9092", compose["services"]["redpanda"]["command"])
        self.assertIn("--set=redpanda.log_retention_ms=3600000", compose["services"]["redpanda"]["command"])
        self.assertIn(
            "--seeds=10.20.0.11:33145,10.20.0.12:33145,10.20.0.13:33145",
            compose["services"]["redpanda"]["command"],
        )
        self.assertEqual(
            set(compose["volumes"]),
            {
                "clickhouse-data",
                "etcd-data",
                "zookeeper-data",
                "zookeeper-datalog",
                "zookeeper-logs",
                "redpanda-data",
                "postgresql-data",
                "redis-data",
                "redis-sentinel-data",
                "flags-redis-data",
                "flags-redis-sentinel-data",
                "valkey-data",
                "valkey-sentinel-data",
                "seaweedfs-data",
            },
        )
        seaweedfs = compose["services"]["seaweedfs"]
        self.assertEqual(seaweedfs["image"], "chrislusf/seaweedfs:4.29")
        self.assertEqual(seaweedfs["command"][0], "server")
        self.assertIn("-ip.bind=10.20.0.12", seaweedfs["command"])
        self.assertIn("-master.peers=10.20.0.11:9333,10.20.0.12:9333,10.20.0.13:9333", seaweedfs["command"])
        self.assertIn("-master.defaultReplication=001", seaweedfs["command"])
        self.assertEqual(seaweedfs["environment"]["WEED_LEVELDB2_ENABLED"], "false")
        self.assertEqual(seaweedfs["environment"]["WEED_POSTGRES2_ENABLED"], "true")
        self.assertEqual(seaweedfs["environment"]["WEED_POSTGRES2_HOSTNAME"], "127.0.0.1")
        self.assertEqual(seaweedfs["environment"]["WEED_POSTGRES2_DATABASE"], "seaweedfs")
        self.assertEqual(seaweedfs["environment"]["WEED_POSTGRES2_PASSWORD"], "a-generated-seaweedfs-password")
        self.assertEqual(seaweedfs["environment"]["OBJECT_STORAGE_ACCESS_KEY_ID"], "object-storage-key")
        self.assertEqual(seaweedfs["environment"]["SESSION_REPLAY_ACCESS_KEY_ID"], "session-replay-key")
        self.assertEqual(seaweedfs["environment"]["SEAWEEDFS_BOOTSTRAP_ENABLED"], "false")
        self.assertIn("seaweedfs-data:/data", seaweedfs["volumes"])
        self.assertEqual(
            compose["x-posthog-endpoints"]["seaweedfs_hosts"],
            "10.20.0.11:8333,10.20.0.12:8333,10.20.0.13:8333",
        )
        self.assertIn("postgresql-patroni", compose["configs"])
        self.assertIn("seaweedfs-postgres-haproxy", compose["configs"])
        self.assertIn("127.0.0.1:5432", compose["configs"]["seaweedfs-postgres-haproxy"]["content"])
        self.assertIn("/primary", compose["configs"]["seaweedfs-postgres-haproxy"]["content"])
        temporal = compose["services"]["temporal"]
        self.assertEqual(temporal["image"], "temporalio/server:1.26.2")
        self.assertEqual(temporal["profiles"], ["temporal"])
        self.assertEqual(temporal["environment"]["DB"], "postgres12")
        self.assertEqual(temporal["environment"]["POSTGRES_SEEDS"], "127.0.0.1")
        self.assertEqual(temporal["environment"]["POSTGRES_USER"], "temporal")
        self.assertEqual(temporal["environment"]["TEMPORAL_BROADCAST_ADDRESS"], "10.20.0.12")
        self.assertEqual(temporal["environment"]["BIND_ON_IP"], "10.20.0.12")
        self.assertEqual(temporal["environment"]["SKIP_SCHEMA_SETUP"], "true")
        self.assertEqual(temporal["environment"]["DYNAMIC_CONFIG_FILE_PATH"], "config/dynamicconfig/self-managed.yaml")

    def test_temporal_schema_setup_only_renders_on_the_first_data_node(self) -> None:
        compose = render_data_node_compose(CLUSTER, "data-1", CLICKHOUSE_ENDPOINTS)
        temporal_setup = compose["services"]["temporal-schema-setup"]

        self.assertEqual(temporal_setup["image"], "temporalio/admin-tools:1.26.2")
        self.assertEqual(temporal_setup["profiles"], ["init"])
        self.assertIn("temporal-sql-tool", temporal_setup["command"][0])
        self.assertIn("setup-schema -v 0.0", temporal_setup["command"][0])
        self.assertIn(
            "update-schema -d /etc/temporal/schema/postgresql/v12/temporal/versioned", temporal_setup["command"][0]
        )
        self.assertNotIn("$", temporal_setup["command"][0])
        self.assertEqual(temporal_setup["environment"]["SQL_PASSWORD"], "a-generated-temporal-password")
        namespace_setup = compose["services"]["temporal-namespace-setup"]
        self.assertEqual(namespace_setup["profiles"], ["init"])
        self.assertIn("temporal operator namespace create", namespace_setup["command"][0])
        self.assertEqual(namespace_setup["environment"]["TEMPORAL_ADDRESS"], "127.0.0.1:7233")
        self.assertEqual(temporal_setup["profiles"], ["init"])

    def test_temporal_initialization_services_only_render_on_data_one(self) -> None:
        compose = render_data_node_compose(CLUSTER, "data-2", CLICKHOUSE_ENDPOINTS)

        self.assertNotIn("temporal-schema-setup", compose["services"])
        self.assertNotIn("temporal-namespace-setup", compose["services"])

    def test_seaweedfs_entrypoint_bootstraps_buckets_and_both_s3_identities(self) -> None:
        with tempfile.TemporaryDirectory() as temp_directory:
            temp_path = Path(temp_directory)
            calls_file = temp_path / "weed-calls"
            fake_weed = temp_path / "weed"
            fake_weed.write_text(
                "#!/bin/sh\n"
                'if [ "$1" = shell ]; then\n'
                "  input=$(cat)\n"
                '  printf \'%s\\n\' "$input" >> "$WEED_CALLS"\n'
                '  case "$input" in\n'
                "    s3.bucket.list*) printf 'posthog\\nai-blobs\\n' ;;\n"
                "    s3.configure*) printf 'object-storage-key session-replay-key\\n' ;;\n"
                "  esac\n"
                "  exit 0\n"
                "fi\n"
                'printf \'server:%s\\n\' "$*" >> "$WEED_CALLS"\n'
            )
            fake_weed.chmod(0o755)
            ready_file = temp_path / "ready"
            environment = {
                **os.environ,
                "WEED_BIN": str(fake_weed),
                "WEED_CALLS": str(calls_file),
                "WEED_MASTER": "127.0.0.1:9333",
                "SEAWEEDFS_READY_FILE": str(ready_file),
                "SEAWEEDFS_BOOTSTRAP_ENABLED": "true",
                "OBJECT_STORAGE_ACCESS_KEY_ID": "object-storage-key",
                "OBJECT_STORAGE_SECRET_ACCESS_KEY": "object-storage-secret",
                "SESSION_REPLAY_ACCESS_KEY_ID": "session-replay-key",
                "SESSION_REPLAY_SECRET_ACCESS_KEY": "session-replay-secret",
            }

            subprocess.run(
                ["sh", str(REPOSITORY_ROOT / "docker/seaweedfs/entrypoint.sh"), "server", "-s3"],
                check=True,
                env=environment,
                capture_output=True,
                text=True,
            )

            calls = calls_file.read_text()
            self.assertTrue(ready_file.exists())
            self.assertIn("s3.configure -access_key=object-storage-key", calls)
            self.assertIn("s3.configure -access_key=session-replay-key", calls)
            self.assertNotIn("-delete", calls)
            self.assertIn("s3.bucket.create -name posthog", calls)
            self.assertIn("s3.bucket.create -name ai-blobs", calls)
            self.assertIn("server:server -s3", calls)

    def test_seaweedfs_hosts_must_match_the_configured_cluster_nodes(self) -> None:
        endpoints = {**CLICKHOUSE_ENDPOINTS, "seaweedfs_hosts": "10.20.0.11:8333,10.20.0.13:8333,10.20.0.12:8333"}

        with self.assertRaisesRegex(ValueError, "seaweedfs_hosts must match cluster node addresses in order"):
            render_data_node_compose(CLUSTER, "data-1", endpoints)
        compose = render_data_node_compose(CLUSTER, "data-2", CLICKHOUSE_ENDPOINTS)
        self.assertIn("clickhouse-main", compose["configs"])
        postgres_config = json.loads(compose["configs"]["postgresql-patroni"]["content"])
        self.assertEqual(postgres_config["scope"], "posthog")
        self.assertEqual(postgres_config["name"], "data-2")
        self.assertEqual(postgres_config["etcd3"]["hosts"], ["10.20.0.11:2379", "10.20.0.12:2379", "10.20.0.13:2379"])
        self.assertEqual(postgres_config["postgresql"]["listen"], "10.20.0.12:5433")
        self.assertEqual(postgres_config["postgresql"]["connect_address"], "10.20.0.12:5433")
        self.assertEqual(postgres_config["restapi"]["listen"], "10.20.0.12:8008")
        postgres_environment = compose["services"]["postgresql"]["environment"]
        self.assertEqual(postgres_environment["PATRONI_SUPERUSER_PASSWORD"], "a-generated-superuser-password")
        self.assertEqual(postgres_environment["PATRONI_REPLICATION_PASSWORD"], "a-generated-replication-password")
        redis = compose["services"]["redis"]
        self.assertEqual(redis["environment"]["REDIS_NODE_ADDRESS"], "10.20.0.12")
        self.assertEqual(redis["environment"]["REDIS_INITIAL_PRIMARY"], "10.20.0.11")
        self.assertEqual(redis["environment"]["REDIS_PASSWORD"], "a-generated-redis-password")
        self.assertEqual(redis["command"], ["/usr/local/bin/posthog-redis-node"])
        self.assertIn("redis-data:/data", redis["volumes"])
        sentinel = compose["services"]["redis-sentinel"]
        self.assertEqual(sentinel["environment"]["REDIS_INITIAL_PRIMARY"], "10.20.0.11")
        self.assertEqual(sentinel["environment"]["REDIS_PASSWORD"], "a-generated-redis-password")
        self.assertEqual(sentinel["command"], ["/usr/local/bin/posthog-redis-sentinel"])
        self.assertIn("redis-sentinel-data:/data", sentinel["volumes"])
        flags_redis = compose["services"]["flags-redis"]
        self.assertEqual(flags_redis["environment"]["REDIS_PORT"], "6380")
        self.assertEqual(flags_redis["environment"]["REDIS_SENTINEL_PORT"], "26380")
        self.assertEqual(flags_redis["environment"]["REDIS_SENTINEL_NAME"], "posthog-flags")
        self.assertEqual(flags_redis["environment"]["REDIS_PASSWORD"], "a-generated-flags-password")
        self.assertIn("flags-redis-data:/data", flags_redis["volumes"])
        flags_sentinel = compose["services"]["flags-redis-sentinel"]
        self.assertEqual(flags_sentinel["environment"]["REDIS_NODE_ADDRESS"], "10.20.0.12")
        self.assertIn("flags-redis-sentinel-data:/data", flags_sentinel["volumes"])
        valkey = compose["services"]["valkey"]
        self.assertEqual(valkey["environment"]["REDIS_PORT"], "6381")
        self.assertEqual(valkey["environment"]["REDIS_SENTINEL_PORT"], "26381")
        self.assertEqual(valkey["environment"]["REDIS_SERVER_BIN"], "valkey-server")
        self.assertEqual(valkey["environment"]["REDIS_CLI_BIN"], "valkey-cli")
        self.assertEqual(valkey["environment"]["REDIS_RUNTIME_USER"], "valkey")
        self.assertEqual(valkey["environment"]["REDIS_PASSWORD"], "")
        self.assertIn("valkey-data:/data", valkey["volumes"])
        self.assertNotIn("a-generated-superuser-password", compose["configs"]["postgresql-patroni"]["content"])
        self.assertNotIn("a-generated-replication-password", compose["configs"]["postgresql-patroni"]["content"])
        self.assertTrue(postgres_config["bootstrap"]["dcs"]["synchronous_mode"])
        self.assertTrue(postgres_config["bootstrap"]["dcs"]["synchronous_mode_strict"])
        self.assertEqual(postgres_config["bootstrap"]["dcs"]["synchronous_node_count"], 1)
        self.assertIn("host all all 10.20.0.0/16 scram-sha-256", postgres_config["postgresql"]["pg_hba"])
        main_config = ET.fromstring(compose["configs"]["clickhouse-main"]["content"])
        self.assertEqual(
            [node.findtext("host") for node in main_config.findall("./zookeeper/node")],
            ["10.20.0.11", "10.20.0.12", "10.20.0.13"],
        )
        self.assertEqual([element.text for element in main_config.findall("listen_host")], ["127.0.0.1", "10.20.0.12"])
        self.assertEqual(main_config.findtext("interserver_http_host"), "10.20.0.12")
        default_config = ET.fromstring(compose["configs"]["clickhouse-default"]["content"])
        replicas = default_config.findall("./remote_servers/posthog/shard/replica")
        self.assertEqual([replica.findtext("host") for replica in replicas], ["10.20.0.11", "10.20.0.12", "10.20.0.13"])
        self.assertEqual(default_config.findtext("./remote_servers/posthog/shard/internal_replication"), "true")
        self.assertEqual(
            [replica.findtext("user") for replica in replicas],
            [CLICKHOUSE_ENDPOINTS["clickhouse_user"]] * 3,
        )
        self.assertEqual(
            [replica.findtext("password") for replica in replicas],
            [CLICKHOUSE_ENDPOINTS["clickhouse_password"]] * 3,
        )
        self.assertEqual(default_config.findtext("./macros/replica"), "data-2")
        users_config = compose["configs"]["clickhouse-users"]["content"]
        self.assertNotIn(CLICKHOUSE_ENDPOINTS["clickhouse_password"], users_config)
        self.assertIn("<password_sha256_hex>", users_config)
        users = ET.fromstring(users_config)
        self.assertEqual(
            [element.text for element in users.findall("./users/default/networks/ip")], ["127.0.0.1", "::1"]
        )
        self.assertEqual(
            users.findtext("./users/app/password_sha256_hex"),
            hashlib.sha256(CLICKHOUSE_ENDPOINTS["clickhouse_password"].encode()).hexdigest(),
        )

    def test_rejects_non_three_member_cluster_and_unknown_local_node(self) -> None:
        with self.assertRaisesRegex(ValueError, "exactly three nodes"):
            render_data_node_compose({"nodes": CLUSTER["nodes"][:2]}, "data-1", CLICKHOUSE_ENDPOINTS)

        with self.assertRaisesRegex(ValueError, "must match one configured node"):
            render_data_node_compose(CLUSTER, "data-4", CLICKHOUSE_ENDPOINTS)

    def test_rejects_duplicate_addresses_and_node_ids(self) -> None:
        cluster = {"nodes": [dict(node) for node in CLUSTER["nodes"]]}
        cluster["nodes"][2]["address"] = cluster["nodes"][0]["address"]

        with self.assertRaisesRegex(ValueError, "node addresses must be unique"):
            render_data_node_compose(cluster, "data-1", CLICKHOUSE_ENDPOINTS)

        cluster = {"nodes": [dict(node) for node in CLUSTER["nodes"]]}
        cluster["nodes"][2]["id"] = cluster["nodes"][0]["id"]

        with self.assertRaisesRegex(ValueError, "node ids must be unique"):
            render_data_node_compose(cluster, "data-1", CLICKHOUSE_ENDPOINTS)

    def test_rejects_public_ip_addresses_for_internal_cluster_members(self) -> None:
        cluster = {"nodes": [dict(node) for node in CLUSTER["nodes"]]}
        cluster["nodes"][0]["address"] = "8.8.8.8"

        with self.assertRaisesRegex(ValueError, "invalid private node address"):
            render_data_node_compose(cluster, "data-1", CLICKHOUSE_ENDPOINTS)

    def test_rejects_service_hosts_that_do_not_match_the_cluster_node_addresses(self) -> None:
        endpoints = {**CLICKHOUSE_ENDPOINTS, "flags_redis_hosts": "10.20.0.21:6380,10.20.0.22:6380,10.20.0.23:6380"}

        with self.assertRaisesRegex(ValueError, "flags_redis_hosts must match cluster node addresses"):
            render_data_node_compose(CLUSTER, "data-1", endpoints)

    def test_rejects_missing_or_public_postgres_client_cidrs(self) -> None:
        cluster = {"nodes": CLUSTER["nodes"]}
        with self.assertRaisesRegex(ValueError, "client_cidrs"):
            render_data_node_compose(cluster, "data-1", CLICKHOUSE_ENDPOINTS)

        cluster = {**CLUSTER, "client_cidrs": ["8.8.8.0/24"]}
        with self.assertRaisesRegex(ValueError, "private client CIDR"):
            render_data_node_compose(cluster, "data-1", CLICKHOUSE_ENDPOINTS)

    def test_rejects_kafka_retention_shorter_than_redpanda_minimum(self) -> None:
        cluster = {**CLUSTER, "kafka_retention_ms": 30_000}

        with self.assertRaisesRegex(ValueError, "kafka_retention_ms"):
            render_data_node_compose(cluster, "data-1", CLICKHOUSE_ENDPOINTS)


if __name__ == "__main__":
    unittest.main()
