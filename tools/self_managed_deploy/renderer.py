import re
import ipaddress
from copy import deepcopy
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urlsplit, urlunsplit

STATE_SERVICES = {
    "db",
    "clickhouse",
    "etcd",
    "kafka",
    "objectstorage",
    "redis7",
    "seaweedfs",
    "temporal",
    "valkey",
    "zookeeper",
    "temporal-admin-tools",
    "temporal-ui",
}
INIT_SERVICES = {"asyncmigrationscheck", "kafka-init", "personhog-etcd-init", "personhog-tables-ready"}
POSTGRES_URL_KEYS = {
    "DATABASE_URL",
    "PERSONS_DATABASE_URL",
    "PERSONS_URL",
    "PERSONS_DB_READER_URL",
    "PERSONS_DB_WRITER_URL",
    "PERSONS_READ_DATABASE_URL",
    "PERSONS_WRITE_DATABASE_URL",
    "PRIMARY_DATABASE_URL",
    "FALLBACK_DATABASE_URL",
    "READ_DATABASE_URL",
    "WRITE_DATABASE_URL",
}
REDIS_URL_KEYS = {"ISSUE_BUCKETS_REDIS_URL"}
CLICKHOUSE_HOST_KEYS = {"CLICKHOUSE_HOST", "CLICKHOUSE_LOGS_CLUSTER_HOST"}
REDIS_HOST_KEYS = {
    "CDP_REDIS_HOST",
    "COOKIELESS_REDIS_HOST",
    "LOGS_REDIS_HOST",
    "METRICS_REDIS_HOST",
    "SESSION_RECORDING_API_REDIS_HOST",
    "TRACES_REDIS_HOST",
}
KAFKA_HOST_KEYS = {"KAFKA_HOSTS", "KAFKA_INGESTION_PRODUCER_METADATA_BROKER_LIST", "OUTPUT_KAFKA_HOSTS"}
REQUIRED_ENDPOINTS = {
    "postgres_url",
    "postgres_hosts",
    "postgres_superuser_password",
    "postgres_replication_password",
    "clickhouse_hosts",
    "clickhouse_user",
    "clickhouse_password",
    "kafka_hosts",
    "redis_hosts",
    "flags_redis_hosts",
    "valkey_hosts",
    "redis_url",
    "flags_redis_url",
    "valkey_host",
    "valkey_port",
    "seaweedfs_hosts",
    "seaweedfs_database_password",
    "object_storage_access_key_id",
    "object_storage_secret_access_key",
    "object_storage_region",
    "object_storage_public_endpoint",
    "session_replay_access_key_id",
    "session_replay_secret_access_key",
    "session_replay_region",
    "temporal_hosts",
    "temporal_database_password",
    "etcd_endpoints",
    "domain",
}


