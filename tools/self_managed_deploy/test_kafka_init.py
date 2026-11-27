import os
import tempfile
import subprocess
from pathlib import Path

import unittest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
INIT_SCRIPT = REPOSITORY_ROOT / "docker/kafka/init-topics.sh"


class KafkaTopicInitializationTest(unittest.TestCase):
    def run_initializer(self, replication_factor: str, existing_topics: str) -> tuple[str, str]:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bin_directory = root / "bin"
            bin_directory.mkdir()
            rpk_log = root / "rpk.log"
            topics_file = root / "topics.txt"
            topics_file.write_text("sample_topic\n")
            rpk = bin_directory / "rpk"
            rpk.write_text(
                "#!/bin/sh\n"
                'printf \'%s\\n\' "$*" >> "$RPK_LOG"\n'
                "if [ \"$1 $2\" = 'topic list' ]; then\n"
                "  printf '%s\\n' $EXISTING_TOPICS\n"
                "  exit 0\n"
                "fi\n"
                'if [ "$1 $2" = \'topic create\' ] && [ -n "$EXISTING_TOPICS" ]; then exit 1; fi\n'
            )
            rpk.chmod(0o755)
            environment = {
                **os.environ,
                "PATH": f"{bin_directory}:{os.environ['PATH']}",
                "RPK_LOG": str(rpk_log),
                "KAFKA_HOSTS": "broker-1:9092,broker-2:9092,broker-3:9092",
                "KAFKA_TOPIC_REPLICATION_FACTOR": replication_factor,
                "KAFKA_TOPIC_LIST_FILE": str(topics_file),
                "EXISTING_TOPICS": existing_topics,
            }

            result = subprocess.run(
                ["/bin/sh", str(INIT_SCRIPT)], capture_output=True, check=True, text=True, env=environment
            )
            return result.stdout, rpk_log.read_text()

    def test_creates_new_topics_with_configured_replication_factor(self) -> None:
        _, commands = self.run_initializer("3", "")

        self.assertIn("cluster config set default_topic_replications=3", commands)
        self.assertIn("cluster config set minimum_topic_replications=3", commands)
        self.assertIn(
            "topic create sample_topic --brokers broker-1:9092,broker-2:9092,broker-3:9092 -p 1 -r 3", commands
        )

    def test_updates_existing_topics_to_configured_replication_factor(self) -> None:
        existing = "sample_topic personhog_updates personhog_changelog_relay_dlq"

        _, commands = self.run_initializer("3", existing)

        self.assertIn(
            "topic alter-config sample_topic --brokers broker-1:9092,broker-2:9092,broker-3:9092 --set replication.factor=3",
            commands,
        )

    def test_hobby_default_does_not_change_existing_topic_replication(self) -> None:
        existing = "sample_topic personhog_updates personhog_changelog_relay_dlq"

        _, commands = self.run_initializer("1", existing)

        self.assertNotIn("topic alter-config", commands)
        self.assertNotIn("cluster config set", commands)


if __name__ == "__main__":
    unittest.main()
