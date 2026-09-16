import io
import tarfile
import zipfile

import pytest

from app.runtime.docker_files import _parse_stat_line
from app.runtime.files import FileError, resolve, validate_name
from tests.test_servers import create, create_installed


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("", ""), ("/", ""), ("world", "world"), ("world/", "world"), ("/a/./b", "a/b"),
     ("../../etc/passwd", "etc/passwd"), ("a/../../b", "b")],
)  # fmt: skip
def test_resolve_never_leaves_the_root(raw, expected):
    assert resolve(raw) == expected


@pytest.mark.parametrize("name", ["", ".", "..", "a/b", "x\0y", "n" * 256])
def test_invalid_names(name):
    with pytest.raises(FileError):
        validate_name(name)


def test_parse_stat_line_keeps_pipes_in_names():
    entry = _parse_stat_line("regular file|12|1789429763|/data/logs/a|b.log")
    assert (entry.name, entry.type, entry.size) == ("a|b.log", "file", 12)
    assert _parse_stat_line("garbage") is None


@pytest.fixture
def server(client):
    return create_installed(client)


def url(server, suffix=""):
    return f"/api/servers/{server['id']}/files{suffix}"


def listing(client, server, path=""):
    return {e["name"]: e["type"] for e in client.get(url(server), params={"path": path}).json()["entries"]}


def upload(client, server, directory, files: dict[str, bytes]):
    return client.post(
        url(server, "/upload"),
        data={"directory": directory, "paths": list(files)},
        files=[("files", (name.rsplit("/", 1)[-1], data)) for name, data in files.items()],
    )


def test_files_need_an_installed_server(client, runtime):
    runtime.fail_install = True
    server = create(client, "cs16").json()
    assert client.get(url(server)).status_code == 409


def test_upload_folder_tree_and_list(client, server):
    resp = upload(client, server, "", {"server.properties": b"motd=hi\n", "world/region/r.0.0.mca": b"\1\2"})
    assert resp.status_code == 204, resp.text
    assert listing(client, server) == {"world": "dir", "server.properties": "file"}
    assert listing(client, server, "world/region") == {"r.0.0.mca": "file"}
    # Folders first.
    names = [e["name"] for e in client.get(url(server)).json()["entries"]]
    assert names == ["world", "server.properties"]


def test_upload_cannot_escape_the_volume(client, server):
    client.post(url(server, "/mkdir"), json={"path": "sub"})
    # "../" is normalised away: the file lands inside the target, never above the root.
    assert upload(client, server, "sub", {"../../x": b""}).status_code == 204
    assert listing(client, server, "sub") == {"x": "file"}


def test_mkdir_rename_move_copy_delete(client, server):
    upload(client, server, "", {"a.txt": b"A"})
    assert client.post(url(server, "/mkdir"), json={"path": "plugins"}).status_code == 204
    assert client.post(url(server, "/mkdir"), json={"path": "plugins"}).status_code == 409

    assert client.post(url(server, "/rename"), json={"path": "a.txt", "name": "b.txt"}).status_code == 204
    assert client.post(url(server, "/rename"), json={"path": "b.txt", "name": "../evil"}).status_code == 400

    assert (
        client.post(url(server, "/copy"), json={"sources": ["b.txt"], "destination": "plugins"}).status_code
        == 204
    )
    resp = client.post(url(server, "/move"), json={"sources": ["b.txt"], "destination": "plugins"})
    assert resp.status_code == 409  # a copy is already there
    resp = client.post(url(server, "/move"), json={"sources": ["plugins"], "destination": "plugins"})
    assert resp.status_code == 400  # into itself

    assert client.post(url(server, "/delete"), json={"paths": ["b.txt"]}).status_code == 204
    assert listing(client, server) == {"plugins": "dir"}
    assert client.post(url(server, "/delete"), json={"paths": ["/"]}).status_code == 400


