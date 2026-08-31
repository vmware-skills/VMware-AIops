"""Regression — the error-actionability grader must not credit phantom tools.

The ``names_artifact`` dimension used to match on the prefixes ``list_`` and
``get_``, which made it blind in both directions. It scored zero for real tools
named anything else (``cluster_info``, ``browse_datastore``, ``vm_info``), and
it scored full marks for names that do not exist — vmware-monitor shipped an
error telling the model to run ``list_vms``, a tool with no such name on the
surface, and the rubric called that a well-taught error.

That second half is the one worth a regression test. A grader that rewards
pointing at an imaginary tool actively selects for the failure it claims to
measure: the message reads as helpful, scores as helpful, and strands the model
on a call it cannot make.

These run against a fabricated tool list rather than the live registry on
purpose. The property under test is "only names actually on the surface count",
and passing the surface in explicitly is what makes that property visible —
against the real registry the test would drift every time a tool is added.
"""

from __future__ import annotations

from types import SimpleNamespace

from ..capability._scoring import undocumented_args, value_set_candidates
from ..capability._skill import CLI_NAME
from ..capability.test_error_actionability import _artifact_matcher

SURFACE = ("cluster_info", "list_virtual_machines", "vm_power_on")


def _names_artifact(text: str) -> bool:
    return _artifact_matcher([SimpleNamespace(name=n) for n in SURFACE])(text)


def test_credits_a_tool_that_is_on_the_surface():
    assert _names_artifact("Host not in cluster. Run cluster_info to see its members.")


def test_credits_a_tool_whose_name_has_no_list_or_get_prefix():
    """The original blind spot: a real tool the prefix heuristic could not see."""
    assert _names_artifact("Not found. Run vm_power_on after checking the state.")


def test_rejects_a_tool_that_is_not_on_the_surface():
    assert not _names_artifact("VM not found. Run list_vms to see available VMs.")


def test_rejects_a_name_that_merely_extends_a_real_tool():
    """Substring matching would credit this; word boundaries are why it does not."""
    assert not _names_artifact("Run cluster_infos to see members.")


def test_rejects_a_truncated_real_tool_name():
    assert not _names_artifact("Run cluster_inf to see members.")


def test_credits_a_companion_skill_handoff():
    """Routing to a sibling skill is a followable instruction, not a dead end."""
    assert _names_artifact("Run 'vmware-storage iscsi status esx-01' (vmware-storage skill).")


def test_rejects_an_unrelated_command():
    assert not _names_artifact("Run 'kubectl rollout status' and retry.")


def test_rejects_a_remedy_that_names_nothing():
    assert not _names_artifact("Verify connectivity and retry the operation.")


# ── a cited CLI command must exist, like a cited tool ────────────────────────
#
# Tool names were checked against the live registry while CLI invocations were
# taken on faith, so one skill shipped thirteen hints telling the model to run
# `<cli> doctor` — a command it does not have; the real one is `check`. Every
# one scored full marks, which made the fix and the phantom worth the same.

#: Full runnable paths, as the click tree reports them. `pool` is a group and
#: appears only as a prefix — never as a command you can run on its own.
REAL_COMMANDS = frozenset({"check", "status", "init", "pool members", "pool enable"})


def _with_commands(text: str, commands=REAL_COMMANDS) -> bool:
    return _artifact_matcher(
        [SimpleNamespace(name=n) for n in SURFACE], cli_commands=commands
    )(text)


def test_credits_a_cli_command_that_exists():
    assert _with_commands(f"Run '{CLI_NAME} check' to verify connectivity.")


def test_rejects_a_cli_command_that_does_not_exist():
    assert not _with_commands(f"Run '{CLI_NAME} not-a-command' to verify connectivity.")


def test_a_companion_prefix_does_not_credit_this_skill_naming_itself():
    """``vmware-nsx`` is a companion; ``vmware-nsx-security`` is not its own.

    Substring matching let the longer-named skill score a "documented hand-off"
    on every mention of itself, which also meant its own CLI citations were
    never checked against its own command tree — the phantom check silently
    did not apply to one member of the family.
    """
    assert not _with_commands(f"Run '{CLI_NAME} not-a-command' to fix it.")


