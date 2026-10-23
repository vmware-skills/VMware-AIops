"""Regression tests for private local state and failed atomic writes."""

from __future__ import annotations

import os
import stat

import pytest

from vmware_aiops import _fsutil


@pytest.mark.unit
def test_secure_mkdir_restricts_an_existing_directory(tmp_path) -> None:
    directory = tmp_path / "state"
    directory.mkdir(mode=0o755)
    os.chmod(directory, 0o755)

    _fsutil.secure_mkdir(directory)

    if os.name == "posix":
        assert stat.S_IMODE(directory.stat().st_mode) == 0o700


@pytest.mark.unit
def test_secure_chmod_file_restricts_an_existing_file(tmp_path) -> None:
    state_file = tmp_path / "state.json"
    state_file.write_text("{}", encoding="utf-8")
    os.chmod(state_file, 0o644)

    _fsutil.secure_chmod_file(state_file)

    if os.name == "posix":
        assert stat.S_IMODE(state_file.stat().st_mode) == 0o600


@pytest.mark.unit
def test_atomic_write_failure_preserves_old_file_and_removes_temporary_file(
    tmp_path, monkeypatch
) -> None:
    state_file = tmp_path / "state.json"
    state_file.write_text("old", encoding="utf-8")

    def fail_replace(_source, _destination) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(_fsutil.os, "replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        _fsutil.atomic_write_text(state_file, "new")

    assert state_file.read_text(encoding="utf-8") == "old"
    assert sorted(path.name for path in tmp_path.iterdir()) == ["state.json"]
