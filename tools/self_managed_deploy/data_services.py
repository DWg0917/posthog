import re
import json
import hashlib
import ipaddress
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree as ET

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]

DATA_IMAGES = {
    "etcd": "quay.io/coreos/etcd:v3.6.8",
    "zookeeper": "zookeeper:3.7.0",
    "redpanda": "docker.io/redpandadata/redpanda:v25.1.9",
    "clickhouse": "ghcr.io/posthog/clickhouse-posthog:26.9.5.2-posthog-cf6928b17299",
    "postgresql": "posthog-postgres-ha:15.12-patroni4.1.5",
    "redis": "posthog-redis-ha:7.2-sentinel",
    "valkey": "posthog-valkey-ha:8.1-sentinel",
    "seaweedfs": "chrislusf/seaweedfs:4.29",
    "temporal": "temporalio/server:1.26.2",
    "temporal-setup": "temporalio/admin-tools:1.26.2",
}


def render_data_node_compose(cluster: dict[str, Any], node_name: str, endpoints: dict[str, Any]) -> dict[str, Any]:
    nodes = _validated_nodes(cluster)
    _validate_endpoint_host_mappings(nodes, endpoints)
    kafka_retention_ms = _validated_kafka_retention_ms(cluster)
    local_node = next((node for node in nodes if node["name"] == node_name), None)
    if local_node is None:
        raise ValueError("node_name must match one configured node")
    clickhouse_user = endpoints.get("clickhouse_user")
    clickhouse_password = endpoints.get("clickhouse_password")
    if clickhouse_user != "app":
        raise ValueError("clickhouse_user must be app for the self-managed ClickHouse configuration")
    if not isinstance(clickhouse_password, str) or not clickhouse_password:
        raise ValueError("clickhouse_password must be a non-empty string")

    initial_cluster = ",".join(f"{node['name']}=http://{node['address']}:2380" for node in nodes)
    zookeeper_servers = " ".join(f"server.{node['id']}={node['address']}:2888:3888;2181" for node in nodes)
    seed_brokers = ",".join(f"{node['address']}:33145" for node in nodes)
    kafka_hosts = ",".join(f"{node['address']}:9092" for node in nodes)
    node_address = str(local_node["address"])
    node_id = int(local_node["id"])
    master_peers = ",".join(f"{node['address']}:9333" for node in nodes)
    seaweedfs_hosts = ",".join(f"{node['address']}:8333" for node in nodes)
    temporal_hosts = ",".join(f"{node['address']}:7233" for node in nodes)
    clickhouse_configs = _render_clickhouse_configs(nodes, node_name, clickhouse_password)
    postgres_config = _render_postgres_patroni_config(nodes, local_node, cluster, endpoints)
    seaweedfs_postgres_router = _render_seaweedfs_postgres_router_config(nodes)
    redis_password = _redis_password(endpoints)
    flags_redis_password = _redis_password(endpoints, "flags_redis_url")
    initial_redis_master = str(nodes[0]["address"])
    redis_services = {
        **_render_redis_services(node_address, initial_redis_master, redis_password, "redis", 6379, 26379),
        **_render_redis_services(node_address, initial_redis_master, flags_redis_password, "flags-redis", 6380, 26380),
        **_render_redis_services(
            node_address,
            initial_redis_master,
            "",
            "valkey",
            6381,
            26381,
            image_key="valkey",
            dockerfile="docker/valkey-ha/Dockerfile",
            server_bin="valkey-server",
            cli_bin="valkey-cli",
            runtime_user="valkey",
            sentinel_name="posthog-cdp",
        ),
    }
    seaweedfs_database_password = _validated_postgres_password(endpoints, "seaweedfs_database_password")
    object_storage_access_key = _validated_s3_access_key(endpoints, "object_storage_access_key_id")
    object_storage_secret_key = _validated_s3_secret_key(endpoints, "object_storage_secret_access_key")
    replay_access_key = _validated_s3_access_key(endpoints, "session_replay_access_key_id")
    replay_secret_key = _validated_s3_secret_key(endpoints, "session_replay_secret_access_key")
    temporal_database_password = _validated_postgres_password(endpoints, "temporal_database_password")
    temporal_environment = {
        "DB": "postgres12",
        "DB_PORT": "5432",
        "POSTGRES_USER": "temporal",
        "POSTGRES_PWD": temporal_database_password,
        "POSTGRES_SEEDS": "127.0.0.1",
        "DYNAMIC_CONFIG_FILE_PATH": "config/dynamicconfig/self-managed.yaml",
        "TEMPORAL_HOME": "/etc/temporal",
        "ENABLE_ES": "false",
        "BIND_ON_IP": node_address,
        "TEMPORAL_BROADCAST_ADDRESS": node_address,
        "TEMPORAL_ADDRESS": f"{node_address}:7233",
        "TEMPORAL_CLI_ADDRESS": f"{node_address}:7233",
    }

    result = {
        "services": {
            "clickhouse": {
                "image": DATA_IMAGES["clickhouse"],
                "environment": {"CLICKHOUSE_SKIP_USER_SETUP": "1", "KAFKA_HOSTS": kafka_hosts},
                "network_mode": "host",
                "restart": "unless-stopped",
                "volumes": [
                    {
                        "type": "bind",
                        "source": str(REPOSITORY_ROOT / "posthog/idl"),
                        "target": "/idl",
                        "read_only": True,
                    },
                    {
                        "type": "bind",
                        "source": str(REPOSITORY_ROOT / "docker/clickhouse/docker-entrypoint-initdb.d"),
                        "target": "/docker-entrypoint-initdb.d",
                        "read_only": True,
                    },
                    {
                        "type": "bind",
                        "source": str(REPOSITORY_ROOT / "docker/clickhouse/config.d/hobby.xml"),
                        "target": "/etc/clickhouse-server/config.d/hobby.xml",
                        "read_only": True,
                    },
                    {
                        "type": "bind",
                        "source": str(REPOSITORY_ROOT / "docker/clickhouse/user_defined_function.xml"),
                        "target": "/etc/clickhouse-server/user_defined_function.xml",
                        "read_only": True,
                    },
                    {
                        "type": "bind",
                        "source": str(REPOSITORY_ROOT / "posthog/user_scripts"),
                        "target": "/var/lib/clickhouse/user_scripts",
                        "read_only": True,
                    },
                    "clickhouse-data:/var/lib/clickhouse",
                ],
                "configs": [
                    {"source": "clickhouse-main", "target": "/etc/clickhouse-server/config.xml"},
                    {"source": "clickhouse-default", "target": "/etc/clickhouse-server/config.d/default.xml"},
                    {"source": "clickhouse-users", "target": "/etc/clickhouse-server/users.xml"},
                ],
                "healthcheck": {
                    "test": [
                        "CMD-SHELL",
                        "wget --no-verbose --tries=1 --spider http://localhost:8123/ping || exit 1",
                    ],
                    "interval": "10s",
                    "timeout": "10s",
                    "retries": 12,
                },
            },
            "postgresql": {
                "image": DATA_IMAGES["postgresql"],
                "build": {"context": str(REPOSITORY_ROOT), "dockerfile": "docker/postgres-ha/Dockerfile"},
                "command": ["patroni", "/etc/patroni.yml"],
                "configs": [{"source": "postgresql-patroni", "target": "/etc/patroni.yml", "mode": 0o444}],
                "environment": {
                    "POSTHOG_APP_USER": _postgres_app_user(str(endpoints["postgres_url"])),
                    "POSTHOG_APP_PASSWORD": _postgres_app_password(str(endpoints["postgres_url"])),
                    "PATRONI_SUPERUSER_USERNAME": "postgres",
                    "PATRONI_SUPERUSER_PASSWORD": str(endpoints["postgres_superuser_password"]),
                    "PATRONI_REPLICATION_USERNAME": "standby",
                    "PATRONI_REPLICATION_PASSWORD": str(endpoints["postgres_replication_password"]),
                    "SEAWEEDFS_DATABASE_PASSWORD": seaweedfs_database_password,
                    "TEMPORAL_DATABASE_PASSWORD": temporal_database_password,
                },
                "network_mode": "host",
                "restart": "unless-stopped",
                "volumes": ["postgresql-data:/var/lib/postgresql/data"],
                "healthcheck": {
                    "test": ["CMD", "pg_isready", "-h", "/var/run/postgresql", "-p", "5433", "-U", "postgres"],
                    "interval": "10s",
                    "timeout": "5s",
                    "retries": 6,
                    "start_period": "30s",
                },
            },
            **redis_services,
            "redis": {
                "image": DATA_IMAGES["redis"],
                "build": {"context": str(REPOSITORY_ROOT), "dockerfile": "docker/redis-ha/Dockerfile"},
                "command": ["/usr/local/bin/posthog-redis-node"],
                "environment": {
                    "REDIS_NODE_ADDRESS": node_address,
                    "REDIS_INITIAL_PRIMARY": initial_redis_master,
                    "REDIS_SENTINEL_HOST": node_address,
                    "REDIS_SENTINEL_PORT": "26379",
                    "REDIS_PASSWORD": redis_password,
                },
                "network_mode": "host",
                "restart": "unless-stopped",
                "volumes": ["redis-data:/data"],
                "healthcheck": {
                    "test": ["CMD", "redis-cli", "-a", redis_password, "ping"],
                    "interval": "10s",
                    "timeout": "5s",
                    "retries": 6,
                    "start_period": "15s",
                },
            },
            "redis-sentinel": {
                "image": DATA_IMAGES["redis"],
                "build": {"context": str(REPOSITORY_ROOT), "dockerfile": "docker/redis-ha/Dockerfile"},
                "command": ["/usr/local/bin/posthog-redis-sentinel"],
                "environment": {
                    "REDIS_NODE_ADDRESS": node_address,
                    "REDIS_INITIAL_PRIMARY": initial_redis_master,
                    "REDIS_PASSWORD": redis_password,
                },
                "network_mode": "host",
                "restart": "unless-stopped",
                "volumes": ["redis-sentinel-data:/data"],
                "healthcheck": {
                    "test": ["CMD", "redis-cli", "-p", "26379", "PING"],
                    "interval": "10s",
                    "timeout": "5s",
                    "retries": 6,
                    "start_period": "10s",
                },
            },
            "seaweedfs-postgres-router": {
                "image": "haproxy:3.2.27-alpine",
                "configs": [{"source": "seaweedfs-postgres-haproxy", "target": "/usr/local/etc/haproxy/haproxy.cfg"}],
                "command": ["haproxy", "-W", "-db", "-f", "/usr/local/etc/haproxy/haproxy.cfg"],
                "network_mode": "host",
                "restart": "unless-stopped",
                "healthcheck": {
                    "test": ["CMD", "haproxy", "-c", "-f", "/usr/local/etc/haproxy/haproxy.cfg"],
                    "interval": "10s",
                    "timeout": "5s",
                    "retries": 3,
                },
            },
            "seaweedfs": {
                "image": DATA_IMAGES["seaweedfs"],
                "entrypoint": ["/bin/sh", "/usr/local/bin/posthog-seaweedfs-entrypoint"],
                "command": [
                    "server",
                    f"-ip={node_address}",
                    f"-ip.bind={node_address}",
                    "-master.port=9333",
                    f"-master.peers={master_peers}",
                    "-master.defaultReplication=001",
                    "-master.volumePreallocate=false",
                    "-volume.max=1000",
                    "-dir=/data",
                    "-s3",
                    "-s3.port=8333",
                ],
                "environment": {
                    "WEED_LEVELDB2_ENABLED": "false",
                    "WEED_POSTGRES2_ENABLED": "true",
                    "WEED_POSTGRES2_HOSTNAME": "127.0.0.1",
                    "WEED_POSTGRES2_PORT": "5432",
                    "WEED_POSTGRES2_DATABASE": "seaweedfs",
                    "WEED_POSTGRES2_USERNAME": "seaweedfs",
                    "WEED_POSTGRES2_PASSWORD": seaweedfs_database_password,
                    "WEED_POSTGRES2_SSLMODE": "disable",
                    "WEED_POSTGRES2_CONNECTION_MAX_IDLE": "5",
                    "WEED_POSTGRES2_CONNECTION_MAX_OPEN": "20",
                    "OBJECT_STORAGE_ACCESS_KEY_ID": object_storage_access_key,
                    "OBJECT_STORAGE_SECRET_ACCESS_KEY": object_storage_secret_key,
                    "SESSION_REPLAY_ACCESS_KEY_ID": replay_access_key,
                    "SESSION_REPLAY_SECRET_ACCESS_KEY": replay_secret_key,
                    "SEAWEEDFS_BOOTSTRAP_ENABLED": str(node_name == nodes[0]["name"]).lower(),
                },
                "network_mode": "host",
                "restart": "unless-stopped",
                "volumes": [
                    "seaweedfs-data:/data",
                    {
                        "type": "bind",
                        "source": str(REPOSITORY_ROOT / "docker/seaweedfs/entrypoint.sh"),
                        "target": "/usr/local/bin/posthog-seaweedfs-entrypoint",
                        "read_only": True,
                    },
                ],
                "healthcheck": {
                    "test": ["CMD", "test", "-f", "/tmp/posthog-seaweedfs-ready"],
                    "interval": "5s",
                    "timeout": "5s",
                    "retries": 30,
                    "start_period": "10s",
                },
            },
            "temporal": {
                "image": DATA_IMAGES["temporal"],
                "environment": {**temporal_environment, "SKIP_SCHEMA_SETUP": "true"},
                "profiles": ["temporal"],
                "network_mode": "host",
                "restart": "unless-stopped",
                "volumes": [
                    {
                        "type": "bind",
                        "source": str(REPOSITORY_ROOT / "docker/temporal/dynamicconfig/self-managed.yaml"),
                        "target": "/etc/temporal/config/dynamicconfig/self-managed.yaml",
                        "read_only": True,
                    }
                ],
                "healthcheck": {
                    "test": ["CMD-SHELL", "nc -z 127.0.0.1 7233"],
                    "interval": "10s",
                    "timeout": "5s",
                    "retries": 12,
                    "start_period": "60s",
                },
            },
            "etcd": {
                "image": DATA_IMAGES["etcd"],
                "command": [
                    "etcd",
                    f"--name={node_name}",
                    "--data-dir=/etcd-data",
                    f"--listen-client-urls=http://127.0.0.1:2379,http://{node_address}:2379",
                    f"--advertise-client-urls=http://{node_address}:2379",
                    f"--listen-peer-urls=http://{node_address}:2380",
                    f"--initial-advertise-peer-urls=http://{node_address}:2380",
                    f"--initial-cluster={initial_cluster}",
                    "--initial-cluster-token=posthog-self-managed-v1",
                    "--initial-cluster-state=new",
                ],
                "network_mode": "host",
                "restart": "unless-stopped",
                "volumes": ["etcd-data:/etcd-data"],
                "healthcheck": {
                    "test": ["CMD", "etcdctl", "--endpoints=http://127.0.0.1:2379", "endpoint", "health"],
                    "interval": "10s",
                    "timeout": "5s",
                    "retries": 6,
                },
            },
            "zookeeper": {
                "image": DATA_IMAGES["zookeeper"],
                "environment": {
                    "ZOO_MY_ID": str(node_id),
                    "ZOO_SERVERS": zookeeper_servers,
                    "ZOO_4LW_COMMANDS_WHITELIST": "ruok",
                    "ZOO_CFG_EXTRA": f"clientPortAddress={node_address} quorumListenOnAllIPs=false",
                },
                "network_mode": "host",
                "restart": "unless-stopped",
                "volumes": [
                    "zookeeper-data:/data",
                    "zookeeper-datalog:/datalog",
                    "zookeeper-logs:/logs",
                ],
                "healthcheck": {
                    "test": ["CMD-SHELL", "echo ruok | nc 127.0.0.1 2181 | grep imok"],
                    "interval": "10s",
                    "timeout": "5s",
                    "retries": 6,
                },
            },
            "redpanda": {
                "image": DATA_IMAGES["redpanda"],
                "command": [
                    "redpanda",
                    "start",
                    f"--kafka-addr=internal://{node_address}:9092",
                    f"--advertise-kafka-addr=internal://{node_address}:9092",
                    f"--rpc-addr={node_address}:33145",
                    f"--advertise-rpc-addr={node_address}:33145",
                    f"--seeds={seed_brokers}",
                    "--smp=2",
                    "--memory=4G",
                    "--reserve-memory=1G",
                    "--set=redpanda.empty_seed_starts_cluster=false",
                    "--set=redpanda.auto_create_topics_enabled=true",
                    f"--set=redpanda.log_retention_ms={kafka_retention_ms}",
                ],
                "network_mode": "host",
                "restart": "unless-stopped",
                "ulimits": {"nofile": {"soft": 65536, "hard": 65536}},
                "volumes": ["redpanda-data:/var/lib/redpanda/data"],
                "healthcheck": {
                    "test": ["CMD-SHELL", "curl -fsS http://127.0.0.1:9644/v1/status/ready"],
                    "interval": "10s",
                    "timeout": "5s",
                    "retries": 12,
                },
            },
        },
        "volumes": {
            "clickhouse-data": {},
            "etcd-data": {},
            "zookeeper-data": {},
            "zookeeper-datalog": {},
            "zookeeper-logs": {},
            "redpanda-data": {},
            "postgresql-data": {},
            "redis-data": {},
            "redis-sentinel-data": {},
            "flags-redis-data": {},
            "flags-redis-sentinel-data": {},
            "valkey-data": {},
            "valkey-sentinel-data": {},
            "seaweedfs-data": {},
        },
        "configs": {
            **clickhouse_configs,
            "postgresql-patroni": {"content": json.dumps(postgres_config, indent=2)},
            "seaweedfs-postgres-haproxy": {"content": seaweedfs_postgres_router},
        },
        "x-posthog-endpoints": {
            "etcd_endpoints": ",".join(f"http://{node['address']}:2379" for node in nodes),
            "zookeeper_hosts": ",".join(f"{node['address']}:2181" for node in nodes),
            "kafka_hosts": ",".join(f"{node['address']}:9092" for node in nodes),
            "clickhouse_hosts": ",".join(f"{node['address']}:9000" for node in nodes),
            "postgres_hosts": ",".join(f"{node['address']}:5433" for node in nodes),
            "seaweedfs_hosts": seaweedfs_hosts,
            "temporal_hosts": temporal_hosts,
        },
    }

    if node_name == nodes[0]["name"]:
        result["services"]["temporal-schema-setup"] = {
            "image": DATA_IMAGES["temporal-setup"],
            "entrypoint": ["/bin/bash", "-e", "-c"],
            "command": [
                "set -e; "
                "temporal-sql-tool --plugin postgres12 --ep 127.0.0.1 -u temporal -p 5432 "
                "--db temporal setup-schema -v 0.0; "
                "temporal-sql-tool --plugin postgres12 --ep 127.0.0.1 -u temporal -p 5432 "
                "--db temporal update-schema -d /etc/temporal/schema/postgresql/v12/temporal/versioned; "
                "temporal-sql-tool --plugin postgres12 --ep 127.0.0.1 -u temporal -p 5432 "
                "--db temporal_visibility setup-schema -v 0.0; "
                "temporal-sql-tool --plugin postgres12 --ep 127.0.0.1 -u temporal -p 5432 "
                "--db temporal_visibility update-schema -d /etc/temporal/schema/postgresql/v12/visibility/versioned"
            ],
            "environment": {
                **temporal_environment,
                "SQL_PASSWORD": temporal_database_password,
                "SKIP_DB_CREATE": "true",
            },
            "network_mode": "host",
            "profiles": ["init"],
            "restart": "no",
        }
        result["services"]["temporal-namespace-setup"] = {
            "image": "temporalio/admin-tools:1.26.2",
            "entrypoint": ["/bin/bash", "-e", "-c"],
            "command": [
                "for attempt in {1..300}; do "
                "temporal operator cluster health 2>/dev/null | grep -q SERVING && break; sleep 1; "
                "done; temporal operator cluster health 2>/dev/null | grep -q SERVING; "
                "temporal operator namespace describe --namespace default >/dev/null 2>&1 "
                "|| temporal operator namespace create --retention 30d --namespace default"
            ],
            "environment": {"TEMPORAL_ADDRESS": "127.0.0.1:7233", "TEMPORAL_CLI_ADDRESS": "127.0.0.1:7233"},
            "network_mode": "host",
            "profiles": ["init"],
            "restart": "no",
        }
    return result


