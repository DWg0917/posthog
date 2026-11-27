import json
import tempfile
import subprocess
from io import StringIO
from pathlib import Path

import unittest
from unittest.mock import patch

from tools.self_managed_deploy.operations import run_operation


class DeploymentOperationsTest(unittest.TestCase):
    def test_status_reports_running_and_healthy_services(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            compose_file = Path(directory) / "compose.json"
            compose_file.touch()
            stdout = StringIO()
            with (
                patch("tools.self_managed_deploy.operations.subprocess.run") as run,
                patch("sys.stdout", stdout),
            ):
                run.side_effect = [
                    subprocess.CompletedProcess([], 0, json.dumps({"services": {"web": {}}}), ""),
                    subprocess.CompletedProcess([], 0, '{"Service":"web","State":"running","Health":"healthy"}\n', ""),
                ]
                result = run_operation("status", compose_file)

        self.assertEqual(result, 0)
        self.assertIn("web: running (healthy)", stdout.getvalue())
        self.assertEqual(run.call_args_list[-1].args[0][-4:], ["ps", "--all", "--format", "json"])

    def test_status_fails_when_a_service_is_unhealthy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            compose_file = Path(directory) / "compose.json"
            compose_file.touch()
            stderr = StringIO()
            with (
                patch("tools.self_managed_deploy.operations.subprocess.run") as run,
                patch("sys.stderr", stderr),
            ):
                run.side_effect = [
                    subprocess.CompletedProcess([], 0, json.dumps({"services": {"worker": {}}}), ""),
                    subprocess.CompletedProcess(
                        [], 0, '{"Service":"worker","State":"running","Health":"unhealthy"}\n', ""
                    ),
                ]
                result = run_operation("status", compose_file)

        self.assertEqual(result, 1)
        self.assertIn("worker: running (unhealthy)", stderr.getvalue())

    def test_status_fails_when_no_containers_are_running(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            compose_file = Path(directory) / "compose.json"
            compose_file.touch()
            stderr = StringIO()
            with (
                patch("tools.self_managed_deploy.operations.subprocess.run") as run,
                patch("sys.stderr", stderr),
            ):
                run.side_effect = [
                    subprocess.CompletedProcess([], 0, json.dumps({"services": {"web": {}}}), ""),
                    subprocess.CompletedProcess([], 0, "", ""),
                ]
                result = run_operation("status", compose_file)

        self.assertEqual(result, 1)
        self.assertIn("No Compose containers were found", stderr.getvalue())

    def test_temporal_schema_initialization_requires_explicit_confirmation(self) -> None:
        with patch("tools.self_managed_deploy.operations.subprocess.run") as run:
            result = run_operation("temporal-init", Path("data-1.json"), confirm=False)

        self.assertEqual(result, 2)
        run.assert_not_called()

    def test_temporal_namespace_initialization_requires_explicit_confirmation(self) -> None:
        with patch("tools.self_managed_deploy.operations.subprocess.run") as run:
            result = run_operation("temporal-namespace-init", Path("data-1.json"), confirm=False)

        self.assertEqual(result, 2)
        run.assert_not_called()

    def test_temporal_namespace_initialization_runs_the_namespace_one_shot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            compose_file = Path(directory) / "data-1.json"
            compose_file.touch()
            with patch("tools.self_managed_deploy.operations.subprocess.run") as run:
                run.return_value.stdout = json.dumps({"services": {"temporal-namespace-setup": {}}})
                result = run_operation("temporal-namespace-init", compose_file, confirm=True)

        self.assertEqual(result, 0)
        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual(commands[1][-4:], ["run", "--rm", "--no-deps", "temporal-namespace-setup"])

    def test_temporal_schema_initialization_runs_only_the_one_shot_setup_service(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            compose_file = Path(directory) / "data-1.json"
            compose_file.touch()
            with patch("tools.self_managed_deploy.operations.subprocess.run") as run:
                run.return_value.stdout = json.dumps({"services": {"temporal-schema-setup": {}}})
                result = run_operation("temporal-init", compose_file, confirm=True)

        self.assertEqual(result, 0)
        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual(len(commands), 2)
        self.assertIn("init", commands[0])
        self.assertEqual(commands[1][-4:], ["run", "--rm", "--no-deps", "temporal-schema-setup"])

    def test_preflight_rejects_missing_build_context_and_bind_mounts(self) -> None:
        compose_config = {
            "services": {
                "web": {"build": {"context": "/missing/repository", "dockerfile": "Dockerfile"}},
                "kafka-init": {"volumes": [{"type": "bind", "source": "/missing/topics.txt", "target": "/topics.txt"}]},
            }
        }
        with tempfile.TemporaryDirectory() as directory:
            compose_file = Path(directory) / "compose.json"
            compose_file.touch()
            with patch("tools.self_managed_deploy.operations.subprocess.run") as run:
                run.return_value.stdout = json.dumps(compose_config)
                result = run_operation("preflight", compose_file)

        self.assertEqual(result, 2)
        self.assertEqual(run.call_count, 1)
        self.assertIn("init", run.call_args.args[0])
        self.assertIn("--profile", run.call_args.args[0])

    def test_preflight_resolves_all_optional_profiles(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            compose_file = Path(directory) / "compose.json"
            compose_file.touch()
            with patch("tools.self_managed_deploy.operations.subprocess.run") as run:
                run.return_value.stdout = json.dumps({"services": {}})
                result = run_operation("preflight", compose_file)

        self.assertEqual(result, 0)
        command = run.call_args.args[0]
        for profile in ("init", "temporal", "scheduler"):
            with self.subTest(profile=profile):
                self.assertIn(profile, command)

    def test_migrations_require_explicit_confirmation(self) -> None:
        with patch("tools.self_managed_deploy.operations.subprocess.run") as run:
            result = run_operation("migrate", Path("compose.json"), confirm=False)

        self.assertEqual(result, 2)
        run.assert_not_called()

    def test_migrations_reject_a_compose_file_without_primary_only_init_services(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            compose_file = Path(directory) / "app-2.json"
            compose_file.touch()
            stderr = StringIO()
            with patch("tools.self_managed_deploy.operations.subprocess.run") as run, patch("sys.stderr", stderr):
                run.return_value.stdout = json.dumps({"services": {"web": {}}})
                result = run_operation("migrate", compose_file, confirm=True)

        self.assertEqual(result, 2)
        self.assertIn("app-1", stderr.getvalue())
        self.assertEqual(run.call_count, 1)

    def test_migrations_run_checks_and_one_shot_jobs_before_application_migrations(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            compose_file = Path(directory) / "compose.json"
            compose_file.touch()
            with patch("tools.self_managed_deploy.operations.subprocess.run") as run:
                run.return_value.stdout = json.dumps(
                    {
                        "services": {
                            "asyncmigrationscheck": {},
                            "kafka-init": {},
                            "personhog-etcd-init": {},
                            "web": {},
                            "personhog-tables-ready": {},
                        }
                    }
                )
                result = run_operation("migrate", compose_file, confirm=True)

        self.assertEqual(result, 0)
        commands = [call.args[0] for call in run.call_args_list]
        self.assertIn("--profile", commands[0])
        self.assertIn("init", commands[0])
        self.assertEqual(commands[0][-3:], ["config", "--format", "json"])
        self.assertEqual(
            [command[-1] for command in commands[1:]],
            [
                "asyncmigrationscheck",
                "kafka-init",
                "personhog-etcd-init",
                "./bin/migrate",
                "personhog-tables-ready",
                "register_temporal_search_attributes",
            ],
        )
        migration_command = next(command for command in commands if command[-1] == "./bin/migrate")
        self.assertNotIn("--no-deps", migration_command)
        async_migration_check = next(command for command in commands if command[-1] == "asyncmigrationscheck")
        self.assertNotIn("--no-deps", async_migration_check)
        self.assertFalse(any(command[-1] == "up" for command in commands))

    def test_up_starts_services_already_selected_by_the_node_compose_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            compose_file = Path(directory) / "compose.json"
            compose_file.touch()
            with patch("tools.self_managed_deploy.operations.subprocess.run") as run:
                run.return_value.stdout = json.dumps({"services": {}})
                result = run_operation("up", compose_file)

        self.assertEqual(result, 0)
        command = run.call_args_list[-1].args[0]
        self.assertNotIn("--profile", command)
        self.assertEqual(command[-6:], ["up", "--detach", "--build", "--wait", "--wait-timeout", "300"])

    def test_up_can_enable_the_standby_scheduler_for_manual_failover(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            compose_file = Path(directory) / "compose.json"
            compose_file.touch()
            with patch("tools.self_managed_deploy.operations.subprocess.run") as run:
                run.return_value.stdout = json.dumps({"services": {}})
                result = run_operation("up", compose_file, scheduler=True)

        self.assertEqual(result, 0)
        command = run.call_args_list[-1].args[0]
        self.assertIn("--profile", command)
        self.assertIn("scheduler", command)

    def test_up_can_start_temporal_cluster_profile(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            compose_file = Path(directory) / "compose.json"
            compose_file.touch()
            with patch("tools.self_managed_deploy.operations.subprocess.run") as run:
                run.return_value.stdout = json.dumps({"services": {}})
                result = run_operation("up", compose_file, temporal=True)

        self.assertEqual(result, 0)
        command = run.call_args_list[-1].args[0]
        self.assertIn("--profile", command)
        self.assertIn("temporal", command)

    def test_propagates_failed_compose_operations(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            compose_file = Path(directory) / "compose.json"
            compose_file.touch()
            with patch(
                "tools.self_managed_deploy.operations.subprocess.run",
                side_effect=subprocess.CalledProcessError(1, ["docker", "compose"]),
            ):
                result = run_operation("up", compose_file)

        self.assertEqual(result, 1)


if __name__ == "__main__":
    unittest.main()