def test_rejects_a_phantom_subcommand_of_a_COMPANION_cli():
    """The companion path was exempt from the check built to catch exactly this.

    Any mention of a sibling skill's CLI scored a documented hand-off outright,
    so `run 'vmware-storage not-a-command'` earned full marks in all twelve
    repos — the `<cli> doctor` phantom, reopened one hop away.
    """
    assert not _names_artifact("Run 'vmware-storage not-a-command' to fix it.")


def test_credits_a_real_subcommand_of_a_companion_cli():
    assert _names_artifact("Run 'vmware-storage iscsi status esx-01' to check it.")


def test_credits_a_companion_named_without_a_subcommand():
    """A bare hand-off claims nothing to verify."""
    assert _names_artifact("Datastore names come from the vmware-storage skill.")


def test_a_companion_tool_is_not_a_phantom():
    """Cross-skill routing is the promoted pattern; a sibling's tool is real.

    Exercises the union the live check builds, not a set assembled here — the
    sibling half can be dropped without any repo noticing if its own messages
    happen not to route outward, so the union itself is what needs pinning.
    """
    from ..capability.test_error_actionability import (
        _citable_tool_names,
        _phantom_tool_citations,
    )

    known = _citable_tool_names([SimpleNamespace(name=n) for n in SURFACE])
    assert "list_esxi_hosts" in known, "a sibling skill's tool must be citable"
    assert not _phantom_tool_citations("run list_esxi_hosts to see hosts.", known)
    assert _phantom_tool_citations("run list_vms to see vms.", known) == ["list_vms"]


def test_a_filename_after_a_citation_verb_is_not_a_tool():
    """A citation ends at the word, not at a dot.

    "run migrate_db.sh" names a script, not a tool, and flagging it would be a
    false phantom. An earlier version of this test used "see RELEASE_NOTES.md"
    — which passes whether or not the guard exists, because ``see`` is not a
    citation verb at all. It asserted nothing about the thing it was named for.
    """
    from ..capability.test_error_actionability import _phantom_tool_citations
    from ..capability._family import ALL_TOOLS

    known = frozenset(n.lower() for n in ALL_TOOLS)
    assert not _phantom_tool_citations("run migrate_db.sh and retry", known)
    assert _phantom_tool_citations("run migrate_db and retry", known) == ["migrate_db"]


def test_an_error_returned_via_a_local_is_still_seen(tmp_path):
    """`msg = render(...)` then `return msg` must not hide the payload.

    Reading only the return expression made one skill's entire error surface
    invisible — the scan reported zero sites for a repo with 28 tools, and the
    empty-guard fired instead of the metric. The style is ordinary; the scan was
    narrow.

    Fed a fabricated module rather than this repo's, so the property is pinned
    whether or not any real skill currently writes its handler that way.
    """
    from ..capability.test_error_actionability import _error_returns_in_server

    (tmp_path / "server.py").write_text(
        "_HINT = 'Run cluster_info to see members.'\n"
        "def direct():\n"
        "    try:\n"
        "        pass\n"
        "    except Exception as exc:\n"
        "        return {'error': str(exc), 'hint': _HINT}\n"
        "def via_local():\n"
        "    try:\n"
        "        pass\n"
        "    except Exception as exc:\n"
        "        payload = {'error': str(exc), 'hint': _HINT}\n"
        "        return payload\n"
    , encoding="utf-8")
    sites = list(_error_returns_in_server(server_dir=tmp_path, module=None))

    assert len(sites) == 2, f"a returned local was not seen: {sites}"
    assert all(is_dict for _ln, is_dict, *_ in sites)
    assert all(has_hint for _ln, _d, has_hint, *_ in sites), "the hint constant must resolve"


# ── the score file is a baseline, and a baseline must not shrink ────────────


