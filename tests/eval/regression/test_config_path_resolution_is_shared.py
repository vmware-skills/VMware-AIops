"""The CLI, the doctor and the MCP server must open the same config file.

Swept for after the same defect was found on real hardware in the sibling Aria
skill, 2026-08-30. This skill had it in the Storage variant, which is the worse
one: ``load_config`` resolved ``config_path or CONFIG_FILE`` and never looked at
``VMWARE_AIOPS_CONFIG`` at all, while the MCP server read the variable — once in
``_ensure_conn_mgr``, and again through ``mtime_cached_loader`` — and passed the
result down explicitly.

Reproduced before the fix, against a real config at the default path and the
variable pointing elsewhere::

    load_config()  -> targets: ['home-vcenter', 'home-esxi']   # CLI and doctor
    mtime_cached() -> targets: ['from-env']                    # MCP server

Two different vCenters in one installation, selected by which surface you came
in through. The agent powers off a VM on one of them and the operator, looking
at the other, cannot find the VM that moved. And the doctor — which is what
people run when something is wrong — reported on the CLI's file, so it could
green-light a configuration no tool would open.

``VMWARE_AIOPS_CONFIG`` is this skill's advertised ``primaryEnv`` in its
OpenClaw metadata, so the CLI honouring it is the documented behaviour; ignoring
it was the bug.

The precedence now lives in exactly one function, ``resolve_config_path``, that
every reader goes through — copies of a rule do not disagree loudly, they
disagree slowly, which is how this one drifted (CLAUDE.md 形态 #6).
"""

from __future__ import annotations

import inspect

import pytest

from vmware_aiops import config as cfg
from vmware_aiops import doctor as doc

# Deliberately different target counts *and* hostnames. The count says which
# file the target check parsed; the hostnames say which file every other check
# parsed — without them, a check that reverted to the default on its own would
# still produce an identical report, and the mutation proving that is exactly
# what survived the first pass of this file.
#
# Both are .invalid, which is reserved and resolves nowhere, so the
# connectivity check fails fast on DNS rather than waiting out a connect.
_DEFAULT_HOST = "only-in-the-default.invalid"
_ENV_HOST = "only-in-the-env-var.invalid"

_ONE_TARGET = f"""
targets:
  - name: only-in-the-default
    host: {_DEFAULT_HOST}
    port: 1
    username: admin
"""

_THREE_TARGETS = f"""
targets:
  - name: a
    host: a.{_ENV_HOST}
    port: 1
    username: admin
  - name: b
    host: b.{_ENV_HOST}
    port: 1
    username: admin
  - name: c
    host: c.{_ENV_HOST}
    port: 1
    username: admin
"""


def _flat(text: str) -> str:
    """The report with whitespace and table drawing removed.

    Rich wraps a long path across cells, so flattening keeps the assertions
    about *which file* independent of the table layout.
    """
    return "".join(ch for ch in text if not ch.isspace() and ch not in "│┃")


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    """A default config and .env that are both entirely valid.

    The point of making the default healthy is that the only way the doctor can
    end up reporting on the variable's file is by resolving it — a red report
    for an unrelated reason would prove nothing.
    """
    default = tmp_path / "default.yaml"
    default.write_text(_ONE_TARGET)
    env_file = tmp_path / "dot.env"
    env_file.write_text("")
    env_file.chmod(0o600)

    monkeypatch.setattr(cfg, "CONFIG_FILE", default)
    monkeypatch.setattr(doc, "ENV_FILE", env_file)
    monkeypatch.delenv("VMWARE_AIOPS_CONFIG", raising=False)
    # Rich elides long details at 80 columns, so an assertion about a tmp_path
    # would be measuring the terminal rather than the doctor.
    monkeypatch.setenv("COLUMNS", "300")
    return default


@pytest.mark.unit
def test_the_env_var_decides_which_file_is_resolved(sandbox, tmp_path, monkeypatch):
    elsewhere = tmp_path / "elsewhere.yaml"
    elsewhere.write_text(_THREE_TARGETS)
    monkeypatch.setenv("VMWARE_AIOPS_CONFIG", str(elsewhere))

    assert cfg.resolve_config_path() == elsewhere
    assert len(cfg.load_config().targets) == 3, (
        "load_config ignored $VMWARE_AIOPS_CONFIG, so the CLI reads one file "
        "and the MCP server another"
    )


@pytest.mark.unit
def test_an_explicit_path_still_beats_the_env_var(sandbox, tmp_path, monkeypatch):
    """The control on precedence: an explicit path is the caller saying which
    file they mean, and it has to keep winning.

    A "fix" that let the variable overtake ``--config`` would pass the test
    above and break the flag.
    """
    explicit = tmp_path / "explicit.yaml"
    explicit.write_text(_ONE_TARGET)
    monkeypatch.setenv("VMWARE_AIOPS_CONFIG", str(tmp_path / "ignored.yaml"))

    assert cfg.resolve_config_path(explicit) == explicit
    assert len(cfg.load_config(explicit).targets) == 1