def _render_postgres_patroni_config(
    nodes: list[dict[str, Any]], local_node: dict[str, Any], cluster: dict[str, Any], endpoints: dict[str, Any]
) -> dict[str, Any]:
    _postgres_app_user(str(endpoints["postgres_url"]))
    for key in ("postgres_superuser_password", "postgres_replication_password"):
        if not isinstance(endpoints.get(key), str) or not endpoints[key]:
            raise ValueError(f"{key} must be a non-empty string")
    client_cidrs = _validated_client_cidrs(cluster)
    node_address = str(local_node["address"])
    pg_hba = ["local all all peer", "host all all 127.0.0.1/32 scram-sha-256", "host all all ::1/128 scram-sha-256"]
    pg_hba.extend(f"host replication standby {cidr} scram-sha-256" for cidr in client_cidrs)
    pg_hba.extend(f"host all all {cidr} scram-sha-256" for cidr in client_cidrs)

    return {
        "scope": "posthog",
        "namespace": "/posthog/",
        "name": str(local_node["name"]),
        "etcd3": {"hosts": [f"{node['address']}:2379" for node in nodes]},
        "restapi": {"listen": f"{node_address}:8008", "connect_address": f"{node_address}:8008"},
        "bootstrap": {
            "dcs": {
                "ttl": 30,
                "loop_wait": 10,
                "retry_timeout": 10,
                "maximum_lag_on_failover": 1048576,
                "synchronous_mode": True,
                "synchronous_mode_strict": True,
                "synchronous_node_count": 1,
                "postgresql": {
                    "use_pg_rewind": True,
                    "use_slots": True,
                    "parameters": {"wal_level": "replica", "hot_standby": "on", "password_encryption": "scram-sha-256"},
                },
            },
            "initdb": [{"encoding": "UTF8"}, {"data-checksums": True}, {"auth-host": "scram-sha-256"}],
            "post_init": "/usr/local/bin/posthog-post-init",
        },
        "postgresql": {
            "listen": f"{node_address}:5433",
            "connect_address": f"{node_address}:5433",
            "data_dir": "/var/lib/postgresql/data",
            "bin_dir": "/usr/lib/postgresql/15/bin",
            "pg_hba": pg_hba,
        },
    }