def render_multihost_compose(
    compose_config: dict[str, Any],
    endpoints: dict[str, Any],
    ingress_mode: str,
    node_name: str = "app-1",
    build_context: str = ".",
) -> dict[str, Any]:
    if ingress_mode not in {"direct", "lb"}:
        raise ValueError(f"unsupported ingress mode: {ingress_mode}")
    if node_name not in {"app-1", "app-2"}:
        raise ValueError("node_name must be app-1 or app-2")

    missing = sorted(key for key in REQUIRED_ENDPOINTS if not str(endpoints.get(key, "")).strip())
    if missing:
        raise ValueError(f"missing required external endpoints: {', '.join(missing)}")
    _validate_endpoint_values(endpoints)
    if ingress_mode == "lb" and not endpoints.get("trusted_proxy_cidrs"):
        raise ValueError("ingress mode lb requires trusted_proxy_cidrs")

    rendered = deepcopy(compose_config)
    services = rendered.get("services")
    if not isinstance(services, dict):
        raise ValueError("Compose config must contain a services mapping")

    for service_name in STATE_SERVICES:
        services.pop(service_name, None)

    rendered.setdefault("configs", {})["clickhouse-haproxy"] = {
        "content": _render_clickhouse_router_config(str(endpoints["clickhouse_hosts"]))
    }
    rendered["configs"]["postgres-haproxy"] = {
        "content": _render_postgres_router_config(str(endpoints["postgres_hosts"]))
    }
    rendered["configs"]["redis-haproxy"] = {
        "content": _render_redis_router_config(str(endpoints["redis_hosts"]), str(endpoints["redis_url"]))
    }
    rendered["configs"]["flags-redis-haproxy"] = {
        "content": _render_redis_router_config(
            str(endpoints["flags_redis_hosts"]), str(endpoints["flags_redis_url"]), redis_port=6380
        )
    }
    rendered["configs"]["valkey-haproxy"] = {
        "content": _render_redis_router_config(
            str(endpoints["valkey_hosts"]), "redis://valkey-router:6381/0", redis_port=6381
        )
    }
    rendered["configs"]["seaweedfs-haproxy"] = {
        "content": _render_seaweedfs_router_config(str(endpoints["seaweedfs_hosts"]))
    }
    rendered["configs"]["temporal-haproxy"] = {
        "content": _render_temporal_router_config(str(endpoints["temporal_hosts"]))
    }
    services["clickhouse-router"] = {
        "image": "haproxy:3.2.27-alpine",
        "restart": "unless-stopped",
        "configs": [{"source": "clickhouse-haproxy", "target": "/usr/local/etc/haproxy/haproxy.cfg"}],
        "healthcheck": {
            "test": ["CMD", "haproxy", "-c", "-f", "/usr/local/etc/haproxy/haproxy.cfg"],
            "interval": "10s",
            "timeout": "5s",
            "retries": 3,
        },
    }
    services["postgres-router"] = {
        "image": "haproxy:3.2.27-alpine",
        "restart": "unless-stopped",
        "configs": [{"source": "postgres-haproxy", "target": "/usr/local/etc/haproxy/haproxy.cfg"}],
        "healthcheck": {
            "test": ["CMD", "haproxy", "-c", "-f", "/usr/local/etc/haproxy/haproxy.cfg"],
            "interval": "10s",
            "timeout": "5s",
            "retries": 3,
        },
    }
    services["redis-router"] = {
        "image": "haproxy:3.2.27-alpine",
        "restart": "unless-stopped",
        "configs": [{"source": "redis-haproxy", "target": "/usr/local/etc/haproxy/haproxy.cfg"}],
        "healthcheck": {
            "test": ["CMD", "haproxy", "-c", "-f", "/usr/local/etc/haproxy/haproxy.cfg"],
            "interval": "10s",
            "timeout": "5s",
            "retries": 3,
        },
    }
    services["flags-redis-router"] = {
        "image": "haproxy:3.2.27-alpine",
        "restart": "unless-stopped",
        "configs": [{"source": "flags-redis-haproxy", "target": "/usr/local/etc/haproxy/haproxy.cfg"}],
        "healthcheck": {
            "test": ["CMD", "haproxy", "-c", "-f", "/usr/local/etc/haproxy/haproxy.cfg"],
            "interval": "10s",
            "timeout": "5s",
            "retries": 3,
        },
    }
    services["valkey-router"] = {
        "image": "haproxy:3.2.27-alpine",
        "restart": "unless-stopped",
        "configs": [{"source": "valkey-haproxy", "target": "/usr/local/etc/haproxy/haproxy.cfg"}],
        "healthcheck": {
            "test": ["CMD", "haproxy", "-c", "-f", "/usr/local/etc/haproxy/haproxy.cfg"],
            "interval": "10s",
            "timeout": "5s",
            "retries": 3,
        },
    }
    services["seaweedfs-router"] = {
        "image": "haproxy:3.2.27-alpine",
        "restart": "unless-stopped",
        "configs": [{"source": "seaweedfs-haproxy", "target": "/usr/local/etc/haproxy/haproxy.cfg"}],
        "healthcheck": {
            "test": ["CMD", "haproxy", "-c", "-f", "/usr/local/etc/haproxy/haproxy.cfg"],
            "interval": "10s",
            "timeout": "5s",
            "retries": 3,
        },
    }
    services["temporal-router"] = {
        "image": "haproxy:3.2.27-alpine",
        "restart": "unless-stopped",
        "configs": [{"source": "temporal-haproxy", "target": "/usr/local/etc/haproxy/haproxy.cfg"}],
        "healthcheck": {
            "test": ["CMD", "haproxy", "-c", "-f", "/usr/local/etc/haproxy/haproxy.cfg"],
            "interval": "10s",
            "timeout": "5s",
            "retries": 3,
        },
    }

    _normalize_repository_paths(services, build_context)
    _configure_source_builds(services, build_context)

    services["flags-cache-builder"] = {
        "image": "posthog-flags-cache-builder:local",
        "build": {
            "context": f"{build_context.rstrip('/')}/rust",
            "args": {"BIN": "flags-cache-builder", "BUILD_FEATURES": "feature-flags/flags-cache-builder"},
        },
        "restart": "unless-stopped",
        "healthcheck": {
            "test": ["CMD", "curl", "-f", "http://localhost:9090/_readiness"],
            "interval": "10s",
            "timeout": "5s",
            "retries": 3,
            "start_period": "20s",
        },
        "environment": {
            "KAFKA_HOSTS": str(endpoints["kafka_hosts"]),
            "KAFKA_CONSUMER_GROUP": "flags-cache-builder",
            "KAFKA_CONSUMER_TOPIC": "flags_cache_invalidation",
            "READ_DATABASE_URL": str(endpoints["postgres_url"]),
            "FLAGS_REDIS_URL": str(endpoints["flags_redis_url"]),
            "OBJECT_STORAGE_BUCKET": "posthog",
            "OBJECT_STORAGE_REGION": str(endpoints["object_storage_region"]),
            "OBJECT_STORAGE_ENDPOINT": "http://seaweedfs-router:8333",
            "AWS_ACCESS_KEY_ID": str(endpoints["object_storage_access_key_id"]),
            "AWS_SECRET_ACCESS_KEY": str(endpoints["object_storage_secret_access_key"]),
        },
    }

    if node_name != "app-1":
        for service_name in INIT_SERVICES:
            services.pop(service_name, None)

    for service_name, service in services.items():
        if not isinstance(service, dict):
            continue
        existing_dependencies = service.get("depends_on", {})
        if isinstance(existing_dependencies, dict):
            dependencies = {name: value for name, value in existing_dependencies.items() if name in services}
        elif isinstance(existing_dependencies, list):
            dependencies = {
                name: {"condition": "service_started"} for name in existing_dependencies if name in services
            }
        else:
            dependencies = {}
        _replace_external_endpoints(service, endpoints)
        if _uses_clickhouse(service):
            clickhouse_environment = service.setdefault("environment", {})
            clickhouse_environment["CLICKHOUSE_USER"] = str(endpoints["clickhouse_user"])
            clickhouse_environment["CLICKHOUSE_PASSWORD"] = str(endpoints["clickhouse_password"])
        if service_name in {"web", "worker", "asyncmigrationscheck", "temporal-django-worker"}:
            service.setdefault("environment", {})["TEMPORAL_HOST"] = "temporal-router"
        if service_name in {"web", "worker", "temporal-django-worker", "feature-flags"}:
            service.setdefault("environment", {})["FLAGS_REDIS_URL"] = _redis_router_url(
                str(endpoints["flags_redis_url"]), "flags-redis-router", 6380
            )
        _rewrite_initialization_command(service_name, service, endpoints)
        if service_name in {"web", "worker", "temporal-django-worker", "asyncmigrationscheck"}:
            environment = service.setdefault("environment", {})
            environment["BATCH_EXPORT_OBJECT_STORAGE_ENDPOINT"] = "http://seaweedfs-router:8333"
            environment["BATCH_EXPORT_OBJECT_STORAGE_REGION"] = str(endpoints["object_storage_region"])
            environment["OBJECT_STORAGE_REGION"] = str(endpoints["object_storage_region"])
        if service_name in INIT_SERVICES:
            service["profiles"] = ["init"]
        if service_name == "kafka-init":
            environment = service.setdefault("environment", {})
            environment["KAFKA_TOPIC_REPLICATION_FACTOR"] = "3"
        if _uses_clickhouse(service):
            dependencies["clickhouse-router"] = {"condition": "service_healthy"}
        if _uses_postgres(service):
            dependencies["postgres-router"] = {"condition": "service_healthy"}
        if _uses_redis(service):
            dependencies["redis-router"] = {"condition": "service_healthy"}
        if _uses_flags_redis(service):
            dependencies["flags-redis-router"] = {"condition": "service_healthy"}
        if _uses_valkey(service):
            dependencies["valkey-router"] = {"condition": "service_healthy"}
        if _uses_object_storage(service):
            dependencies["seaweedfs-router"] = {"condition": "service_healthy"}
        if service_name == "proxy":
            dependencies["seaweedfs-router"] = {"condition": "service_healthy"}
        if _uses_temporal(service):
            dependencies["temporal-router"] = {"condition": "service_healthy"}
        if dependencies:
            service["depends_on"] = dependencies
        else:
            service.pop("depends_on", None)

    web = services.get("web")
    if isinstance(web, dict):
        web["command"] = ["./bin/docker-server"]
        _remove_compose_mounts(web)

    worker = services.get("worker")
    if isinstance(worker, dict):
        worker["command"] = ["./bin/docker-worker-celery"]
        celery_beat = deepcopy(worker)
        celery_beat["command"] = ["./bin/docker-worker-beat"]
        if node_name == "app-1":
            celery_beat.pop("profiles", None)
        else:
            celery_beat["profiles"] = ["scheduler"]
        services["celery-beat"] = celery_beat

    temporal_worker = services.get("temporal-django-worker")
    if isinstance(temporal_worker, dict):
        temporal_worker["command"] = ["./bin/temporal-django-worker"]
        _remove_compose_mounts(temporal_worker)

    logs_ingestion = services.get("ingestion-logs")
    if isinstance(logs_ingestion, dict):
        metrics_ingestion = deepcopy(logs_ingestion)
        metrics_environment = metrics_ingestion.setdefault("environment", {})
        metrics_environment["PLUGIN_SERVER_MODE"] = "ingestion-metrics"
        redis_url = _redis_router_url(str(endpoints["redis_url"]))
        metrics_environment["METRICS_REDIS_HOST"] = _url_host(redis_url)
        metrics_environment["METRICS_REDIS_PORT"] = str(_url_port(redis_url, 6379))
        metrics_environment["METRICS_REDIS_TLS"] = str(urlsplit(redis_url).scheme == "rediss").lower()
        redis_password = _url_password(str(endpoints["redis_url"]))
        if redis_password is not None:
            metrics_environment["METRICS_REDIS_PASSWORD"] = redis_password
        services["ingestion-metrics"] = metrics_ingestion

    _configure_ingress(services, endpoints, ingress_mode)
    _apply_service_environment_overrides(services, endpoints)
    _retain_referenced_volumes(rendered, services)
    return rendered


