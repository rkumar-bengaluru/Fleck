"""Loaded by every Python started with this folder on PYTHONPATH (test_local_only.py). With FLECK_SOCKET_GUARD set to a
file path, any connection or name lookup that would leave this Mac is written to that file and refused."""
import os
import socket

_PATH = os.environ.get("FLECK_SOCKET_GUARD")
LOCAL = {"127.0.0.1", "::1", "localhost", "0.0.0.0", "::"}


def _refuse(what: str):
    with open(_PATH, "a") as f:
        f.write(f"{os.getpid()} {what}\n")
    raise OSError(f"socket guard: {what} would leave this Mac")


if _PATH:
    _connect, _connect_ex, _getaddrinfo = socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo

    def _check(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6) and str(address[0]).split("%")[0] not in LOCAL:
            _refuse(f"connect {address!r}")

    def connect(self, address):
        _check(self, address)
        return _connect(self, address)

    def connect_ex(self, address):
        _check(self, address)
        return _connect_ex(self, address)

    def getaddrinfo(host, *a, **kw):
        if host is not None and str(host) not in LOCAL:
            _refuse(f"getaddrinfo {host!r}")
        return _getaddrinfo(host, *a, **kw)

    socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo = connect, connect_ex, getaddrinfo