def _postgres_app_user(value: str) -> str:
    parsed = urlsplit(value)
    if not parsed.scheme or not parsed.hostname or not parsed.username or not parsed.password:
        raise ValueError("postgres_url must contain an application username and password")
    return unquote(parsed.username)


def _postgres_app_password(value: str) -> str:
    parsed = urlsplit(value)
    if not parsed.password:
        raise ValueError("postgres_url must contain an application username and password")
    return unquote(parsed.password)


def _render_seaweedfs_postgres_router_config(nodes: list[dict[str, Any]]) -> str:
    servers = "\n".join(
        f"    server postgres-{node['name']} {node['address']}:5433 check port 8008 inter 2s fall 3 rise 2"
        for node in nodes
    )
    return (
        "global\n    log stdout format raw local0\n\n"
        "defaults\n    mode tcp\n    log global\n    timeout connect 5s\n    timeout client 1m\n    timeout server 1m\n\n"
        "frontend seaweedfs-postgres\n    bind 127.0.0.1:5432\n    default_backend postgres_primary\n\n"
        "backend postgres_primary\n    option httpchk GET /primary\n    http-check expect status 200\n"
        f"{servers}\n"
    )


def _validated_postgres_password(endpoints: dict[str, Any], key: str) -> str:
    password = endpoints.get(key)
    if not isinstance(password, str) or not password:
        raise ValueError(f"{key} must be a non-empty string")
    return password


