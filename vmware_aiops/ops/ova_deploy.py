"""OVA import: parse an OVA, create the import spec, upload VMDKs via HTTP NFC.

Extracted from ``vm_deploy`` to keep each module focused — this file owns the
OVA-specific machinery (tar safety, OVF parsing, chunked VMDK upload, lease
progress) and the ``deploy_ova`` entry point. ``vm_deploy`` re-exports
``deploy_ova`` so existing ``vm_deploy.deploy_ova`` call sites are unchanged.
"""

from __future__ import annotations

import logging
import tarfile
import time
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.request import Request, urlopen

from pyVmomi import vim

from vmware_aiops.connection import get_verify_ssl
from vmware_aiops.ops.inventory import (
    InventoryError,
    find_compute_resource,
    find_datastore_by_name,
    resolve_datacenter,
)
from vmware_aiops.ops.vm_lifecycle import create_snapshot, power_on_vm

if TYPE_CHECKING:
    from pyVmomi.vim import ServiceInstance

_log = logging.getLogger("vmware-aiops.deploy")

_HTTP_TIMEOUT = 300  # seconds — VMDK upload urlopen must never hang the MCP server

# Upload VMDKs in fixed-size chunks rather than slurping the whole disk into
# RAM, and report lease progress as bytes flow so vCenter does not abort the
# HttpNfcLease (~5 min idle timeout) on large/slow uploads.
_UPLOAD_CHUNK_SIZE = 8 * 1024 * 1024  # 8 MiB

# Maximum uncompressed size per tar member (2 GiB) — prevents tar bombs
# that claim a small compressed size but expand to huge files on extraction.
_MAX_TAR_MEMBER_SIZE = 2 * 1024 * 1024 * 1024  # 2 GiB

# Maximum aggregate uncompressed size across all members in one OVA (20 GiB).
_MAX_TAR_TOTAL_SIZE = 20 * 1024 * 1024 * 1024  # 20 GiB


def _safe_tar_member(member: tarfile.TarInfo, dest_dir: Path | None = None) -> bool:
    """Check a tar member is safe to extract.

    Rejects:
    - Absolute paths or path traversal (CVE-2007-4559)
    - Members exceeding the per-file size limit (tar bomb protection)
    - Symlinks or hardlinks that point outside dest_dir
    - Device files (block, character, FIFO)

    Returns True if safe, False otherwise (caller should skip the member).
    """
    # Path traversal check
    if member.name.startswith("/") or ".." in member.name:
        return False

    # Per-member size limit
    if member.size > _MAX_TAR_MEMBER_SIZE:
        _log.warning(
            "Rejecting tar member %r: size %d bytes exceeds per-member limit %d",
            member.name, member.size, _MAX_TAR_MEMBER_SIZE,
        )
        return False

    # Symlink / hardlink pointing outside dest_dir
    if dest_dir is not None and (member.issym() or member.islnk()):
        try:
            target = (dest_dir / member.linkname).resolve()
            target.relative_to(dest_dir.resolve())
        except ValueError:
            _log.warning(
                "Rejecting tar symlink/hardlink pointing outside dest: %r -> %r",
                member.name, member.linkname,
            )
            return False

    # Special device files (block, character, FIFO)
    if member.isdev() or member.ischr() or member.isblk() or member.isfifo():
        _log.warning(
            "Rejecting tar special file: %r (type %r)", member.name, member.type
        )
        return False

    return True


def _read_ovf_from_ova(ova_path: str) -> tuple[str, dict[str, int]]:
    """Extract OVF descriptor and disk file info from an OVA (tar archive).

    Args:
        ova_path: Local file path to the .ova file.

    Returns:
        Tuple of (ovf_xml_string, {vmdk_filename: file_size_bytes})
    """
    disks: dict[str, int] = {}
    ovf_content = ""

    with tarfile.open(ova_path, "r", encoding="utf-8") as tar:
        members = tar.getmembers()

        # Aggregate size check — guard against tar bombs before processing.
        total_size = sum(m.size for m in members)
        if total_size > _MAX_TAR_TOTAL_SIZE:
            raise ValueError(
                f"OVA total uncompressed size {total_size} bytes exceeds "
                f"limit {_MAX_TAR_TOTAL_SIZE} bytes (20 GiB). Refusing to process. "
                f"Import this image once via the vSphere Client, then use "
                f"deploy_vm_from_template (CLI: vmware-aiops deploy template) for "
                f"subsequent deployments."
            )

        for member in members:
            if not _safe_tar_member(member):
                _log.warning("Skipping unsafe tar member: %s", member.name)
                continue
            if member.name.endswith(".ovf"):
                f = tar.extractfile(member)
                if f:
                    ovf_content = f.read().decode("utf-8")
            elif member.name.endswith((".vmdk", ".img")):
                disks[member.name] = member.size

    if not ovf_content:
        raise ValueError(
            f"No .ovf descriptor found in OVA: {ova_path}. Check the file is a real OVA "
            f"(a tar archive containing an .ovf), not a bare .ovf, a .zip, or a partial "
            f"download; repackage with ovftool if needed, then pass it again to "
            f"vmware-aiops deploy ova."
        )

    return ovf_content, disks


