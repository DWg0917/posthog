# Custom Hobby deployment images

Run `sudo go run . --custom` from `bin/hobby-installer` in the custom branch checkout.
The installer records the app, node, and Feature Flags image references in `.env`.
Set `POSTHOG_FEATURE_FLAGS_IMAGE` to a published image compatible with the app database schema.
The deployment pulls that image from its registry before starting migrations and services.
It does not build Feature Flags on the deployment host or use the upstream `master` image.

For existing configurations, add `POSTHOG_FEATURE_FLAGS_IMAGE` before running `deploy.sh`.
Compose fails before deployment if that reference is missing.
An image tag alone does not prove compatibility: verify migrations, readiness, and an actual flag evaluation.

The current shared Rust Dockerfile expects proto files inside its build context.
CI copies the repository `proto/` directory into `rust/proto/` before building.
A bare build with only `rust/` and no proto files fails during protobuf compilation.
Do not substitute an upstream image to work around this failure.

To publish a replacement from committed sources on a build server:

```sh
bin/build-custom-feature-flags REGISTRY/IMAGE:TAG GIT_REF
```

This uses the repository Rust Dockerfile with the same proto layout as CI,
adds the source revision as an image label, and pushes the amd64 image.
It uses an isolated temporary context and leaves the checkout unchanged.
Deploy the published tag through the Go installer after verifying the build.

## Optional Temporal worker groups

The custom deployment wizard lets you select worker groups before deployment.
It stores the selected Compose profile names in `COMPOSE_PROFILES`; the shared
general-purpose group is selected by default. The original `temporal-django-worker`
service keeps its existing behavior.

| Group | Queues |
| --- | --- |
| `worker-general-purpose` | `general-purpose-task-queue` |
| `worker-data-warehouse` | `data-warehouse-task-queue`, `data-warehouse-cdp-producer-task-queue`, `data-warehouse-metadata-task-queue`, `data-modeling-task-queue`, `ducklake-task-queue` |
| `worker-exports` | `batch-exports-task-queue`, `no-sandbox-python-django`, `video-export-task-queue`, `weekly-digest-task-queue` |
| `worker-experiments-billing-tasks` | `experiments-recalculation-task-queue`, `billing-task-queue`, `tasks-task-queue` |
| `worker-session-replay` | `session-replay-task-queue` |
| `worker-error-tracking-logs` | `error-tracking-task-queue`, `error-tracking-lifecycle-task-queue`, `event-screenshots-task-queue`, `logs-alerting-task-queue`, `logs-volume-tick-task-queue` |
| `worker-ai-analytics` | `llm-analytics-task-queue`, `llm-analytics-evals-task-queue` |
| `worker-system` | `health-check-task-queue`, `stamphog-task-queue`, `test-task-queue` |
| `worker-replay-vision` | `replay-vision-task-queue` |

Each group runs one Django worker subprocess per queue, with metrics ports starting
at 8001 inside that group's container. If a child worker exits unexpectedly, the
group exits and stops its remaining workers. To enable a group on an existing host:

```sh
sudo docker compose --env-file .env -f docker-compose.hobby.yml up -d temporal-django-worker-data-warehouse
```

Replace `worker-data-warehouse` with the selected group after the
`temporal-django-` service prefix. To stop it, run
`sudo docker compose --env-file .env -f docker-compose.hobby.yml stop temporal-django-worker-data-warehouse`.
For durable selection across future deployments, update `COMPOSE_PROFILES` in `.env`
to the comma-separated group names you want enabled, then rerun `deploy.sh`.

`worker-replay-vision` is off by default. The separate Node.js
`rasterization-task-queue` is not managed by these Django worker groups.
