# Self-managed multi-host deployment

This tool renders a PostHog application stack for two application hosts and state services for three data hosts. It uses Docker Compose on each host. It does not create cloud machines, firewalls, DNS records, or a load balancer. The Tencent Cloud validation steps use temporary, pay-as-you-go CVMs; compute instances can be released after the test while retained CBS disks and snapshots are managed separately.

The generated configuration and instructions have not yet been exercised on Linux CVMs. The CBS backup procedure below is an operator-run, maintenance-window snapshot and restore drill. It is not online point-in-time recovery (PITR), scheduled backup automation, or proof of high availability. The `status` command reports local Compose container state; it does not send alerts.

## Tencent Cloud validation environment

Create five temporary CVMs in one VPC and private subnet, plus one CLB only if using `lb` ingress. Use the `app-1`, `app-2`, `data-1`, `data-2`, and `data-3` roles below. Select temporary pay-as-you-go billing and destroy the CVMs after validation. Keep any CBS data disks and snapshots only as long as the test requires; deleting a CVM does not mean its separately managed disks or snapshots are automatically deleted. Place app nodes on separate hosts. For a test of availability-zone failure, spread the three data nodes across three zones and the two app nodes across at least two zones; a same-zone test does not prove zone-level recovery.

| CVM | Run | Persistent data |
| --- | --- | --- |
| `app-1` | Edge proxy/Caddy, web, capture, ingestion consumers, PersonHog, product and Celery workers, primary Celery beat | No PostHog database volumes expected; keep its private Compose and environment files backed up separately. |
| `app-2` | Same application services; standby Celery beat is started manually during failover | No PostHog database volumes expected; keep its private Compose and environment files backed up separately. |
| `data-1` | PostgreSQL/Patroni, ClickHouse, Redpanda, ZooKeeper, etcd, Redis/Sentinel, flags Redis/Sentinel, Valkey/Sentinel, SeaweedFS, Temporal | Dedicated CBS data disk for Docker volumes. |
| `data-2` | Same state services, as cluster members/replicas | Dedicated CBS data disk for Docker volumes. |
| `data-3` | Same state services, as cluster members/replicas | Dedicated CBS data disk for Docker volumes. |

Use the same Linux distribution release, Docker Engine, Compose v2, repository commit, VPC, and security-group rules on all five hosts. The repository does not provision CVMs, CBS disks, CLB, DNS, or Tencent Cloud credentials. Do not choose machine sizes from this table: the service placement is logical, not a measured resource plan.

### Provision and prepare the hosts

1. Create one VPC and subnet. Create five temporary CVMs with private addresses and SSH access restricted to the operator's IP. Add a public CLB only for `lb` ingress. Create and attach one persistent CBS data disk to each data CVM.
2. Create security-group rules from `python3 -m tools.self_managed_deploy.cli plan --ingress lb` or `--ingress direct`. Add the complete data-node mesh rules from the table below. Do not expose data ports to the public internet. Configure DNS according to the selected ingress mode.
3. On data-1/2/3, initialize and mount the attached CBS disk before the first PostHog Compose deployment. Configure Docker `data-root` on that mount as described above. Reboot once and check that the mount and `docker info` path persist before putting data on it.
4. Install Git, Python 3, Docker Engine, and the Docker Compose v2 plugin on all five CVMs. For an Ubuntu or Debian image, follow Docker's [Engine installation guide](https://docs.docker.com/engine/install/ubuntu/) or [Debian guide](https://docs.docker.com/engine/install/debian/), then install `docker-compose-plugin` from Docker's package repository and verify `docker compose version`. Install the Compose plugin only after configuring the official Docker package repository.
5. Clone the same fork/repository on all five CVMs and check out one identical commit. Keep the commit fixed through the deployment and restore drill. The app renderer builds from this checkout, so each host needs the source tree and its build dependencies.
6. From a trusted operator host, generate `endpoints.json` and `app.env` once using the commands below. Transfer only the required private files to each CVM over an encrypted channel, set owner-only permissions, and do not put credentials in the Git repository or shell history.
7. Start the three data nodes, initialize Temporal schema/namespace once, then run the PostHog migration once from app-1 and start both app nodes. Use the sections below in order. Do not begin the restore drill until the normal deployment and end-to-end product smoke test pass.

