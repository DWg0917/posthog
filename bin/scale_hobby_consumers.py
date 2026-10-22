#!/usr/bin/env python3
import os
import re
import json
import time
import pathlib
import argparse
import datetime
import subprocess

PAIRS = {
    "kafka_events_json": "events_json_mv",
    "kafka_events_json_native_json": "events_json_table_mv",
}


def compose_exec_command(service: str, env_file: str, compose_file: str) -> list[str]:
    return [
        "sudo",
        "-n",
        "docker",
        "compose",
        "--env-file",
        env_file,
        "-f",
        compose_file,
        "exec",
        "-T",
        service,
    ]


def query(sql: str, env_file: str, compose_file: str) -> str:
    result = subprocess.run(
        [
            *compose_exec_command("clickhouse", env_file, compose_file),
            "clickhouse-client",
            "--query",
            sql,
        ],
        text=True,
        capture_output=True,
        timeout=120,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(result.stderr)
    return result.stdout.strip()


def settings(ddl: str, consumers: int) -> str:
    for key, value in [
        ("kafka_num_consumers", consumers),
        ("kafka_thread_per_consumer", 1),
    ]:
        pattern = rf"\b{key}\s*=\s*\d+"
        if re.search(pattern, ddl):
            ddl = re.sub(pattern, f"{key} = {value}", ddl)
        else:
            ddl += (", " if "SETTINGS " in ddl else " SETTINGS ") + f"{key} = {value}"
    return ddl


def topic_partitions(env_file: str, compose_file: str) -> dict[str, int]:
    listing = subprocess.check_output(
        [
            *compose_exec_command("kafka", env_file, compose_file),
            "rpk",
            "topic",
            "list",
            "--brokers",
            "kafka:9092",
        ],
        text=True,
        timeout=30,
    )
    return {
        parts[0]: int(parts[1])
        for line in listing.splitlines()
        if len(parts := line.split()) >= 3 and parts[1].isdigit()
    }


def ensure_topic_partitions(
    topics: tuple[str, ...],
    consumers: int,
    apply: bool,
    env_file: str,
    compose_file: str,
) -> None:
    for _ in range(24):
        partitions = topic_partitions(env_file, compose_file)
        if all(topic in partitions for topic in topics):
            break
        time.sleep(5)
    else:
        missing = ", ".join(topic for topic in topics if topic not in partitions)
        raise SystemExit("Required topic is missing: " + missing)

    for topic in topics:
        current = partitions[topic]
        if current >= consumers:
            continue
        additional = consumers - current
        print(topic, "=>", consumers, "partitions", flush=True)
        if apply:
            subprocess.run(
                [
                    *compose_exec_command("kafka", env_file, compose_file),
                    "rpk",
                    "topic",
                    "add-partitions",
                    topic,
                    "--num",
                    str(additional),
                    "--brokers",
                    "kafka:9092",
                ],
                check=True,
                timeout=60,
            )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--partitions", type=int, default=None)
    parser.add_argument("--consumers", type=int, default=8)
    parser.add_argument("--backup-dir", type=pathlib.Path, required=True)
    parser.add_argument("--env-file", default=os.environ.get("ENV_FILE", ".env"))
    parser.add_argument(
        "--compose-file",
        default=os.environ.get("COMPOSE_FILE", "docker-compose.hobby.yml"),
    )
    args = parser.parse_args()
    partitions = args.partitions if args.partitions is not None else args.consumers
    if not 1 <= partitions <= 1024:
        raise SystemExit("Partition count must be between 1 and 1024.")
    if not 1 <= args.consumers <= 1024:
        raise SystemExit("Consumer count must be between 1 and 1024.")
    if args.consumers > partitions:
        raise SystemExit("Consumer count cannot exceed the topic partition count.")
    required_topics = ("events_plugin_ingestion", "clickhouse_events_json")
    ensure_topic_partitions(
        required_topics,
        partitions,
        args.apply,
        args.env_file,
        args.compose_file,
    )
    rows = [
        json.loads(row)
        for row in query(
            "SELECT name, engine, create_table_query FROM system.tables WHERE database='posthog' "
            "AND name IN ('kafka_events_json','kafka_events_json_native_json','events_json_mv','events_json_table_mv') "
            "FORMAT JSONEachRow",
            args.env_file,
            args.compose_file,
        ).splitlines()
    ]
    objects = {row["name"]: row for row in rows}
    if len(objects) != 4:
        raise SystemExit("Expected both Kafka tables and both materialized views.")
    for table, view in PAIRS.items():
        if objects[table]["engine"] != "Kafka" or objects[view]["engine"] != "MaterializedView":
            raise SystemExit("Unexpected table engine.")
        ddl = objects[view]["create_table_query"]
        if not re.search(r"\bTO posthog\.writable_events(?:_json)?\s", ddl):
            raise SystemExit("View must write to an external events table.")
        if not ddl.rstrip().endswith("FROM posthog." + table):
            raise SystemExit("Unexpected materialized view source.")
    args.backup_dir.mkdir(parents=True, exist_ok=True)
    backup = args.backup_dir / (
        "engine-ddl-" + datetime.datetime.now(datetime.UTC).strftime("%Y%m%dT%H%M%S%f") + ".json"
    )
    backup.write_text(json.dumps(objects, indent=2))
    backup.chmod(0o600)
    print("Backup:", backup, flush=True)
    for table, view in PAIRS.items():
        old_table = objects[table]["create_table_query"]
        old_view = objects[view]["create_table_query"]
        new_table = settings(old_table, args.consumers)
        if new_table == old_table:
            print(table, "already configured", flush=True)
            continue
        print(
            table,
            "=>",
            args.consumers,
            "consumers; same topic and consumer group",
            flush=True,
        )
        if not args.apply:
            continue
        view_removed = False
        table_removed = False
        new_created = False
        try:
            query(f"DROP TABLE IF EXISTS posthog.{view} SYNC", args.env_file, args.compose_file)
            view_removed = True
            query(f"DROP TABLE IF EXISTS posthog.{table} SYNC", args.env_file, args.compose_file)
            table_removed = True
            query(new_table, args.env_file, args.compose_file)
            new_created = True
            query(old_view, args.env_file, args.compose_file)
            view_removed = False
        except BaseException:
            if new_created:
                query(f"DROP TABLE IF EXISTS posthog.{table} SYNC", args.env_file, args.compose_file)
            if table_removed:
                query(old_table, args.env_file, args.compose_file)
            if view_removed:
                query(old_view, args.env_file, args.compose_file)
            print(table, "restored original definitions", flush=True)
            raise
        print(table, "configured; materialized view restored", flush=True)


if __name__ == "__main__":
    main()
