"""
The worker-host supervisor: it listens on TCP and runs one worker, in its own process group, per connection, carrying the connection's bytes to the worker's stdin and its stdout back.
When the peer closes or dies the worker gets end of input and, if it is still running after `GRACE` seconds, its whole group is killed; a worker that exits closes the connection.
It reads `DAEDALUS_HOST_PORT` (7000) and `DAEDALUS_HOST_ARGV`, a JSON list that defaults to `python -m service.worker`, and `--check` exits 0 when this port is listening, without starting a worker.
"""
import json
import os
import signal
import socket
import subprocess
import sys
import threading

PORT_VARIABLE = "DAEDALUS_HOST_PORT"
ARGV_VARIABLE = "DAEDALUS_HOST_ARGV"
GRACE = 5.0
CHUNK = 65536


def _kill_group(process):
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass  # except-pass-allowed: nothing of the group is left; macOS refuses a group whose leader is a zombie


def _serve(conn, argv, grace):
    process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, start_new_session=True)

    def feed():
        try:
            while data := conn.recv(CHUNK):
                process.stdin.write(data)
                process.stdin.flush()
        except OSError:
            pass  # except-pass-allowed: the peer or the worker is gone and the group is ended below
        try:
            process.stdin.close()
            process.wait(grace)
        except (OSError, subprocess.TimeoutExpired):
            pass  # except-pass-allowed: the group kill below ends it
        _kill_group(process)

    def reap():
        process.wait()
        _kill_group(process)

    threading.Thread(target=feed, daemon=True).start()
    threading.Thread(target=reap, daemon=True).start()
    try:
        while data := os.read(process.stdout.fileno(), CHUNK):
            conn.sendall(data)
    except OSError:
        pass  # except-pass-allowed: the peer is gone and the group is ended below
    _kill_group(process)
    process.wait()
    try:
        conn.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass  # except-pass-allowed: the peer closed first
    conn.close()


def listen(port, argv, grace=GRACE, address="0.0.0.0"):
    """The listening socket, already accepting on a daemon thread: closing it stops new workers and leaves running ones."""
    server = socket.socket()
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((address, port))
    server.listen()

    def accept():
        while True:
            try:
                conn, _ = server.accept()
            except OSError:
                return
            conn.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            threading.Thread(target=_serve, args=(conn, argv, grace), daemon=True).start()

    threading.Thread(target=accept, daemon=True).start()
    return server


def listening(port):
    """True when this machine has a TCP socket in LISTEN on *port*, read from the kernel so that asking starts nothing."""
    suffix = ":%04X" % port
    for table in ("/proc/net/tcp", "/proc/net/tcp6"):
        if os.path.exists(table):
            with open(table, encoding="utf-8") as handle:
                rows = [line.split() for line in handle.read().splitlines()[1:]]
            if any(row[3] == "0A" and row[1].endswith(suffix) for row in rows):
                return True
    return False


def main(arguments):
    port = int(os.environ.get(PORT_VARIABLE, "7000"))
    if arguments == ["--check"]:
        return 0 if listening(port) else 1
    argv = json.loads(os.environ.get(ARGV_VARIABLE, "null")) or [sys.executable, "-m", "service.worker"]
    listen(port, argv)
    threading.Event().wait()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