The first isolated smoke test confirms deployment mechanics only. For a host-failure test, leave one app CVM and one data CVM available with enough spare capacity; this logical layout does not guarantee that a workload will fit after a host failure.

Before starting Docker on each data CVM, attach and initialize a CBS data disk. Follow Tencent's current [Linux data-disk initialization procedure](https://cloud.tencent.com/document/product/213/17487); identify the disk by its console ID/UUID and never format it by guessing a device name. Add its UUID to `/etc/fstab` mounted at `/mnt/posthog-data`, then configure Docker to store all named volumes on that mount:

```bash
sudo install -d -m 0755 /etc/docker /etc/systemd/system/docker.service.d
sudo tee /etc/docker/daemon.json >/dev/null <<'EOF'
{"data-root":"/mnt/posthog-data/docker"}
EOF
sudo tee /etc/systemd/system/docker.service.d/10-posthog-data-disk.conf >/dev/null <<'EOF'
[Unit]
RequiresMountsFor=/mnt/posthog-data
EOF
sudo systemctl daemon-reload
sudo mountpoint -q /mnt/posthog-data && findmnt /mnt/posthog-data && sudo systemctl restart docker && docker info --format '{{.DockerRootDir}}'
```

Run this before any Compose deployment. Reboot once and confirm `findmnt` shows the CBS disk and `docker info` reports `/mnt/posthog-data/docker`. This keeps named Compose volumes on the disk that will be snapshotted and prevents Docker from starting before the required mount. The three data hosts must have stable private addresses; replace the example addresses in `cluster.json` with those addresses.

### Domain and ingress options

Choose exactly one ingress mode for both app nodes:

| Mode | DNS record | Routing and TLS | Limitation |
| --- | --- | --- | --- |
| `lb` | Point the product hostname's A record to the CLB VIP. | CLB HTTPS listener terminates TLS and forwards HTTP to both app CVMs on port 80. Configure the backend health check as HTTP `/_readyz?role=web`, expected status 200. | Allow backend port 80 only from the CLB health-check/request source ranges and app VPC rules. Tencent documents that health-check source can be the CLB VIP or `100.64.0.0/10`; confirm the actual mode before tightening the security group. |
| `direct` | Create A records for both app CVM public IPs, or use a DNS provider that supports health-based failover. | Caddy on each app CVM terminates TLS; public TCP 80/443 reach the app nodes. | Ordinary DNS round-robin does not remove an unhealthy app node. |

The `lb` mode is the recommended validation path when you want to test load-balancer routing. DNS should point to CLB, not to the app CVMs. For `direct`, DNS points to the app CVMs and no CLB is needed. Keep data CVMs on private addresses and do not publish their service ports. See Tencent's [CLB HTTP health-check behavior](https://cloud.tencent.com/document/product/214/6097) and [health-check source identifiers](https://cloud.tencent.com/document/product/214/6097).

## Prepare configuration

Check out the same PostHog commit on all five hosts. Run the render commands on each target host because generated Compose files contain paths to that host's checkout. Keep the generated credentials private.

Copy `cluster.example.json` to the private deployment directory and edit it. Use the three data host private IPv4 addresses, unique node IDs 1 to 3, and private CIDRs that include all application hosts and data hosts allowed to connect to PostgreSQL.

The example keeps Kafka events for one hour, matching the Hobby Compose default. Set `kafka_retention_ms` to a longer value if the deployment needs a longer recovery window and has enough broker disk capacity.

Generate service credentials once on a trusted machine:

```bash
install -d -m 700 "$HOME/.config/posthog-deploy"
cp tools/self_managed_deploy/cluster.example.json "$HOME/.config/posthog-deploy/cluster.json"

python3 -m tools.self_managed_deploy.cli init-config \
  --cluster "$HOME/.config/posthog-deploy/cluster.json" \
  --domain posthog.example.com \
  --output "$HOME/.config/posthog-deploy/endpoints.json"

python3 -m tools.self_managed_deploy.cli init-env \
  --endpoints "$HOME/.config/posthog-deploy/endpoints.json" \
  --output "$HOME/.config/posthog-deploy/app.env"
```

