"""The only repo-specific facts in this capability suite.

Every ``test_*.py`` file in this directory is byte-identical across the family
repos; they differ only through this module. Keeping the difference in one small
file is what makes a rubric change portable — edit the eval once, copy it, and
the scores stay comparable between skills.
"""

from __future__ import annotations

#: Import path of the Python package under test.
PACKAGE = "vmware_aiops"

#: Module holding the FastMCP ``mcp`` instance.
SERVER_MODULE = "vmware_aiops.mcp_server.server"

#: CLI entry point name, used when scoring whether an error names something
#: concrete for the operator to run.
CLI_NAME = "vmware-aiops"

#: Companion skills this one legitimately routes to. A required entity name that
#: this surface cannot produce is not a dead end *if* the description says which
#: sibling skill produces it — that is a documented hand-off rather than a gap.
COMPANION_SKILLS = (
    "vmware-aiops",
    "vmware-monitor",
    "vmware-storage",
    "vmware-vks",
    "vmware-nsx",
    "vmware-nsx-security",
    "vmware-aria",
    "vmware-avi",
    "vmware-harden",
    "vmware-pilot",
)
#: Entity tokens this skill's tools name, mapped to the words its listing tools
#: use. Drives ``test_entity_reachability``: a required parameter whose stem is
#: not here is invisible to that eval, so an incomplete map understates the
#: surface rather than failing — which is why the suite asserts coverage.
ENTITY_WORDS = {
    "vm": ("vm", "virtual_machine", "virtualmachine", "vms", "source_vm"),
    "host": ("host", "esxi", "hosts"),
    "datastore": ("datastore", "datastores", "ds"),
    "cluster": ("cluster", "clusters"),
    "network": ("network", "networks", "portgroup"),
    "snapshot": ("snapshot", "snapshots"),
    "image": ("image", "images", "ova", "iso", "template"),
    "alarm": ("alarm", "alarms"),
    "plan": ("plan", "plans"),
    # The vSphere object an alarm fires on. Deliberately mapped to nothing that
    # enumerates it: no tool is named for producing entity names, so this stays
    # reachable only if a description says where to get one. Inventing a producer
    # here would make the eval green without making the surface navigable.
    "entity": ("entity",),
    # DRS affinity/anti-affinity rules. list_drs_rules enumerates them, so a
    # rule_name is obtainable from this surface.
    "rule": ("rule", "rules"),
}

#: Skill-specific parameters that end in an entity suffix but are supplied by the
#: operator rather than discovered from an API. Universal exclusions (``target``,
#: paths, filters) live in the eval itself.
NOT_AN_ENTITY = frozenset(
    {
        "folder_filter",
        "cluster_filter",
        "power_state",
        "task_id",
        "spec_name",
        # The name of the VM being created — chosen by the operator, not looked
        # up. The creation-verb rule misses it because the stem is "new", not the
        # entity, so it has to be named explicitly.
        "new_name",
        # The VLAN to configure on a new portgroup. A number the operator
        # decides from their network plan, not an object discovered from vSphere.
        "vlan_id",
        # Which dvSwitch to create a portgroup on. This surface has no tool that
        # enumerates dvSwitches — list_dvs_portgroups reports the parent switch
        # of each portgroup, which is not the same as being able to list them.
        # Excluded rather than mapped: pointing it at a producer that does not
        # exist would turn the eval green without making the surface navigable,
        # which is the failure this vocabulary is meant to expose.
        "dvs_name",
    }
)
def get_server(module):
    """Return the FastMCP instance ``SERVER_MODULE`` exposes.

    The family has two shapes: a module-level ``mcp`` built at import time, and a
    ``build_server()`` factory (vmware-harden, vmware-debug). Declared per skill
    rather than probed with a try/except chain — a fallback would let a server
    that stops exposing what this file says silently resolve to the other shape,
    and the suite would go on scoring something nobody meant to measure.
    """
    return module.mcp
