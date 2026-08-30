"""Session-wide sandbox: the suite must not touch the operator's real files.

Installed at *import* time, not in a fixture. The per-skill audit logger binds
``Path.home()`` when its module is imported, and a fixture — even a
session-scoped autouse one — runs after collection has already imported every
test module and, with them, the package. By then the path is a constant.

Two variables, because the family writes two audit trails:

* ``OPS_HOME`` moves ``vmware_policy``'s shared ``audit.db`` (and the policy,
  budget and undo state beside it); ``vmware_policy.paths.ops_home()`` reads it
  on every call and defaults to ``~/.vmware``.
* ``HOME`` moves the per-skill JSON Lines log under ``~/.vmware-aiops``, which
  resolves through ``Path.home()`` and ignores ``OPS_HOME`` entirely.

Before this, running the suite appended 19 rows per run to the operator's
real ``~/.vmware/audit.db`` — which held 30,779 rows dominated by tool names no
one had ever invoked, including 1,400 ``ako_config_upgrade`` entries for a
destructive operation that never happened. An audit trail containing test
fiction cannot answer the question it is kept to answer.
"""

from __future__ import annotations

import atexit
import os
import shutil
import tempfile
from pathlib import Path

from vmware_policy.audit import reset_engine

#: The operator's real home, captured before the redirect, so a regression test
#: can express "not the real audit database" against it.
REAL_HOME = Path(os.path.expanduser("~"))

SANDBOX_HOME = Path(tempfile.mkdtemp(prefix="vmware-aiops-tests-"))

os.environ["HOME"] = str(SANDBOX_HOME)
os.environ["OPS_HOME"] = str(SANDBOX_HOME / ".vmware")
# expanduser() consults USERPROFILE on Windows; keep every spelling pointing
# here so the sandbox holds on the family's Windows test host too.
os.environ["USERPROFILE"] = str(SANDBOX_HOME)

# vmware_policy's audit engine is a lazily built singleton keyed to the path it
# first resolved. A stale binding would send every write back to the real file.
reset_engine()

atexit.register(shutil.rmtree, SANDBOX_HOME, True)
