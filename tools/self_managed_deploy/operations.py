import sys
import json
import argparse
import subprocess
from collections.abc import Sequence
from pathlib import Path


def run_operation(action: str, compose_file: Path, scheduler: bool = False, confirm: bool = False) -> int:
    if action == "migrate" and not confirm:
        sys.stderr.write("Migration can change shared database schemas. Rerun with --yes after reviewing the target.\n")
        return 2

    compose = ["docker", "compose"]
    if scheduler and action == "up":
        compose.extend(["--profile", "scheduler"])
    compose.extend(["-f", str(compose_file.resolve())])
    try:
        resolved = subprocess.run([*compose, "config", "--format", "json"], check=True, capture_output=True, text=True)
        compose_config = json.loads(resolved.stdout)
    except (json.JSONDecodeError, subprocess.CalledProcessError) as error:
        sys.stderr.write("Unable to resolve the rendered Compose configuration.\n")
        if isinstance(error, subprocess.CalledProcessError) and error.stderr:
            sys.stderr.write(f"{error.stderr.strip()}\n")
        return error.returncode if isinstance(error, subprocess.CalledProcessError) else 1

    missing_paths = _missing_local_paths(compose_config)
    if missing_paths:
        sys.stderr.write("Compose references missing local paths:\n")
        for service, path in missing_paths:
            sys.stderr.write(f"  {service}: {path}\n")
        return 2

    commands: list[list[str]] = []

    if action == "preflight":
        pass
    elif action == "build":
        commands.append([*compose, "build"])
    elif action == "migrate":
        commands.extend(
            [
                [*compose, "run", "--rm", "--no-deps", "asyncmigrationscheck"],
                [*compose, "run", "--rm", "--no-deps", "kafka-init"],
                [*compose, "run", "--rm", "--no-deps", "personhog-etcd-init"],
                [*compose, "run", "--rm", "--no-deps", "web", "./bin/migrate"],
                [*compose, "run", "--rm", "--no-deps", "personhog-tables-ready"],
            ]
        )
    elif action == "up":
        commands.append([*compose, "up", "--detach", "--build", "--wait", "--wait-timeout", "300"])
    else:
        raise ValueError(f"unsupported operation: {action}")

    try:
        for command in commands:
            subprocess.run(command, check=True)
    except subprocess.CalledProcessError as error:
        sys.stderr.write(f"Docker Compose operation failed with exit code {error.returncode}.\n")
        return error.returncode or 1
    return 0


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
    parser.add_argument("action", choices=("preflight", "build", "migrate", "up"))
    parser.add_argument("--compose", required=True, type=Path, help="rendered Compose JSON file")
    parser.add_argument("--scheduler", action="store_true", help="start the dedicated Celery beat service")
    parser.add_argument("--yes", action="store_true", help="confirm the migration action")
    args = parser.parse_args(argv)
    return run_operation(args.action, args.compose, scheduler=args.scheduler, confirm=args.yes)


if __name__ == "__main__":
    raise SystemExit(main())
