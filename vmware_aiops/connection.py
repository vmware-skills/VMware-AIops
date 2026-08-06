"""Connection management for vCenter and ESXi hosts.

Handles multi-target connections via pyVmomi with session reuse.
"""

from __future__ import annotations

import atexit
import socket
import ssl
from typing import TYPE_CHECKING

from pyVmomi import vim

if TYPE_CHECKING:
    from pyVmomi.vim import ServiceInstance

from vmware_aiops.config import CONFIG_FILE, AppConfig, ConfigError, TargetConfig, load_config


# ServiceInstance is a pyVmomi ManagedObject — its __setattr__ rejects any
# attribute not in its allowed list (raises "Managed object attributes are
# read-only" on pyVmomi 8.x). We keep per-connection metadata in this module
# dict, keyed by id(si). Cleared via atexit when the SI is disconnected.
# 踩坑 #32 (2026-05-19, 客户 vCenter 8.0U3 现场).
_SI_VERIFY_SSL: dict[int, bool] = {}


def get_verify_ssl(si: ServiceInstance) -> bool:
    """Return verify_ssl flag stashed by the connect() that created ``si``.

    Defaults to True (strict) if the SI was created outside this manager.
    """
    return _SI_VERIFY_SSL.get(id(si), True)


class ConnectionManager:
    """Manages connections to multiple vCenter/ESXi targets."""

    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._connections: dict[str, ServiceInstance] = {}

    @classmethod
    def from_config(cls, config: AppConfig | None = None) -> ConnectionManager:
        cfg = config or load_config()
        return cls(cfg)

    def connect(self, target_name: str | None = None) -> ServiceInstance:
        """Connect to a target by name, or the default target."""
        target = (
            self._config.get_target(target_name)
            if target_name
            else self._config.default_target
        )

        if target.name in self._connections:
            si = self._connections[target.name]
            try:
                # Probe liveness; expired tokens can surface as a None
                # currentSession instead of raising.
                alive = si.content.sessionManager.currentSession is not None
            except Exception:
                # Any failure (NotAuthenticated, socket error, …) means the
                # cached session is unusable — drop it and reconnect below.
                alive = False
            if alive:
                return si
            # Evict the id(si)-keyed side store NOW rather than waiting for
            # atexit: once the old si is GC'd, a new si for a DIFFERENT
            # target can reuse the same id() value and read stale verify_ssl
            # (id-reuse hazard).
            _SI_VERIFY_SSL.pop(id(si), None)
            del self._connections[target.name]

        si = self._create_connection(target)
        self._connections[target.name] = si
        return si

    def disconnect(self, target_name: str) -> None:
        """Disconnect from a specific target."""
        if target_name in self._connections:
            from pyVim.connect import Disconnect

            Disconnect(self._connections[target_name])
            del self._connections[target_name]

    def disconnect_all(self) -> None:
        """Disconnect from all targets."""
        for name in list(self._connections):
            self.disconnect(name)

    def list_targets(self) -> list[str]:
        """List all configured target names."""
        return [t.name for t in self._config.targets]

    def connect_all(self) -> tuple[list[tuple[str, ServiceInstance]], list[tuple[str, str]]]:
        """Connect to every configured target, tolerating per-target failures.

        Returns ``(sessions, unreachable)`` — ``[(name, si)]`` for targets that
        connected and ``[(name, reason)]`` for those that did not — so the
        cross-vCenter attention view degrades gracefully (one dead vCenter never
        sinks the roll-up). The reason is class-name only, so no host:port or
        credential detail leaks.
        """
        sessions: list[tuple[str, ServiceInstance]] = []
        unreachable: list[tuple[str, str]] = []
        for name in self.list_targets():
            try:
                sessions.append((name, self.connect(name)))
            except Exception as e:  # noqa: BLE001 — any connect failure degrades to "unreachable"
                unreachable.append((name, type(e).__name__))
        return sessions, unreachable

    def list_connected(self) -> list[str]:
        """List currently connected target names."""
        return list(self._connections.keys())

    @staticmethod
    def _create_connection(target: TargetConfig) -> ServiceInstance:
        """Create a new pyVmomi connection."""
        from pyVim.connect import Disconnect, SmartConnect

        context = None
        if not target.verify_ssl:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE

        # Resolve credentials BEFORE the try block. Both are properties, and
        # the missing-password one raises ConfigError — an OSError subclass the
        # handlers below would otherwise relabel as a TLS/DNS failure, burying
        # this family's most common first-run error behind the wrong remedy.
        # Read adjacently so a sidecar rotating both halves cannot split them.
        user, pwd = target.username, target.password

        try:
            si = SmartConnect(
                host=target.host,
                user=user,
                pwd=pwd,
                port=target.port,
                sslContext=context,
                disableSslCertValidation=not target.verify_ssl,
            )
        # These three carry the certificate subject, the unresolved hostname
        # and the full host:port respectively. _safe_error no longer passes
        # bare OSError through, so an agent would see only the class name —
        # translate to authored text that names the target and the setting to
        # change, and never interpolates the original exception. The raw detail
        # stays on __cause__, which only reaches the server-side log.
        except ssl.SSLError as exc:
            raise ConfigError(
                f"TLS verification failed for target '{target.name}' — set "
                f"verify_ssl: false on that target in {CONFIG_FILE} if it uses a "
                f"self-signed certificate, or install its CA on this host."
            ) from exc
        except socket.gaierror as exc:
            raise ConfigError(
                f"Could not resolve the host configured for target '{target.name}' "
                f"— check that target's 'host' value in {CONFIG_FILE} for a typo "
                f"or a DNS suffix this machine cannot resolve."
            ) from exc
        except OSError as exc:
            raise ConnectionError(
                f"Could not reach target '{target.name}' — check that the "
                f"vCenter/ESXi host is up and that its 'host' and 'port' in "
                f"{CONFIG_FILE} are reachable from this machine."
            ) from exc

        # Stash verify_ssl in module dict (NOT on si — pyVmomi 8.x rejects
        # setattr on ManagedObject, see 踩坑 #32). Consumers in ops/* read via
        # get_verify_ssl(si).
        _SI_VERIFY_SSL[id(si)] = target.verify_ssl

        def _cleanup(_si: ServiceInstance = si) -> None:
            _SI_VERIFY_SSL.pop(id(_si), None)
            try:
                Disconnect(_si)
            except Exception:
                pass

        atexit.register(_cleanup)
        return si


def get_content(si: ServiceInstance) -> vim.ServiceInstanceContent:
    """Shortcut to get ServiceContent from a ServiceInstance."""
    return si.RetrieveContent()
