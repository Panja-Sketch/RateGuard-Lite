"""Socket guard: any non-loopback outbound connection raises (spec 11.1)."""

from __future__ import annotations

import ipaddress
import socket
from typing import Any

from ..errors import NetworkBlockedError

_orig_connect = socket.socket.connect
_orig_connect_ex = socket.socket.connect_ex
_orig_create = socket.create_connection
_orig_getaddrinfo = socket.getaddrinfo
_installed = False


def _is_loopback(host: Any) -> bool:
    if isinstance(host, bytes):
        host = host.decode(errors="ignore")
    if host in ("localhost", "", None):
        return True
    try:
        return ipaddress.ip_address(str(host).split("%")[0]).is_loopback
    except ValueError:
        return False


def _check(address: Any) -> None:
    if isinstance(address, tuple) and address:
        host = address[0]
    else:
        return  # AF_UNIX etc. are local
    if not _is_loopback(host):
        raise NetworkBlockedError(
            f"Blocked outbound network connection to {host!r}.",
            where="RateGuard-Lite offline guard",
            next_step="This tool never uses the network; remove the code that attempted the connection.",
        )


def install_socket_guard() -> None:
    global _installed
    if _installed:
        return

    def connect(self: socket.socket, address: Any) -> None:
        _check(address)
        return _orig_connect(self, address)

    def connect_ex(self: socket.socket, address: Any) -> int:
        _check(address)
        return _orig_connect_ex(self, address)

    def create_connection(address: Any, *a: Any, **k: Any) -> socket.socket:
        _check(address)
        return _orig_create(address, *a, **k)

    def getaddrinfo(host: Any, *a: Any, **k: Any) -> Any:
        if not _is_loopback(host):
            # Name resolution of external hosts is the first step of any outbound call.
            _check((host, 0))
        return _orig_getaddrinfo(host, *a, **k)

    setattr(socket.socket, "connect", connect)  # noqa: B010
    setattr(socket.socket, "connect_ex", connect_ex)  # noqa: B010
    setattr(socket, "create_connection", create_connection)  # noqa: B010
    setattr(socket, "getaddrinfo", getaddrinfo)  # noqa: B010
    _installed = True


def uninstall_socket_guard() -> None:
    global _installed
    setattr(socket.socket, "connect", _orig_connect)  # noqa: B010
    setattr(socket.socket, "connect_ex", _orig_connect_ex)  # noqa: B010
    setattr(socket, "create_connection", _orig_create)  # noqa: B010
    setattr(socket, "getaddrinfo", _orig_getaddrinfo)  # noqa: B010
    _installed = False
