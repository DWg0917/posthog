import re
import ipaddress
from copy import deepcopy
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit, urlunsplit

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
    "clickhouse_host",
    "clickhouse_user",
    "clickhouse_password",
    "clickhouse_secure",
    "kafka_hosts",
    "redis_url",
    "valkey_host",
    "valkey_port",
    "object_storage_endpoint",
    "object_storage_access_key_id",
    "object_storage_secret_access_key",
    "object_storage_region",
    "object_storage_public_endpoint",
    "session_replay_endpoint",
    "session_replay_access_key_id",
    "session_replay_secret_access_key",
    "session_replay_region",
    "temporal_host",
    "etcd_endpoints",
    "domain",
}


def render_multihost_compose(
    compose_config: dict[str, Any], endpoints: dict[str, Any], ingress_mode: str, build_context: str = "."
) -> dict[str, Any]:
    if ingress_mode not in {"direct", "lb"}:
        raise ValueError(f"unsupported ingress mode: {ingress_mode}")

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

    _normalize_repository_paths(services, build_context)
    _configure_source_builds(services, build_context)

    for service_name, service in services.items():
        if not isinstance(service, dict):
            continue
        service.pop("depends_on", None)
        _replace_external_endpoints(service, endpoints)
        _rewrite_initialization_command(service_name, service, endpoints)
        if service_name in {"web", "worker", "temporal-django-worker", "asyncmigrationscheck"}:
            environment = service.setdefault("environment", {})
            environment["BATCH_EXPORT_OBJECT_STORAGE_ENDPOINT"] = str(endpoints["object_storage_endpoint"])
            environment["BATCH_EXPORT_OBJECT_STORAGE_REGION"] = str(endpoints["object_storage_region"])
            environment["OBJECT_STORAGE_REGION"] = str(endpoints["object_storage_region"])
        if service_name in INIT_SERVICES:
            service["profiles"] = ["init"]

    web = services.get("web")
    if isinstance(web, dict):
        web["command"] = ["./bin/docker-server"]
        _remove_compose_mounts(web)

    worker = services.get("worker")
    if isinstance(worker, dict):
        worker["command"] = ["./bin/docker-worker-celery"]
        celery_beat = deepcopy(worker)
        celery_beat["command"] = ["./bin/docker-worker-beat"]
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
        metrics_environment["METRICS_REDIS_HOST"] = _url_host(str(endpoints["redis_url"]))
        metrics_environment["METRICS_REDIS_PORT"] = str(_url_port(str(endpoints["redis_url"]), 6379))
        metrics_environment["METRICS_REDIS_TLS"] = str(urlsplit(str(endpoints["redis_url"])).scheme == "rediss").lower()
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

    postgres_url = str(endpoints["postgres_url"])
    clickhouse_host = str(endpoints["clickhouse_host"])
    redis_url = str(endpoints["redis_url"])
    redis_host = _url_host(redis_url)
    kafka_hosts = str(endpoints["kafka_hosts"])
    temporal_host = str(endpoints["temporal_host"])
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
            environment[key] = str(endpoints["clickhouse_secure"]).lower()
    if "CLICKHOUSE_USER" in environment:
        environment["CLICKHOUSE_USER"] = str(endpoints["clickhouse_user"])
    if "CLICKHOUSE_PASSWORD" in environment:
        environment["CLICKHOUSE_PASSWORD"] = str(endpoints["clickhouse_password"])
    if "CLICKHOUSE_SECURE" in environment:
        environment["CLICKHOUSE_SECURE"] = str(endpoints["clickhouse_secure"]).lower()
    if "CLICKHOUSE_VERIFY" in environment:
        environment["CLICKHOUSE_VERIFY"] = "true"

    for key in KAFKA_HOST_KEYS:
        if key in environment:
            environment[key] = kafka_hosts

    for key in REDIS_URL_KEYS | {"REDIS_URL"}:
        if key in environment:
            environment[key] = redis_url
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

    object_storage_endpoint = str(endpoints["object_storage_endpoint"])
    object_storage_access_key = str(endpoints["object_storage_access_key_id"])
    object_storage_secret_key = str(endpoints["object_storage_secret_access_key"])
    session_replay_endpoint = str(endpoints["session_replay_endpoint"])
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
        environment["CDP_VALKEY_HOST"] = str(endpoints["valkey_host"])
    if "CDP_VALKEY_PORT" in environment:
        environment["CDP_VALKEY_PORT"] = str(endpoints["valkey_port"])

    for key in ("TEMPORAL_HOST", "TEMPORAL_CLI_ADDRESS", "TEMPORAL_ADDRESS"):
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
            r"(?ms)^\s*@objectstorage\s*\{.*?^\s*handle @objectstorage\s*\{\s*"
            r"reverse_proxy objectstorage:19000\s*\}\s*$",
            "",
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
        _replace_command_text(service, "kafka:9092", str(endpoints["kafka_hosts"]))
    elif service_name == "personhog-etcd-init":
        _replace_command_text(service, "http://etcd:2379", str(endpoints["etcd_endpoints"]))
    elif service_name == "personhog-tables-ready":
        postgres = urlsplit(str(endpoints["postgres_url"]))
        database = postgres.path.lstrip("/") or "posthog"
        user = postgres.username or "posthog"
        _replace_command_text(service, "-h db -U posthog -d posthog", f"-h {postgres.hostname} -U {user} -d {database}")
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
    schemes = {
        "postgres_url": {"postgres", "postgresql"},
        "redis_url": {"redis", "rediss"},
        "object_storage_endpoint": {"http", "https"},
        "object_storage_public_endpoint": {"http", "https"},
        "session_replay_endpoint": {"http", "https"},
    }
    for key, allowed_schemes in schemes.items():
        parsed = urlsplit(str(endpoints[key]))
        if parsed.scheme not in allowed_schemes or not parsed.hostname:
            raise ValueError(f"{key} must be a URL with a host and one of {', '.join(sorted(allowed_schemes))}")
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
    if str(endpoints["clickhouse_secure"]).lower() not in {"true", "false"}:
        raise ValueError("clickhouse_secure must be true or false")
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
    if not str(endpoints["kafka_hosts"]).split(",") or any(
        not broker.strip() for broker in str(endpoints["kafka_hosts"]).split(",")
    ):
        raise ValueError("kafka_hosts must be a comma-separated list of brokers")
