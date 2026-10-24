import json
import secrets
import select
import socket
import struct
import threading
import time
from datetime import datetime, timezone

from tcp_stats import read_tcp_stats


PARAM_EXCHANGE = 9
CREATE_STREAMS = 10
TEST_START = 1
TEST_RUNNING = 2
TEST_END = 4
EXCHANGE_RESULTS = 13
DISPLAY_RESULTS = 14
IPERF_DONE = 16
ACCESS_DENIED = -1
SERVER_ERROR = -2
SERVER_TERMINATE = 11


class TransferError(Exception):
    pass


def read_exact(connection, length):
    chunks = []
    while length:
        chunk = connection.recv(length)
        if not chunk:
            raise TransferError("connection closed before the protocol message completed")
        chunks.append(chunk)
        length -= len(chunk)
    return b"".join(chunks)


def read_state(control):
    state = struct.unpack("b", read_exact(control, 1))[0]
    if state == ACCESS_DENIED:
        raise TransferError("server busy or access denied")
    if state == SERVER_TERMINATE:
        raise TransferError("server terminated the test early")
    if state == SERVER_ERROR:
        internal, system = struct.unpack("!II", read_exact(control, 8))
        raise TransferError(f"server error {internal}, system error {system}")
    return state


def expect_state(control, expected):
    actual = read_state(control)
    if actual != expected:
        raise TransferError(f"expected protocol state {expected}, received {actual}")


def send_json(control, value):
    payload = json.dumps(value, separators=(",", ":")).encode()
    control.sendall(struct.pack("!I", len(payload)) + payload)


def read_json(control):
    length = struct.unpack("!I", read_exact(control, 4))[0]
    if not 0 < length <= 2_000_000:
        raise TransferError(f"invalid JSON message size: {length}")
    return json.loads(read_exact(control, length))


def read_server_bytes(control):
    remote = read_json(control)
    streams = remote.get("streams") if isinstance(remote, dict) else None
    if not isinstance(streams, list) or not streams or not isinstance(streams[0], dict):
        raise TransferError("server returned no stream results")
    server_bytes = streams[0].get("bytes")
    if type(server_bytes) is not int or server_bytes <= 0:
        raise TransferError("server reported no received data")
    return server_bytes


def connect_data(host, port, algorithm, timeout):
    last_error = None
    for family, kind, protocol, _, address in socket.getaddrinfo(
        host, port, type=socket.SOCK_STREAM
    ):
        data_socket = socket.socket(family, kind, protocol)
        data_socket.settimeout(timeout)
        try:
            data_socket.setsockopt(
                socket.IPPROTO_TCP, socket.TCP_CONGESTION, algorithm.encode()
            )
            data_socket.connect(address)
            selected = data_socket.getsockopt(
                socket.IPPROTO_TCP, socket.TCP_CONGESTION, 16
            ).split(b"\0", 1)[0].decode()
            if selected != algorithm:
                raise TransferError(f"requested {algorithm}, but the socket uses {selected}")
            return data_socket, selected
        except OSError as error:
            last_error = error
            data_socket.close()
        except TransferError:
            data_socket.close()
            raise
    raise TransferError(f"data connection failed: {last_error}")


def sample(data_socket, previous, started, run_id, host, port, algorithm):
    now = time.monotonic()
    stats = read_tcp_stats(data_socket)
    elapsed = now - previous["clock"]
    acknowledged = stats["bytes_acked"] - previous["bytes_acked"]
    retransmitted = stats["total_retrans_packets"] - previous["total_retrans_packets"]
    if elapsed <= 0 or acknowledged < 0 or retransmitted < 0:
        raise TransferError("invalid TCP counter interval")
    row = {
        "run_id": run_id,
        "destination": host,
        "port": port,
        "algorithm": algorithm,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "time_seconds": now - started,
        "interval_seconds": elapsed,
        "goodput_bps": acknowledged * 8 / elapsed,
        "retransmits_interval": retransmitted,
        **stats,
    }
    return row, {"clock": now, **stats}


