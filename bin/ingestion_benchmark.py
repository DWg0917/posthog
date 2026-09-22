#!/usr/bin/env python3
import argparse
import concurrent.futures
import datetime
import http.client
import json
import math
import pathlib
import queue
import socket
import ssl
import subprocess
import threading
import time
import uuid
from urllib.parse import urlsplit


def run_command(command: list[str], timeout: int = 30) -> str:
    return subprocess.check_output(command, text=True, stderr=subprocess.STDOUT, timeout=timeout).strip()


def clickhouse(sql: str) -> str:
    return run_command(['sudo', '-n', 'docker', 'exec', 'posthog-clickhouse-1', 'clickhouse-client', '--query', sql])


def percentile(values: list[float], percent: int) -> float:
    return sorted(values)[max(0, math.ceil(len(values) * percent / 100) - 1)] if values else 0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', default='https://dwg.asia/batch/')
    parser.add_argument('--connect-address')
    parser.add_argument('--rate', type=int, default=500)
    parser.add_argument('--seconds', type=int, default=60)
    parser.add_argument('--workers', type=int, default=20)
    parser.add_argument('--batch', type=int, default=100)
    parser.add_argument('--payload-bytes', type=int, default=512)
    parser.add_argument('--team-id', type=int, default=1)
    parser.add_argument('--users', type=int, default=10000)
    parser.add_argument('--person-processing', action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument('--drain-seconds', type=int, default=120)
    parser.add_argument('--output', type=pathlib.Path, required=True)
    args = parser.parse_args()
    if min(args.rate, args.seconds, args.batch, args.workers, args.users) <= 0:
        parser.error('Rate, duration, batch size, workers and users must be positive.')
    if args.rate * args.seconds > 500000:
        parser.error('One run is limited to 500000 events.')
    target = urlsplit(args.url)
    if target.scheme != 'https':
        parser.error('Use HTTPS with certificate verification.')
    token = run_command(['sudo', '-n', 'docker', 'exec', 'posthog-db-1', 'psql', '-U', 'posthog', '-d', 'posthog', '-Atc',
                         f'SELECT api_token FROM posthog_team WHERE id={args.team_id}'])
    if not token:
        raise SystemExit('Project token was empty.')
    run_id = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S') + '_' + uuid.uuid4().hex[:8]
    event = 'perf_test_' + run_id
    tls = ssl.create_default_context()
    jobs: queue.Queue = queue.Queue(maxsize=args.workers)
    stop = threading.Event()
    results: list[dict] = []
    snapshots: list[dict] = []
    lock = threading.Lock()
    output_lock = threading.Lock()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    log = args.output.open('x')

    def emit(data: dict) -> None:
        with output_lock:
            line = json.dumps(data, ensure_ascii=False)
            if data.get('kind') != 'monitor':
                print(line, flush=True)
            log.write(line + '\n')
            log.flush()

    def monitor() -> None:
        while not stop.is_set():
            sample: dict = {'kind': 'monitor', 'time': time.time()}
            try:
                sample['docker'] = run_command(['sudo', '-n', 'docker', 'stats', '--no-stream', '--format', '{{json .}}'])
                sample['groups'] = {
                    group: run_command(['sudo', '-n', 'docker', 'exec', 'posthog-kafka-1', 'rpk', 'group', 'describe', group])
                    for group in ['clickhouse-ingestion', 'group1', 'clickhouse_events_json_native_json']
                }
                sample['landed'] = clickhouse(
                    f"SELECT count(), uniqExact(uuid) FROM posthog.events WHERE team_id={args.team_id} AND event='{event}'"
                )
            except Exception as error:
                sample['error'] = str(error)[:300]
            snapshots.append(sample)
            emit(sample)
            stop.wait(5)

    def worker() -> None:
        connection = http.client.HTTPSConnection(target.hostname, target.port or 443, timeout=15, context=tls)
        if args.connect_address:
            def connect_to_address(address: tuple, timeout: float, source_address: tuple | None = None) -> socket.socket:
                return socket.create_connection((args.connect_address, address[1]), timeout, source_address)
            connection._create_connection = connect_to_address
        while True:
            job = jobs.get()
            if job is None:
                jobs.task_done()
                break
            first, size, scheduled = job
            timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
            batch = [{
                'uuid': str(uuid.uuid5(uuid.NAMESPACE_URL, f'{run_id}:{number}')),
                'event': event, 'timestamp': timestamp,
                'properties': {
                    'distinct_id': f'perf_{run_id}_user_{number % args.users}',
                    'perf_test': True, 'perf_run_id': run_id, 'sequence': number,
                    '$process_person_profile': args.person_processing,
                    'payload': 'x' * args.payload_bytes,
                },
            } for number in range(first, first + size)]
            body = json.dumps({'api_key': token, 'batch': batch}, separators=(',', ':')).encode()
            started = time.monotonic()
            code = 0
            error = ''
            try:
                connection.request('POST', target.path or '/batch/', body=body, headers={'Content-Type': 'application/json'})
                response = connection.getresponse()
                reply = response.read()
                code = response.status
                success = 200 <= code < 300 and (json.loads(reply).get('status') in ('Ok', 'ok', 1))
                if not success:
                    error = reply.decode(errors='replace')[:200]
            except Exception as exception:
                success = False
                error = f'{type(exception).__name__}: {exception}'
                connection.close()
            ended = time.monotonic()
            result = {'first': first, 'size': size, 'code': code, 'success': success,
                      'latency_ms': (ended - started) * 1000,
                      'scheduled_latency_ms': (ended - scheduled) * 1000,
                      'request_bytes': len(body), 'error': error}
            with lock:
                results.append(result)
            jobs.task_done()
        connection.close()

    emit({'kind': 'start', 'run_id': run_id, 'event': event, 'rate': args.rate,
          'seconds': args.seconds, 'workers': args.workers, 'batch': args.batch,
          'payload_bytes': args.payload_bytes, 'users': args.users,
          'person_processing': args.person_processing, 'url': args.url,
          'resolved': socket.gethostbyname(target.hostname), 'connect_address': args.connect_address})
    monitoring = threading.Thread(target=monitor, daemon=True)
    monitoring.start()
    threads = [threading.Thread(target=worker) for _ in range(args.workers)]
    for thread in threads:
        thread.start()
    started = time.monotonic()
    scheduled_count = args.rate * args.seconds
    skipped = 0
    aborted = False
    for first in range(0, scheduled_count, args.batch):
        due = started + first / args.rate
        time.sleep(max(0, due - time.monotonic()))
        with lock:
            recent = results[-20:]
        if len(recent) >= 20 and sum(not item['success'] for item in recent) > 2:
            aborted = True
            emit({'kind': 'abort', 'reason': 'More than 10% failures in last 20 completed requests'})
            break
        size = min(args.batch, scheduled_count - first)
        try:
            jobs.put_nowait((first, size, due))
        except queue.Full:
            skipped += size
    for thread in threads:
        jobs.put(None)
    for thread in threads:
        thread.join()
    finished = time.monotonic()
    attempted = sum(result['size'] for result in results)
    accepted = sum(result['size'] for result in results if result['success'])
    deadline = time.monotonic() + args.drain_seconds
    landed = 0
    unique = 0
    while True:
        landed, unique = map(int, clickhouse(
            f"SELECT count(), uniqExact(uuid) FROM posthog.events WHERE team_id={args.team_id} AND event='{event}'"
        ).split('\t'))
        if unique >= attempted or time.monotonic() >= deadline:
            break
        time.sleep(5)
    native_rows, native_unique = map(int, clickhouse(
        f"SELECT count(), uniqExact(uuid) FROM posthog.events_json WHERE team_id={args.team_id} AND event='{event}'"
    ).split('\t'))
    stop.set()
    monitoring.join(timeout=40)
    latencies = [result['latency_ms'] for result in results]
    report = {'kind': 'result', 'run_id': run_id, 'event': event, 'target_eps': args.rate,
              'workers': args.workers, 'attempted_events': attempted, 'http_accepted_events': accepted,
              'http_failed_requests': sum(not result['success'] for result in results),
              'skipped_events': skipped, 'aborted': aborted, 'send_elapsed_s': finished - started,
              'http_accepted_eps': accepted / (finished - started),
              'landed_rows': landed, 'landed_unique': unique,
              'native_rows': native_rows, 'native_unique': native_unique,
              'drain_s': time.monotonic() - finished,
              'p50_ms': percentile(latencies, 50), 'p95_ms': percentile(latencies, 95),
              'p99_ms': percentile(latencies, 99),
              'p95_scheduled_ms': percentile([result['scheduled_latency_ms'] for result in results], 95),
              'request_bytes': sum(result['request_bytes'] for result in results),
              'errors': [result for result in results if not result['success']][:5]}
    emit(report)
    log.close()
    if aborted or accepted != attempted or unique != attempted or native_unique != attempted or skipped:
        raise SystemExit(2)


if __name__ == '__main__':
    main()