def _configure_source_builds(services: dict[str, Any], build_context: str) -> None:
    for service_name in ("web", "worker", "asyncmigrationscheck", "temporal-django-worker"):
        service = services.get(service_name)
        if isinstance(service, dict):
            service["build"] = {"context": build_context, "dockerfile": "Dockerfile"}

    for service in services.values():
        if not isinstance(service, dict):
            continue
        image = str(service.get("image", "")).rsplit("/", 1)[-1].split(":", 1)[0]
        if image.endswith("-node"):
            service["build"] = {"context": build_context, "dockerfile": "Dockerfile.node"}

    livestream = services.get("livestream")
    if isinstance(livestream, dict):
        livestream["build"] = {"context": f"{build_context.rstrip('/')}/livestream", "dockerfile": "Dockerfile"}


def _normalize_repository_paths(services: dict[str, Any], build_context: str) -> None:
    root = Path(build_context).resolve()
    legacy_prefix = root / "posthog"

    def normalize(path_value: Any) -> Any:
        if not isinstance(path_value, str):
            return path_value
        path = Path(path_value).resolve()
        if not path.is_absolute():
            return path_value
        try:
            relative_path = path.relative_to(legacy_prefix)
        except ValueError:
            return path_value
        if path.exists():
            return path_value
        checkout_path = root / relative_path
        return str(checkout_path) if checkout_path.exists() else path_value

    for service in services.values():
        if not isinstance(service, dict):
            continue
        build = service.get("build")
        if isinstance(build, dict) and "context" in build:
            build["context"] = normalize(build["context"])
        volumes = service.get("volumes")
        if isinstance(volumes, list):
            for volume in volumes:
                if isinstance(volume, dict) and volume.get("type") == "bind" and "source" in volume:
                    volume["source"] = normalize(volume["source"])


