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

    A port that `follows` another keeps its distance from it (Valheim: query = game + 1), so the
    whole group moves together when the default is busy.
    `taken` holds ports already given to other servers, which may be stopped and so not bound.
    """
    is_free = is_free or is_port_free
    taken = set(taken)
    result: dict[str, int] = {}
    by_name = {p.name: p for p in ports}

    def usable(port: int, protocol: str) -> bool:
        return 0 < port < 65536 and (port, protocol) not in taken and is_free(port, protocol)

    for leader in (p for p in ports if not p.follows):
        group = [leader] + [p for p in ports if _root(p, by_name) is leader and p is not leader]
        for base in range(leader.default_host, min(leader.default_host + PORT_SEARCH_LIMIT, 65536)):
            chosen = {p.name: base + (p.default_host - leader.default_host) for p in group}
            if all(usable(chosen[p.name], p.protocol) for p in group):
                result.update(chosen)
                taken.update((chosen[p.name], p.protocol) for p in group)
                break
        else:
            raise RuntimeError(f"no free {leader.protocol} port near {leader.default_host}")
    return result


def _root(port: Port, by_name: dict[str, Port]) -> Port:
    while port.follows:
        port = by_name[port.follows]
    return port
