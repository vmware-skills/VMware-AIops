"""Text I/O must declare its codec, or it decodes with the machine's locale.

Real-hardware finding, 2026-08-30. On a Windows Server 2025 host with locale
cp936 (GBK), reading a UTF-8 file with no ``encoding=`` uses the locale codec.
In vmware-policy that broke the rules file and the policy engine **failed
open**: a ``freeze-production-writes`` rule that should DENY came back ALLOW. In
vmware-harden it killed the baselines while the doctor still reported them
loaded.

This family ships a 等保 2.0 baseline and Chinese documentation, so its users
are exactly the people on non-UTF-8 Windows — the population least likely to be
represented on the UTF-8 CI runner where every one of these files is green.

Mechanical rather than remembered (形态 #6): a new undeclared read fails here.

Known blind spot, stated rather than left implicit: ``Path.open()`` is not
checked. Distinguishing it from ``tarfile.open`` / ``gzip.open`` needs the
receiver's type, which a parser does not have, and flagging those would push
someone into a wrong "fix" — their ``encoding`` means member names, or nothing.
Nothing in this package opens text through ``Path.open`` today.
"""

import ast
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[3] / "vmware_aiops"


def test_every_text_read_declares_its_encoding() -> None:
    sources = sorted(PACKAGE.rglob("*.py"))
    assert sources, (
        f"no sources under {PACKAGE} — an empty scan is not a pass (形态 #1)"
    )

    offenders: list[str] = []
    for src in sources:
        tree = ast.parse(src.read_text(encoding="utf-8"), filename=str(src))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Name):
                # Bare open(): the builtin.
                name = func.id if func.id == "open" else ""
            elif isinstance(func, ast.Attribute):
                # Path.read_text / write_text. Deliberately NOT every `.open`:
                # tarfile.open, gzip.open and zipfile.open are archive readers
                # whose `encoding` means something else or nothing at all, and
                # flagging them would push someone into a wrong "fix".
                name = func.attr if func.attr in ("read_text", "write_text") else ""
            else:
                name = ""
            if not name:
                continue
            mode = ""
            for kw in node.keywords:
                if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
                    mode = str(kw.value.value)
            if name == "open" and len(node.args) >= 2:
                arg = node.args[1]
                if isinstance(arg, ast.Constant):
                    mode = str(arg.value)
            if "b" in mode:
                continue  # binary: no codec involved
            if not any(kw.arg == "encoding" for kw in node.keywords):
                offenders.append(
                    f"{src.relative_to(PACKAGE.parent)}:{node.lineno} {name}()"
                )

    assert not offenders, (
        "text I/O without encoding='utf-8' decodes with the machine's locale "
        "codec and breaks on cp936 / Shift-JIS / latin-1 hosts:\n  "
        + "\n  ".join(offenders)
    )