def test_a_partial_run_does_not_erase_metrics_it_did_not_measure(tmp_path):
    """Running one measurement must not delete the other twelve.

    ``pytest tests/eval/capability/test_x.py`` used to rewrite the whole file
    with only what that selection collected. Running a single measurement across
    the family cut all twelve baselines from thirteen metrics to three, and one
    ``git add -A`` would have made it permanent — leaving the next release with
    nothing to diff against, which is the corruption this file exists to prevent.
    """
    import json

    from ..capability._scoring import Score, ScoreBoard

    path = tmp_path / "_scores.json"

    full = ScoreBoard()
    full.add(Score(name="alpha", value=1, maximum=1))
    full.add(Score(name="beta", value=1, maximum=2))
    full.write(path)

    partial = ScoreBoard()
    partial.add(Score(name="alpha", value=2, maximum=2))
    partial.write(path)

    scores = json.loads(path.read_text(encoding="utf-8"))["scores"]
    assert set(scores) == {"alpha", "beta"}, "a partial run deleted a metric"
    assert scores["alpha"]["value"] == 2, "the fresh measurement must win"
    assert not scores["alpha"].get("stale"), "a freshly measured metric is not stale"
    assert scores["beta"]["stale"] is True, "a carried-over metric must say so"


def test_an_empty_run_leaves_the_baseline_alone(tmp_path):
    """No records at all means nothing was measured — not that all is gone."""
    import json

    from ..capability._scoring import Score, ScoreBoard

    path = tmp_path / "_scores.json"
    seeded = ScoreBoard()
    seeded.add(Score(name="alpha", value=1, maximum=1))
    seeded.write(path)

    ScoreBoard().write(path)
    assert set(json.loads(path.read_text(encoding="utf-8"))["scores"]) == {"alpha"}


def test_credits_a_real_subcommand_under_a_group():
    assert _with_commands(f"Run '{CLI_NAME} pool members web-pool' to list them.")


def test_rejects_a_phantom_subcommand_under_a_real_group():
    """Checking only the first token accepted this, because the group is real.

    An agent writing family error messages produced exactly this string, caught
    it by hand against `--help`, and reported that the rubric had not. A check
    that validates a prefix of the claim is not validating the claim.
    """
    assert not _with_commands(f"Run '{CLI_NAME} pool list' to see pools.")


def test_credits_a_command_followed_by_prose():
    """Real messages trail words that are not arguments."""
    assert _with_commands(f"Run '{CLI_NAME} init' to rebuild the config file.")


def test_credits_a_bare_cli_mention():
    """Naming the executable is followable even without a subcommand."""
    assert _with_commands(f"Install {CLI_NAME} and retry.")


def test_a_tool_reference_after_the_cli_name_is_not_a_subcommand_claim():
    """Subcommands are kebab-case, tools are snake_case.

    "run 'vmware-monitor list_virtual_machines'" cites a tool, and reading the
    first word as a phantom command named "list" would refute a perfectly good
    hand-off.
    """
    assert _with_commands(f"Run '{CLI_NAME} list_virtual_machines' and copy a name.")


def test_unresolvable_cli_is_not_treated_as_refuted():
    """An app we cannot introspect makes the claim unverifiable, not false."""
    assert _with_commands(f"Run '{CLI_NAME} not-a-command' to verify.", commands=None)


# ── flags and env vars ──────────────────────────────────────────────────────


def test_credits_a_real_long_flag():
    assert _names_artifact("Pass --target to select a vCenter.")


def test_rejects_a_bare_double_hyphen():
    """``--`` used to be a static marker, so any em-dash-ish text scored free."""
    assert not _names_artifact("The operation failed -- try again later.")


def test_credits_a_named_env_var():
    assert _names_artifact("Password not found. Set VMWARE_PROD_PASSWORD and retry.")


# ── guidance assembled at runtime must be read, not blanked ─────────────────
#
# Every f-string hole used to render as `{}`, so a hint hoisted into a module
# constant or produced by a helper scored as naming nothing. Four skills hit
# this independently, and all four drew the same conclusion: the rubric was
# pushing them to duplicate an inline literal instead of sharing a constant —
# rewarding the worse pattern. Resolving holes is what stops that.

import ast  # noqa: E402 — grouped with the tests that use it

from ..capability.test_error_actionability import _messages_in_tree


