import os
import json
import tempfile
from pathlib import Path

import unittest
from unittest.mock import patch

from tools.self_managed_deploy.cli import main
from tools.self_managed_deploy.test_renderer import ENDPOINTS


class RenderCommandTest(unittest.TestCase):
    def test_writes_private_compose_json_without_running_containers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            endpoint_path = root / "endpoints.json"
            output_path = root / "compose.json"
            endpoint_path.write_text(json.dumps(ENDPOINTS))
            resolved = {"services": {"proxy": {"environment": {}}}, "volumes": {}}

            with patch("tools.self_managed_deploy.cli.subprocess.run") as run:
                run.return_value.stdout = json.dumps(resolved)
                result = main(
                    ["render", "--endpoints", str(endpoint_path), "--ingress", "direct", "--output", str(output_path)]
                )

            self.assertEqual(result, 0)
            self.assertEqual(
                json.loads(output_path.read_text())["services"]["proxy"]["environment"]["CADDY_HOST"],
                "posthog.example.com, http://, https://",
            )
            self.assertEqual(os.stat(output_path).st_mode & 0o777, 0o600)
            args = run.call_args.args[0]
            self.assertEqual(args[2:5], ["-f", os.path.join(os.getcwd(), "docker-compose.hobby.yml"), "config"])
            self.assertNotIn("up", args)

    def test_refuses_to_overwrite_existing_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            endpoint_path = root / "endpoints.json"
            output_path = root / "compose.json"
            endpoint_path.write_text("{}")
            output_path.write_text("keep me")

            with patch("tools.self_managed_deploy.cli.subprocess.run") as run:
                result = main(
                    ["render", "--endpoints", str(endpoint_path), "--ingress", "direct", "--output", str(output_path)]
                )

            self.assertEqual(result, 2)
            self.assertEqual(output_path.read_text(), "keep me")
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
