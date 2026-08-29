"""Datastore tools: browse files, scan for deployable images."""

from typing import Optional

from vmware_policy import paginated, vmware_tool

from vmware_aiops.mcp_server._shared import _get_connection, mcp, tool_errors
from vmware_aiops.ops import datastore_browser


@mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
@vmware_tool(risk_level="low")
@tool_errors("dict")
def browse_datastore(
    datastore_name: str,
    path: str = "",
    pattern: str = "*",
    target: Optional[str] = None,
) -> dict:
    """[READ] Browse files in a vSphere datastore directory.

    Use this to find OVA/ISO/VMDK paths before calling deploy_vm_from_ova or
    attach_iso_to_vm; for an estate-wide image sweep use scan_datastore_images.

    Returns the list envelope: 'items' is one row per file, and
    'returned'/'total'/'truncated' state completeness. Every match in the
    searched folders is returned, so truncated is always false.

    Args:
        datastore_name: Name of the datastore to browse.
        path: Subdirectory path (empty string for root).
        pattern: Glob pattern to filter files (e.g. "*.ova", "*.iso", "*").
        target: Optional vCenter/ESXi target name from config.
    """
    si = _get_connection(target)
    return datastore_browser.browse_datastore(si, datastore_name, path=path, pattern=pattern)


@mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
@vmware_tool(risk_level="low")
@tool_errors("dict")
def scan_datastore_images(target: Optional[str] = None) -> dict:
    """[READ] Scan all accessible datastores for deployable images (OVA/ISO/OVF/VMDK).

    Returns the images found and refreshes the cache at
    ~/.vmware-aiops/image_registry.json. Use this when you do not know which
    datastore holds an image; prefer browse_datastore once you do, because this
    walks every datastore and may take minutes on a large estate.

    Args:
        target: Optional vCenter/ESXi target name from config.

    Returns:
        The family list envelope {items, returned, limit, total, truncated,
        hint} plus `last_scan`; each item is one image. `images` is kept as a
        deprecated alias for `items`.
    """
    si = _get_connection(target)
    registry = datastore_browser.update_registry(si)
    # The registry dict is also the ON-DISK format, so it is wrapped here rather
    # than changed at the source. It previously reached the agent as
    # {images, last_scan} with no count and no truncation flag — nothing a model
    # could reason about, and the one shape this family's envelope exists to
    # replace. `images` stays as a deprecated alias.
    images = registry.get("images") or []
    result = paginated(images, limit=None, total=len(images))
    result["images"] = result["items"]
    result["last_scan"] = registry.get("last_scan")
    return result
