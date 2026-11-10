"""Snapshot tree identity, matching and counting; no remote mutations."""

from __future__ import annotations


from typing import TYPE_CHECKING

from pyVmomi import vim

from vmware_policy import sanitize


if TYPE_CHECKING:
    pass


def _require_snapshot_id(node, snap_name: str, expected: str | None) -> str | None:
    """A refusal when the snapshot found by name is not the one a preview measured."""
    if expected is None:
        return None
    try:
        actual = node.snapshot._moId
    except AttributeError:
        actual = None
    if actual == expected:
        return None
    return (
        f"Snapshot '{sanitize(snap_name, 200)}' not changed: it is not the snapshot that "
        f"was previewed (id {sanitize(str(actual), 64)} instead of {sanitize(expected, 64)}). "
        "It was removed or recreated under the same name since. Preview again."
    )


def _count_snapshots(snapshot_info) -> int:
    """Count total snapshots recursively."""
    count = 0
    if snapshot_info and snapshot_info.rootSnapshotList:
        for snap in snapshot_info.rootSnapshotList:
            count += 1 + _count_children(snap)
    return count


def _count_children(snap_tree) -> int:
    count = 0
    for child in snap_tree.childSnapshotList or []:
        count += 1 + _count_children(child)
    return count


def snapshot_nodes(vm: vim.VirtualMachine) -> list:
    """Every snapshot tree node of ``vm``, depth first; [] when it has none."""
    found: list = []

    def _walk(snap_list) -> None:
        for snap in snap_list or []:
            found.append(snap)
            _walk(snap.childSnapshotList)

    if vm.snapshot:
        _walk(vm.snapshot.rootSnapshotList)
    return found


def matching_snapshots(nodes: list, snap_name: str) -> list:
    """The nodes ``snap_name`` could mean.

    ``list_snapshots`` shows ``sanitize(name)``, so a caller who copies a name
    from it may pass the sanitized form of a raw name that carries control or
    invisible characters. A node matches on its raw name or its shown name; two
    nodes that match the same string — duplicates, or names that differ only by
    stripped characters — are ambiguous, and every executor and the MCP gate
    refuse them rather than pick one.
    """
    return [n for n in nodes if n.name == snap_name or sanitize(n.name) == snap_name]


def resolve_snapshot(vm: vim.VirtualMachine, snap_name: str) -> tuple[object | None, str | None]:
    """The one snapshot node ``snap_name`` means, or why there is not exactly one."""
    nodes = snapshot_nodes(vm)
    matches = matching_snapshots(nodes, snap_name)
    if len(matches) == 1:
        return matches[0], None
    shown = sanitize(snap_name, 200)
    if not matches:
        available = ", ".join(sanitize(n.name) for n in nodes) or "none"
        return None, f"Snapshot '{shown}' not found. Available: {available}"
    return None, (
        f"Snapshot '{shown}' not changed: {len(matches)} snapshots on VM "
        f"'{sanitize(vm.name, 200)}' match that name (duplicates, or names that differ "
        "only by control/invisible characters). Rename one in vCenter, then retry."
    )
