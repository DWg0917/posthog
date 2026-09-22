# Hobby ingestion scaling

The hobby deployment uses eight partitions for `events_plugin_ingestion` and `clickhouse_events_json`, four `ingestion-general` replicas, and eight consumers on each of the two ClickHouse event Kafka tables.
Set `INGESTION_GENERAL_REPLICAS` in `.env` to change the replica count.
The Kafka broker remains a single broker with replication factor one.

`deploy.sh` applies the Kafka Engine consumer configuration after services start.
The consumer script checks both topics have enough partitions and saves the live Engine and materialized view definitions before changing them.
It replaces only the Kafka Engine tables and materialized views with external `TO` destinations; the event storage tables are retained.
Consumer group names and committed offsets are retained.
If a replacement fails, the script attempts to restore that pair's original definitions and returns an error.
Consumption pauses during replacement. Kafka's at-least-once delivery can replay uncommitted messages during a restart; verify unique event UUIDs as well as row counts.

`bin/scale_hobby_consumers.py --apply` checks the existing topic counts and idempotently adds any missing partitions before changing the ClickHouse Engine tables. The deployment script calls it automatically. Inspect the current counts with `sudo docker exec posthog-kafka-1 rpk topic list`.
Drain the ingestion groups before manually increasing partitions because partition expansion can move a message key to a different partition.
For topics with exactly one partition, this manual command adds seven partitions:

```sh
sudo docker exec posthog-kafka-1 rpk topic add-partitions events_plugin_ingestion clickhouse_events_json --num 7
```

Do not repeat the manual command without checking counts: `--num` means additional partitions.
Partitions cannot be reduced in place.

To apply the consumer settings to an existing eight-partition installation:

```sh
python3 bin/scale_hobby_consumers.py --apply --backup-dir "$PWD/share/ingestion-backups"
sudo docker compose --env-file .env -f docker-compose.hobby.yml up -d --no-deps --scale ingestion-general=4 ingestion-general
```

To inspect the plan and save definitions without changing the tables, omit `--apply`.
To reduce consumer threads after a trial, use `--consumers 1`; partitions stay unchanged.
The script is specific to the hobby deployment's Compose container names and two event Engine tables.

## Measuring ingestion

```sh
python3 bin/ingestion_benchmark.py \
  --url https://example.com/batch/ \
  --rate 1000 --seconds 60 --workers 50 \
  --output /tmp/ingestion-benchmark.jsonl
```

Run this on the deployment host.
It reads the selected project's capture token from PostgreSQL without printing it.
Each request contains 100 synthetic events by default, with 512 bytes of payload plus event metadata.
Person processing is enabled and IDs rotate through 10,000 synthetic users.
Each run uses unique event names, person IDs and event UUIDs.
The chosen project receives real test events and person records; use `--team-id` to select a dedicated test project.

The sender reuses HTTPS connections and verifies TLS certificates.
`--workers` is the maximum number of concurrent requests, not a target number of simultaneously active users.
`--rate` specifies events per second; skipped submissions and HTTP timeouts are reported rather than silently retried.
The run stops scheduling when more than 10% of the last 20 completed requests fail.
Each run is limited to 500,000 events.

The JSONL output includes resource snapshots taken during sending, Kafka group offsets and lag, HTTP latency, and row/unique UUID counts from both ClickHouse event storage paths.
HTTP acceptance is distinct from successful storage.
Latency percentiles measure batch HTTP requests, not per-event time to storage.
`drain_s` includes final verification and monitor shutdown time, so it is not an ingestion latency percentile.

Use `--connect-address 127.0.0.1` for a server-local control measurement.
The URL hostname remains the HTTP Host and TLS SNI, and certificate verification remains enabled.
This includes the proxy, capture and ingestion path but bypasses the public network path.
Compare it with the same workload using normal DNS before attributing HTTPS timeouts to Kafka consumption.
Server-local results share CPU with the load generator and do not establish public ingress capacity or long-duration production capacity.
