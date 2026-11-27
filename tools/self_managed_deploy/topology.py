from typing import Any

DATA_NODE_COMPOSE_SERVICES = [
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
]
DATA_NODE_PROFILES = ["temporal"]
DATA_NODE_INIT_SERVICES = ["temporal-schema-setup", "temporal-namespace-setup"]
UNCONFIGURED_DATA_SERVICES: list[str] = []

APPLICATION_SERVICE_GROUPS = {
    "web_and_edge": [
        "proxy",
        "web",
        "capture",
        "browserless",
        "clickhouse-router",
        "postgres-router",
        "redis-router",
        "flags-redis-router",
        "valkey-router",
        "seaweedfs-router",
        "temporal-router",
    ],
    "event_ingestion": [
        "capture-ai",
        "capture-logs",
        "ingestion-error-tracking",
        "ingestion-general",
        "ingestion-logs",
        "ingestion-metrics",
        "ingestion-sessionreplay",
        "ingestion-traces",
        "plugins",
    ],
    "personhog": [
        "personhog-changelog-relay",
        "personhog-identity",
        "personhog-leader",
        "personhog-publisher",
        "personhog-replica",
        "personhog-router",
        "personhog-router-leader",
        "personhog-writer",
    ],
    "workers_and_products": [
        "cymbal",
        "cymbal-resolution",
        "feature-flags",
        "hypercache-server",
        "livestream",
        "property-defs-ai",
        "property-defs-rs",
        "recording-api",
        "replay-capture",
        "worker",
        "temporal-django-worker",
    ],
    "dedicated_consumers": ["flags-cache-builder"],
    "scheduler": ["celery-beat"],
}


UNFINISHED_WORK = [
    "PostgreSQL services and primary routing are generated; bootstrap, failover, backups, and restores have not been verified.",
    "ClickHouse replica and ZooKeeper settings are generated, but startup, table migrations, and replica recovery are not runtime-verified.",
    "Kafka topic initialization, three brokers, and a configurable retention default are generated, but broker startup and runtime failover are not verified.",
    "General Redis, dedicated flags Redis, and Valkey Sentinel services plus primary routing are generated; failover and asynchronous-replication data loss are not runtime-verified.",
    "SeaweedFS services, replication, PostgreSQL metadata, S3 bootstrap, and S3 routing are generated; placement, failover, authorization, and storage recovery are not runtime-verified.",
    "Temporal servers, PostgreSQL schema initialization, and gRPC routing are generated; schema setup, cluster membership, and worker recovery are not runtime-verified.",
    "An operator-run Tencent CBS cold-snapshot and isolated restore procedure is documented, but no snapshot automation, online PITR, off-region copy, or restore drill has been validated.",
    "Tencent CVM/CBS/CLB monitoring setup and local Compose status checks are documented; container health alerts and PostHog service-level metric aggregation are not wired to an alerting system.",
    "Machine sizes and capacity limits require workload measurements and are intentionally unspecified.",
]


def render_topology_plan(ingress_mode: str) -> dict[str, Any]:
    if ingress_mode not in {"direct", "lb"}:
        raise ValueError("ingress_mode must be direct or lb")

    data_nodes = [
        {
            "name": f"data-{index}",
            "role": "data",
            "services": list(DATA_NODE_COMPOSE_SERVICES),
            "profiles": list(DATA_NODE_PROFILES),
            "init_services": list(DATA_NODE_INIT_SERVICES) if index == 1 else [],
            "unconfigured_services": list(UNCONFIGURED_DATA_SERVICES),
        }
        for index in range(1, 4)
    ]
    app_nodes = [
        {
            "name": f"app-{index}",
            "role": "application",
            "services": {group: list(services) for group, services in APPLICATION_SERVICE_GROUPS.items()},
        }
        for index in range(1, 3)
    ]
    ingress = {
        "mode": ingress_mode,
        "target_ports": [80] if ingress_mode == "lb" else [80, 443],
        "availability_note": (
            "Direct mode points DNS at application hosts. DNS records alone do not provide health checks or automatic "
            "failover when a host becomes unavailable."
            if ingress_mode == "direct"
            else "An external load balancer must health-check both application hosts and send traffic only to healthy hosts."
        ),
    }
    if ingress_mode == "lb":
        ingress["placement"] = "external load balancer, or a separately operated redundant load balancer pair"
        ingress["health_check"] = {
            "protocol": "HTTP",
            "port": 80,
            "path": "/_readyz?role=web",
            "expected_status": 200,
        }

    firewall = {
        "application_ingress": {
            "source": "load balancer" if ingress_mode == "lb" else "public clients",
            "destination": "app hosts",
            "protocol": "TCP",
            "ports": ingress["target_ports"],
        },
        "application_to_data": {
            "source": "app hosts",
            "destination": "data hosts",
            "protocol": "TCP",
            "ports": [5433, 9000, 9092, 2379, 6379, 6380, 6381, 8333, 7233],
        },
        "data_node_mesh": {
            "source": "data hosts",
            "destination": "data hosts",
            "protocol": "TCP",
            "ports": [
                2181,
                2379,
                2380,
                26379,
                26380,
                26381,
                2888,
                3888,
                5433,
                6933,
                6934,
                6935,
                6936,
                6939,
                7233,
                7234,
                7235,
                7236,
                7239,
                8008,
                8080,
                8333,
                9000,
                9009,
                9333,
                18080,
                19333,
                33145,
            ],
        },
    }
    if ingress_mode == "lb":
        firewall["public_to_load_balancer"] = {
            "source": "public clients",
            "destination": "load balancer",
            "protocol": "TCP",
            "ports": [443],
            "optional_ports": [80],
        }

    return {
        "status": "logical-plan-only",
        "source": "PostHog Hobby Compose service inventory",
        "ingress": ingress,
        "firewall": firewall,
        "nodes": [*app_nodes, *data_nodes],
        "data_node_compose_services": list(DATA_NODE_COMPOSE_SERVICES),
        "data_node_profiles": list(DATA_NODE_PROFILES),
        "data_node_init_services": list(DATA_NODE_INIT_SERVICES),
        "scheduler": {
            "primary": "app-1",
            "standby": "app-2",
            "failover": "manual; automatic scheduler leadership is not configured",
        },
        "capacity": "unknown; do not purchase machine sizes from this logical placement alone",
        "unfinished_work": list(UNFINISHED_WORK),
        "deployment_ready": False,
    }
