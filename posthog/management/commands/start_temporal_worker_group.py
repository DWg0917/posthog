import signal
import subprocess
import sys
import time
from types import FrameType

from django.core.management.base import BaseCommand, CommandError


def parse_task_queues(value: str) -> list[str]:
    task_queues = [queue.strip() for queue in value.split(",") if queue.strip()]
    if not task_queues:
        raise CommandError("at least one task queue is required")
    if len(set(task_queues)) != len(task_queues):
        raise CommandError("task queues must be unique within a group")
    return task_queues


def validate_metrics_port_range(start: int, count: int) -> None:
    if start < 1 or count < 1 or start + count - 1 > 65535:
        raise CommandError("metrics port range is invalid")


class Command(BaseCommand):
    help = "Start one Temporal worker process for each task queue in a group"

    def add_arguments(self, parser):
        parser.add_argument("--task-queues", required=True, help="Comma-separated Temporal task queues")
        parser.add_argument(
            "--metrics-port-start", type=int, default=8001, help="Metrics port for the first queue worker"
        )

    def handle(self, *args, **options):
        task_queues = parse_task_queues(options["task_queues"])
        metrics_port_start = options["metrics_port_start"]
        validate_metrics_port_range(metrics_port_start, len(task_queues))

        processes: list[subprocess.Popen[bytes]] = []
        stopping = False

        def stop_workers(signum: int, frame: FrameType | None) -> None:
            nonlocal stopping
            stopping = True
            for process in processes:
                if process.poll() is None:
                    try:
                        process.send_signal(signal.SIGTERM)
                    except ProcessLookupError:
                        pass

        previous_sigterm = signal.signal(signal.SIGTERM, stop_workers)
        previous_sigint = signal.signal(signal.SIGINT, stop_workers)
        try:
            for index, task_queue in enumerate(task_queues):
                command = [
                    sys.executable,
                    "manage.py",
                    "start_temporal_worker",
                    "--task-queue",
                    task_queue,
                    "--metrics-port",
                    str(metrics_port_start + index),
                ]
                self.stdout.write(f"Starting Temporal worker for {task_queue}")
                processes.append(subprocess.Popen(command))

            while not stopping:
                for process, task_queue in zip(processes, task_queues):
                    return_code = process.poll()
                    if return_code is not None:
                        raise CommandError(f"worker for {task_queue} exited with status {return_code}")
                time.sleep(1)
        finally:
            stop_workers(signal.SIGTERM, None)
            for process in processes:
                try:
                    process.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            signal.signal(signal.SIGTERM, previous_sigterm)
            signal.signal(signal.SIGINT, previous_sigint)
