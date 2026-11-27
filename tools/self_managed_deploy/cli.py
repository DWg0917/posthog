import os
import sys
import json
import argparse
import subprocess
from collections.abc import Sequence
from pathlib import Path

from tools.self_managed_deploy.data_services import render_data_node_compose
from tools.self_managed_deploy.env_config import write_app_env, write_endpoints
from tools.self_managed_deploy.renderer import render_multihost_compose
from tools.self_managed_deploy.topology import render_topology_plan

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Plan host placement or render a PostHog app-only Compose configuration."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    plan_parser = subparsers.add_parser("plan", help="print the logical multi-host service placement plan")
    plan_parser.add_argument("--ingress", choices=("direct", "lb"), required=True)
    data_parser = subparsers.add_parser(
        "render-data-node", help="render a state-service Compose file for one data host"
    )
    data_parser.add_argument("--cluster", required=True, type=Path, help="JSON file with the three data node addresses")
    data_parser.add_argument("--endpoints", required=True, type=Path, help="JSON file with service credentials")
    data_parser.add_argument("--node", required=True, help="name of this data node")
    data_parser.add_argument("--output", required=True, type=Path, help="new output path; existing files are preserved")
    env_parser = subparsers.add_parser("init-env", help="generate a private app environment file for a new deployment")
    env_parser.add_argument("--endpoints", required=True, type=Path, help="JSON file with service credentials")
    env_parser.add_argument("--output", required=True, type=Path, help="new environment file path")
    config_parser = subparsers.add_parser(
        "init-config", help="generate service credentials and endpoint mappings for a new deployment"
    )
    config_parser.add_argument("--cluster", required=True, type=Path, help="JSON file with three data node addresses")
    config_parser.add_argument("--domain", required=True, help="hostname that will serve PostHog")
    config_parser.add_argument(
        "--trusted-proxy-cidr",
        action="append",
        default=[],
        help="CIDR range allowed to set forwarded headers when using a load balancer",
    )
    config_parser.add_argument("--output", required=True, type=Path, help="new endpoint credentials file")
    render_parser = subparsers.add_parser("render", help="render an app-only multi-host Compose file")
    render_parser.add_argument(
        "--endpoints", required=True, type=Path, help="JSON file with external service endpoints"
    )
    render_parser.add_argument("--ingress", choices=("direct", "lb"), required=True)
    render_parser.add_argument("--node", choices=("app-1", "app-2"), required=True)
    render_parser.add_argument(
        "--output", required=True, type=Path, help="new output path; existing files are preserved"
    )
    render_parser.add_argument("--env-file", type=Path, help="Compose interpolation file (defaults to Compose's .env)")
    render_parser.add_argument(
        "--compose-file", type=Path, default=REPOSITORY_ROOT / "docker-compose.hobby.yml", help=argparse.SUPPRESS
    )
    args = parser.parse_args(argv)

    if args.command == "plan":
        sys.stdout.write(json.dumps(render_topology_plan(args.ingress), indent=2) + "\n")
        return 0

    if args.command == "init-config":
        try:
            if args.output.exists():
                sys.stderr.write(f"Refusing to overwrite existing output: {args.output}\n")
                return 2
            cluster = json.loads(args.cluster.read_text())
            if not isinstance(cluster, dict):
                raise ValueError("cluster configuration must be a JSON object")
            write_endpoints(cluster, args.domain, args.output.resolve(), args.trusted_proxy_cidr)
        except FileExistsError:
            sys.stderr.write(f"Refusing to overwrite existing output: {args.output}\n")
            return 2
        except (OSError, json.JSONDecodeError, ValueError) as error:
            sys.stderr.write(f"Unable to generate endpoint configuration: {error}\n")
            return 1

        sys.stdout.write(f"Generated private endpoint credentials at {args.output.resolve()}\n")
        sys.stdout.write("Keep this file private and use the same copy on all deployment hosts.\n")
        return 0

    if args.command == "render-data-node":
        try:
            if args.output.exists():
                sys.stderr.write(f"Refusing to overwrite existing output: {args.output}\n")
                return 2
            cluster = json.loads(args.cluster.read_text())
            if not isinstance(cluster, dict):
                raise ValueError("cluster configuration must be a JSON object")
            endpoints = json.loads(args.endpoints.read_text())
            if not isinstance(endpoints, dict):
                raise ValueError("endpoint configuration must be a JSON object")
            rendered = render_data_node_compose(cluster, args.node, endpoints)
            output = args.output.resolve()
            output.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w") as output_file:
                json.dump(rendered, output_file, indent=2)
                output_file.write("\n")
        except FileExistsError:
            sys.stderr.write(f"Refusing to overwrite existing output: {args.output}\n")
            return 2
        except (OSError, json.JSONDecodeError, ValueError) as error:
            sys.stderr.write(f"Unable to render data-node Compose configuration: {error}\n")
            return 1

        sys.stdout.write(f"Rendered data-node Compose configuration to {output}\n")
        sys.stdout.write("This command did not start or deploy any containers.\n")
        return 0

    if args.command == "init-env":
        try:
            if args.output.exists():
                sys.stderr.write(f"Refusing to overwrite existing output: {args.output}\n")
                return 2
            endpoints = json.loads(args.endpoints.read_text())
            if not isinstance(endpoints, dict):
                raise ValueError("endpoint configuration must be a JSON object")
            revision = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=REPOSITORY_ROOT,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            write_app_env(endpoints, revision, args.output.resolve())
        except FileExistsError:
            sys.stderr.write(f"Refusing to overwrite existing output: {args.output}\n")
            return 2
        except (OSError, json.JSONDecodeError, ValueError, subprocess.CalledProcessError) as error:
            sys.stderr.write(f"Unable to generate app environment file: {error}\n")
            return 1

        sys.stdout.write(f"Generated private app environment file at {args.output.resolve()}\n")
        sys.stdout.write("Keep this file private and copy the same file to both app nodes.\n")
        return 0

    try:
        if args.output.exists():
            sys.stderr.write(f"Refusing to overwrite existing output: {args.output}\n")
            return 2
        endpoints = json.loads(args.endpoints.read_text())
        if not isinstance(endpoints, dict):
            raise ValueError("endpoint configuration must be a JSON object")
        compose_command = ["docker", "compose"]
        if args.env_file:
            compose_command.extend(["--env-file", str(args.env_file.resolve())])
        compose_command.extend(["-f", str(args.compose_file.resolve()), "config", "--format", "json"])
        resolved = subprocess.run(
            compose_command,
            cwd=REPOSITORY_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        compose_config = json.loads(resolved.stdout)
        rendered = render_multihost_compose(
            compose_config,
            endpoints,
            args.ingress,
            node_name=args.node,
            build_context=str(REPOSITORY_ROOT),
        )
        output = args.output.resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as output_file:
            json.dump(rendered, output_file, indent=2)
            output_file.write("\n")
    except FileExistsError:
        sys.stderr.write(f"Refusing to overwrite existing output: {args.output}\n")
        return 2
    except (OSError, json.JSONDecodeError, ValueError, subprocess.CalledProcessError) as error:
        sys.stderr.write(f"Unable to render Compose configuration: {error}\n")
        if isinstance(error, subprocess.CalledProcessError) and error.stderr:
            sys.stderr.write(f"{error.stderr.strip()}\n")
        return 1

    sys.stdout.write(f"Rendered {args.node} app Compose configuration to {output}\n")
    sys.stdout.write("This command did not start or deploy any containers.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
