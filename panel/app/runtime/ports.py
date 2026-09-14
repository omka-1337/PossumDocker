import socket

from app.games.schema import Port

PORT_SEARCH_LIMIT = 100


def is_port_free(port: int, protocol: str) -> bool:
    kind = socket.SOCK_STREAM if protocol == "tcp" else socket.SOCK_DGRAM
    with socket.socket(socket.AF_INET, kind) as sock:
        try:
            sock.bind(("0.0.0.0", port))
        except OSError:
            return False
    return True


def allocate_ports(
    ports: list[Port],
    taken: set[tuple[int, str]],
    is_free=None,
) -> dict[str, int]:
    """Pick a host port for each template port: the default one, or the next free one after it.

    `taken` holds ports already given to other servers, which may be stopped and so not bound.
    """
    is_free = is_free or is_port_free
    taken = set(taken)
    result: dict[str, int] = {}
    for port in ports:
        for candidate in range(port.default_host, min(port.default_host + PORT_SEARCH_LIMIT, 65536)):
            if (candidate, port.protocol) not in taken and is_free(candidate, port.protocol):
                result[port.name] = candidate
                taken.add((candidate, port.protocol))
                break
        else:
            raise RuntimeError(f"no free {port.protocol} port near {port.default_host}")
    return result