def _messages(src: str):
    return list(_messages_in_tree(ast.parse(src), "sample.py"))


def test_resolves_a_hint_hoisted_into_a_module_constant():
    (_f, _ln, text, _holes, composed), = _messages(
        '_HINT = "Run cluster_info to see members."\n'
        'def f():\n'
        '    raise ValueError(f"Host {name} unknown. {_HINT}")\n'
    )
    assert "cluster_info" in text
    assert composed is False, "a constant resolves exactly; nothing is approximated"


def test_folds_in_the_strings_a_hint_helper_can_return():
    (_f, _ln, text, _holes, composed), = _messages(
        'def _hint(status):\n'
        '    if status == 404:\n'
        '        return "Run list_virtual_machines and copy an exact name."\n'
        '    return "Check the controller."\n'
        'def g(status):\n'
        '    raise ValueError(f"Request failed. {_hint(status)}")\n'
    )
    assert "list_virtual_machines" in text
    assert composed is True, "only some branches name a tool — the credit is an upper bound"


# ── the cap the agent actually reads through ───────────────────────────────
#
# `_safe_error` truncates with no ellipsis, so a cut is invisible. One skill was
# found shipping a 396-character message whose closing remedy had never once
# been delivered, while the rubric scored it a perfectly taught error. These run
# on fabricated messages because a repo with nothing over budget would pass this
# check without exercising a line of it.

from ..capability.test_error_actionability import _truncation_findings

_TAUGHT = {"names_input": True, "states_remedy": True, "names_artifact": True}
_BARE = {"names_input": True, "states_remedy": False, "names_artifact": False}


def _graded(text, grade=_TAUGHT):
    return [("f.py", 1, text, False, grade)]


def test_flags_a_message_longer_than_the_cap():
    over, _ = _truncation_findings(_graded("Run list_vms. " + "x" * 400), budget=300)
    assert over and over[0]["chars"] > 300


def test_accepts_a_message_within_the_cap():
    over, _ = _truncation_findings(_graded("Not found. Run cluster_info."), budget=300)
    assert not over


def test_ignores_messages_with_no_remedy_to_lose():
    over, _ = _truncation_findings(_graded("x" * 400, grade=_BARE), budget=300)
    assert not over


def test_does_not_charge_a_composed_message_for_every_folded_branch():
    """Folded branches are a content view, not a length.

    The first run of this check reported a 759-character message in a repo whose
    longest real message is a third of that — the union of one helper's branches,
    none of which is ever emitted whole.
    """
    graded = [("f.py", 1, "Run cluster_info. " + "x" * 400, True, _TAUGHT)]
    over, _ = _truncation_findings(graded, budget=300)
    assert not over


def test_flags_a_remedy_that_trails_an_interpolation():
    """A short template still loses its remedy when the value runs long."""
    _, at_risk = _truncation_findings(_graded("Failed: {}. Run cluster_info."), budget=300)
    assert at_risk == ["f.py:1"]


def test_does_not_flag_a_remedy_placed_before_the_interpolation():
    """Putting the remedy first is the fix; it must read as safe."""
    _, at_risk = _truncation_findings(_graded("Run cluster_info. Detail: {}"), budget=300)
    assert not at_risk


def test_unresolvable_holes_still_render_as_placeholders():
    (_f, _ln, text, holes, composed), = _messages(
        'def h(vm):\n'
        '    raise ValueError(f"VM {vm} not found.")\n'
    )
    assert text == "VM {} not found."
    assert holes is True and composed is False


# ---------------------------------------------------------------------------
# undocumented_args — the grader must read the schema, not just the prose.
#
# It read the prose alone, which was a fair proxy only while every ``Args:``
# block still sat inside the description. `describe_tool_parameters` moved those
# entries into the JSON schema and stripped the block from the prose, because
# both copies otherwise ship in every `tools/list` response. The grader went on
# reading the half that had been emptied on purpose and scored eleven of twelve
# skills under the floor -- while all ~889 of their parameters carried a real
# description. A grader that reports a deliberate saving as a defect trains
# people to undo the saving.
#
# Fabricated schemas, like the surface above: the property under test has to be
# visible in the test, and against the live registry these would drift with
# every tool added.
# ---------------------------------------------------------------------------


