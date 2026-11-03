#!/usr/bin/env python3
"""Check the AIops file lock on a real Windows host (msvcrt.locking).

The family's ``_filelock.py`` (VMware-AIops and VMware-Storage) has a Windows
branch that the ordinary CI tests drive with a fake ``msvcrt``. Run the real
branch on Windows with:

    py tests\\windows_lock_check.py vmware_aiops\\_filelock.py

Standard library only; writes only under a fresh temp directory. Checks:
  1. four processes doing read-modify-write under the lock keep every row;
  2. a lock held by another process times out within the bound and names the
     lock file;
  3. the lock is free again once the holder exits (the OS releases it).
Exit 0 = all pass, 1 = a check failed, 2 = not run on Windows.
"""

from __future__ import annotations

import importlib.util
import json
import multiprocessing as mp
import sys
import tempfile
import time
from pathlib import Path


def _load(path: str):
    spec = importlib.util.spec_from_file_location("_filelock_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _writer(module_path: str, work: str, name: str, start) -> None:
    lock = _load(module_path)
    data = Path(work) / "registry.json"
    start.wait()
    with lock.locked(Path(work) / "registry.json.lock", timeout=30.0):
        rows = json.loads(data.read_text(encoding="utf-8")) if data.exists() else []
        time.sleep(0.2)  # widen the window a lost update needs
        data.write_text(json.dumps(rows + [name]), encoding="utf-8")


def _holder(module_path: str, work: str, held, release) -> None:
    lock = _load(module_path)
    with lock.locked(Path(work) / "registry.json.lock", timeout=5.0):
        held.set()
        release.wait(30)


def main() -> int:
    if sys.platform != "win32":
        print("not Windows: this checks the msvcrt branch only")
        return 2
    module_path = sys.argv[1]
    lock = _load(module_path)
    assert lock.fcntl is None and lock.msvcrt is not None, "Windows branch not selected"
    failures = []
    with tempfile.TemporaryDirectory() as work:
        start = mp.Event()
        procs = [mp.Process(target=_writer, args=(module_path, work, f"t{i}", start))
                 for i in range(4)]
        for p in procs:
            p.start()
        start.set()
        for p in procs:
            p.join(60)
            if p.is_alive():
                p.terminate()
                p.join(5)
        crashed = [p.exitcode for p in procs if p.exitcode != 0]
        data = Path(work) / "registry.json"
        rows = json.loads(data.read_text(encoding="utf-8")) if data.exists() else []
        if crashed:
            failures.append(f"1. writer processes failed (exit codes {crashed}); rows: {rows}")
        elif sorted(rows) != ["t0", "t1", "t2", "t3"]:
            failures.append(f"1. concurrent writers lost rows: {rows}")
        else:
            print("1. ok: four concurrent writers kept every row")

        held, release = mp.Event(), mp.Event()
        holder = mp.Process(target=_holder, args=(module_path, work, held, release))
        holder.start()
        if not held.wait(30):
            failures.append("2. the holder process never took the lock")
            release.set()
            holder.join(30)
            if holder.is_alive():
                holder.terminate()
                holder.join(5)
            for f in failures:
                print("FAIL", f)
            return 1
        started = time.monotonic()
        try:
            with lock.locked(Path(work) / "registry.json.lock", timeout=0.5):
                failures.append("2. took a lock another process holds")
        except lock.FileLockTimeoutError as exc:
            waited = time.monotonic() - started
            if "registry.json.lock" not in str(exc) or waited > 5:
                failures.append(f"2. timeout message or wait wrong: {exc} ({waited:.1f}s)")
            else:
                print(f"2. ok: held lock timed out after {waited:.1f}s naming the lock file")
        release.set()
        holder.join(30)
        if holder.is_alive():
            holder.terminate()
            holder.join(5)
            failures.append("2. the holder did not exit after release")

        try:
            with lock.locked(Path(work) / "registry.json.lock", timeout=2.0):
                print("3. ok: lock free after the holder exited")
        except lock.FileLockTimeoutError:
            failures.append("3. lock still held after the holder exited")
    for f in failures:
        print("FAIL", f)
    return 1 if failures else 0


if __name__ == "__main__":
    mp.freeze_support()
    sys.exit(main())