def _validated_s3_access_key(endpoints: dict[str, Any], key: str) -> str:
    value = endpoints.get(key)
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{3,128}", value):
        raise ValueError(f"{key} must contain 3 to 128 URL-safe characters")
    return value


def _validated_s3_secret_key(endpoints: dict[str, Any], key: str) -> str:
    value = endpoints.get(key)
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9._~-]{8,128}", value):
        raise ValueError(f"{key} must contain 8 to 128 URL-safe characters")
    return value


def _redis_password(endpoints: dict[str, Any], key: str = "redis_url") -> str:
    value = str(endpoints.get(key, ""))
    parsed = urlsplit(value)
    password = unquote(parsed.password) if parsed.password else ""
    if parsed.scheme not in {"redis", "rediss"} or not parsed.hostname or not password:
        raise ValueError(f"{key} must contain a Redis scheme, host, and password")
    if parsed.username not in {None, "", "default"} or not re.fullmatch(r"[A-Za-z0-9._~-]+", password):
        raise ValueError(f"{key} must use the default ACL user and a URL-safe Redis password")
    return password


def _render_redis_services(
    node_address: str,
    initial_primary: str,
    password: str,
    service_name: str,
    redis_port: int,
    sentinel_port: int,
    image_key: str = "redis",
    dockerfile: str = "docker/redis-ha/Dockerfile",
    server_bin: str = "redis-server",
    cli_bin: str = "redis-cli",
    runtime_user: str = "redis",
    sentinel_name: str | None = None,
) -> dict[str, dict[str, Any]]:
    sentinel_name = sentinel_name or ("posthog" if service_name == "redis" else "posthog-flags")
    script_prefix = "posthog-valkey" if image_key == "valkey" else "posthog-redis"
    healthcheck = ["CMD", cli_bin, "-h", "127.0.0.1", "-p", str(redis_port)]
    if password:
        healthcheck.extend(["--no-auth-warning", "-a", password])
    healthcheck.append("PING")
    return {
        service_name: {
            "image": DATA_IMAGES[image_key],
            "build": {"context": str(REPOSITORY_ROOT), "dockerfile": dockerfile},
            "command": [f"/usr/local/bin/{script_prefix}-node"],
            "environment": {
                "REDIS_NODE_ADDRESS": node_address,
                "REDIS_INITIAL_PRIMARY": initial_primary,
                "REDIS_SENTINEL_HOST": node_address,
                "REDIS_SENTINEL_NAME": sentinel_name,
                "REDIS_SENTINEL_PORT": str(sentinel_port),
                "REDIS_PORT": str(redis_port),
                "REDIS_PASSWORD": password,
                "REDIS_SERVER_BIN": server_bin,
                "REDIS_CLI_BIN": cli_bin,
                "REDIS_RUNTIME_USER": runtime_user,
            },
            "network_mode": "host",
            "restart": "unless-stopped",
            "volumes": [f"{service_name}-data:/data"],
            "healthcheck": {
                "test": healthcheck,
                "interval": "10s",
                "timeout": "5s",
                "retries": 6,
                "start_period": "15s",
            },
        },
        f"{service_name}-sentinel": {
            "image": DATA_IMAGES[image_key],
            "build": {"context": str(REPOSITORY_ROOT), "dockerfile": dockerfile},
            "command": [f"/usr/local/bin/{script_prefix}-sentinel"],
            "environment": {
                "REDIS_NODE_ADDRESS": node_address,
                "REDIS_INITIAL_PRIMARY": initial_primary,
                "REDIS_SENTINEL_NAME": sentinel_name,
                "REDIS_SENTINEL_PORT": str(sentinel_port),
                "REDIS_PORT": str(redis_port),
                "REDIS_PASSWORD": password,
                "REDIS_SERVER_BIN": server_bin,
                "REDIS_CLI_BIN": cli_bin,
                "REDIS_RUNTIME_USER": runtime_user,
            },
            "network_mode": "host",
            "restart": "unless-stopped",
            "volumes": [f"{service_name}-sentinel-data:/data"],
            "healthcheck": {
                "test": ["CMD", cli_bin, "-h", node_address, "-p", str(sentinel_port), "PING"],
                "interval": "10s",
                "timeout": "5s",
                "retries": 6,
                "start_period": "10s",
            },
        },
    }


