import json
import tempfile
import subprocess
from pathlib import Path

import unittest
from unittest.mock import patch

from tools.self_managed_deploy.operations import run_operation


class DeploymentOperationsTest(unittest.TestCase):
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

    def test_migrations_require_explicit_confirmation(self) -> None:
        with patch("tools.self_managed_deploy.operations.subprocess.run") as run:
            result = run_operation("migrate", Path("compose.json"), confirm=False)

        self.assertEqual(result, 2)
        run.assert_not_called()

    def test_migrations_run_checks_and_one_shot_jobs_before_application_migrations(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            compose_file = Path(directory) / "compose.json"
            compose_file.touch()
            with patch("tools.self_managed_deploy.operations.subprocess.run") as run:
                run.return_value.stdout = json.dumps({"services": {}})
                result = run_operation("migrate", compose_file, confirm=True)

        self.assertEqual(result, 0)
        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual(commands[0][4:], ["config", "--format", "json"])
        self.assertEqual(
            [command[-1] for command in commands[1:]],
            ["asyncmigrationscheck", "kafka-init", "personhog-etcd-init", "./bin/migrate", "personhog-tables-ready"],
        )
        self.assertFalse(any(command[-1] == "up" for command in commands))

    def test_up_builds_and_waits_for_the_scheduler_profile_when_requested(self) -> None:
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
        self.assertEqual(command[-6:], ["up", "--detach", "--build", "--wait", "--wait-timeout", "300"])

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
