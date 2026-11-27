import sys
import json
import argparse
import subprocess
from collections.abc import Sequence
from pathlib import Path

MIGRATION_SERVICES = {
    "asyncmigrationscheck",
    "kafka-init",
    "personhog-etcd-init",
    "personhog-tables-ready",
    "web",
}


def run_operation(
    action: str, compose_file: Path, scheduler: bool = False, temporal: bool = False, confirm: bool = False
) -> int:
    if action in {"migrate", "temporal-init", "temporal-namespace-init"} and not confirm:
        if action == "temporal-namespace-init":
            sys.stderr.write(
                "Temporal namespace setup changes shared state. Rerun with --yes after reviewing the target.\n"
            )
        else:
            sys.stderr.write(
                "Database schema setup changes shared schemas. Rerun with --yes after reviewing the target.\n"
            )
        return 2

    compose = ["docker", "compose"]
    if action == "preflight":
        for profile in ("init", "temporal", "scheduler"):
            compose.extend(["--profile", profile])
    elif action in {"migrate", "temporal-init", "temporal-namespace-init"}:
        compose.extend(["--profile", "init"])
    if action == "up":
        if scheduler:
            compose.extend(["--profile", "scheduler"])
        if temporal:
            compose.extend(["--profile", "temporal"])
    compose.extend(["-f", str(compose_file.resolve())])
    try:
        resolved = subprocess.run([*compose, "config", "--format", "json"], check=True, capture_output=True, text=True)
        compose_config = json.loads(resolved.stdout)
    except (json.JSONDecodeError, subprocess.CalledProcessError) as error:
        sys.stderr.write("Unable to resolve the rendered Compose configuration.\n")
        if isinstance(error, subprocess.CalledProcessError) and error.stderr:
            sys.stderr.write(f"{error.stderr.strip()}\n")
        return error.returncode if isinstance(error, subprocess.CalledProcessError) else 1

    services = compose_config.get("services")
    if action == "migrate" and (not isinstance(services, dict) or not MIGRATION_SERVICES <= services.keys()):
        sys.stderr.write("Database migrations and initialization must run from the app-1 Compose configuration.\n")
        return 2
    if action == "temporal-init" and (not isinstance(services, dict) or "temporal-schema-setup" not in services):
        sys.stderr.write("Temporal schema setup must run from the data-1 Compose configuration.\n")
        return 2
    if action == "temporal-namespace-init" and (
        not isinstance(services, dict) or "temporal-namespace-setup" not in services
    ):
        sys.stderr.write("Temporal namespace setup must run from the data-1 Compose configuration.\n")
        return 2

    missing_paths = _missing_local_paths(compose_config)
    if missing_paths:
        sys.stderr.write("Compose references missing local paths:\n")
        for service, path in missing_paths:
            sys.stderr.write(f"  {service}: {path}\n")
        return 2

    commands: list[list[str]] = []

    if action == "preflight":
        pass
    elif action == "status":
        commands.append([*compose, "ps", "--all", "--format", "json"])
    elif action == "temporal-init":
        commands.append([*compose, "run", "--rm", "--no-deps", "temporal-schema-setup"])
    elif action == "temporal-namespace-init":
        commands.append([*compose, "run", "--rm", "--no-deps", "temporal-namespace-setup"])
    elif action == "build":
        commands.append([*compose, "build"])
    elif action == "migrate":
        commands.extend(
            [
                [*compose, "run", "--rm", "asyncmigrationscheck"],
                [*compose, "run", "--rm", "--no-deps", "kafka-init"],
                [*compose, "run", "--rm", "--no-deps", "personhog-etcd-init"],
                [*compose, "run", "--rm", "web", "./bin/migrate"],
                [*compose, "run", "--rm", "--no-deps", "personhog-tables-ready"],
                [*compose, "run", "--rm", "web", "python", "manage.py", "register_temporal_search_attributes"],
            ]
        )
    elif action == "up":
        commands.append([*compose, "up", "--detach", "--build", "--wait", "--wait-timeout", "300"])
    else:
        raise ValueError(f"unsupported operation: {action}")

    try:
        for command in commands:
            if action == "status":
                result = subprocess.run(command, check=True, capture_output=True, text=True)
                return _report_status(result.stdout)
            subprocess.run(command, check=True)
    except subprocess.CalledProcessError as error:
        sys.stderr.write(f"Docker Compose operation failed with exit code {error.returncode}.\n")
        return error.returncode or 1
    return 0


def _report_status(output: str) -> int:
    try:
        containers = [json.loads(line) for line in output.splitlines() if line.strip()]
    except json.JSONDecodeError:
        sys.stderr.write("Unable to parse Docker Compose container status output.\n")
        return 1

    if not containers:
        sys.stderr.write("No Compose containers were found for this node.\n")
        return 1

    failed = False
    for container in sorted(containers, key=lambda item: str(item.get("Service", ""))):
        service = str(container.get("Service", "<unknown>"))
        state = str(container.get("State", "unknown")).lower()
        health = str(container.get("Health", "")).lower()
        suffix = f" ({health})" if health else ""
        stream = sys.stdout if state == "running" and health != "unhealthy" else sys.stderr
        stream.write(f"{service}: {state}{suffix}\n")
        if state != "running" or health == "unhealthy" or health == "starting":
            failed = True
    return 1 if failed else 0


def _missing_local_paths(compose_config: dict[str, object]) -> list[tuple[str, str]]:
    services = compose_config.get("services")
    if not isinstance(services, dict):
        return [("<compose>", "services mapping is missing")]

    missing: list[tuple[str, str]] = []
    for service_name, service in services.items():
        if not isinstance(service, dict):
            continue
        build = service.get("build")
        if isinstance(build, dict):
            context = Path(str(build.get("context", "")))
            if not context.is_dir():
                missing.append((service_name, str(context)))
            else:
                dockerfile = Path(str(build.get("dockerfile", "Dockerfile")))
                dockerfile_path = dockerfile if dockerfile.is_absolute() else context / dockerfile
                if not dockerfile_path.is_file():
                    missing.append((service_name, str(dockerfile_path)))

        volumes = service.get("volumes")
        if isinstance(volumes, list):
            for volume in volumes:
                if not isinstance(volume, dict) or volume.get("type") != "bind":
                    continue
                source = Path(str(volume.get("source", "")))
                if not source.exists():
                    missing.append((service_name, str(source)))
    return missing


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build and run a self-managed PostHog Compose stack.")
    parser.add_argument(
        "action",
        choices=("preflight", "build", "migrate", "temporal-init", "temporal-namespace-init", "up", "status"),
    )
    parser.add_argument("--compose", required=True, type=Path, help="rendered Compose JSON file")
    parser.add_argument(
        "--scheduler",
        action="store_true",
        help="start the standby Celery beat service for manual scheduler failover",
    )
    parser.add_argument("--temporal", action="store_true", help="start the Temporal server profile")
    parser.add_argument("--yes", action="store_true", help="confirm the migration action")
    args = parser.parse_args(argv)
    return run_operation(args.action, args.compose, scheduler=args.scheduler, temporal=args.temporal, confirm=args.yes)


if __name__ == "__main__":
    raise SystemExit(main())