def _apply_service_environment_overrides(services: dict[str, Any], endpoints: dict[str, Any]) -> None:
    overrides = endpoints.get("service_environment_overrides", {})
    if not isinstance(overrides, dict):
        raise ValueError("service_environment_overrides must be a mapping")
    for service_name, values in overrides.items():
        service = services.get(service_name)
        if not isinstance(service, dict):
            raise ValueError(f"service_environment_overrides references unknown service: {service_name}")
        if not isinstance(values, dict):
            raise ValueError(f"environment overrides for {service_name} must be a mapping")
        environment = service.setdefault("environment", {})
        if not isinstance(environment, dict):
            raise ValueError(f"environment for {service_name} must be a mapping")
        for key, value in values.items():
            if not isinstance(key, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
                raise ValueError(f"invalid environment variable name in overrides for {service_name}")
            if not isinstance(value, (str, int, float, bool)):
                raise ValueError(f"environment override for {service_name}.{key} must be a scalar")
            environment[key] = str(value).lower() if isinstance(value, bool) else str(value)


def _replace_external_endpoints(service: dict[str, Any], endpoints: dict[str, Any]) -> None:
    environment = service.get("environment")
    if not isinstance(environment, dict):
        return

    postgres_url = _postgres_router_url(str(endpoints["postgres_url"]))
    clickhouse_host = "clickhouse-router"
    redis_url = _redis_router_url(str(endpoints["redis_url"]))
    redis_host = _url_host(redis_url)
    kafka_hosts = str(endpoints["kafka_hosts"])
    temporal_host = "temporal-router:7233"
    etcd_endpoints = str(endpoints["etcd_endpoints"])

    for key in POSTGRES_URL_KEYS - {"DATABASE_URL"}:
        if key in environment:
            environment[key] = _replace_postgres_authority(str(environment[key]), postgres_url)
    if "DATABASE_URL" in environment:
        environment["DATABASE_URL"] = postgres_url
    for key in tuple(environment):
        if key.endswith("_DATABASE_URL") and key in environment and key not in POSTGRES_URL_KEYS:
            environment[key] = _replace_postgres_authority(str(environment[key]), postgres_url)
    if "PGHOST" in environment:
        environment["PGHOST"] = _url_host(postgres_url)
    if "PGPORT" in environment:
        environment["PGPORT"] = str(_url_port(postgres_url, 5432))
    if "PGUSER" in environment:
        environment["PGUSER"] = _url_username(postgres_url) or environment["PGUSER"]
    if "PGPASSWORD" in environment:
        environment["PGPASSWORD"] = _url_password(postgres_url) or environment["PGPASSWORD"]
    if "POSTGRES_SEEDS" in environment:
        environment["POSTGRES_SEEDS"] = _url_host(postgres_url)

    for key in CLICKHOUSE_HOST_KEYS:
        if key in environment:
            environment[key] = clickhouse_host
    for key in environment:
        if key.startswith("CLICKHOUSE_") and key.endswith("_HOST"):
            environment[key] = clickhouse_host
        elif key.startswith("CLICKHOUSE_") and key.endswith("_USER"):
            environment[key] = str(endpoints["clickhouse_user"])
        elif key.startswith("CLICKHOUSE_") and key.endswith("_PASSWORD"):
            environment[key] = str(endpoints["clickhouse_password"])
        elif key.startswith("CLICKHOUSE_") and key.endswith("_SECURE"):
            environment[key] = "false"
    if "CLICKHOUSE_USER" in environment:
        environment["CLICKHOUSE_USER"] = str(endpoints["clickhouse_user"])
    if "CLICKHOUSE_PASSWORD" in environment:
        environment["CLICKHOUSE_PASSWORD"] = str(endpoints["clickhouse_password"])
    if "CLICKHOUSE_SECURE" in environment:
        environment["CLICKHOUSE_SECURE"] = "false"
    if "CLICKHOUSE_VERIFY" in environment:
        environment["CLICKHOUSE_VERIFY"] = "true"

    for key in KAFKA_HOST_KEYS:
        if key in environment:
            environment[key] = kafka_hosts

    for key in REDIS_URL_KEYS | {"REDIS_URL"}:
        if key in environment:
            environment[key] = redis_url
    if "FLAGS_REDIS_URL" in environment:
        environment["FLAGS_REDIS_URL"] = _redis_router_url(
            str(endpoints["flags_redis_url"]), "flags-redis-router", 6380
        )
    for key in environment:
        if key in REDIS_HOST_KEYS or key.endswith("_REDIS_HOST"):
            environment[key] = redis_host
        elif key == "REDIS_PORT" or key.endswith("_REDIS_PORT"):
            environment[key] = str(_url_port(redis_url, 6379))
        elif key.endswith("_REDIS_TLS"):
            environment[key] = str(urlsplit(redis_url).scheme == "rediss").lower()
    redis_password = _url_password(redis_url)
    if redis_password is not None:
        for key in tuple(environment):
            if key.endswith("_REDIS_PASSWORD"):
                environment[key] = redis_password
        for key in tuple(environment):
            if key.endswith("_REDIS_HOST"):
                password_key = f"{key.removesuffix('_HOST')}_PASSWORD"
                environment[password_key] = redis_password

    object_storage_endpoint = "http://seaweedfs-router:8333"
    object_storage_access_key = str(endpoints["object_storage_access_key_id"])
    object_storage_secret_key = str(endpoints["object_storage_secret_access_key"])
    session_replay_endpoint = "http://seaweedfs-router:8333"
    session_replay_access_key = str(endpoints["session_replay_access_key_id"])
    session_replay_secret_key = str(endpoints["session_replay_secret_access_key"])
    object_storage_region = str(endpoints["object_storage_region"])
    session_replay_region = str(endpoints["session_replay_region"])
    for key in ("OBJECT_STORAGE_ENDPOINT", "BATCH_EXPORT_OBJECT_STORAGE_ENDPOINT", "AI_BLOB_S3_ENDPOINT"):
        if key in environment:
            environment[key] = object_storage_endpoint
    for key in ("OBJECT_STORAGE_ACCESS_KEY_ID", "AI_BLOB_S3_ACCESS_KEY_ID", "AWS_ACCESS_KEY_ID"):
        if key in environment:
            environment[key] = object_storage_access_key
    for key in ("OBJECT_STORAGE_SECRET_ACCESS_KEY", "AI_BLOB_S3_SECRET_ACCESS_KEY", "AWS_SECRET_ACCESS_KEY"):
        if key in environment:
            environment[key] = object_storage_secret_key
    if "OBJECT_STORAGE_PUBLIC_ENDPOINT" in environment:
        environment["OBJECT_STORAGE_PUBLIC_ENDPOINT"] = str(endpoints["object_storage_public_endpoint"])
    if "OBJECT_STORAGE_REGION" in environment:
        environment["OBJECT_STORAGE_REGION"] = object_storage_region
    if "AI_BLOB_S3_REGION" in environment:
        environment["AI_BLOB_S3_REGION"] = object_storage_region
    for key in ("SESSION_RECORDING_V2_S3_ENDPOINT",):
        if key in environment:
            environment[key] = session_replay_endpoint
    if "SESSION_RECORDING_V2_S3_REGION" in environment:
        environment["SESSION_RECORDING_V2_S3_REGION"] = session_replay_region
    if "SESSION_RECORDING_V2_S3_ACCESS_KEY_ID" in environment:
        environment["SESSION_RECORDING_V2_S3_ACCESS_KEY_ID"] = session_replay_access_key
    if "SESSION_RECORDING_V2_S3_SECRET_ACCESS_KEY" in environment:
        environment["SESSION_RECORDING_V2_S3_SECRET_ACCESS_KEY"] = session_replay_secret_key

    if "CDP_VALKEY_HOST" in environment:
        environment["CDP_VALKEY_HOST"] = "valkey-router"
    if "CDP_VALKEY_PORT" in environment:
        environment["CDP_VALKEY_PORT"] = str(endpoints["valkey_port"])

    if "TEMPORAL_HOST" in environment:
        environment["TEMPORAL_HOST"] = "temporal-router"
    for key in ("TEMPORAL_CLI_ADDRESS", "TEMPORAL_ADDRESS"):
        if key in environment:
            environment[key] = temporal_host
    for key in ("ETCD_ENDPOINTS", "ETCD_ADVERTISE_CLIENT_URLS"):
        if key in environment:
            environment[key] = etcd_endpoints


def _configure_ingress(services: dict[str, Any], endpoints: dict[str, Any], ingress_mode: str) -> None:
    proxy = services.get("proxy")
    if not isinstance(proxy, dict):
        raise ValueError("Compose config must contain the proxy service")
    environment = proxy.setdefault("environment", {})
    if not isinstance(environment, dict):
        raise ValueError("proxy environment must be a mapping")

    domain = str(endpoints["domain"])
    if "CADDYFILE" in environment:
        environment["CADDYFILE"] = re.sub(
            r"reverse_proxy objectstorage:19000",
            "reverse_proxy seaweedfs-router:8333",
            str(environment["CADDYFILE"]),
        )
    if ingress_mode == "direct":
        environment["CADDY_HOST"] = f"{domain}, http://, https://"
        environment["CADDY_TLS_BLOCK"] = ""
    else:
        cidrs = endpoints["trusted_proxy_cidrs"]
        if isinstance(cidrs, str):
            cidrs = [cidr.strip() for cidr in cidrs.split(",") if cidr.strip()]
        if not isinstance(cidrs, list) or not cidrs:
            raise ValueError("ingress mode lb requires trusted_proxy_cidrs")
        cidrs = [str(cidr) for cidr in cidrs]
        for cidr in cidrs:
            try:
                ipaddress.ip_network(cidr, strict=False)
            except ValueError as error:
                raise ValueError(f"invalid trusted proxy CIDR: {cidr}") from error
        environment["CADDY_HOST"] = f"http://{domain}"
        environment["CADDY_TLS_BLOCK"] = f"servers {{ trusted_proxies static {' '.join(cidrs)} }}"
        proxy["ports"] = ["80:80"]


def _retain_referenced_volumes(compose_config: dict[str, Any], services: dict[str, Any]) -> None:
    declared = compose_config.get("volumes")
    if not isinstance(declared, dict):
        return
    referenced: set[str] = set()
    for service in services.values():
        if not isinstance(service, dict):
            continue
        for volume in service.get("volumes", []):
            if isinstance(volume, str):
                source = volume.split(":", 1)[0]
            elif isinstance(volume, dict):
                source = volume.get("source", "")
            else:
                continue
            if source in declared:
                referenced.add(source)
    compose_config["volumes"] = {name: value for name, value in declared.items() if name in referenced}


def _remove_compose_mounts(service: dict[str, Any]) -> None:
    volumes = service.get("volumes")
    if not isinstance(volumes, list):
        return
    service["volumes"] = [
        volume
        for volume in volumes
        if not (isinstance(volume, dict) and volume.get("target") == "/compose")
        and not (isinstance(volume, str) and volume.split(":")[-1] == "/compose")
    ]
    if not service["volumes"]:
        service.pop("volumes")


def _url_host(value: str) -> str:
    parsed = urlsplit(value)
    return parsed.hostname or value.split(":", 1)[0]


def _url_port(value: str, default: int) -> int:
    return urlsplit(value).port or default


def _replace_postgres_authority(original: str, configured: str) -> str:
    original_url = urlsplit(original)
    configured_url = urlsplit(configured)
    if not configured_url.scheme or not configured_url.hostname:
        raise ValueError("postgres_url must include a scheme and host")
    return urlunsplit(
        (
            configured_url.scheme,
            configured_url.netloc,
            original_url.path or configured_url.path,
            original_url.query or configured_url.query,
            original_url.fragment,
        )
    )


def _rewrite_initialization_command(service_name: str, service: dict[str, Any], endpoints: dict[str, Any]) -> None:
    if service_name == "kafka-init":
        environment = service.setdefault("environment", {})
        environment["KAFKA_HOSTS"] = str(endpoints["kafka_hosts"])
    elif service_name == "personhog-etcd-init":
        _replace_command_text(service, "http://etcd:2379", str(endpoints["etcd_endpoints"]))
    elif service_name == "personhog-tables-ready":
        postgres = urlsplit(str(endpoints["postgres_url"]))
        database = postgres.path.lstrip("/") or "posthog"
        user = postgres.username or "posthog"
        _replace_command_text(service, "-h db -U posthog -d posthog", f"-h postgres-router -U {user} -d {database}")
        environment = service.setdefault("environment", {})
        environment["PGPASSWORD"] = postgres.password or ""


def _replace_command_text(service: dict[str, Any], old: str, new: str) -> None:
    for key in ("command", "entrypoint"):
        value = service.get(key)
        if isinstance(value, str):
            service[key] = value.replace(old, new)
        elif isinstance(value, list):
            service[key] = [item.replace(old, new) if isinstance(item, str) else item for item in value]


def _url_username(value: str) -> str | None:
    username = urlsplit(value).username
    return unquote(username) if username is not None else None


def _url_password(value: str) -> str | None:
    password = urlsplit(value).password
    return unquote(password) if password is not None else None


def _validate_endpoint_values(endpoints: dict[str, Any]) -> None:
    if endpoints["clickhouse_user"] != "app":
        raise ValueError("clickhouse_user must be app for the self-managed ClickHouse configuration")
    schemes = {
        "postgres_url": {"postgres", "postgresql"},
        "redis_url": {"redis", "rediss"},
        "flags_redis_url": {"redis", "rediss"},
        "object_storage_public_endpoint": {"http", "https"},
    }
    for key, allowed_schemes in schemes.items():
        parsed = urlsplit(str(endpoints[key]))
        if parsed.scheme not in allowed_schemes or not parsed.hostname:
            raise ValueError(f"{key} must be a URL with a host and one of {', '.join(sorted(allowed_schemes))}")
    redis = urlsplit(str(endpoints["redis_url"]))
    flags_redis = urlsplit(str(endpoints["flags_redis_url"]))
    if not redis.password:
        raise ValueError("redis_url must include a password for the self-managed Redis service")
    if redis.username not in {None, "", "default"}:
        raise ValueError("redis_url must use Redis's default ACL user for the self-managed service")
    redis_password = unquote(redis.password)
    if not re.fullmatch(r"[A-Za-z0-9._~-]+", redis_password):
        raise ValueError("redis_url must use a URL-safe password for the Redis Sentinel configuration")
    if not flags_redis.password:
        raise ValueError("flags_redis_url must include a password for the self-managed Redis service")
    if flags_redis.username not in {None, "", "default"}:
        raise ValueError("flags_redis_url must use Redis's default ACL user for the self-managed service")
    flags_redis_password = unquote(flags_redis.password)
    if not re.fullmatch(r"[A-Za-z0-9._~-]+", flags_redis_password):
        raise ValueError("flags_redis_url must use a URL-safe password for the Redis Sentinel configuration")
    redis_port = redis.port or (6380 if redis.scheme == "rediss" else 6379)
    flags_redis_port = flags_redis.port or (6380 if flags_redis.scheme == "rediss" else 6379)
    if (redis.hostname.lower(), redis_port) == (flags_redis.hostname.lower(), flags_redis_port):
        raise ValueError("flags_redis_url must use a dedicated Redis endpoint separate from redis_url")
    for endpoint in str(endpoints["etcd_endpoints"]).split(","):
        parsed = urlsplit(endpoint.strip())
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("etcd_endpoints must contain HTTP or HTTPS URLs")
    try:
        port = int(endpoints["valkey_port"])
    except (TypeError, ValueError) as error:
        raise ValueError("valkey_port must be an integer from 1 to 65535") from error
    if not 1 <= port <= 65535:
        raise ValueError("valkey_port must be an integer from 1 to 65535")
    if port != 6381:
        raise ValueError("valkey_port must be 6381 for the self-managed Valkey service")
    _parse_clickhouse_hosts(str(endpoints["clickhouse_hosts"]))
    _parse_postgres_hosts(str(endpoints["postgres_hosts"]))
    _parse_redis_hosts(str(endpoints["redis_hosts"]))
    _parse_redis_hosts(str(endpoints["flags_redis_hosts"]), expected_port=6380)
    _parse_redis_hosts(str(endpoints["valkey_hosts"]), expected_port=6381)
    _parse_service_hosts(str(endpoints["seaweedfs_hosts"]), expected_port=8333, service_name="seaweedfs")
    _parse_service_hosts(str(endpoints["temporal_hosts"]), expected_port=7233, service_name="temporal")
    domain = str(endpoints["domain"])
    parsed_domain = urlsplit(f"//{domain}")
    hostname = parsed_domain.hostname or ""
    try:
        ipaddress.ip_address(hostname)
        valid_ip = True
    except ValueError:
        valid_ip = False
    labels = hostname.rstrip(".").split(".")
    valid_hostname = len(hostname) <= 253 and all(
        re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label) for label in labels
    )
    if (
        not hostname
        or parsed_domain.path
        or parsed_domain.username
        or parsed_domain.password
        or parsed_domain.port
        or not (valid_ip or valid_hostname)
    ):
        raise ValueError("domain must be a hostname or IP address without a scheme, port, path, or credentials")
    brokers = [broker.strip() for broker in str(endpoints["kafka_hosts"]).split(",")]
    if any(not broker for broker in brokers):
        raise ValueError("kafka_hosts must be a comma-separated list of brokers")
    if len(set(brokers)) < 3:
        raise ValueError("kafka_hosts must contain at least three unique Kafka brokers for replicated topics")


