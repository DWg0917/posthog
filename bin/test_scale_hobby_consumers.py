import unittest

import scale_hobby_consumers


class ComposeExecCommandTest(unittest.TestCase):
    def test_uses_compose_service_and_deployment_files(self) -> None:
        self.assertEqual(
            scale_hobby_consumers.compose_exec_command("kafka", ".env.custom", "docker-compose.hobby.yml"),
            [
                "sudo",
                "-n",
                "docker",
                "compose",
                "--env-file",
                ".env.custom",
                "-f",
                "docker-compose.hobby.yml",
                "exec",
                "-T",
                "kafka",
            ],
        )


if __name__ == "__main__":
    unittest.main()
