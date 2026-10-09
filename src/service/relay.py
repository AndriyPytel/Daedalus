"""
The gateway's `DAEDALUS_WORKER_ARGV`: `python -m service.relay HOST:PORT` connects to a worker host and pipes its own stdin to the socket and the socket to its stdout.
Closing its stdin half-closes the socket so the worker ends; it exits when the host closes the connection, and if it is killed the closed socket is what ends the worker.
"""
import os
import socket
import sys
import threading

CHUNK = 65536


def main(address):
    host, _, port = address.rpartition(":")
    conn = socket.create_connection((host, int(port)))

    def feed():
        try:
            while data := os.read(0, CHUNK):
                conn.sendall(data)
            conn.shutdown(socket.SHUT_WR)
        except OSError:
            pass  # except-pass-allowed: the host closed first and the loop below ends

    threading.Thread(target=feed, daemon=True).start()
    try:
        while data := conn.recv(CHUNK):
            sys.stdout.buffer.write(data)
            sys.stdout.buffer.flush()
    except OSError:
        pass  # except-pass-allowed: the host or the reader of stdout is gone


if __name__ == "__main__":
    main(sys.argv[1])