For a load balancer, add one `--trusted-proxy-cidr` argument for each source range the load balancer uses to connect to the app hosts. Copy `endpoints.json` to all five hosts and the same `app.env` to both app hosts. Do not run `init-config` or `init-env` again for an existing deployment. Keep its existing secrets.

Keep generated files outside the repository. Restrict access to the directory and files because the Compose files contain database and storage credentials.

## Start the data services

On each data host, render its own Compose file. The example paths below assume the repository checkout is at the current directory. Start the base Compose stack on all three data hosts at the same time. etcd and Patroni need a quorum, so a single host can remain waiting for the other two:

```bash
python3 -m tools.self_managed_deploy.cli render-data-node \
  --cluster "$HOME/.config/posthog-deploy/cluster.json" \
  --endpoints "$HOME/.config/posthog-deploy/endpoints.json" \
  --node data-1 \
  --output "$HOME/.config/posthog-deploy/data-1.json"

python3 -m tools.self_managed_deploy.operations preflight --compose "$HOME/.config/posthog-deploy/data-1.json"
python3 -m tools.self_managed_deploy.operations up --compose "$HOME/.config/posthog-deploy/data-1.json"
```

Use `data-2` and `data-3` as the node name and Compose file on the other hosts. After PostgreSQL has elected a primary and all three nodes are running, initialize the Temporal schemas once on data-1:

```bash
python3 -m tools.self_managed_deploy.operations temporal-init --compose "$HOME/.config/posthog-deploy/data-1.json" --yes
```

Start the Temporal server profile on each data host:

```bash
python3 -m tools.self_managed_deploy.operations up --compose "$HOME/.config/posthog-deploy/data-1.json" --temporal
python3 -m tools.self_managed_deploy.operations up --compose "$HOME/.config/posthog-deploy/data-2.json" --temporal
python3 -m tools.self_managed_deploy.operations up --compose "$HOME/.config/posthog-deploy/data-3.json" --temporal
```

Then create Temporal's default namespace once on data-1:

```bash
python3 -m tools.self_managed_deploy.operations temporal-namespace-init --compose "$HOME/.config/posthog-deploy/data-1.json" --yes
```

## Start the application services

On each app host, render a local Compose file with the same endpoint and environment files. Choose `direct` when DNS points to the app hosts, or `lb` when DNS points to a load balancer.

```bash
python3 -m tools.self_managed_deploy.cli render \
  --endpoints "$HOME/.config/posthog-deploy/endpoints.json" \
  --env-file "$HOME/.config/posthog-deploy/app.env" \
  --ingress lb \
  --node app-1 \
  --output "$HOME/.config/posthog-deploy/app-1.json"

python3 -m tools.self_managed_deploy.operations preflight --compose "$HOME/.config/posthog-deploy/app-1.json"
```

On app-1, run the migrations and one-time initializers once. Temporal must be running and its namespace must exist before this command registers PostHog search attributes:

```bash
python3 -m tools.self_managed_deploy.operations migrate --compose "$HOME/.config/posthog-deploy/app-1.json" --yes
```

Then start the app services on both app hosts:

```bash
python3 -m tools.self_managed_deploy.operations up --compose "$HOME/.config/posthog-deploy/app-1.json"
```

Render and run the corresponding commands with `--node app-2` and `$HOME/.config/posthog-deploy/app-2.json` on app-2. The Celery scheduler runs on app-1. App-2's scheduler can be started with `up --scheduler` during a manual failover.

For direct ingress, DNS should direct traffic to both app hosts. DNS records do not provide health-based failover. For load balancer ingress, terminate HTTPS on the load balancer, forward HTTP to app-host port 80, and health-check `/_readyz?role=web` on port 80. The app host's port 443 is not used in this mode.

## Configure network access

Print the machine-readable port plan with:

```bash
python3 -m tools.self_managed_deploy.cli plan --ingress lb
```

Allow these TCP connections in the host firewall or cloud security groups. These are destination ports on the receiving hosts:

| Source | Destination | TCP ports | Purpose |
| --- | --- | --- | --- |
| Public clients | Load balancer | 443; optionally 80 | HTTPS ingress. Also open 80 on the load balancer only if it redirects HTTP to HTTPS. |
| Load balancer | Both app hosts | 80 | HTTP forwarding and `/_readyz?role=web` health checks. Allow only the load balancer's source ranges. |
| Public clients | Both app hosts, direct mode only | 80, 443 | Caddy HTTP and HTTPS ingress. Do not use this rule in load balancer mode. |
| Both app hosts | All data hosts | 5433, 9000, 9092, 2379, 6379, 6380, 6381, 8333, 7233 | PostgreSQL, ClickHouse, Kafka, etcd, Redis, flags Redis, Valkey, SeaweedFS S3, and Temporal frontend. |
| All data hosts | All data hosts | 2181, 2379, 2380, 26379, 26380, 26381, 2888, 3888, 5433, 6933-6936, 6939, 7233-7236, 7239, 8008, 8080, 8333, 9000, 9009, 9333, 18080, 19333, 33145 | Coordination, database replication and failover, broker and storage replication, and Temporal cluster membership. |

The port plan is based on the current generated service configuration and the upstream service port settings. Temporal uses separate gRPC and membership ports for its internal services, so opening only 7233 between the data hosts is insufficient. Keep data ports private; do not expose them to the public internet. SSH and provider-management access are separate rules and are not included here. This port list has not yet been exercised across real hosts, so verify it during the first isolated deployment before applying it to production.

## Back up and restore data disks

The generated data-node Compose files use Docker named volumes. With Docker `data-root` on a dedicated CBS disk, these volumes, including PostgreSQL, ClickHouse, Redpanda, Temporal's database, and SeaweedFS, are captured by that data disk's snapshot. Keep the matching PostHog commit, rendered Compose files, endpoint credentials, environment files, and node/IP mapping in a separate encrypted location. Snapshots contain secrets and customer data; restrict their IAM access and retention.

### Create a coordinated cold snapshot

This procedure deliberately stops ingestion and database writes. Schedule a maintenance window and use a separate trusted operator host with SSH access to all five CVMs. Run the commands against each host's private Compose file:

1. Record the exact Git commit, the three data node names/private IPs, each CBS disk ID, and the location of the encrypted deployment configuration. Verify the data disks are mounted and are the Docker roots.
2. Stop both application stacks so they cannot accept new events or write to state services:

   ```bash
   python3 -m tools.self_managed_deploy.operations status --compose "$HOME/.config/posthog-deploy/app-1.json"
   docker compose -f "$HOME/.config/posthog-deploy/app-1.json" down
   docker compose -f "$HOME/.config/posthog-deploy/app-2.json" down
   ```