def _validated_client_cidrs(cluster: dict[str, Any]) -> list[str]:
    raw_cidrs = cluster.get("client_cidrs")
    if not isinstance(raw_cidrs, list) or not raw_cidrs:
        raise ValueError("cluster must include client_cidrs for PostgreSQL access")
    cidrs = []
    for raw_cidr in raw_cidrs:
        try:
            network = ipaddress.ip_network(str(raw_cidr), strict=False)
        except ValueError as error:
            raise ValueError("client_cidrs must contain valid private CIDR ranges") from error
        if not network.is_private or network.is_loopback or network.is_link_local or network.is_multicast:
            raise ValueError(f"client_cidrs must contain private client CIDRs: {raw_cidr}")
        cidrs.append(str(network))
    if len(set(cidrs)) != len(cidrs):
        raise ValueError("client_cidrs must not contain duplicates")
    return cidrs


def _validated_kafka_retention_ms(cluster: dict[str, Any]) -> int:
    retention_ms = cluster.get("kafka_retention_ms", 3_600_000)
    if type(retention_ms) is not int or retention_ms < 60_000:
        raise ValueError("kafka_retention_ms must be an integer of at least 60000")
    return retention_ms


def _render_clickhouse_configs(
    nodes: list[dict[str, Any]], node_name: str, clickhouse_password: str
) -> dict[str, dict[str, str]]:
    main_config = ET.parse(REPOSITORY_ROOT / "docker/clickhouse/config.xml").getroot()
    for listen_host in list(main_config.findall("listen_host")):
        main_config.remove(listen_host)
    first_listen_host = ET.SubElement(main_config, "listen_host", {"replace": "replace"})
    first_listen_host.text = "127.0.0.1"
    node_address = str(next(node["address"] for node in nodes if node["name"] == node_name))
    ET.SubElement(main_config, "listen_host").text = node_address
    interserver_host = main_config.find("interserver_http_host")
    if interserver_host is None:
        interserver_host = ET.SubElement(main_config, "interserver_http_host")
    interserver_host.text = node_address
    zookeeper = main_config.find("zookeeper")
    if zookeeper is None:
        zookeeper = ET.SubElement(main_config, "zookeeper")
    zookeeper.clear()
    for index, node in enumerate(nodes, start=1):
        zookeeper_node = ET.SubElement(zookeeper, "node", {"index": str(index)})
        ET.SubElement(zookeeper_node, "host").text = str(node["address"])
        ET.SubElement(zookeeper_node, "port").text = "2181"

    default_config = ET.parse(REPOSITORY_ROOT / "docker/clickhouse/config.d/default.xml").getroot()
    remote_servers = default_config.find("remote_servers")
    if remote_servers is None:
        raise ValueError("ClickHouse default config is missing remote_servers")
    for clickhouse_cluster in remote_servers:
        for child in list(clickhouse_cluster):
            clickhouse_cluster.remove(child)
        shard = ET.SubElement(clickhouse_cluster, "shard")
        ET.SubElement(shard, "internal_replication").text = "true"
        for node in nodes:
            replica = ET.SubElement(shard, "replica")
            ET.SubElement(replica, "host").text = str(node["address"])
            ET.SubElement(replica, "port").text = "9000"
            ET.SubElement(replica, "user").text = "app"
            ET.SubElement(replica, "password").text = clickhouse_password

    macros = default_config.find("macros")
    if macros is None:
        macros = ET.SubElement(default_config, "macros")
    replica_macro = macros.find("replica")
    if replica_macro is None:
        replica_macro = ET.SubElement(macros, "replica")
    replica_macro.text = node_name

    users_config = ET.parse(REPOSITORY_ROOT / "docker/clickhouse/users.xml").getroot()
    default_user = users_config.find("./users/default")
    if default_user is None:
        raise ValueError("ClickHouse users config is missing the default user")
    default_networks = default_user.find("networks")
    if default_networks is None:
        default_networks = ET.SubElement(default_user, "networks")
    default_networks.clear()
    ET.SubElement(default_networks, "ip").text = "127.0.0.1"
    ET.SubElement(default_networks, "ip").text = "::1"

    app_user = users_config.find("./users/app")
    if app_user is None:
        raise ValueError("ClickHouse users config is missing the app user")
    password = app_user.find("password")
    if password is not None:
        app_user.remove(password)
    password_hash = app_user.find("password_sha256_hex")
    if password_hash is None:
        password_hash = ET.SubElement(app_user, "password_sha256_hex")
    password_hash.text = hashlib.sha256(clickhouse_password.encode()).hexdigest()

    return {
        "clickhouse-main": {"content": ET.tostring(main_config, encoding="unicode")},
        "clickhouse-default": {"content": ET.tostring(default_config, encoding="unicode")},
        "clickhouse-users": {"content": ET.tostring(users_config, encoding="unicode")},
    }


