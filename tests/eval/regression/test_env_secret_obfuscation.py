"""Regression: .env password obfuscation (b64: auto-encode + decode).

A plaintext vCenter/controller password in ~/.<skill>/.env must not survive on
disk in grep-able form: on first load it is rewritten to ``b64:...``
(obfuscation, NOT encryption) and decoded transparently on read. Critically,
the stored value must equal what python-dotenv would have parsed — encoding must
never change the effective password. Failures block release.
"""
from __future__ import annotations

from vmware_policy.fsperms import assert_owner_only

import importlib
import os
import stat

from dotenv import dotenv_values

config = importlib.import_module("vmware_aiops.config")


def _write_env(tmp_path, body: str):
    env = tmp_path / ".env"
    env.write_text(body, encoding="utf-8")
    os.chmod(env, 0o600)
    return env


def _decoded(env):
    """The password the app would actually use, per key, after encode."""
    return {k: config._decode_secret(v) for k, v in dotenv_values(env).items()}


def test_plaintext_password_is_autoencoded_on_disk(tmp_path):
    env = _write_env(tmp_path, "VMWARE_PROD_PASSWORD=Secr3t\nOTHER=keep\n")
    config._autoencode_env_file(env)
    on_disk = env.read_text(encoding="utf-8")
    assert "b64:" in on_disk
    assert "Secr3t" not in on_disk            # plaintext gone -> grep-safe
    assert "OTHER=keep" in on_disk            # unrelated line untouched


def test_inline_comment_value_matches_dotenv(tmp_path):
    """HIGH regression: a value with an inline comment must encode to exactly
    what dotenv parses (the comment stripped), not the raw line."""
    env = _write_env(tmp_path, "VMWARE_PROD_PASSWORD=secret  # note\n")
    config._autoencode_env_file(env)
    assert _decoded(env)["VMWARE_PROD_PASSWORD"] == "secret"
    assert "secret  # note" not in env.read_text(encoding="utf-8")


def test_trailing_whitespace_matches_dotenv(tmp_path):
    """MEDIUM regression: unquoted trailing whitespace is stripped by dotenv;
    the encoded value must match."""
    env = _write_env(tmp_path, "VMWARE_PROD_PASSWORD=a b c   \n")
    config._autoencode_env_file(env)
    assert _decoded(env)["VMWARE_PROD_PASSWORD"] == "a b c"


def test_b64_prefixed_real_password_roundtrips(tmp_path):
    """MEDIUM regression: a real password starting with 'b64:' that is NOT valid
    base64 must round-trip, not be mistaken for an already-encoded value."""
    env = _write_env(tmp_path, "VMWARE_PROD_PASSWORD=b64:hunter2\n")
    config._autoencode_env_file(env)
    assert "hunter2" not in env.read_text(encoding="utf-8")
    assert _decoded(env)["VMWARE_PROD_PASSWORD"] == "b64:hunter2"


def test_roundtrip_with_special_chars(tmp_path):
    secret = "P@ss w0rd#!"
    env = _write_env(tmp_path, f'VMWARE_PROD_PASSWORD="{secret}"\n')
    config._autoencode_env_file(env)
    assert secret not in env.read_text(encoding="utf-8")
    assert _decoded(env)["VMWARE_PROD_PASSWORD"] == secret


def test_idempotent_already_encoded_not_rewritten(tmp_path):
    env = _write_env(tmp_path, "VMWARE_PROD_PASSWORD=Secr3t\n")
    config._autoencode_env_file(env)
    first = env.read_text(encoding="utf-8")
    config._autoencode_env_file(env)          # second pass is a no-op
    assert env.read_text(encoding="utf-8") == first


def test_autoencode_preserves_0600_permissions(tmp_path):
    env = _write_env(tmp_path, "VMWARE_PROD_PASSWORD=Secr3t\n")
    config._autoencode_env_file(env)
    assert_owner_only(env)


def test_empty_value_left_untouched(tmp_path):
    env = _write_env(tmp_path, "VMWARE_PROD_PASSWORD=\n")
    config._autoencode_env_file(env)
    assert env.read_text(encoding="utf-8") == "VMWARE_PROD_PASSWORD=\n"


def test_plaintext_passes_through_decode():
    assert config._decode_secret("plainpw") == "plainpw"


def test_malformed_b64_treated_as_plaintext():
    """A 'b64:'-prefixed value that is not valid base64 is returned verbatim
    (treated as a literal password), never raised."""
    assert config._decode_secret("b64:!!!notbase64!!!") == "b64:!!!notbase64!!!"