def test_text_content(client, server):
    upload(client, server, "", {"ops.json": b"[]", "blob.bin": b"\0\1"})
    assert client.get(url(server, "/content"), params={"path": "ops.json"}).json()["content"] == "[]"
    assert (
        client.put(url(server, "/content"), json={"path": "ops.json", "content": '["me"]'}).status_code == 204
    )
    assert client.get(url(server, "/content"), params={"path": "ops.json"}).json()["content"] == '["me"]'
    assert client.get(url(server, "/content"), params={"path": "blob.bin"}).status_code == 415


def test_download_file_and_folder(client, server):
    upload(client, server, "", {"Привіт.txt": b"hello", "world/level.dat": b"L"})

    resp = client.get(url(server, "/download"), params={"path": "Привіт.txt"})
    assert resp.content == b"hello"
    assert "filename*=UTF-8''%D0%9F" in resp.headers["content-disposition"]

    resp = client.get(url(server, "/download"), params={"path": "world"})
    with tarfile.open(fileobj=io.BytesIO(resp.content), mode="r:gz") as tar:
        assert "world/level.dat" in tar.getnames()

    resp = client.get(url(server, "/download"), params={"path": "", "names": ["world", "Привіт.txt"]})
    assert resp.headers["content-disposition"].endswith("server.tar.gz")


def test_extract_zip(client, server):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("config/a.yml", "a: 1")
    upload(client, server, "", {"pack.zip": buffer.getvalue()})
    assert client.post(url(server, "/extract"), json={"path": "pack.zip"}).json() == {"path": "pack"}
    assert listing(client, server, "pack/config") == {"a.yml": "file"}


def test_docker_files_matches_the_protocol():
    """The tests run against LocalFiles; make sure the real implementation takes the same arguments."""
    import inspect

    from app.runtime.agent import AgentFiles
    from app.runtime.docker_files import DockerFiles
    from app.runtime.files import Files
    from tests.local_files import LocalFiles

    def params(function) -> list[str]:
        # Names only: evaluating the annotations would trip over the class's own `list` method.
        return list(inspect.signature(function, annotation_format=inspect.Format.STRING).parameters)

    methods = [name for name, value in vars(Files).items() if callable(value) and not name.startswith("_")]
    assert methods
    for name in methods:
        expected = params(getattr(Files, name))
        for implementation in (DockerFiles, AgentFiles, LocalFiles):
            actual = params(getattr(implementation, name))
            assert actual == expected, f"{implementation.__name__}.{name}{actual} != Files.{name}{expected}"


def test_agent_runtime_matches_the_protocol():
    import inspect

    from app.runtime.agent import AgentRuntime
    from app.runtime.docker import DockerRuntime
    from app.runtime.manager import Runtime

    def params(function) -> list[str]:
        return list(inspect.signature(function, annotation_format=inspect.Format.STRING).parameters)

    methods = [name for name, value in vars(Runtime).items() if callable(value) and not name.startswith("_")]
    for name in methods:
        for implementation in (DockerRuntime, AgentRuntime):
            assert params(getattr(implementation, name)) == params(getattr(Runtime, name)), (
                implementation,
                name,
            )
    assert hasattr(AgentRuntime, "published_ports") and hasattr(DockerRuntime, "published_ports")


def test_search_finds_names_in_every_folder(client, server):
    upload(
        client,
        server,
        "",
        {"world/level.dat": b"1", "world/region/r.0.0.mca": b"2", "Level-backup.txt": b"3"},
    )
    resp = client.get(url(server, "/search"), params={"q": "LEVEL"})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert [m["path"] for m in data["matches"]] == ["Level-backup.txt", "world/level.dat"]
    assert data["truncated"] is False
    only_world = client.get(url(server, "/search"), params={"q": "r.0", "path": "world"}).json()
    assert [m["path"] for m in only_world["matches"]] == ["world/region/r.0.0.mca"]
    assert client.get(url(server, "/search"), params={"q": "a"}).status_code == 422
