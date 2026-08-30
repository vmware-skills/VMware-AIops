"""``vm_guest_download`` writes the caller's filesystem, and said it did not.

Real-hardware MCP round, 2026-08-30. The tool was annotated
``readOnlyHint: true`` and would replace any file the process could write, at
whatever path it was handed. Reading a file *out of* a VM is a read of the VM,
which is presumably how the annotation was arrived at; writing the result onto
the caller's disk is not, and that half is the half a client is asking about.

``readOnlyHint`` is what an MCP client consults to decide whether a call needs
the user's confirmation, so the annotation was not cosmetic — it was a safety
control that silently did not apply. In this repo it disarmed a second control
as well: ``test_cli_writes_guarded`` *derives* the write set from
``readOnlyHint=False``, so the CLI's ``guest-download`` was the one file-writing
command with no ``@guarded``, while its mirror ``guest-upload`` had one.

The overwrite is a separate hazard from the annotation and is fixed separately:
an honest label on a tool that still clobbers arbitrary paths is an honest
label on a hazard. The caller here is an agent, and the destination may be a
path from a model's imagination, so the destination is now refused unless it is
free — ``overwrite=True`` is the deliberate act, matching ``cp -n`` and the
symlink guard already in this function.

The refusals are checked *before* the transfer starts: refusing after
downloading the bytes wastes the transfer and leaves the guest read done for
nothing.
"""

from __future__ import annotations

import asyncio
import pathlib
import re
from unittest.mock import MagicMock, patch

import pytest

from vmware_aiops.mcp_server._shared import AUTHORED_MESSAGE_CAP, _safe_error
from vmware_aiops.mcp_server.server import mcp
from vmware_aiops.ops import guest_ops

_REPO = pathlib.Path(__file__).resolve().parents[3]
SKILL_MD = _REPO / "skills" / "vmware-aiops" / "SKILL.md"

#: Long enough that a message ending in its remedy loses it to the 300-char cap.
LONG_PATH_PARENT = "/tmp/" + "/".join(f"deeply-nested-directory-{i}" for i in range(12))


@pytest.fixture
def downloadable(monkeypatch):
    """A guest whose file transfer would succeed, so only the destination is under test."""
    transfer = MagicMock()
    transfer.url = "https://esxi.example.com/guestFile?id=1"
    vm = MagicMock()
    monkeypatch.setattr(guest_ops, "_require_vm_with_tools", lambda si, name: vm)
    monkeypatch.setattr(guest_ops, "get_verify_ssl", lambda si: True)
    si = MagicMock()
    si.RetrieveContent().guestOperationsManager.fileManager.InitiateFileTransferFromGuest = (
        MagicMock(return_value=transfer)
    )
    return si


def _urlopen(payload: bytes = b"fresh bytes"):
    response = MagicMock()
    response.read.return_value = payload
    response.__enter__ = MagicMock(return_value=response)
    response.__exit__ = MagicMock(return_value=False)
    return MagicMock(return_value=response)


def _download(si, dest: pathlib.Path, **kwargs):
    return guest_ops.guest_download(
        si, "web-01", "/etc/hosts", str(dest), "root", "pw", **kwargs
    )


# ── the annotation ──────────────────────────────────────────────────────────


def _tool(name: str):
    return next(t for t in asyncio.run(mcp.list_tools()) if t.name == name)


def test_guest_download_is_not_advertised_read_only() -> None:
    """The claim a client reads before deciding whether to ask the user."""
    assert _tool("vm_guest_download").annotations.readOnlyHint is False


def test_guest_download_carries_the_write_marker() -> None:
    """The family's ``[READ]``/``[WRITE]`` prefix is what a model reads."""
    assert _tool("vm_guest_download").description.lstrip().startswith("[WRITE]")


def test_guest_upload_and_download_are_annotated_alike_except_where_direction_matters() -> None:
    """They are the same operation in opposite directions.

    Pinning them against each other rather than against a literal keeps the pair
    honest if the family's convention for guest file transfer ever moves.

    ``destructiveHint`` is excluded from the mirror, and that exclusion is the
    interesting part. The field describes what the tool does to the environment
    it is pointed at, and the direction is exactly what makes the two differ:
    upload replaces whatever already sits at a caller-chosen path *inside the
    guest*, download changes nothing in the VM at all. When this test asserted
    the two were identical on all three fields it was asserting a symmetry the
    field does not have, and it went red on 2026-08-30 for the upload being
    labelled honestly — which is the assertion failing, not the label. Both
    halves are pinned below so neither can quietly follow the other.
    """
    up, down = _tool("vm_guest_upload").annotations, _tool("vm_guest_download").annotations
    assert (down.readOnlyHint, down.idempotentHint) == (up.readOnlyHint, up.idempotentHint)
    assert up.destructiveHint is True, "upload replaces content inside the guest"
    assert down.destructiveHint is False, "download changes nothing inside the guest"


