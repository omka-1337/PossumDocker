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


class PortError(ValueError):
    def __init__(self, errors: dict[str, str]):
        super().__init__(errors)
        self.errors = errors


def move_ports(
    ports: list[Port], current: dict[str, int], requested: dict[str, int], taken: set[tuple[int, str]]
) -> dict[str, int]:
    """New host ports for a server: `requested` sets the ports others follow, and those that follow
    move along at their usual distance. `taken`: ports of other servers and containers."""
    by_name = {p.name: p for p in ports}
    errors: dict[str, str] = {}
    for name in requested:
        if name not in by_name:
            errors[name] = "no such port"
        elif by_name[name].follows:
            errors[name] = f"moves with {by_name[name].follows}"
    result = dict(current)
    for port in ports:
        root = _root(port, by_name)
        if root.name in requested and root.name not in errors:
            base = requested[root.name]
            result[port.name] = base + (port.default_host - root.default_host)
    for port in ports:
        number = result.get(port.name)
        if port.name in errors or number is None:
            continue
        if not 1 <= number <= 65535:
            errors[port.name] = "must be between 1 and 65535"
        elif number != current.get(port.name) and (number, port.protocol) in taken:
            errors[port.name] = f"{number}/{port.protocol} is used by another server"
    if errors:
        raise PortError(errors)
    return result


def _root(port: Port, by_name: dict[str, Port]) -> Port:
    while port.follows:
        port = by_name[port.follows]
    return port
