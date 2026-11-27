import os
import tempfile
from pathlib import Path

import unittest
from unittest.mock import patch

from tools.self_managed_deploy.env_config import write_app_env, write_endpoints

ENDPOINTS = {
    "domain": "posthog.example.com",
    "object_storage_access_key_id": "generated-access-key",
    "object_storage_secret_access_key": "generated-secret-key",
}
CLUSTER = {
    "client_cidrs": ["10.20.0.0/16"],
    "nodes": [
        {"name": "data-1", "address": "10.20.0.11", "id": 1},
        {"name": "data-2", "address": "10.20.0.12", "id": 2},
        {"name": "data-3", "address": "10.20.0.13", "id": 3},
    ],
}


class AppEnvConfigTest(unittest.TestCase):
    def test_generates_all_node_addresses_and_unique_service_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "endpoints.json"
            write_endpoints(CLUSTER, "posthog.example.com", output, ["10.0.0.0/8"])

            import json

            endpoints = json.loads(output.read_text())
            self.assertEqual(endpoints["postgres_hosts"], "10.20.0.11:5433,10.20.0.12:5433,10.20.0.13:5433")
            self.assertEqual(endpoints["temporal_hosts"], "10.20.0.11:7233,10.20.0.12:7233,10.20.0.13:7233")
            self.assertEqual(endpoints["object_storage_public_endpoint"], "https://posthog.example.com/posthog")
            self.assertEqual(endpoints["trusted_proxy_cidrs"], ["10.0.0.0/8"])
            self.assertEqual(len({endpoints["redis_url"], endpoints["flags_redis_url"]}), 2)
            self.assertEqual(os.stat(output).st_mode & 0o777, 0o600)

    def test_writes_private_app_environment_with_matching_storage_keys(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / ".env"
            with patch(
                "tools.self_managed_deploy.env_config.secrets.token_hex",
                side_effect=["a" * 96, "b" * 32, "c" * 96, "d" * 96, "e" * 96],
            ):
                write_app_env(ENDPOINTS, "self-managed-test", output)

            values = dict(line.split("=", 1) for line in output.read_text().splitlines() if "=" in line)
            self.assertEqual(values["POSTHOG_SECRET"], "a" * 96)
            self.assertEqual(values["ENCRYPTION_SALT_KEYS"], "b" * 32)
            self.assertEqual(values["POSTHOG_APP_TAG"], "self-managed-test")
            self.assertEqual(values["OBJECT_STORAGE_ACCESS_KEY_ID"], ENDPOINTS["object_storage_access_key_id"])
            self.assertEqual(values["OBJECT_STORAGE_SECRET_ACCESS_KEY"], ENDPOINTS["object_storage_secret_access_key"])
            self.assertEqual(os.stat(output).st_mode & 0o777, 0o600)

    def test_refuses_to_replace_an_existing_app_environment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / ".env"
            output.write_text("POSTHOG_SECRET=existing\n")

            with self.assertRaises(FileExistsError):
                write_app_env(ENDPOINTS, "self-managed-test", output)

            self.assertEqual(output.read_text(), "POSTHOG_SECRET=existing\n")

    def test_rejects_invalid_trusted_proxy_ranges_before_writing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "endpoints.json"
            with self.assertRaisesRegex(ValueError, "trusted proxy"):
                write_endpoints(CLUSTER, "posthog.example.com", output, ["invalid"])

            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