@pytest.mark.unit
def test_with_neither_it_is_the_default(sandbox):
    assert cfg.resolve_config_path() == cfg.CONFIG_FILE
    assert len(cfg.load_config().targets) == 1


@pytest.mark.unit
def test_the_cli_and_the_mcp_server_open_the_same_file(sandbox, tmp_path, monkeypatch):
    """The defect itself, end to end, against the server's real loader.

    A structural test alone would not have caught this: both surfaces were
    internally tidy, they simply disagreed. So this asserts on the thing that
    was wrong — the two paths returning different vCenters.
    """
    from vmware_aiops.mcp_server import server

    elsewhere = tmp_path / "elsewhere.yaml"
    elsewhere.write_text(_THREE_TARGETS)
    monkeypatch.setenv("VMWARE_AIOPS_CONFIG", str(elsewhere))

    cli_targets = [t.name for t in cfg.load_config().targets]
    server_targets = [t.name for t in server._cached_config().targets]

    assert cli_targets == server_targets, (
        f"the CLI loaded {cli_targets} and the MCP server loaded "
        f"{server_targets}: one installation, two vCenters, chosen by which "
        f"surface you came in through"
    )


@pytest.mark.unit
def test_doctor_does_not_pass_while_the_tools_cannot_load_the_config(
    sandbox, tmp_path, monkeypatch, capsys
):
    """The reported failure in full: the doctor exits 0 — every check green —
    while every tool call raises FileNotFoundError.

    The default config here exists and parses. It is simply not the file the
    tools will open.
    """
    missing = tmp_path / "not-there.yaml"
    monkeypatch.setenv("VMWARE_AIOPS_CONFIG", str(missing))

    with pytest.raises(FileNotFoundError):
        cfg.load_config()

    rc = doc.run_doctor(skip_auth=True)
    out = _flat(capsys.readouterr().out)

    assert rc != 0, (
        "doctor exited 0 against a config file that does not exist; this is "
        "the report that tells an operator their broken setup is fine"
    )
    assert str(missing) in out, (
        "the report must name the file it looked at — a verdict about an "
        "unnamed file is what made this take real hardware to find"
    )
    assert "1target(s)configured" not in out, (
        "doctor parsed the default config and called it green while every "
        "tool call raises FileNotFoundError on the path in $VMWARE_AIOPS_CONFIG"
    )
    assert _DEFAULT_HOST not in out, (
        "some check read the default config's hosts; with the variable set, no "
        "check should be looking at that file at all"
    )


@pytest.mark.unit
def test_doctor_reads_the_env_vars_file_not_the_default(
    sandbox, tmp_path, monkeypatch, capsys
):
    """The positive half: pointed at a real file elsewhere, the doctor reports
    on that one — three targets, not the default's one."""
    elsewhere = tmp_path / "elsewhere.yaml"
    elsewhere.write_text(_THREE_TARGETS)
    monkeypatch.setenv("VMWARE_AIOPS_CONFIG", str(elsewhere))

    doc.run_doctor(skip_auth=True)
    out = _flat(capsys.readouterr().out)

    assert str(elsewhere) in out, "the report must name the file it looked at"
    assert "3target(s)configured" in out, (
        "the doctor counted the default file's targets, so it parsed the file "
        "the tools will never open"
    )
    assert _ENV_HOST in out, "no check reached the hosts in the variable's file"
    assert _DEFAULT_HOST not in out, (
        "a check reverted to the default config on its own and reported on its "
        "hosts — the count above cannot see that, which is why this is here"
    )


@pytest.mark.unit
def test_load_config_and_the_doctor_cannot_disagree():
    """Structural, not behavioural: every reader goes through the one resolver,
    so a future edit cannot silently desynchronise them again.

    The doctor is seven independent check functions, four of which open the
    config. Asserting on each by name would go stale the moment an eighth is
    added, so the assertion is that the module does not name the default config
    path at all: whichever check needs to know, asks.
    """
    assert "resolve_config_path" in inspect.getsource(cfg.load_config), (
        "load_config resolves the config path by itself again; that is the "
        "duplication this test exists to prevent"
    )
    assert "CONFIG_FILE" not in inspect.getsource(doc), (
        "a doctor check names the default config path directly, so it can "
        "diagnose a file the tools will not open"
    )


@pytest.mark.unit
def test_the_mcp_server_does_not_keep_its_own_copy_of_the_precedence():
    """The other copy. ``_ensure_conn_mgr`` read $VMWARE_AIOPS_CONFIG itself
    and passed the result down explicitly, which is precisely why the server
    and the CLI disagreed about which file was in play.

    Asserted on ``os.environ`` rather than on the variable's name: a grep for
    the name cannot tell a read from a docstring that merely mentions it.
    """
    from vmware_aiops.mcp_server import _shared

    source = inspect.getsource(_shared._ensure_conn_mgr)
    assert "os.environ" not in source, (
        "_ensure_conn_mgr resolves the config path itself; let load_config do it"
    )
