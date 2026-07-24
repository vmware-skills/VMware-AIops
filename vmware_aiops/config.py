"""Configuration management for VMware AIops.

Loads targets and settings from YAML config file + environment variables.
Passwords are NEVER stored in config files — always via environment variables.
"""

from __future__ import annotations

import base64
import binascii
import logging
import os
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import yaml
from dotenv import dotenv_values, load_dotenv, set_key

CONFIG_DIR = Path.home() / ".vmware-aiops"
CONFIG_FILE = CONFIG_DIR / "config.yaml"
ENV_FILE = CONFIG_DIR / ".env"

_log = logging.getLogger("vmware-aiops.config")

_PW_KEY_RE = re.compile(r"[A-Z][A-Z0-9_]*_PASSWORD")


def _is_b64_token(value: str) -> tuple[bool, str]:
    """Return ``(True, decoded)`` if ``value`` is a valid ``b64:`` token, else ``(False, "")``.

    Recognises already-encoded values (for idempotency) and decodes on read. A
    value that merely *starts with* ``b64:`` but is not valid base64 (e.g. a real
    password ``b64:hunter2``) is NOT a token — it is treated as plaintext, so such
    a password still round-trips correctly instead of being corrupted.
    """
    if not value.startswith("b64:"):
        return (False, "")
    try:
        return (True, base64.b64decode(value[4:], validate=True).decode("utf-8"))
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return (False, "")


def _decode_secret(value: str) -> str:
    """Decode a ``b64:`` token; any other value passes through unchanged.

    Obfuscation to defeat casual grep — NOT encryption.
    """
    ok, decoded = _is_b64_token(value)
    return decoded if ok else value


def _autoencode_env_file(env_file: Path) -> None:
    """Rewrite plaintext ``*_PASSWORD`` values in .env to grep-safe ``b64:`` form.

    Values are read and written through python-dotenv's own parser/serializer
    (``dotenv_values`` + ``set_key``), so the stored value is exactly what
    ``load_dotenv`` would return — quoting, inline comments, and trailing
    whitespace are handled identically and the secret never drifts from the
    configured one. Idempotent (already-``b64:`` tokens are skipped); only
    ``*_PASSWORD`` keys are touched. Obfuscation, not encryption.
    """
    if not env_file.exists():
        return
    try:
        parsed = dotenv_values(env_file)
    except OSError:
        return

    changed = False
    for key, value in parsed.items():
        if not value or not _PW_KEY_RE.fullmatch(key) or _is_b64_token(value)[0]:
            continue
        encoded = "b64:" + base64.b64encode(value.encode("utf-8")).decode("ascii")
        try:
            set_key(str(env_file), key, encoded, quote_mode="never")
            changed = True
        except OSError as exc:
            _log.warning("Could not auto-encode %s in %s: %s", key, env_file, exc)

    if not changed:
        return
    try:
        os.chmod(env_file, 0o600)
    except OSError:
        pass
    _log.warning(
        "Auto-encoded plaintext password(s) in %s to b64: (grep-safe; "
        "obfuscation, not encryption).",
        env_file,
    )


# Auto-encode any plaintext passwords in .env, then load it into the environment
_autoencode_env_file(ENV_FILE)
load_dotenv(ENV_FILE)


def _check_env_permissions() -> None:
    """Warn if .env file has permissions wider than owner-only (600)."""
    if not ENV_FILE.exists():
        return
    try:
        mode = ENV_FILE.stat().st_mode
        if mode & (stat.S_IRWXG | stat.S_IRWXO):
            _log.warning(
                "Security warning: %s has permissions %s (should be 600). "
                "Run: chmod 600 %s",
                ENV_FILE,
                oct(stat.S_IMODE(mode)),
                ENV_FILE,
            )
    except OSError:
        pass


_check_env_permissions()


class ConfigError(OSError):
    """A configuration problem the operator can fix, safe to show an agent.

    Subclasses ``OSError`` so the CLI paths that already catch ``OSError`` keep
    working. The point of the narrow type is the MCP path: ``_safe_error``
    passes this through verbatim, and passing through bare ``OSError`` also
    passed through TLS, DNS and socket errors carrying hostnames and URLs.
    """