def send_data(data_socket, stop, sent, errors, stall_timeout):
    payload = b"0123456789" * 13107 + b"01"
    last_progress = time.monotonic()
    try:
        while not stop.is_set():
            try:
                count = data_socket.send(payload)
            except socket.timeout:
                if time.monotonic() - last_progress >= stall_timeout:
                    raise TransferError("data socket made no progress")
                continue
            if count == 0:
                raise TransferError("data socket closed during transfer")
            sent[0] += count
            last_progress = time.monotonic()
    except OSError as error:
        errors.append(str(error))
        stop.set()
    except TransferError as error:
        errors.append(str(error))
        stop.set()


def run_transfer(host, port, algorithm, duration, interval, run_id, timeout=5):
    cookie = secrets.token_hex(18).encode() + b"\0"
    control = None
    data_socket = None
    stop = threading.Event()
    sender = None
    try:
        control = socket.create_connection((host, port), timeout=timeout)
        control.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        control.settimeout(timeout)
        control.sendall(cookie)

        expect_state(control, PARAM_EXCHANGE)
        send_json(control, {
            "tcp": True,
            "omit": 0,
            "time": duration,
            "num": 0,
            "blockcount": 0,
            "parallel": 1,
            "len": 131072,
        })
        expect_state(control, CREATE_STREAMS)
        data_socket, selected = connect_data(host, port, algorithm, timeout)
        data_socket.sendall(cookie)
        expect_state(control, TEST_START)
        expect_state(control, TEST_RUNNING)

        data_socket.settimeout(0.5)
        sent = [0]
        errors = []
        started_utc = datetime.now(timezone.utc).isoformat()
        started = time.monotonic()
        previous = {"clock": started, **read_tcp_stats(data_socket)}
        samples = []
        sender = threading.Thread(
            target=send_data, args=(data_socket, stop, sent, errors, timeout), daemon=True
        )
        sender.start()

        deadline = started + duration
        next_sample = started + interval
        while time.monotonic() < deadline:
            wait = max(0, min(next_sample, deadline) - time.monotonic())
            readable, _, _ = select.select([control], [], [], wait)
            if readable:
                raise TransferError(f"server sent state {read_state(control)} during transfer")
            if errors:
                raise TransferError(errors[0])
            if time.monotonic() >= next_sample:
                row, previous = sample(
                    data_socket, previous, started, run_id, host, port, selected
                )
                samples.append(row)
                next_sample += interval

        stop.set()
        sender.join(timeout=2)
        if sender.is_alive():
            raise TransferError("sender did not stop after the requested duration")
        if errors:
            raise TransferError(errors[0])
        if time.monotonic() - previous["clock"] >= interval / 4:
            row, previous = sample(data_socket, previous, started, run_id, host, port, selected)
            samples.append(row)

        control.sendall(struct.pack("b", TEST_END))
        expect_state(control, EXCHANGE_RESULTS)
        send_json(control, {
            "cpu_util_total": 0,
            "cpu_util_user": 0,
            "cpu_util_system": 0,
            "sender_has_retransmits": 1,
            "congestion_used": selected,
            "streams": [{
                "id": 1,
                "bytes": sent[0],
                "retransmits": previous["total_retrans_packets"],
                "jitter": 0,
                "errors": 0,
                "omitted_errors": 0,
                "packets": 0,
                "omitted_packets": 0,
                "start_time": 0,
                "end_time": duration,
            }],
        })
        server_bytes = read_server_bytes(control)
        expect_state(control, DISPLAY_RESULTS)
        control.sendall(struct.pack("b", IPERF_DONE))
        if not samples or samples[-1]["bytes_acked"] <= 0:
            raise TransferError("server completed the test without acknowledged data")
        return {
            "run_id": run_id,
            "destination": host,
            "port": port,
            "algorithm": selected,
            "started_utc": started_utc,
            "duration_seconds": duration,
            "sent_bytes": sent[0],
            "server_bytes": server_bytes,
            "samples": samples,
        }
    except (OSError, ValueError) as error:
        raise TransferError(str(error)) from error
    finally:
        stop.set()
        if data_socket is not None:
            data_socket.close()
        if control is not None:
            control.close()
        if sender is not None and sender.is_alive():
            sender.join(timeout=1)