def _schema(**props):
    return {"properties": {n: d for n, d in props.items()}}


def test_credits_a_parameter_described_only_in_the_schema():
    """The bug. No prose mention, a real schema description -- documented."""
    schema = _schema(top_n={"description": "How many outliers to return."})
    assert undocumented_args(schema) == ()


def test_still_reports_a_parameter_described_nowhere():
    """The other half: the check must remain able to go red.

    A rule that credits everything is the failure this suite keeps
    rediscovering (形态 #1) -- worse than no rule, because it reports green.
    """
    schema = _schema(top_n={})
    assert undocumented_args(schema) == ("top_n",)


def test_rejects_a_schema_description_that_only_restates_the_name():
    """``storage_class: "Storage class."`` leaves a model exactly where it began."""
    schema = _schema(storage_class={"description": "Storage class."})
    assert undocumented_args(schema) == ("storage_class",)


def test_rejects_a_description_that_adds_only_filler():
    schema = _schema(description={"description": "Optional description."})
    assert undocumented_args(schema) == ("description",)


def test_credits_a_terse_description_that_carries_real_information():
    """The rubric must not pay for padding.

    ``"1 or 3."`` is the most useful description on that surface; a word-count
    bar would fail it and buy ``"The number of control plane nodes."`` instead,
    which says strictly less. See ``has_any`` for the same warning.
    """
    schema = _schema(control_plane_count={"description": "1 or 3."})
    assert undocumented_args(schema) == ()


def test_a_prose_mention_alone_does_not_count():
    """The clause that had to be removed, pinned so it cannot come back.

    Crediting a parameter because its name appears somewhere in the tool
    description was the original rule, and keeping it as a fallback looked
    harmless. It is not: strip every schema description out of vmware-debug and
    this test still read 100%, because that surface's prose names each argument
    in passing anyway. A rule written to catch exactly that regression must not
    carry a clause that hides it (形态 #5).
    """
    schema = _schema(top_n={})
    assert undocumented_args(schema) == ("top_n",)


def test_reports_every_parameter_when_the_schema_descriptions_are_gone():
    """The end-to-end shape: `describe_tool_parameters` silently not running.

    This is the whole reason the grader exists. If that call is dropped, or
    fails, or is never added to a new skill's server, every parameter reaches
    the model as a bare name and a type -- and this must say so loudly rather
    than inherit a pass from well-written prose.
    """
    schema = _schema(target={}, severity={}, top_n={})
    # Sorted for comparison: the order is the schema's, and the caller sorts.
    assert sorted(undocumented_args(schema)) == ["severity", "target", "top_n"]


def test_a_tool_with_no_parameters_has_no_gaps():
    assert undocumented_args({}) == ()


# ---------------------------------------------------------------------------
# value_set_candidates — a triage list, never a gate. These pin the two ends
# that matter: it notices a closed set with no enum, and it goes quiet once the
# enum is there. Its middle is admittedly noisy (~2 in 3 on the real surface),
# which is exactly why nothing asserts on it in the capability suite.
# ---------------------------------------------------------------------------


def _tool(name, **props):
    return SimpleNamespace(name=name, inputSchema=_schema(**props))


def test_flags_a_closed_set_with_no_enum():
    tools = [_tool("get_events", severity={"description": 'Either "critical" or "warning".'})]
    assert value_set_candidates(tools) == {"get_events.severity": ["critical", "warning"]}


def test_says_nothing_once_the_enum_is_declared():
    tools = [
        _tool(
            "get_events",
            severity={
                "description": 'Either "critical" or "warning".',
                "enum": ["critical", "warning"],
            },
        )
    ]
    assert value_set_candidates(tools) == {}


def test_does_not_flag_literals_offered_as_examples():
    """``e.g.`` marks an open set. Flagging it would demand an enum that cannot exist."""
    tools = [_tool("apply_vm_tag", tag_scope={"description": "Scope, e.g. 'env' or 'tier'."})]
    assert value_set_candidates(tools) == {}