def _uses_clickhouse(service: dict[str, Any]) -> bool:
    environment = service.get("environment")
    return isinstance(environment, dict) and any(
        key.startswith("CLICKHOUSE_") and key.endswith("_HOST") for key in environment
    )


def _uses_postgres(service: dict[str, Any]) -> bool:
    environment = service.get("environment")
    if isinstance(environment, dict) and (
        "DATABASE_URL" in environment
        or "PGHOST" in environment
        or any(key in environment for key in POSTGRES_URL_KEYS)
        or any(key.endswith("_DATABASE_URL") for key in environment)
    ):
        return True
    command = service.get("entrypoint", service.get("command", []))
    return "postgres-router" in str(command)


def _uses_redis(service: dict[str, Any]) -> bool:
    environment = service.get("environment")
    return isinstance(environment, dict) and any(
        key == "REDIS_URL" or key.endswith("_REDIS_URL") or key.endswith("_REDIS_HOST") for key in environment
    )


def _uses_flags_redis(service: dict[str, Any]) -> bool:
    environment = service.get("environment")
    return isinstance(environment, dict) and "FLAGS_REDIS_URL" in environment


def _uses_valkey(service: dict[str, Any]) -> bool:
    environment = service.get("environment")
    return isinstance(environment, dict) and "CDP_VALKEY_HOST" in environment