3. On each data host, capture status and stop the stack. Do not add `-v`; named volumes must remain on disk:

   ```bash
   python3 -m tools.self_managed_deploy.operations status --compose "$HOME/.config/posthog-deploy/data-1.json"
   docker compose -f "$HOME/.config/posthog-deploy/data-1.json" down
   sync
   ```

   Repeat on `data-2` and `data-3`. Confirm all three Docker stacks are stopped before taking any snapshot. Taking the snapshots only after all writes stop makes the three-disk set a coordinated cold recovery point. Tencent warns that snapshots do not include unflushed application or operating-system buffers; see [CBS snapshot consistency guidance](https://cloud.tencent.com/document/product/362/5755).

4. In the CBS console, create and label one snapshot for each of the three data disks with a shared run identifier, for example `posthog-test-2026-11-27-data-1`. Record the snapshot-to-node mapping and completion time. Do not snapshot app disks as a substitute for these data disks.
5. Start the data Compose stacks on all three hosts with `python3 -m tools.self_managed_deploy.operations up --compose "$HOME/.config/posthog-deploy/data-N.json" --temporal` (replace `N` with 1, 2, and 3). Wait for cluster health checks, then start app-1 and app-2 with the documented `operations up` command. Confirm the application is serving and the CLB reports both backends healthy.
6. Record the maintenance duration and the snapshot IDs. Set a retention/expiry policy in the console. Snapshots in the same account and region are not protection against account or region loss; production recovery needs a separately controlled off-region/off-account copy and a tested retention policy.

This is a cold, full-disk backup, not a database-native logical export. It does not provide a 15-minute RPO while running and the coordinated stop causes downtime. Do not describe it as PITR. The actual recovery point is the recorded time at which all three snapshots completed while every application and data container was stopped.

### Restore drill in an isolated network

Never attach restored cluster disks while the original cluster can still write to its data. Use an isolated VPC/security group with no route from production and block public ingress.

1. Create three new data disks from the corresponding CBS snapshots. Create fresh temporary CVMs in an isolated VPC using the same private CIDR and node IPs as the original cluster, with no route from the original environment, and attach one restored disk to each. Mount the disk at the same Docker `data-root`; do not run `docker compose down -v`, initialize empty volumes, or run migrations against the restored data.
2. Check out the recorded repository commit on each CVM. Restore the matching private deployment files with the original `data-1`/`data-2`/`data-3` identities, private addresses, and service credentials. Keeping the same private addresses avoids changing the cluster membership addresses stored in the restored data.
3. Start the data services on all three restored nodes with `python3 -m tools.self_managed_deploy.operations up --compose "$HOME/.config/posthog-deploy/data-N.json" --temporal` (replace `N` with 1, 2, and 3). Verify PostgreSQL primary/replica state, ClickHouse replicas, Redpanda brokers, Redis/Valkey Sentinel, SeaweedFS, and Temporal before starting the app hosts. A passing container health check alone does not prove cluster consistency.
4. Start app-1 and app-2 in the isolated network. Check login, project access, event ingestion, user identification, query results, dashboards, and any enabled replay/flags features. Compare event IDs and dashboard/query results with the pre-backup record.
5. Record time-to-service, data checks, failed steps, and the actual recovery point. Destroy the isolated CVMs when the evidence is saved. Retain or delete restored disks/snapshots intentionally.

No restore drill has run yet. PostgreSQL WAL/PITR, ClickHouse logical backups, Kafka replay guarantees, and automatic snapshot scheduling remain future work before claiming a production backup objective.

## Monitor the deployment

Use Tencent Cloud Monitor for the CVM, CBS, and CLB infrastructure signals; it is separate from the PostHog product/data services. Install the Tencent CVM monitoring agent on all five hosts using the [official Linux instructions](https://cloud.tencent.com/document/product/248/6211). Confirm the agent appears online before relying on CPU, memory, and disk alerts. Tencent notes that CVM metrics depend on the agent and that TCP 80 must be allowed from the guest for metric reporting.

Create alert policies for:

| Scope | Watch | Initial response |
| --- | --- | --- |
| CVM | CPU, load, memory, filesystem usage, disk I/O, network throughput, and missing agent data | Investigate sustained saturation; warn before a filesystem reaches 80%, critical at 90%, and expand or clean up before the data disk fills. Tune thresholds after observing the test workload. |
| CBS | Disk health, read/write latency, IOPS/throughput near provisioned limits, and available capacity | Investigate rising latency or a disk nearing capacity; database health may degrade before disk usage reaches 100%. |
| CLB | Listener availability and unhealthy backend count | Alert when either app backend is unhealthy. Use HTTP path `/_readyz?role=web` and expected status 200; verify the health-check source range in the CVM security group. |
| PostHog containers | Local Compose state and Docker health | On each CVM, run the following command after deployment, after upgrades, and during an incident. Nonzero means at least one container is absent, stopped, starting, or unhealthy. |

```bash
python3 -m tools.self_managed_deploy.operations status --compose "$HOME/.config/posthog-deploy/app-1.json"
python3 -m tools.self_managed_deploy.operations status --compose "$HOME/.config/posthog-deploy/data-1.json"
```

Run `status` on app-2, data-2, and data-3 as well. For each data host, also inspect the service-specific quorum/replication state before declaring recovery: PostgreSQL/Patroni leader and replicas, ClickHouse replica queue/errors, Redpanda broker and consumer lag, Sentinel primary/quorum, SeaweedFS volume/filer health, and Temporal membership/task queues. These service metrics and notifications are not yet aggregated or automatically alerted by this deployment tool. The initial CVM thresholds are starting points, not measured capacity limits.

When an alert fires, preserve logs and status before restarting a stateful service. Follow the matching service's recovery procedure; do not use a whole-node `docker compose down -v` as a troubleshooting step.
