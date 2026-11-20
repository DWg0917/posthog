import os
import sys
import json
import argparse
import subprocess
from collections.abc import Sequence
from pathlib import Path

from tools.self_managed_deploy.renderer import render_multihost_compose

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render a PostHog app-only Compose configuration.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    render_parser = subparsers.add_parser("render", help="render an app-only multi-host Compose file")
    render_parser.add_argument(
        "--endpoints", required=True, type=Path, help="JSON file with external service endpoints"
    )
    render_parser.add_argument("--ingress", choices=("direct", "lb"), required=True)
    render_parser.add_argument(
        "--output", required=True, type=Path, help="new output path; existing files are preserved"
    )
    render_parser.add_argument("--env-file", type=Path, help="Compose interpolation file (defaults to Compose's .env)")
    render_parser.add_argument(
        "--compose-file", type=Path, default=REPOSITORY_ROOT / "docker-compose.hobby.yml", help=argparse.SUPPRESS
    )
    args = parser.parse_args(argv)

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
        rendered = render_multihost_compose(compose_config, endpoints, args.ingress, build_context=str(REPOSITORY_ROOT))
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

    sys.stdout.write(f"Rendered app-only Compose configuration to {output}\n")
    sys.stdout.write("This command did not start or deploy any containers.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