def _postgres_router_url(value: str) -> str:
    parsed = urlsplit(value)
    userinfo = parsed.netloc.rsplit("@", 1)[0] if "@" in parsed.netloc else ""
    authority = f"{userinfo}@postgres-router:5432" if userinfo else "postgres-router:5432"
    return urlunsplit((parsed.scheme, authority, parsed.path, parsed.query, parsed.fragment))


def _redis_router_url(value: str, hostname: str = "redis-router", port: int = 6379) -> str:
    parsed = urlsplit(value)
    userinfo = ""
    if parsed.username is not None:
        userinfo = quote(unquote(parsed.username), safe="")
    if parsed.password is not None:
        userinfo += f":{quote(unquote(parsed.password), safe='')}"
    if userinfo:
        userinfo += "@"
    return urlunsplit(("redis", f"{userinfo}{hostname}:{port}", parsed.path or "/", parsed.query, parsed.fragment))


def _parse_postgres_hosts(value: str) -> list[str]:
    hosts = [host.strip() for host in value.split(",") if host.strip()]
    normalized = []
    for host in hosts:
        parsed = urlsplit(f"//{host}")
        try:
            port = parsed.port
        except ValueError as error:
            raise ValueError("postgres_hosts must contain host:port entries") from error
        if not parsed.hostname or port != 5433 or parsed.path or parsed.username or parsed.password:
            raise ValueError("postgres_hosts must contain data-node host:5433 entries")
        try:
            address = ipaddress.ip_address(parsed.hostname)
        except ValueError as error:
            raise ValueError("postgres_hosts must use private PostgreSQL data-node IPs") from error
        if address.version != 4 or not address.is_private or address.is_loopback or address.is_link_local:
            raise ValueError("postgres_hosts must use private PostgreSQL data-node IPs")
        normalized.append((parsed.hostname.lower(), port))
    if len(set(normalized)) != 3 or len(hosts) != 3:
        raise ValueError("postgres_hosts must contain exactly three unique PostgreSQL nodes")
    return hosts


