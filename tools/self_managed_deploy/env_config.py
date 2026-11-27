import os
import re
import json
import secrets
import ipaddress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def write_endpoints(cluster: dict[str, Any], domain: str, output: Path, trusted_proxy_cidrs: list[str]) -> None:
    if not _is_hostname(domain):
        raise ValueError("domain must be a hostname")
    raw_nodes = cluster.get("nodes")
    if not isinstance(raw_nodes, list) or len(raw_nodes) != 3:
        raise ValueError("cluster must contain exactly three data nodes")
    addresses: list[str] = []
    for node in raw_nodes:
        if not isinstance(node, dict):
            raise ValueError("cluster nodes must be objects")
        try:
            address = ipaddress.ip_address(str(node.get("address", "")))
        except ValueError as error:
            raise ValueError("data node addresses must be private IPv4 addresses") from error
        if address.version != 4 or not address.is_private or address.is_loopback or address.is_link_local:
            raise ValueError("data node addresses must be private IPv4 addresses")
        addresses.append(str(address))
    if len(set(addresses)) != 3:
        raise ValueError("data node addresses must be unique")
    if len(set(trusted_proxy_cidrs)) != len(trusted_proxy_cidrs):
        raise ValueError("trusted proxy CIDRs must not contain duplicates")
    for cidr in trusted_proxy_cidrs:
        try:
            ipaddress.ip_network(cidr, strict=False)
        except ValueError as error:
            raise ValueError(f"invalid trusted proxy CIDR: {cidr}") from error

    def hosts(port: int) -> str:
        return ",".join(f"{address}:{port}" for address in addresses)

    postgres_password = secrets.token_hex(32)
    redis_password = secrets.token_hex(32)
    flags_redis_password = secrets.token_hex(32)
    endpoints = {
        "postgres_url": f"postgresql://posthog:{postgres_password}@postgres-router:5432/posthog",
        "postgres_hosts": hosts(5433),
        "postgres_superuser_password": secrets.token_hex(32),
        "postgres_replication_password": secrets.token_hex(32),
        "clickhouse_hosts": hosts(9000),
        "clickhouse_user": "app",
        "clickhouse_password": secrets.token_hex(32),
        "kafka_hosts": hosts(9092),
        "redis_url": f"redis://:{redis_password}@redis-router:6379/0",
        "redis_hosts": hosts(6379),
        "flags_redis_url": f"redis://:{flags_redis_password}@flags-redis-router:6380/0",
        "flags_redis_hosts": hosts(6380),
        "valkey_host": "valkey-router",
        "valkey_port": 6381,
        "valkey_hosts": hosts(6381),
        "seaweedfs_hosts": hosts(8333),
        "seaweedfs_database_password": secrets.token_hex(32),
        "object_storage_access_key_id": secrets.token_hex(20),
        "object_storage_secret_access_key": secrets.token_hex(32),
        "object_storage_region": "us-east-1",
        "object_storage_public_endpoint": f"https://{domain}/posthog",
        "session_replay_access_key_id": secrets.token_hex(20),
        "session_replay_secret_access_key": secrets.token_hex(32),
        "session_replay_region": "us-east-1",
        "temporal_hosts": hosts(7233),
        "temporal_database_password": secrets.token_hex(32),
        "etcd_endpoints": ",".join(f"http://{address}:2379" for address in addresses),
        "domain": domain,
        "trusted_proxy_cidrs": trusted_proxy_cidrs,
        "service_environment_overrides": {},
    }
    from tools.self_managed_deploy.data_services import render_data_node_compose

    render_data_node_compose(cluster, str(raw_nodes[0].get("name", "")), endpoints)
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as endpoint_file:
        json.dump(endpoints, endpoint_file, indent=2)
        endpoint_file.write("\n")


def write_app_env(endpoints: dict[str, Any], app_tag: str, output: Path) -> None:
    domain = str(endpoints.get("domain", ""))
    if not re.fullmatch(r"[A-Za-z0-9.-]+", domain) or ".." in domain:
        raise ValueError("domain must be a hostname")
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}", app_tag):
        raise ValueError("app_tag must be a valid Docker image tag")

    values = {
        "POSTHOG_SECRET": secrets.token_hex(48),
        "ENCRYPTION_SALT_KEYS": secrets.token_hex(16),
        "DOMAIN": domain,
        "TLS_BLOCK": "",
        "REGISTRY_URL": "posthog/posthog",
        "CADDY_TLS_BLOCK": "",
        "CADDY_HOST": f'"{domain}, http://, https://"',
        "POSTHOG_APP_TAG": app_tag,
        "POSTHOG_NODE_TAG": "latest",
        "SESSION_RECORDING_V2_METADATA_SWITCHOVER": datetime.now(UTC).isoformat(timespec="seconds"),
        "INTERNAL_REQUEST_TOKEN": secrets.token_hex(48),
        "BROWSERLESS_SECRET": secrets.token_hex(48),
        "RECORDING_API_JWT_SECRET": secrets.token_hex(48),
        "OBJECT_STORAGE_ACCESS_KEY_ID": _required_endpoint(endpoints, "object_storage_access_key_id"),
        "OBJECT_STORAGE_SECRET_ACCESS_KEY": _required_endpoint(endpoints, "object_storage_secret_access_key"),
    }
    content = "".join(f"{key}={value}\n" for key, value in values.items())
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as env_file:
        env_file.write(content)


def _required_endpoint(endpoints: dict[str, Any], key: str) -> str:
    value = str(endpoints.get(key, ""))
    if not value or "\n" in value or "\r" in value:
        raise ValueError(f"{key} must be a non-empty single-line value")
    return value


def _is_hostname(domain: str) -> bool:
    if len(domain) > 253:
        return False
    labels = domain.rstrip(".").split(".")
    return bool(domain) and all(
        1 <= len(label) <= 63 and re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?", label) for label in labels
    )
