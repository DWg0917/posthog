import json
from io import StringIO

import unittest
from unittest.mock import patch

from tools.self_managed_deploy.cli import main
from tools.self_managed_deploy.topology import render_topology_plan


class RenderTopologyPlanTest(unittest.TestCase):
    def test_plan_lists_each_state_service_on_three_data_hosts(self) -> None:
        plan = render_topology_plan("lb")

        self.assertEqual(plan["status"], "logical-plan-only")
        self.assertEqual(plan["ingress"]["target_ports"], [80])
        self.assertEqual(
            plan["ingress"]["health_check"],
            {"protocol": "HTTP", "port": 80, "path": "/_readyz?role=web", "expected_status": 200},
        )
        self.assertEqual(
            set(plan["data_node_compose_services"]),
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
        self.assertEqual(plan["data_node_profiles"], ["temporal"])
        self.assertEqual(plan["data_node_init_services"], ["temporal-schema-setup", "temporal-namespace-setup"])
        self.assertEqual(
            plan["firewall"],
            {
                "public_to_load_balancer": {
                    "source": "public clients",
                    "destination": "load balancer",
                    "protocol": "TCP",
                    "ports": [443],
                    "optional_ports": [80],
                },
                "application_ingress": {
                    "source": "load balancer",
                    "destination": "app hosts",
                    "protocol": "TCP",
                    "ports": [80],
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
            },
        )
        self.assertEqual([node["name"] for node in plan["nodes"]], ["app-1", "app-2", "data-1", "data-2", "data-3"])
        app_nodes = [node for node in plan["nodes"] if node["role"] == "application"]
        for node in app_nodes:
            self.assertIn("clickhouse-router", node["services"]["web_and_edge"])
            self.assertIn("seaweedfs-router", node["services"]["web_and_edge"])
            self.assertIn("temporal-router", node["services"]["web_and_edge"])
            self.assertIn("redis-router", node["services"]["web_and_edge"])
            self.assertIn("ingestion-sessionreplay", node["services"]["event_ingestion"])
            self.assertIn("ingestion-metrics", node["services"]["event_ingestion"])
            self.assertIn("personhog-writer", node["services"]["personhog"])
            self.assertIn("temporal-django-worker", node["services"]["workers_and_products"])
            self.assertIn("flags-cache-builder", node["services"]["dedicated_consumers"])
            self.assertIn("celery-beat", node["services"]["scheduler"])

        data_nodes = [node for node in plan["nodes"] if node["role"] == "data"]
        expected_configured_services = {
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
        }
        expected_unconfigured_services: set[str] = set()

        for node in data_nodes:
            with self.subTest(node=node["name"]):
                self.assertEqual(set(node["services"]), expected_configured_services)
                self.assertEqual(set(node["unconfigured_services"]), expected_unconfigured_services)
                self.assertEqual(node["profiles"], ["temporal"])
                self.assertEqual(
                    node["init_services"],
                    ["temporal-schema-setup", "temporal-namespace-setup"] if node["name"] == "data-1" else [],
                )

    def test_direct_mode_explains_that_dns_alone_does_not_provide_health_failover(self) -> None:
        plan = render_topology_plan("direct")

        self.assertIn("DNS", plan["ingress"]["availability_note"])
        self.assertIn("health", plan["ingress"]["availability_note"])
        self.assertEqual(plan["ingress"]["target_ports"], [80, 443])
        self.assertEqual(plan["firewall"]["application_ingress"]["source"], "public clients")
        self.assertNotIn("public_to_load_balancer", plan["firewall"])

    def test_cli_prints_the_plan_without_calling_docker(self) -> None:
        output = StringIO()
        with patch("tools.self_managed_deploy.cli.subprocess.run") as run, patch("sys.stdout", output):
            result = main(["plan", "--ingress", "lb"])

        self.assertEqual(result, 0)
        run.assert_not_called()
        self.assertEqual(json.loads(output.getvalue())["status"], "logical-plan-only")


if __name__ == "__main__":
    unittest.main()