def _parse_redis_hosts(value: str, expected_port: int = 6379) -> list[str]:
    hosts = [host.strip() for host in value.split(",") if host.strip()]
    normalized = []
    for host in hosts:
        parsed = urlsplit(f"//{host}")
        try:
            port = parsed.port
        except ValueError as error:
            raise ValueError("redis_hosts must contain host:port entries") from error
        if not parsed.hostname or port != expected_port or parsed.path or parsed.username or parsed.password:
            raise ValueError(f"Redis hosts must contain data-node host:{expected_port} entries")
        try:
            address = ipaddress.ip_address(parsed.hostname)
        except ValueError as error:
            raise ValueError("Redis hosts must use private data-node IPs") from error
        if address.version != 4 or not address.is_private or address.is_loopback or address.is_link_local:
            raise ValueError("Redis hosts must use private data-node IPs")
        normalized.append((parsed.hostname.lower(), port))
    if len(set(normalized)) != 3 or len(hosts) != 3:
        raise ValueError("Redis hosts must contain exactly three unique data nodes")
    return hosts


def _parse_clickhouse_hosts(value: str) -> list[str]:
    hosts = [host.strip() for host in value.split(",") if host.strip()]
    normalized = []
    for host in hosts:
        parsed = urlsplit(f"//{host}")
        try:
            port = parsed.port
        except ValueError as error:
            raise ValueError("clickhouse_hosts must contain host:port entries") from error
        if not parsed.hostname or port is None or parsed.path or parsed.username or parsed.password:
            raise ValueError("clickhouse_hosts must contain host:port entries")
        normalized.append((parsed.hostname.lower(), port))
    if len(set(normalized)) < 3:
        raise ValueError("clickhouse_hosts must contain at least three unique ClickHouse nodes")
    return hosts


def _render_clickhouse_router_config(value: str) -> str:
    hosts = _parse_clickhouse_hosts(value)
    server_lines = "\n".join(f"    server ch{index} {host} check" for index, host in enumerate(hosts, start=1))
    return (
        "global\n    maxconn 4096\n\n"
        "defaults\n    mode tcp\n    timeout connect 5s\n    timeout client 1m\n    timeout server 1m\n\n"
        "frontend clickhouse_native\n    bind :9000\n    default_backend clickhouse_nodes\n\n"
        "backend clickhouse_nodes\n    balance leastconn\n    option tcp-check\n"
        f"{server_lines}\n"
    )


