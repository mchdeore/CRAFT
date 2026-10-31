"""Refuse outbound contact from user Python except the active CRAFT bridge.

This is a second layer behind the workspace network policy, not a sandbox: an
audit hook sees Python-level sockets and process launches only. The image loads
this module at interpreter start and installs the hook when ``CRAFT_GUARD=1``,
which the kernel specification and terminal shells set; the Jupyter server
itself runs without it. Keep this module free of heavy imports.
"""

import ipaddress
import os
import socket
import sys
import threading
from time import monotonic

_BLOCKED_PROCESS_EVENTS = frozenset(
    {
        "subprocess.Popen",
        "os.system",
        "os.exec",
        "os.posix_spawn",
        "os.spawn",
        "os.fork",
        "os.forkpty",
        "pty.spawn",
    }
)
_SOCKET_EVENTS = frozenset(
    {"socket.connect", "socket.connect_ex", "socket.sendto", "socket.sendmsg"}
)
_NAME_EVENTS = frozenset({"socket.getaddrinfo", "socket.gethostbyname", "socket.gethostbyname_ex"})

# A refused connection on the API's port looks the API host up again, at most this often: the
# kernel outlives the address it resolved at start (the API's Service can move).
REFRESH_SECONDS = 30

MESSAGE = (
    "Outbound access is blocked in the CRAFT workspace. "
    "Use the craft access points, such as craft.search or craft.cube.find."
)

_lock = threading.Lock()
_local = threading.local()
_installed = False


class _Endpoint:
    __slots__ = ("addresses", "host", "port", "resolved")

    def __init__(self, host: str, port: int, addresses: frozenset[str]):
        self.host, self.port, self.addresses = host, port, addresses
        self.resolved = monotonic()


# Named endpoints: "api" from CRAFT_API_URL and "bridge" during a bound agent run.
_endpoints: dict[str, _Endpoint] = {}


class OutboundBlocked(PermissionError):
    """Raised for a refused connection, name lookup or process launch."""


def _address_allowed(address: object) -> bool:
    if not isinstance(address, tuple) or len(address) < 2:
        return False  # Unix sockets and unknown address forms
    host, port = str(address[0]), address[1]
    if any(port == e.port and host in e.addresses for e in list(_endpoints.values())):
        return True
    api = _endpoints.get("api")
    if api is None or port != api.port or monotonic() - api.resolved < REFRESH_SECONDS:
        return False
    allow("api", api.host, api.port)
    refreshed = _endpoints.get("api")  # revoked meanwhile: None
    return refreshed is not None and host in refreshed.addresses


def _name_allowed(host: object) -> bool:
    if isinstance(host, bytes):
        host = host.decode(errors="replace")
    return host is not None and any(
        host == e.host or str(host) in e.addresses for e in list(_endpoints.values())
    )


def _hook(event: str, args: tuple) -> None:
    if getattr(_local, "bypass", False):
        return
    if event in _SOCKET_EVENTS:
        address = args[1] if len(args) > 1 else None
        if address is None and event in {"socket.sendto", "socket.sendmsg"}:
            return  # sending on an already connected socket
        if not _address_allowed(address):
            raise OutboundBlocked(MESSAGE)
    elif event in _NAME_EVENTS:
        if not _name_allowed(args[0] if args else None):
            raise OutboundBlocked(MESSAGE)
    elif event in _BLOCKED_PROCESS_EVENTS:
        raise OutboundBlocked(
            "Starting other programs is blocked in the CRAFT workspace kernel. "
            "Use Python libraries in the image and the craft access points."
        )


def install() -> None:
    """Install the audit hook once; it cannot be removed for this process."""
    global _installed
    with _lock:
        if not _installed:
            sys.addaudithook(_hook)
            _installed = True


def installed() -> bool:
    return _installed


def allow(name: str, host: str, port: int) -> None:
    """Permit one named endpoint, resolving its host name now."""
    addresses: set[str] = set()
    try:
        ipaddress.ip_address(host)
        addresses.add(host)
    except ValueError:
        _local.bypass = True
        try:
            for *_, sockaddr in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM):
                addresses.add(str(sockaddr[0]))
        except OSError:
            pass
        finally:
            _local.bypass = False
    with _lock:
        _endpoints[name] = _Endpoint(host, port, frozenset(addresses))


def revoke(name: str) -> None:
    with _lock:
        _endpoints.pop(name, None)


def allow_bridge(host: str, port: int) -> None:
    """Permit the one root-provisioned bridge endpoint for the active run."""
    allow("bridge", host, port)


def revoke_bridge() -> None:
    revoke("bridge")


def _allow_api() -> None:
    from urllib.parse import urlsplit

    target = urlsplit(os.environ.get("CRAFT_API_URL", ""))
    if target.scheme in {"http", "https"} and target.hostname:
        allow("api", target.hostname, target.port or (443 if target.scheme == "https" else 80))


if os.environ.get("CRAFT_GUARD") == "1":
    install()
    _allow_api()