def _validated_nodes(cluster: dict[str, Any]) -> list[dict[str, Any]]:
    raw_nodes = cluster.get("nodes")
    if not isinstance(raw_nodes, list) or len(raw_nodes) != 3:
        raise ValueError("cluster must contain exactly three nodes")

    nodes: list[dict[str, Any]] = []
    for raw_node in raw_nodes:
        if not isinstance(raw_node, dict):
            raise ValueError("each node must be a mapping")
        name = raw_node.get("name")
        address = raw_node.get("address")
        node_id = raw_node.get("id")
        if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,30}", name):
            raise ValueError("node names must be lowercase host labels")
        if not isinstance(address, str) or not _valid_node_address(address):
            raise ValueError(f"invalid private node address for {name}")
        if isinstance(node_id, bool) or not isinstance(node_id, int) or not 1 <= node_id <= 255:
            raise ValueError(f"node id for {name} must be an integer from 1 to 255")
        nodes.append({"name": name, "address": address, "id": node_id})

    if len({node["name"] for node in nodes}) != 3:
        raise ValueError("node names must be unique")
    if len({node["address"] for node in nodes}) != 3:
        raise ValueError("node addresses must be unique")
    if len({node["id"] for node in nodes}) != 3:
        raise ValueError("node ids must be unique")
    return nodes


def _validate_endpoint_host_mappings(nodes: list[dict[str, Any]], endpoints: dict[str, Any]) -> None:
    ports = {
        "postgres_hosts": 5433,
        "clickhouse_hosts": 9000,
        "kafka_hosts": 9092,
        "redis_hosts": 6379,
        "flags_redis_hosts": 6380,
        "valkey_hosts": 6381,
        "seaweedfs_hosts": 8333,
        "temporal_hosts": 7233,
    }
    for key, port in ports.items():
        raw_hosts = endpoints.get(key)
        if not isinstance(raw_hosts, str):
            raise ValueError(f"{key} must list the configured cluster node addresses")
        configured_hosts = [host.strip() for host in raw_hosts.split(",")]
        expected_hosts = [f"{node['address']}:{port}" for node in nodes]
        if configured_hosts != expected_hosts:
            raise ValueError(f"{key} must match cluster node addresses in order")


def _valid_node_address(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    return (
        ip.version == 4
        and ip.is_private
        and not (ip.is_loopback or ip.is_link_local or ip.is_unspecified or ip.is_multicast)
    )