def _upload_disk(
    lease: vim.HttpNfcLease,
    ova_path: str,
    disk_name: str,
    upload_url: str,
    disk_size: int,
    verify_ssl: bool = True,
) -> None:
    """Upload a VMDK from an OVA to the vSphere HTTP NFC lease URL.

    Streams the tar member in fixed-size chunks (never loading the whole disk
    into RAM) and periodically reports HttpNfcLeaseProgress based on bytes
    uploaded so vCenter keeps the lease alive during large/slow uploads.
    """
    # Validate upload URL scheme — only HTTPS allowed (B310)
    if not upload_url.lower().startswith("https://"):
        raise ValueError(f"Refusing non-HTTPS upload URL: {upload_url}")

    with tarfile.open(ova_path, "r", encoding="utf-8") as tar:
        member = tar.getmember(disk_name)
        if not _safe_tar_member(member):
            raise ValueError(
                f"Unsafe tar member path: {disk_name}. This OVA is malformed or "
                f"hostile (the entry escapes the archive root) and will not be "
                f"deployed. Do not retry with this file; obtain a re-exported OVA."
            )

        f = tar.extractfile(member)
        if f is None:
            raise ValueError(
                f"Cannot extract {disk_name} from OVA — the archive is truncated or "
                f"corrupt. Do not retry with this file; re-download or re-export the "
                f"OVA and start the deploy again."
            )

        total = member.size

        def _chunked_body():
            uploaded = 0
            last_percent = -1
            while True:
                chunk = f.read(_UPLOAD_CHUNK_SIZE)
                if not chunk:
                    break
                uploaded += len(chunk)
                if total > 0:
                    percent = min(99, uploaded * 100 // total)
                    if percent != last_percent:
                        last_percent = percent
                        _report_lease_progress(lease, percent)
                yield chunk

        req = Request(
            upload_url,
            data=_chunked_body(),
            method="PUT",
            headers={
                "Content-Type": "application/x-vnd.vmware-streamVmdk",
                "Content-Length": str(total),
            },
        )

        # SSL context: respect verify_ssl passed from the ServiceInstance.
        # Only disable verification when the target uses self-signed certs.
        import ssl
        if verify_ssl:
            ctx = ssl.create_default_context()
        else:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE  # nosec B501 — ESXi self-signed certs

        with urlopen(req, context=ctx, timeout=_HTTP_TIMEOUT):  # nosec B310 — scheme validated above
            pass

    if lease is not None:
        _report_lease_progress(lease, 100)


def _report_lease_progress(lease: vim.HttpNfcLease, percent: int) -> None:
    """Report upload progress to the HttpNfcLease, tolerating SDK naming.

    pyVmomi exposes this as HttpNfcLeaseProgress on some versions and Progress
    on others; a progress-report failure must never abort an otherwise-healthy
    upload.
    """
    if lease is None:
        return
    report = getattr(lease, "HttpNfcLeaseProgress", None) or getattr(
        lease, "Progress", None
    )
    if report is None:
        return
    try:
        report(percent)
    except Exception as e:  # progress is best-effort — never fail the upload
        _log.debug("Lease progress report failed at %d%%: %s", percent, e)


def deploy_ova(
    si: ServiceInstance,
    ova_path: str,
    vm_name: str,
    datastore_name: str,
    network_name: str = "VM Network",
    folder_path: str | None = None,
    power_on: bool = False,
    snapshot_name: str | None = None,
    datacenter_name: str | None = None,
    cluster: str | None = None,
) -> str:
    """Deploy a VM from a local OVA file.

    Flow:
    1. Parse OVF from OVA
    2. Create import spec via OvfManager
    3. Import via ResourcePool.ImportVApp
    4. Upload VMDKs via HTTP NFC lease
    5. Optionally power on + create baseline snapshot

    Args:
        si: vSphere ServiceInstance
        ova_path: Path to local .ova file
        vm_name: Desired VM name
        datastore_name: Target datastore
        network_name: Network to attach
        folder_path: VM folder path (optional)
        power_on: Power on after deploy
        snapshot_name: Create baseline snapshot with this name (optional)

    Returns:
        Status message.
    """
    content = si.RetrieveContent()

    # Find datastore
    ds = find_datastore_by_name(si, datastore_name)
    if ds is None:
        return f"Datastore '{datastore_name}' not found."

    # Find datacenter, folder, resource pool
    try:
        datacenter = resolve_datacenter(si, datacenter_name)
        compute_resource = find_compute_resource(datacenter, cluster)
    except InventoryError as e:
        return str(e)
    vm_folder = datacenter.vmFolder
    if folder_path:
        for part in folder_path.split("/"):
            found = False
            for child in vm_folder.childEntity:
                if hasattr(child, "childEntity") and child.name == part:
                    vm_folder = child
                    found = True
                    break
            if not found:
                return f"Folder '{folder_path}' not found."

    resource_pool = compute_resource.resourcePool

    # Parse OVA
    ovf_content, disks = _read_ovf_from_ova(ova_path)
    _log.info("OVA parsed: %d disk(s) found", len(disks))

    # Create import spec
    ovf_manager = content.ovfManager
    import_spec_params = vim.OvfManager.CreateImportSpecParams(
        entityName=vm_name,
    )

    # Map OVF networks to vSphere networks
    import_spec_result = ovf_manager.CreateImportSpec(
        ovfDescriptor=ovf_content,
        resourcePool=resource_pool,
        datastore=ds,
        cisp=import_spec_params,
    )

    if import_spec_result.error:
        errors = "; ".join(str(e.msg) for e in import_spec_result.error)
        return f"OVF validation failed: {errors}"

    if import_spec_result.warning:
        for w in import_spec_result.warning:
            _log.warning("OVF warning: %s", w.msg)

    # Start import
    lease = resource_pool.ImportVApp(
        spec=import_spec_result.importSpec,
        folder=vm_folder,
    )

    # Wait for lease to be ready
    timeout = 120
    start = time.time()
    while lease.state == vim.HttpNfcLease.State.initializing:
        if time.time() - start > timeout:
            return "Import lease timed out during initialization."
        time.sleep(2)

    if lease.state == vim.HttpNfcLease.State.error:
        return f"Import lease error: {lease.error.msg if lease.error else 'Unknown'}"

    _ova_verify_ssl = get_verify_ssl(si)

    # Map each device URL to its source file by importKey, not pop-order.
    # import_spec_result.fileItem links the OVF deviceId (== deviceUrl.importKey)
    # to the file path inside the OVA. Pop-ordering writes multi-disk OVAs whose
    # device URLs arrive out of order to the wrong device.
    file_by_device = {
        fi.deviceId: fi.path for fi in (import_spec_result.fileItem or [])
    }

    # Upload disks
    try:
        device_urls = lease.info.deviceUrl
        for device_url in device_urls:
            target_url = device_url.url

            disk_name = file_by_device.get(device_url.importKey)
            if disk_name is None:
                # Fall back to deviceUrl.targetId (file path) when no fileItem
                # mapping is available.
                disk_name = getattr(device_url, "targetId", None)
            if disk_name is None or disk_name not in disks:
                _log.warning(
                    "No OVA disk maps to device %r (importKey=%r) — skipping",
                    target_url, device_url.importKey,
                )
                continue

            disk_size = disks[disk_name]
            _log.info("Uploading %s (%d MB)...", disk_name,
                      disk_size // (1024 * 1024))
            _upload_disk(lease, ova_path, disk_name, target_url, disk_size,
                         verify_ssl=_ova_verify_ssl)

        lease.Complete()
    except Exception as e:
        lease.Abort()
        return f"OVA deploy failed during upload: {e}"

    result_parts = [f"VM '{vm_name}' deployed from OVA successfully."]

    # Post-deploy: power on
    if power_on:
        try:
            msg = power_on_vm(si, vm_name)
            result_parts.append(msg)
        except Exception as e:
            result_parts.append(f"Power on failed: {e}")

    # Post-deploy: create baseline snapshot
    if snapshot_name:
        try:
            msg = create_snapshot(si, vm_name, snapshot_name,
                                  description="Baseline snapshot for sandbox",
                                  memory=False)
            result_parts.append(msg)
        except Exception as e:
            result_parts.append(f"Snapshot creation failed: {e}")

    return " | ".join(result_parts)