@dataclass(frozen=True)
class TargetConfig:
    """A vCenter or ESXi connection target."""

    name: str
    host: str
    config_username: str
    """Username as written in config.yaml. Read :attr:`username` instead — the
    env var overrides this, and the override is what actually gets used."""
    type: Literal["vcenter", "esxi"] = "vcenter"
    port: int = 443
    verify_ssl: bool = True
    environment: str = ""
    """Which environment this target is, e.g. production / staging / lab.

    An optional label. A ``deny`` rule may scope itself to an environment
    (for example, refusing a tool only where ``environment: production``); a
    target that declares none is simply not matched by such a rule and is
    never refused for lacking a label. See :mod:`vmware_policy.environment`.
    """

    @property
    def username(self) -> str:
        """Username for this target, env var winning over config.yaml.

        Resolved on every access, exactly like :attr:`password`. Reading it
        once at load time would split the pair the override exists to keep
        whole: a secret sidecar that rotates both halves mid-process would
        move the password and leave the username behind, and the login would
        use an account/password combination that was never issued together.
        """
        return os.environ.get(
            f"VMWARE_{self.name.upper().replace('-', '_')}_USERNAME",
            self.config_username,
        )

    @property
    def password(self) -> str:
        env_key = f"VMWARE_{self.name.upper().replace('-', '_')}_PASSWORD"
        pw = os.environ.get(env_key, "")
        if not pw:
            raise ConfigError(
                f"Password not found for target '{self.name}'. "
                f"Set environment variable {env_key}, or add "
                f"{env_key}=<password> to {ENV_FILE} (chmod 600). "
                f"Run 'vmware-aiops init' to do both, then 'vmware-aiops doctor' to verify."
            )
        return _decode_secret(pw)


@dataclass(frozen=True)
class ScannerConfig:
    """Scanner daemon settings."""

    enabled: bool = True
    interval_minutes: int = 15
    log_types: tuple[str, ...] = ("vpxd", "hostd", "vmkernel")
    severity_threshold: str = "warning"
    lookback_hours: int = 1


@dataclass(frozen=True)
class NotifyConfig:
    """Notification settings."""

    log_file: str = str(CONFIG_DIR / "scan.log")
    webhook_url: str = ""
    webhook_timeout: int = 10


@dataclass(frozen=True)
class AppConfig:
    """Top-level application config."""

    targets: tuple[TargetConfig, ...] = ()
    scanner: ScannerConfig = field(default_factory=ScannerConfig)
    notify: NotifyConfig = field(default_factory=NotifyConfig)

    def get_target(self, name: str) -> TargetConfig:
        for t in self.targets:
            if t.name == name:
                return t
        available = ", ".join(t.name for t in self.targets)
        raise KeyError(
            f"Target '{name}' not found. Available: {available}. "
            f"Pass --target with one of those names, or add the target to "
            f"{CONFIG_FILE} and re-run."
        )

    def environment_for(self, name: str | None) -> str:
        """Return the environment declared by ``name``, or by the default target.

        An empty name means "the caller omitted --target", which resolves to
        ``default_target`` — the same target the connection layer would use, so
        policy and connection never disagree about which host is in play.
        Returns "" when the target is unknown or declares nothing.
        """
        try:
            target = self.get_target(name) if name else self.default_target
        except (KeyError, ValueError):
            return ""
        return target.environment

    @property
    def default_target(self) -> TargetConfig:
        if not self.targets:
            raise ValueError(
                f"No targets configured in {CONFIG_FILE}. "
                f"Run 'vmware-aiops init' to create one, then 'vmware-aiops doctor' to verify."
            )
        return self.targets[0]


def load_config(config_path: Path | None = None) -> AppConfig:
    """Load config from YAML file, with env var overrides for passwords."""
    path = config_path or CONFIG_FILE
    if not path.exists():
        raise FileNotFoundError(
            f"Config file not found: {path}\n"
            f"Run 'vmware-aiops init' to create it, or copy config.example.yaml "
            f"to {CONFIG_FILE} and edit it."
        )

    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    if isinstance(raw, dict) and "read_only" in raw:
        _log.warning(
            "'read_only' in config is no longer honored (the skill-level read-only "
            "switch was removed in v1.8.7). To run this agent read-only, point it at "
            "a read-only vCenter/NSX service account (RBAC) — enforced at the "
            "platform. Remove the 'read_only' key to silence this warning."
        )

    # `username` is a property on TargetConfig, resolved env-first at access
    # time like `password`. See TargetConfig.username for why late binding
    # matters here.
    targets = tuple(
        TargetConfig(
            name=t["name"],
            host=t["host"],
            config_username=t.get("username", "administrator@vsphere.local"),
            type=t.get("type", "vcenter"),
            port=t.get("port", 443),
            verify_ssl=t.get("verify_ssl", True),
            environment=str(t.get("environment", "") or "").strip(),
        )
        for t in raw.get("targets", [])
    )

    scanner_raw = raw.get("scanner", {})
    scanner = ScannerConfig(
        enabled=scanner_raw.get("enabled", True),
        interval_minutes=scanner_raw.get("interval_minutes", 15),
        log_types=tuple(scanner_raw.get("log_types", ["vpxd", "hostd", "vmkernel"])),
        severity_threshold=scanner_raw.get("severity_threshold", "warning"),
        lookback_hours=scanner_raw.get("lookback_hours", 1),
    )

    notify_raw = raw.get("notify", {})
    notify = NotifyConfig(
        log_file=notify_raw.get("log_file", str(CONFIG_DIR / "scan.log")),
        webhook_url=notify_raw.get("webhook_url", ""),
        webhook_timeout=notify_raw.get("webhook_timeout", 10),
    )

    return AppConfig(
        targets=targets,
        scanner=scanner,
        notify=notify,
    )
