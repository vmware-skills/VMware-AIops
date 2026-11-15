"""Remove the directory a failed CreateVM_Task leaves behind, and nothing else.

A failed ``CreateVM_Task`` can leave an empty ``[ds] <name>/`` directory on
the datastore (issue #69, standalone ESXi 8.0.1). create_vm removes it only
when it can show the directory is its own:

- the VM name is plain (letters, digits, ``.``, ``_``, ``-``, space), so the
  directory vSphere creates has exactly that name (others are escaped);
- ``[ds] <name>/`` was absent just before the create, so vSphere used that
  name and not ``<name>_1/`` next to an older directory;
- it is present and empty after the failure.

Everything else is reported, never deleted. A directory that already exists
is not reused by a later CreateVM_Task (vSphere picks ``<name>_1/``), so a
concurrent create does not write into the one being removed; the remaining
window is a manual upload into it between the listing and the delete.

Task faults arrive as ``TaskFailedError``; ``.fault`` tells a missing
directory (``vim.fault.FileNotFound``) apart from a failure.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable

from pyVmomi import vim

from vmware_aiops.ops._vm_tasks import TaskFailedError

log = logging.getLogger(__name__)

Wait = Callable[..., object]
_PLAIN_NAME = re.compile(r"[A-Za-z0-9._ -]+")
_PROBE_TIMEOUT_S = 30


def _list_dir(ds, dir_path: str, wait: Wait) -> list[str] | None:
    """File names in ``dir_path``; None when the directory does not exist."""
    spec = vim.host.DatastoreBrowser.SearchSpec()
    spec.matchPattern = ["*"]
    # The generic Query: without it the browser shows ISO/VMDK/folder rows only.
    spec.query = [vim.host.DatastoreBrowser.Query()]
    try:
        result = wait(ds.browser.SearchDatastore_Task(datastorePath=dir_path, searchSpec=spec),
                      timeout=_PROBE_TIMEOUT_S)
    except TaskFailedError as exc:
        if isinstance(exc.fault, vim.fault.FileNotFound):
            return None
        raise
    return [f.path for f in (getattr(result, "file", None) or [])]


def vm_dir_state(ds, ds_name: str | None, vm_name: str, wait: Wait) -> bool | None:
    """Whether ``[ds_name] vm_name/`` exists; None when this cannot be told safely."""
    if ds is None or not ds_name or not _PLAIN_NAME.fullmatch(vm_name):
        return None
    try:
        return _list_dir(ds, f"[{ds_name}] {vm_name}", wait) is not None
    except Exception as exc:  # noqa: BLE001 — unknown means "never delete"
        log.warning("create_vm: could not check [%s] %s/ before the create: %s",
                    ds_name, vm_name, exc)
        return None


def cleanup_failed_create(si, datacenter, ds, ds_name: str | None, vm_name: str,
                          existed_before: bool | None, wait: Wait) -> str:
    """Return a sentence for the error: what was removed, or what to check."""
    dir_path = f"[{ds_name}] {vm_name}" if ds_name else vm_name
    manual = (f"The failed create may have left an empty '{dir_path}/' directory (or a "
              f"'{vm_name}_1/'-style sibling) on the datastore; remove it before retrying "
              "with the same name.")
    if existed_before is None:
        return manual
    if existed_before:
        return (f"'{dir_path}/' existed before this create and was left as it is; the "
                f"failed create may have left a '{vm_name}_1/'-style sibling. Check both "
                "before retrying with the same name.")
    try:
        entries = _list_dir(ds, dir_path, wait)
    except Exception as exc:  # noqa: BLE001 — keep the create's own error in front
        log.warning("create_vm: could not list %s/ after the failed create: %s", dir_path, exc)
        return manual
    if entries is None:
        return f"No '{dir_path}/' directory was left behind."
    if entries:
        return (f"The failed create left '{dir_path}/' with {len(entries)} file(s); it was "
                "not removed automatically. Inspect and delete it before retrying.")
    try:
        wait(si.content.fileManager.DeleteDatastoreFile_Task(name=dir_path, datacenter=datacenter),
             timeout=_PROBE_TIMEOUT_S)
    except Exception as exc:  # noqa: BLE001 — report it; the create already failed
        log.warning("create_vm: could not remove empty %s/: %s", dir_path, exc)
        return (f"The failed create left an empty '{dir_path}/' directory and create_vm "
                "could not remove it; delete it before retrying with the same name.")
    log.info("create_vm: removed empty %s/ left by a failed create", dir_path)
    return f"Removed the empty '{dir_path}/' directory the failed create left behind."