def _render_postgres_router_config(value: str) -> str:
    hosts = _parse_postgres_hosts(value)
    server_lines = "\n".join(
        f"    server pg{index} {host} check port 8008 inter 2s fall 3 rise 2"
        for index, host in enumerate(hosts, start=1)
    )
    return (
        "global\n    maxconn 4096\n\n"
        "defaults\n    mode tcp\n    timeout connect 5s\n    timeout client 1m\n    timeout server 1m\n\n"
        "frontend postgresql_primary\n    bind :5432\n    default_backend postgresql_primary_nodes\n\n"
        "backend postgresql_primary_nodes\n    option httpchk\n"
        "    http-check send meth GET uri /primary ver HTTP/1.1 hdr Host patroni\n"
        "    http-check expect status 200\n"
        f"{server_lines}\n"
    )


def _render_redis_router_config(hosts_value: str, redis_url: str, redis_port: int = 6379) -> str:
    hosts = _parse_redis_hosts(hosts_value, expected_port=redis_port)
    password = _url_password(redis_url)
    username = _url_username(redis_url) or None
    auth_parts = []
    if username is not None:
        auth_parts.append(username.encode())
    if password is not None:
        auth_parts.append(password.encode())
    checks = ""
    if auth_parts:
        auth_command = [b"AUTH", *auth_parts]
        auth_payload = (
            b"*"
            + str(len(auth_command)).encode()
            + b"\r\n"
            + b"".join(b"$" + str(len(part)).encode() + b"\r\n" + part + b"\r\n" for part in auth_command)
        )
        checks = f"    tcp-check send-binary {auth_payload.hex()}\n    tcp-check expect string +OK\n"
    info_payload = b"*1\r\n$4\r\nINFO\r\n"
    server_lines = "\n".join(
        f"    server redis{index} {host} check inter 2s fall 3 rise 2" for index, host in enumerate(hosts, start=1)
    )
    return (
        "global\n    maxconn 4096\n\n"
        "defaults\n    mode tcp\n    timeout connect 5s\n    timeout client 1m\n    timeout server 1m\n\n"
        f"frontend redis_primary\n    bind :{redis_port}\n    default_backend redis_primary_nodes\n\n"
        "backend redis_primary_nodes\n    balance first\n    option tcp-check\n"
        f"{checks}    tcp-check send-binary {info_payload.hex()}\n"
        "    tcp-check expect string role:master\n"
        f"{server_lines}\n"
    )


def _render_seaweedfs_router_config(hosts_value: str) -> str:
    hosts = _parse_service_hosts(hosts_value, expected_port=8333, service_name="seaweedfs")
    servers = "\n".join(
        f"    server seaweedfs{index} {host} check inter 2s fall 3 rise 2" for index, host in enumerate(hosts, start=1)
    )
    return (
        "global\n    maxconn 4096\n\n"
        "defaults\n    mode tcp\n    timeout connect 5s\n    timeout client 1m\n    timeout server 1m\n\n"
        "frontend seaweedfs_s3\n    bind :8333\n    default_backend seaweedfs_nodes\n\n"
        "backend seaweedfs_nodes\n    balance leastconn\n    option tcp-check\n"
        f"{servers}\n"
    )


def _render_temporal_router_config(hosts_value: str) -> str:
    hosts = _parse_service_hosts(hosts_value, expected_port=7233, service_name="temporal")
    servers = "\n".join(
        f"    server temporal{index} {host} check inter 2s fall 3 rise 2" for index, host in enumerate(hosts, start=1)
    )
    return (
        "global\n    maxconn 4096\n\n"
        "defaults\n    mode tcp\n    timeout connect 5s\n    timeout client 1m\n    timeout server 1m\n\n"
        "frontend temporal_grpc\n    bind :7233\n    default_backend temporal_nodes\n\n"
        "backend temporal_nodes\n    balance leastconn\n    option tcp-check\n"
        f"{servers}\n"
    )


def _parse_service_hosts(value: str, expected_port: int, service_name: str) -> list[str]:
    hosts = [host.strip() for host in value.split(",") if host.strip()]
    normalized = []
    for host in hosts:
        parsed = urlsplit(f"//{host}")
        try:
            port = parsed.port
        except ValueError as error:
            raise ValueError(f"{service_name}_hosts must contain host:port entries") from error
        if not parsed.hostname or port != expected_port or parsed.path or parsed.username or parsed.password:
            raise ValueError(f"{service_name}_hosts must contain data-node host:{expected_port} entries")
        try:
            address = ipaddress.ip_address(parsed.hostname)
        except ValueError as error:
            raise ValueError(f"{service_name}_hosts must use private data-node IPs") from error
        if address.version != 4 or not address.is_private or address.is_loopback or address.is_link_local:
            raise ValueError(f"{service_name}_hosts must use private data-node IPs")
        normalized.append((parsed.hostname.lower(), port))
    if len(set(normalized)) != 3 or len(hosts) != 3:
        raise ValueError(f"{service_name}_hosts must contain exactly three unique data nodes")
    return hosts


def _uses_object_storage(service: dict[str, Any]) -> bool:
    environment = service.get("environment")
    if not isinstance(environment, dict):
        return False
    return any(
        "S3_ENDPOINT" in key or key in {"OBJECT_STORAGE_ENDPOINT", "BATCH_EXPORT_OBJECT_STORAGE_ENDPOINT"}
        for key in environment
    )


def _uses_temporal(service: dict[str, Any]) -> bool:
    environment = service.get("environment")
    return isinstance(environment, dict) and any(
        key in environment for key in {"TEMPORAL_HOST", "TEMPORAL_ADDRESS", "TEMPORAL_CLI_ADDRESS"}
    )
