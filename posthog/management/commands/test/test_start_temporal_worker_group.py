import pytest
from django.core.management.base import CommandError

from posthog.management.commands.start_temporal_worker_group import parse_task_queues, validate_metrics_port_range


def test_parse_task_queues_trims_and_ignores_empty_items() -> None:
    assert parse_task_queues(" queue-a, queue-b,, ") == ["queue-a", "queue-b"]


@pytest.mark.parametrize("value", ["", " , ", "queue-a,queue-a"])
def test_parse_task_queues_rejects_empty_or_duplicate_queues(value: str) -> None:
    with pytest.raises(CommandError):
        parse_task_queues(value)


@pytest.mark.parametrize("start,count", [(1, 1), (8001, 5), (65535, 1)])
def test_validate_metrics_port_range_accepts_valid_ranges(start: int, count: int) -> None:
    validate_metrics_port_range(start, count)


@pytest.mark.parametrize("start,count", [(0, 1), (8001, 0), (65535, 2)])
def test_validate_metrics_port_range_rejects_invalid_ranges(start: int, count: int) -> None:
    with pytest.raises(CommandError):
        validate_metrics_port_range(start, count)