def test_skill_md_read_write_split_matches_the_annotations() -> None:
    """The documented split is derived from ``readOnlyHint``, so it must track it.

    A prose count with no mechanical link to the code drifts silently (形态 #6),
    and this one is quoted twice in SKILL.md.
    """
    tools = asyncio.run(mcp.list_tools())
    reads = sum(1 for t in tools if getattr(t.annotations, "readOnlyHint", None) is True)
    writes = len(tools) - reads

    text = SKILL_MD.read_text()
    header = re.search(r"## MCP Tools \((\d+) — (\d+) read, (\d+) write\)", text)
    assert header, "SKILL.md no longer states the split in the expected form"
    assert (int(header[1]), int(header[2]), int(header[3])) == (len(tools), reads, writes)
    assert f"{reads} tools are read-only" in text, "the body count disagrees with the header"


# ── the unconditional overwrite ─────────────────────────────────────────────


def test_an_existing_file_is_not_replaced_by_default(downloadable, tmp_path) -> None:
    dest = tmp_path / "id_rsa"
    dest.write_bytes(b"the caller's only copy")

    opener = _urlopen()
    with patch("urllib.request.urlopen", opener):
        with pytest.raises(ValueError, match="overwrite"):
            _download(downloadable, dest)

    assert dest.read_bytes() == b"the caller's only copy"
    assert not opener.called, "refused only after paying for the transfer"


def test_overwrite_true_replaces_it(downloadable, tmp_path) -> None:
    """The deliberate act still works — a refusal with no way through is a bug."""
    dest = tmp_path / "id_rsa"
    dest.write_bytes(b"stale")

    with patch("urllib.request.urlopen", _urlopen(b"fresh bytes")):
        _download(downloadable, dest, overwrite=True)

    assert dest.read_bytes() == b"fresh bytes"


def test_a_free_destination_still_writes(downloadable, tmp_path) -> None:
    """Positive control: the ordinary case must not need a flag."""
    dest = tmp_path / "sub" / "hosts"

    with patch("urllib.request.urlopen", _urlopen(b"127.0.0.1 localhost")):
        result = _download(downloadable, dest)

    assert dest.read_bytes() == b"127.0.0.1 localhost"
    assert "hosts" in result


def test_a_directory_destination_is_refused(downloadable, tmp_path) -> None:
    """``open(dir, "wb")`` raises ``IsADirectoryError``, which ``_safe_error``
    reduces to its class name — the agent learns nothing. Refuse it by name."""
    dest = tmp_path / "downloads"
    dest.mkdir()

    with patch("urllib.request.urlopen", _urlopen()):
        with pytest.raises(ValueError, match="director"):
            _download(downloadable, dest, overwrite=True)


def test_a_symlink_destination_is_still_refused(downloadable, tmp_path) -> None:
    """The pre-existing guard: a symlink would redirect the write elsewhere.

    ``overwrite=True`` does not open it — consenting to replace the named path
    is not consenting to replace whatever it points at.
    """
    secret = tmp_path / "elsewhere"
    secret.write_bytes(b"untouched")
    dest = tmp_path / "link"
    dest.symlink_to(secret)

    with patch("urllib.request.urlopen", _urlopen()):
        with pytest.raises(ValueError, match="symlink"):
            _download(downloadable, dest, overwrite=True)

    assert secret.read_bytes() == b"untouched"


# ── the remedy has to survive the 300-char cap ──────────────────────────────


@pytest.mark.parametrize(
    ("setup", "kwargs", "remedy"),
    [
        # Each phrase must be the *instruction*, not a word that also occurs in
        # the leading description of the problem. Asserting bare "overwrite"
        # here let a mutation that moved the path in front of the remedy pass:
        # the word survived truncation in "Refusing to overwrite ...", while
        # the part telling the agent what to do did not.
        ("file", {}, "Pass overwrite=true"),
        ("dir", {"overwrite": True}, "Pass a destination file path"),
        ("symlink", {"overwrite": True}, "does not exist yet"),
    ],
    ids=["exists", "directory", "symlink"],
)
def test_the_refusal_keeps_its_remedy_through_sanitize(
    downloadable, tmp_path, setup, kwargs, remedy
) -> None:
    """A message that outgrows the cap loses whichever end is last.

    ``_safe_error`` caps authored text at ``AUTHORED_MESSAGE_CAP``, and the destination is
    caller-supplied and unbounded — so the remedy goes before the path, not
    after it. Same shape as the connection-failure tests in
    ``test_safe_error_passthrough``.
    """
    dest = tmp_path / LONG_PATH_PARENT.lstrip("/") / "destination-file-with-a-long-name"
    dest.parent.mkdir(parents=True)
    if setup == "file":
        dest.write_bytes(b"x")
    elif setup == "dir":
        dest.mkdir()
    else:
        dest.symlink_to(tmp_path / "elsewhere")

    with patch("urllib.request.urlopen", _urlopen()):
        with pytest.raises(ValueError) as caught:
            _download(downloadable, dest, **kwargs)

    assert len(str(dest)) > 300, "the path must be long enough to crowd the message"
    out = _safe_error(caught.value, "vm_guest_download")
    assert len(out) <= AUTHORED_MESSAGE_CAP, "sanitize truncates authored text"
    assert remedy in out, "the remedy was truncated away"
