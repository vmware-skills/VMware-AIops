"""Two more annotations that claimed less than the tool does.

From the same 2026-08-30 audit that found ``vm_guest_download`` advertising
``readOnlyHint: true`` while writing the caller's disk. The instruction with
that finding was not to assume the report had found them all, so every one of
the 60 tools was re-read against its body rather than its docstring. These two
did not survive it. Both are cases where this repo's *own* artifacts already
contradicted the annotation, which is why they are fixed here rather than
merely noted.

``vm_set_ttl`` — ``destructiveHint: false``. It schedules an unattended
auto-delete of the VM; the daemon does the deleting later. Issue #25 already
settled that this is a destructive operation *for the CLI*: `vm set-ttl` gained
double-confirmation and ``--dry-run`` because of it, and SKILL.md lists it among
the destructive operations, annotating it in passing as "schedules an unattended
auto-delete". The MCP annotation was the one place still saying otherwise, and
it is the place an agent reads. Deferred destruction is destruction — the field
asks whether the tool "may perform destructive updates", not whether they land
before it returns.

``vm_create_snapshot`` — ``idempotentHint: true``. The body calls
``CreateSnapshot_Task`` unconditionally; there is no check for an existing
snapshot of that name, and vSphere is happy to hold siblings with identical
names. Calling it twice therefore leaves two snapshots, which is the definition
of not idempotent. The consequence is not theoretical: ``idempotentHint`` is
what a client consults before retrying, and this family's own error model
(CLAUDE.md 错误恢复分层) retries transient failures once. A timeout on a snapshot
that actually succeeded would be retried into a second delta-disk chain on a
production VM — and snapshots left lying around are the thing every runbook in
this repo warns about.

Both are pinned against the behaviour rather than against a literal, so a fix
that changed the behaviour instead of the label would still pass.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import pathlib

from vmware_aiops.mcp_server import tools as mcp_server_tools
from vmware_aiops.mcp_server.server import mcp
from vmware_aiops.ops import guest_ops, vm_lifecycle


def _annotations(name: str):
    tool = next(t for t in asyncio.run(mcp.list_tools()) if t.name == name)
    return tool.annotations


def test_scheduling_an_unattended_delete_is_advertised_destructive() -> None:
    assert _annotations("vm_set_ttl").destructiveHint is True


def test_cancelling_a_ttl_is_the_one_that_destroys_nothing() -> None:
    """Control: the inverse tool only removes a schedule and must stay non-destructive.

    Without this, flipping every TTL tool to destructive would pass the test
    above and tell an agent that calling off a deletion is as risky as
    arranging one.
    """
    assert _annotations("vm_cancel_ttl").readOnlyHint is False


def test_snapshot_creation_is_not_advertised_idempotent() -> None:
    assert _annotations("vm_create_snapshot").idempotentHint is False


def test_snapshot_creation_really_is_not_idempotent() -> None:
    """The reason for the label above, checked against the code, not the docstring.

    If a duplicate-name guard is ever added, this goes red and the annotation
    can be revisited — which is the point. The claim tracks the body.
    """
    source = inspect.getsource(vm_lifecycle.create_snapshot)
    assert "CreateSnapshot_Task" in source, "the op no longer creates snapshots here"
    assert "existing" not in source.lower() and "already" not in source.lower(), (
        "create_snapshot appears to have gained a duplicate check — if a second "
        "call with the same name is now a no-op, idempotentHint may become true"
    )


def test_no_tool_claims_to_be_read_only_and_destructive_at_once() -> None:
    """An invariant over all 60, not a re-check of a known answer.

    ``destructiveHint`` is only meaningful when ``readOnlyHint`` is false, so a
    tool asserting both is stating a contradiction, and a client resolving it
    either way is acting on a coin flip. Written after a mutation that marked a
    listing tool destructive slipped past the per-tool controls above: those ask
    "is this one right?", which is the shape that misses everything nobody
    thought to name (形态 #2).
    """
    offenders = [
        t.name
        for t in asyncio.run(mcp.list_tools())
        if t.annotations.readOnlyHint and t.annotations.destructiveHint
    ]
    assert not offenders, f"read-only tools claiming to destroy: {offenders}"


def test_the_read_write_marker_agrees_with_the_annotation() -> None:
    """Two statements of the same fact, in the two places a model reads.

    The ``[READ]``/``[WRITE]`` prefix is prose a model reads; ``readOnlyHint``
    is a field a client acts on. They describe the same property and had no
    mechanical link, so either could drift alone (形态 #6). Checked over the
    whole surface so a new tool cannot be added inconsistent.
    """
    disagreeing = []
    for tool in asyncio.run(mcp.list_tools()):
        description = tool.description.lstrip()
        if description.startswith("[READ]"):
            marker_says_read = True
        elif description.startswith("[WRITE]"):
            marker_says_read = False
        else:
            disagreeing.append((tool.name, "no [READ]/[WRITE] marker"))
            continue
        if marker_says_read is not bool(tool.annotations.readOnlyHint):
            disagreeing.append(
                (tool.name, f"marker vs readOnlyHint={tool.annotations.readOnlyHint}")
            )
    assert not disagreeing, f"marker and annotation disagree: {disagreeing}"


def test_the_tools_that_do_claim_idempotence_earn_it() -> None:
    """Control: this must not become "nothing is idempotent".

    ``set_drs_rule_enabled`` and ``set_vmk_service`` both return a documented
    ``noop`` when the requested state already holds, so re-applying them really
    has no further effect. They stay true.
    """
    for name in ("set_drs_rule_enabled", "set_vmk_service", "vm_power_on"):
        assert _annotations(name).idempotentHint is True, name


# ---------------------------------------------------------------------------
# Guest operations — the widest blast radius in the skill, labelled additive
# ---------------------------------------------------------------------------

#: The two vSphere Guest Operations calls that push caller-supplied content
#: *into* a guest: run this program, place this file at this path. Their mirror
#: ``InitiateFileTransferFromGuest`` is deliberately absent — reading a file out
#: of a VM changes nothing inside it.
_INTO_THE_GUEST = ("StartProgramInGuest", "InitiateFileTransferToGuest")


def _ops_reaching_into_the_guest() -> frozenset[str]:
    """Ops functions that reach one of those calls, directly or through another.

    Derived from vSphere's own API names rather than a list of ops functions
    kept here: a fifth guest tool, or a refactor that routes an existing one
    through a new helper, is picked up without this file being edited. The
    transitive step is what makes ``guest_exec_with_output`` and
    ``guest_provision`` resolve — neither touches the API directly.
    """
    tree = ast.parse(pathlib.Path(guest_ops.__file__).read_text(encoding="utf-8"))
    bodies = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    reaching = {
        name
        for name, node in bodies.items()
        if any(
            isinstance(n, ast.Attribute) and n.attr in _INTO_THE_GUEST
            for n in ast.walk(node)
        )
    }
    assert reaching, (
        f"no function in {guest_ops.__file__} calls any of {_INTO_THE_GUEST} — "
        f"the API names have changed and this derivation now checks nothing"
    )
    changed = True
    while changed:
        changed = False
        for name, node in bodies.items():
            if name in reaching:
                continue
            called = {
                c.func.id
                for c in ast.walk(node)
                if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
            }
            if called & reaching:
                reaching.add(name)
                changed = True
    return frozenset(reaching)


def _tools_calling(ops_names: frozenset[str]) -> dict[str, set[str]]:
    """MCP tool name -> the ops functions in ``ops_names`` its body calls."""
    tools_dir = pathlib.Path(mcp_server_tools.__file__).parent
    out: dict[str, set[str]] = {}
    for path in sorted(tools_dir.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            called = {
                c.func.id
                for c in ast.walk(node)
                if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
            } & ops_names
            if called:
                out[node.name] = called
    return out


def test_pushing_anything_into_a_guest_is_advertised_destructive() -> None:
    """``vm_guest_exec`` ran arbitrary commands as root and said ``destructive: false``.

    The annotation is what a client consults before deciding whether to ask its
    user, and this is the tool with the widest blast radius in the skill: the
    command string is caller-supplied and runs with the credentials handed to
    it, which the docstring's own example makes ``root``. Nothing in the skill
    bounds what it may be — ``rm -rf /`` is a well-formed argument. The same
    label was on ``vm_guest_exec_output`` and ``vm_guest_provision``, which
    execute the same way, and on ``vm_guest_upload``, which replaces whatever
    already sits at the caller's chosen guest path (its CLI mirror has carried a
    comment calling that destructive since it was written).

    Derived, not listed: any tool reaching a vSphere call that puts content into
    a guest must carry the label.
    """
    reaching = _ops_reaching_into_the_guest()
    tools = _tools_calling(reaching)
    assert len(tools) >= 4, (
        f"only {sorted(tools)} derived as guest-writing tools — the "
        f"ops-name derivation has gone stale and this checks almost nothing"
    )
    mislabelled = {
        name: sorted(ops)
        for name, ops in tools.items()
        if _annotations(name).destructiveHint is not True
    }
    assert not mislabelled, (
        f"these tools push caller-supplied commands or files into a guest OS "
        f"and are advertised as non-destructive: {mislabelled}"
    )


def test_reading_a_file_out_of_a_guest_stays_non_destructive() -> None:
    """Control: the fix must not become "every guest tool is destructive".

    ``vm_guest_download`` writes the *caller's* disk, which is why it is a write
    tool, and it refuses an occupied destination. It changes nothing inside the
    VM, so the guest-side destructive label would be false — and a label applied
    to everything tells a client nothing.
    """
    assert _annotations("vm_guest_download").destructiveHint is False
    assert _annotations("vm_guest_download").readOnlyHint is False


def test_additive_writes_stay_non_destructive() -> None:
    """Control over the rest of the write surface, not just the guest corner."""
    for name in ("vm_power_on", "vm_create", "vm_create_snapshot", "vm_clone"):
        assert _annotations(name).destructiveHint is False, name
