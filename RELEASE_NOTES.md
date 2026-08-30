## v1.8.15 — the schema an agent reads now carries the descriptions

Parameter descriptions reach the JSON schema for the first time. An MCP client
sees the schema, not the docstring, and this repo's coverage of `description`
and `additionalProperties` was 0% — while nearly every parameter was already
described in an `Args:` block no client ever receives.

Measured on a real VCF 9.1 estate, the gap produced a silent failure with no
error at any stage: a parameter name guessed wrong is discarded and the tool
returns the full unfiltered result; a value guessed wrong (`power_state=
"running"`) returns 0 rows where there were 11.

vmware-policy 1.10.0's `describe_tool_parameters` copies what is already
written, so the docstring is now load-bearing and the two cannot drift apart. It
removes the `Args:` block from the description once copied — both travel in
every `tools/list` response, so leaving it bills the same sentences twice
against the manifest's token budget. `additionalProperties` is closed: an open
schema is room for a model to invent arguments that are then silently
discarded, which is the other half of the same failure.

**The `vmware-policy` floor moves to >=1.10.0.** Older releases have no
`describe_tool_parameters`, and resolving one gives an ImportError at server
start rather than a missing feature.

Also in this release: three MCP annotations that were lying, and errors that
were invisible at the protocol level.

`vm_guest_download` claimed `readOnlyHint: true` while overwriting an arbitrary
caller-supplied local path — and `readOnlyHint` is what a client consults to
decide whether a call needs confirmation, so the annotation was a safety control
that silently did not apply. It is now a write, and it refuses an occupied
destination unless `overwrite=true`, refuses directories, and refuses symlinks
even with `overwrite=true`, because consenting to replace a path is not
consenting to replace what it points at. `vm_set_ttl` claimed
`destructive: false` while scheduling an unattended auto-delete.
`vm_create_snapshot` claimed `idempotent: true` while calling
`CreateSnapshot_Task` unconditionally — that is the field a client reads before
*retrying*, and this family retries transient failures once, so a timeout on a
snapshot that had in fact succeeded would be retried into a second delta-disk
chain.

Correcting the first one disarmed a second control by itself: the guarded-CLI
test derives its write set from `readOnlyHint`, so `vm guest-download` had been
the one file-writing command with no `@guarded`. It has one now.

And exceptions were caught inside tools and returned as ordinary values, so
`isError` was always false: over stdio a client could not tell "the target does
not exist" from "succeeded". The frame is now marked, with the authored
teaching message intact — verified in a live JSON-RPC session showing a failing
call and a succeeding one in the same transcript.

## v1.8.14 — a host nobody could reach is not a host with no adapters

Found against a real VCF 9.1 estate where four of eight ESXi hosts were
`notResponding`. vCenter keeps answering for such a host out of its own cache,
with no error and no marker, so a read "succeeds" and looks authoritative.

**`list_host_vmks` dropped unreachable hosts and still said the list was
complete.** Asked for one, it raised a bare `AttributeError`; enumerating all
of them, it skipped the four silently and reported `truncated: false` — the
envelope positively certifying a list that was missing half the estate. Unread
hosts now appear as rows with `reachable: false`, null facts and the
`connectionState` in the note, and the envelope carries `hosts_unreachable`.
Three sibling write tools carried the identical bare dereference and were found
by grepping for the shape rather than waiting for them to be reported; they now
raise a teaching error, because reading the adapter list in order to *change*
it is a different question from listing it. The read and the writes deliberately
answer differently, and the reasoning is in the docstrings.

The distinction that makes it work: PropertyCollector *omits* a property it
could not read, so an absent `config.network.vnic` means "not read" while `[]`
means "read, and empty". `p.get(...) or []` collapsed the two, which is exactly
how four hosts disappeared.

**`doctor` cleared an estate it had not checked**, authenticating only the
default target — so five targets with three wrong passwords came back "All
checks passed", and the failure that followed told the user to run the doctor
that had just cleared them.

**The CLI, the doctor and the MCP server opened different config files.** With
`VMWARE_AIOPS_CONFIG` set, the server read that file while `load_config()` read
the default: the agent and the human on different vCenters, with the doctor
reporting on the human's. The precedence now lives in one `resolve_config_path`.

**`server.json` never started the MCP server** — it carried only the package
identifier, so a registry client composed `uvx vmware-aiops`, which runs the CLI
and exits.

The floor on `vmware-monitor` moves to 1.8.13. The cluster-health triage this
skill exposes is delegated to that package, and on anything older it reports
unreachable hosts as healthy ones.

## v1.8.13 — list tools that agents could not read, and a documented key the code ignores

- **Three list tools answered in a shape the family contract does not use.**
  `list_dvs_portgroups` returned `{total, returned, portgroups}`,
  `list_host_vmks` returned `{total, returned, vmks}`, and
  `scan_datastore_images` returned `{images, last_scan}` with no count at all.
  Each is self-consistent; each is wrong, because an agent that has learned this
  family reads `items` and gets nothing. The absent keys are the ones that
  carry meaning: `truncated` and `hint` exist because of issue #31, where a
  model handed a bare list "incorrectly states that no data was returned". All
  three now return the envelope, with the old keys kept as deprecated aliases so
  nothing that reads them breaks.
- **Offset paging claimed there was more at the end of the list.** Truncation
  was derived from `returned < total`, which is wrong once `offset` is
  involved — the last page of five rows fetched with `offset=4` returns one and
  reported `truncated: true`, advising the caller to raise a limit that cannot
  help them. Both offset-supporting tools now share one helper.
- **The SSL config key in every doc was one the code has never read.** Docs said
  `disableSslCertValidation: true`; `config.py` reads `verify_ssl`. Following the
  instruction produced a config that is ignored.

Found by running against a real vCenter rather than a mock.

## v1.8.12 — two wrong numbers: the server's own version, and the advertised tool count

Both defects were invisible to the test suites and both were user-facing.

- **The MCP server reported the SDK's version as its own.** `FastMCP` accepts no
  `version` argument and leaves the lowlevel server's at `None`; with it `None`
  the SDK answers `initialize` with its OWN version. Every skill in the family
  therefore told its client it was mcp 1.29.1 — a number that exists for no
  package here, and one that would change with an SDK bump and no code change of
  ours. Verified end to end rather than by reading: unset the field and a probe
  server reports the installed SDK's version; set it and it reports ours.
- **server.json advertised a stale tool count.** That number is what MCP Registry
  publishes and what the plugin manifest and marketplace copy, so one stale
  integer was wrong in three public places. Corrected against the registered
  tools: 31 advertised, 60 real. README and SKILL.md were already right.

Also new: this repo is installable as a Claude Code plugin
(`/plugin install vmware-aiops@vmware-skills`). The skill and its MCP server arrive in
one step; nothing is duplicated, the manifest points at the existing `skills/`
tree. family_smoke gained three gates — the server's reported version, the plugin
manifest's agreement with pyproject, and the advertised tool count against the
live registration.

## v1.8.11 — moved to vmware-skills org + MCP Registry namespace io.github.vmware-skills/vmware-aiops

Repo transferred from github.com/zw008 to github.com/vmware-skills (redirects preserve old links).
MCP Registry server renamed to `io.github.vmware-skills/*`; the old `io.github.zw008/*` entry is deprecated.
All in-repo links updated. No functional code change on this line beyond the org move.

## v1.8.10 — DRS VM-VM rules + vmk service tagging + UTF-8 I/O (55 → 60 tools)

Three community contributions by @wright-bench (PRs #36, #37, #38), plus CLI
parity for the DRS rule surface.

- **DRS rule suite (PR #37)** — four MCP tools for VM-VM affinity/anti-affinity
  rules, closing the day-2 gap `cluster_configure` left (cluster-wide DRS toggled,
  individual rules had no surface):
  - `list_drs_rules` [READ] — VM-VM and VM-Host rules (key, name, type, enabled,
    mandatory, member VMs / group names). The verify pair for the writes.
  - `set_drs_rule_enabled` [WRITE] — flip a rule's enabled flag; idempotent.
  - `create_drs_rule` [WRITE] — VM-VM affinity/anti-affinity, ≥2 distinct VMs, all
    validated as cluster members.
  - `delete_drs_rule` [WRITE] — VM-VM only; **refuses** VM-Host and other rule
    types (they can carry licensing/compliance placement constraints); preview and
    result both record the full definition for recreate-from-audit.
  - **CLI parity added**: `cluster drs-rules`, `cluster drs-rule-set`,
    `cluster drs-rule-create`, `cluster drs-rule-delete` — `@guarded`, double-confirm
    on writes, `--dry-run` on all three writes.
- **`set_vmk_service` (PR #36)** — tag/untag a host service (vmotion, management,
  vsan, vSphereProvisioning, …) on an existing vmk, completing `add_host_vmk`
  (adapters are created serviceless by design). Idempotent, preview/confirm gated.
  **Fail-closed**: refuses both directions when the service map is unreadable, and
  refuses (no override) to untag `management` from the host's only
  management-enabled vmk — the call rides the interface it would untag. Remains
  MCP-only, consistent with the rest of the host-network suite.
- **UTF-8 I/O pin (PR #38)** — `encoding="utf-8"` pinned on every text-mode
  `read_text`/`write_text`/`open` across source and tests (46 sites). Fixes
  `UnicodeDecodeError` on Windows/cp1252 checkouts where unpinned reads land on
  files containing emoji/arrow bytes, and locale-encoded write round-trips.
  Mechanical only — no logic touched.

Tool count 55 → 60 (18 read, 42 write). Thanks again to @wright-bench.

## v1.8.9 — Network authoring: dvSwitch portgroups + host VMkernel adapters (49 → 55 tools)

Six new MCP tools land the family's first network-authoring surface (community
contribution, PR #35 by @wright-bench):

- `list_dvs_portgroups` / `create_dvs_portgroup` — distributed-switch portgroups.
  Create is preview/confirm gated and supports `earlyBinding` and `ephemeral`
  (ephemeral attaches from the ESXi host client with vCenter down — the
  self-hosted-VCSA case). `lateBinding` is deprecated by vSphere and not offered.
- `list_host_vmks` / `add_host_vmk` / `remove_host_vmk` — host VMkernel adapters.
  `add` is the throwaway-test-vmk shape (static IP, no gateway, no services).
  `remove` is **fail-closed**: it refuses when the vmk is selected for a host
  service (management/vMotion/vSAN), lives on a non-default netstack (NSX TEPs,
  dedicated vMotion/provisioning stacks), carries a default route, or when any of
  that cannot be verified — `force_unprotected=True` overrides all but the
  only-management-vmk absolute (the call rides the interface it would delete).
- `vmk_ping` — DF-bit-capable ping sourced from a vmk via esxcli-over-API (no host
  SSH). `df=True size=1572` proves a ≥1600 overlay floor; `size=8972` proves full
  jumbo. Oversized DF'd packets report the esxcli fault structurally, not as an
  error — that failure *is* the MTU diagnostic.

This is the underlay surface (VLAN-backed DVS portgroups, host kernel
interfaces); for NSX overlay segments/gateways/NAT use vmware-nsx. Writes are
preview/confirm gated, risk-tiered, and audit-logged like every other AIops write.
Tool count 49 → 55 (17 read / 38 write). No dependency or breaking changes.

## v1.8.8 — CLI writes now route through policy + audit, exactly like the MCP tools

Every state-changing CLI command is now wrapped by `@guarded`, the CLI counterpart
to the MCP `@vmware_tool` decorator: it runs the same vmware-policy `guard()`
authorization and writes the same `audit_call()` row to `~/.vmware/audit.db`. A
`delete`/`disable`/destructive command run through a shell is now authorized and
recorded exactly like the equivalent MCP tool — closing the gap where CLI writes
bypassed policy and landed only in the legacy per-skill log (HLD I-1/I-8).

- a policy `deny` rule now refuses the operation on the CLI with a teaching line
  naming the rule that fired, not a traceback
- the legacy per-skill audit log is still written this release (dual-write); it is
  removed at 2.0
- **requires vmware-policy >= 1.8.8** (the release that adds the shared `guarded` core)
- a regression test derives the write-command set from the MCP `[WRITE]` markers and
  asserts every one is `@guarded`, so a new write command cannot ship unguarded

Also carries the environment-field docstring correction (an optional label a `deny`
rule may scope to — there is no "warn now / refuse next major" gate).

## v1.8.7 (2026-07-21) — the skill-level read-only switch is removed; read/write authorization is the vCenter account's job (RBAC)

### Removed: `VMWARE_READ_ONLY` / `read_only:` — give the agent a read-only service account instead

The skill-level read-only switch is gone. It was enforced only on the MCP tool
registry, and any agent with a shell (every SKILL.md grants `allowed-tools: Bash`)
could reach the same change one CLI command away — so it withheld the *tool*, not
the *capability*. It was never a real boundary.

To run an agent read-only, give it a **read-only vCenter/NSX service account
(RBAC)**. Writes are then refused at the platform, un-bypassably, regardless of
surface or shell — the one place read/write control cannot be stepped around. A
config still carrying `read_only: true` is ignored, with a one-time warning that
names the replacement (no silent behavior change).

### Removed: approval tiers and the declared-environment gate (via vmware-policy)

The graduated-autonomy approval tiers (`confirm`/`dual`/`review`) and the "declare
an environment or be refused" baseline are removed — they only ever fired on the
rarest configuration while carrying the family's most complex machinery. Opt-in
`deny` rules and the maintenance window remain, and apply identically wherever a
tool runs.

### Added: offline / air-gapped install docs

The README now covers installing from source without editable mode (for older
`pip`) and building wheels to carry onto an air-gapped host — the modern PEP 517
layout has no `setup.py` by design, which is expected, not a missing file.

This release also carries the accumulated fixes staged since 1.8.5.

## v1.8.5 (2026-07-20) — the two fixes v1.8.4 announced now actually work

Four adversarial reviews of v1.8.4 found that both of its headline fixes were
incomplete in ways the release notes did not reflect. This release makes them
real. If you are on 1.8.4, this is the one to take.

### Fixed — a failure that was *returned* was still audited as a success

vmware-policy 1.8.4 added `report_tool_failure()` for tools that catch an
exception and return an error payload instead of raising. **No skill called it.**

Every string-returning tool therefore kept doing exactly what 1.8.4 said it had
stopped doing: writing `status=ok` to `~/.vmware/audit.db` for an operation that
failed, recording an undo token for a change that never happened, and telling the
circuit breaker the call succeeded so repeated failures never tripped it.

The surface this covered is not marginal:

| Skill | What was mis-audited |
|---|---|
| vmware-aiops | 25 of 49 tools, including **every undo-bearing write** — a failed `vm_power_on` left an undo token saying "power it back off" |
| vmware-avi | all 28 tools, including `vs_toggle` and `ako_restart` |
| vmware-storage | all 4 write tools |
| vmware-nsx | the 5 delete tools |

vmware-avi is worth calling out: before 1.8.4 its exceptions propagated and the
audit was correct. 1.8.4 caught them and returned a string, so **that release made
its audit trail worse than it had been.**

Skills whose tools already return dict payloads (vmware-monitor, vmware-vks,
vmware-aria, vmware-log-insight, vmware-harden, vmware-debug, vmware-pilot) were
already detected correctly. They gained a test proving it rather than a redundant
call.

### Fixed — narrowing `OSError` did not close the leak it was meant to close

1.8.4 narrowed the `_safe_error` passthrough because bare `OSError` let TLS and
DNS failures reach the agent with hostnames and certificate subjects in them.
That narrowing had no effect on the error it was written for:

```
ssl.SSLCertVerificationError → ssl.SSLError → OSError, ValueError
```

`ValueError` has been on every allowlist since long before 1.8.4, so a
certificate failure kept passing through — the commonest self-signed-certificate
failure in this family, carrying the hostname it was checked against. An
allowlist structurally cannot express "not this one".

Where `ssl.SSLError` can actually surface — the pyVmomi skills — it is now
reduced *ahead* of the allowlist. In the httpx skills TLS arrives wrapped as
`httpx.ConnectError`, and in vmware-avi as `requests.exceptions.SSLError`, so the
guard cannot fire there; in those skills the leak was the raw exception
interpolated into an already-allowlisted `*ApiError`, and that is now authored
text naming the config target and `verify_ssl` instead of the exception.

The missing-password error — this family's most common first-run failure, whose
entire remedy is the environment variable name it carries — keeps its message
through a narrow `ConfigError(OSError)` rather than the base class. Connection
failures are translated at the connection layer into an authored remedy that
names the target and the setting to change, with the raw detail left on
`__cause__` for the server log.

### Also fixed

- **vmware-vks**: the quickstart documented a password variable the code never
  reads — following `README.md` verbatim produced "Password not found". Five
  places, plus six references to a `doctor` command this CLI has never had, two
  descriptions promising fields the tools do not return, and eight teaching
  messages that `RuntimeError` was masking.
- **vmware-nsx**: an error cited `--route-advertisement`; the flag is `--advertise`.
- **vmware-pilot**: `get_workflow_status` told the model to call `approve` — a
  tool the read-only gate withholds — as the required next step; and a hint
  pointed at a filename that could never appear in that message.
- **vmware-aiops**: `vm_task_status` polling a *failed task* returned
  `{"state": "error", "error": ...}` from a successful read, which the new
  detection read as the call itself failing. The field is now `task_error`.
  **This is a breaking change for anything parsing that payload.**
- Several remedies that were still being cut by the 300-character cap the 1.8.4
  notes claimed to have addressed.

### Known and not fixed

`ConnectionError` remains one type from two sources in several skills — a
skill's own authored message and urllib3's `HTTPSConnectionPool(host=..., port=...)`
share it, and an allowlist cannot separate them. vmware-vks is converted; the
rest need their own domain type and are deferred rather than half-done.

## v1.8.4 (2026-07-20) — errors that teach, and tool descriptions a small model can route from

A capability eval was rolled out across the family and asked two open questions:
when a call fails, is the model told enough to fix it, and can it pick the right
tool from the description alone? Both answers were worse than anyone thought, and
in several places the reason was that the measurement was looking somewhere other
than where the model reads.

### Fixed — teaching messages were being discarded on the way to the agent

`_safe_error` reduces unrecognised exceptions to `"<Class>: operation failed."`
so raw API text, credentials in URLs and internal paths cannot reach an agent.
Its allowlist held only the builtin validation errors — so this skill's **own**
domain exceptions, the ones that exist precisely to carry a corrected next step,
had their messages replaced by their class names.

The effect was invisible from the CLI, which prints those messages in full.

The worst case was shared by nine skills: `config.py` raises exactly one
`OSError`, the missing-password error, whose entire remedy is the environment
variable name it names. An agent hitting an unconfigured target received
`OSError: operation failed.` and had nothing to act on. That is the family's most
common first-run failure, and it landed one release after the documented variable
names were corrected — so the message that would have unstuck the operator was
the one being thrown away.

The rule is now the property it always meant: **every exception this skill raises
on purpose passes through**, and only genuinely unplanned ones are reduced.
`RuntimeError` stays reduced — it is the generic catch-all and in several skills
carries raw upstream text.

### Fixed — error messages now carry the correction

Every message that reported a failure without saying how to recover was
rewritten: it names the offending value, gives an imperative remedy, and names
something concrete to act on — a tool that exists, a real CLI command, a config
file, an environment variable. Recovery becomes an instruction-following problem
rather than an inference one, which is what a weak model can still do.

Three classes of defect surfaced while doing it:

- **Remedies that were never delivered.** `_safe_error` truncates with no
  ellipsis, so a message longer than the cap loses its closing sentence
  silently. One message had been shipping at 396 characters against a 300-char
  cap — its remedy had never once reached an agent. Messages now lead with the
  remedy so a long interpolated value truncates the expendable detail instead.
- **Commands that do not exist.** One skill's error hints named a `doctor`
  subcommand it does not have.
- **Tools that do not exist.** A tool description pointed at two sibling-skill
  tools that had been renamed, and another named a tool that had moved to a
  different skill entirely.

### Improved — tool descriptions state when to use them and what to call next

The description is the API for a small model: an unstated routing rule is a
routing rule that does not exist, and a tool with no stated next hop is one the
model stops at. Descriptions now say when to prefer this tool over a sibling,
what shape comes back, the caveat that bites, and which tool to call after.

**Manifest size did not grow.** Descriptions load into every session, so the
routing clauses were paid for by cutting duplicated reference material —
repeated boilerplate, examples that restated the parameter list, and prose
copies of the pagination contract.

### Note

Every tool and CLI command named anywhere in this release was verified against
the live MCP registry and the live command tree, not against documentation.

## v1.8.3 (2026-07-20) — credentials resolve as a pair; documented env vars now exist

### Added — the per-target username can come from the environment

Adapted from [VMware-AIops#33](https://github.com/vmware-skills/VMware-AIops/pull/33) by
@wright-bench, with thanks. The password already resolved from an env var; the
username did not, so a deployment injecting credentials from a secret store
(systemd `EnvironmentFile`, container secrets, a vault sidecar) could externalise
only half of the pair — and a config-file username paired with an env password
from a different account logs in as nobody.

`<PASSWORD-KEY-PREFIX>_USERNAME` now overrides the `username:` in config.yaml,
using that skill's own password-key convention. Absent, config.yaml still wins;
nothing changes for anyone not setting it.

**Resolved on every access, like the password.** The contributed version read the
username once at load time while the password stayed a property, which
reintroduces exactly the split the override exists to prevent: a sidecar rotating
both halves mid-process moves the password and leaves the username behind. A test
pins that both halves resolve at the same moment.

### Fixed — documented credential variables that the code never read

Rolling the above across the family surfaced a separate defect: four skills
documented a password variable their own loader does not look up. An operator
following the documentation exactly — correct file, correct place, correct-looking
name — got "Password not found".

| Skill | Documented | Actually read |
|---|---|---|
| vmware-nsx | `VMWARE_NSX_<TARGET>_PASSWORD` for target `nsx-prod` → `VMWARE_NSX_PROD_PASSWORD` | `VMWARE_NSX_NSX_PROD_PASSWORD` |
| vmware-nsx-security | `VMWARE_<TARGET>_PASSWORD` | `VMWARE_NSX_SECURITY_<TARGET>_PASSWORD` |
| vmware-aria | `VMWARE_<TARGET>_PASSWORD` | `VMWARE_ARIA_<TARGET>_PASSWORD` |
| vmware-vks | `VMWARE_<TARGET>_PASSWORD` | `VMWARE_VKS_<TARGET>_PASSWORD` |
| vmware-avi | three different forms across three files | `<CONTROLLER>_PASSWORD` |

The prefixes genuinely differ per skill, so nothing could be fixed by
standardising a pattern — each repo's docs were corrected against its own code.
The code was left alone: changing a key would break every existing deployment.

`family_smoke.sh` now compares the credential variables named in each repo's docs
against the ones that repo's code builds, so the two cannot drift apart again.

## v1.8.2 (2026-07-20) — the MCP server moves into the package namespace

### Fixed — co-installing two skills broke all but the last one

Every skill shipped its MCP server as a **top-level `mcp_server` package**. Python
has one top-level namespace, so installing any two of them into one environment let
the second overwrite the first — silently, with no error and no warning.

    uv tool install vmware-aiops   ->  49 tools   (correct)
    uv pip  install vmware-aiops   ->  27 tools   (Monitor's read-only server)

vmware-aiops depends on vmware-monitor, so this was not an edge case: **every pip
install hit it**, and the operator got 27 read-only tools where 49 were expected,
with all 35 write tools missing. Docker images, shared MCP hosts and CI runners that
install more than one skill were affected the same way.

The server now lives at `vmware_<skill>/mcp_server/`, a name only this package can
claim. Introduced 2026-02-26; it survived 70 releases because every test ran against
a single package in its own repo, where the local directory shadows site-packages —
the conflict was invisible by construction.

**Migration.** Console scripts are unchanged: `vmware-<skill>` and
`vmware-<skill>-mcp` work exactly as before, as does `"command": "vmware-<skill>",
"args": ["mcp"]` in an MCP client config. Only a direct `python -m mcp_server`
breaks; use `python -m vmware_<skill>.mcp_server`.

### Added — `references/agent-guardrails.md` in every skill

The operating rules for local and small models (Llama 3.3 70B, Qwen, Mistral via
Goose / Ollama / OpenShift AI) existed in two skills. They now ship in all 13, each
with its own tool counts and failure modes, and are linked from every SKILL.md.

### Changed — 46 error messages rewritten to teach recovery

A large model reads `VM 'web-99' not found` and recovers on its own. A small model
either surfaces it as a dead end or smooths it into a confident, wrong summary — the
failure [VMware-AIops#31](https://github.com/vmware-skills/VMware-AIops/issues/31) reported.
The difference is entirely in the message text.

    error_actionability  43.5% -> 90.5%
    teaching_error_rate  10.2% -> 77.6%   (5/49 -> 38/49)
    dead-end errors      35/49 -> 2/49

Two shipped bugs surfaced while doing it: `_safe_error` silently swallowed
`InventoryError`, `HostNotFoundError` and `ISCSIError` (their text became
`"InventoryError: operation failed."` over MCP), and two error messages named
commands that do not exist — `vmware-aiops storage iscsi-enable` (there is no
`storage` command here) and `vm guest-exec` for host maintenance mode (that runs
inside a guest, not on a host).

The three raises left alone are the ones where no advice would be true: a
vCenter-supplied non-HTTPS lease URL, and a malformed inventory graph. Inventing a
remedy scores better and helps nobody.

### Fixed — documentation that pointed at files which do not exist

93 install instructions across Monitor and AIops told operators to copy
`codex-skill/AGENTS.md`, `gemini-extension/GEMINI.md`, `trae-rules/project_rules.md`
or `kimi-skill/SKILL.md`. None of those directories exist in any repo, and CLAUDE.md
forbids recreating them — so every one of those commands failed. Where a platform
reads plain markdown (Codex, Kimi, Trae, Aider, Continue) they now point at the real
`skills/vmware-<skill>/SKILL.md`; Gemini CLI, which needs a manifest none of the
repos ship, was downgraded to the context-file and MCP paths that do work.

`/plugin marketplace add` was removed from both repos and from three setup guides:
the `plugins/` source directory it needs was deleted in March and is on CLAUDE.md's
forbidden list, so that path could not be made to work either.

### Removed — `.claude-plugin/marketplace.json` and the stale `.agents/` copy

The manifest's `source: "./plugins/vmware-ops"` pointed at a directory deleted on
2026-03-22; it had been broken for four months and was still at version 1.0.16. The
`.agents/` copy was a 2026-04 snapshot that had diverged 314 lines from the
maintained SKILL.md, hidden from `git status` by its own `.gitignore` entry.

## v1.8.1 (2026-07-19) — read-only mode reaches the surfaces that teach it

v1.8.0 put read-only mode in the code and documented it in the README only.
Every other layer was empty, and each serves a different reader: SKILL.md is what
the agent loads, setup-guide is what an operator reads while configuring, `doctor`
is where they verify it took. The gap had two concrete costs.

An agent read SKILL.md, called a write tool the gate had withheld, and got nothing
back — with no way to learn that the absence was a deliberate lockdown rather than
a fault. It reads as a broken tool, so the model retries or hunts for a workaround.

An operator who set the switch had no way to confirm it. The only signal was a line
in the MCP server's start-up log.

### Added — the feature is now documented where each reader looks

- **SKILL.md** — a short section telling the agent that a missing write tool is a
  lockdown, not a fault: name the blocked operation, do not retry, do not route
  around it.
- **references/setup-guide.md** — the operator's view: how to enable it, the
  precedence chain, and how to verify.
- **references/capabilities.md** — which tools the gate withholds.

### Added — `doctor` reports the read-only state

`vmware-aiops doctor` now shows whether read-only mode is on, **which** of the three
switches decided it, and the value as written. A typo'd value (`ture`) is called
out as a typo rather than reported as a confident ON — it resolves to on, which is
fail-closed but almost never what was meant.

The resolution runs through `vmware_policy.read_only_status()` rather than a local
copy of the precedence chain: a doctor that disagrees with the gate it reports on is
worse than no doctor. Requires `vmware-policy>=1.8.1`.

## v1.8.0 (2026-07-18) — read-only mode, working policy defaults, declared environments

Family release driven by [VMware-AIops#31](https://github.com/vmware-skills/VMware-AIops/issues/31),
where an operator running Llama 3.3 70B (Goose / OpenShift AI, on-prem H100) had to
hand-write 17 prompt guardrails to make tool calling reliable. A prompt is advisory — a
model can ignore it. Every guardrail that could move into the harness has.

### Added
- **Read-only mode.** Set `VMWARE_READ_ONLY=true` (or `VMWARE_<SKILL>_READ_ONLY`, or
  `read_only: true` in config.yaml) and every write tool is removed from the MCP registry
  at start-up. `list_tools()` never offers them, so the model cannot call what it cannot
  see. **Off by default** — nothing changes unless you turn it on. Fail-closed: if the
  mode is requested but cannot be guaranteed, the server refuses to start rather than
  running open.
- **`environment:` on each config target**, declaring which environment it is
  (production / staging / lab). Policy rules scope by this value.

### Added — list results now state whether they are complete

Every `[READ]` list tool returns the family envelope instead of a bare array:

    {"items": [...], "returned": 50, "limit": 50, "total": 213,
     "truncated": true, "hint": "Showing 50 of 213. Raise limit or narrow the query..."}

This closes the reported failure where long responses were summarised as "no data
returned": a bare list gives a model no way to tell a complete answer from page one, so
it guessed. `truncated: false` now positively states completeness — including when
`items` is empty, which means "checked, found none", not "the call failed".

- **5 tool(s) converted** across ops, MCP and CLI. All five report a real `total`; each enumerates its collection fully before limiting.
  The three `batch_*` write tools keep returning a bare list and are pinned that way:
  a batch result is complete by construction, so a `truncated` field there would be noise.

### Changed — migration, read this
- **Approval tiers now actually run.** They shipped in v1.6.0 but the engine only ever
  read `~/.vmware/rules.yaml`, and a fresh install has no such file — so every deny rule,
  maintenance window and approval tier had been inert on every install that never
  hand-authored one. A packaged baseline now loads when you have written no rules of your
  own. Writes at medium risk and above are stamped with their tier in the audit log;
  irreversible work and guest execution against a target declared `production` require a
  named approver via `VMWARE_AUDIT_APPROVED_BY`.
- **`environment:` will become required for writes.** Today a state-changing operation
  against a target that declares none still runs and logs a warning. **The next major
  release refuses it.** Declare it now and that upgrade is a no-op:

      targets:
        prod-vc01:
          host: vc01.corp.local
          environment: production

  Read-only operations are never affected, in this release or the next. Check what applies
  to your targets before upgrading: `vmware-audit policy --operation vm_delete --env <env>`.

### Fixed
- **Policy glob patterns with a leading wildcard silently matched nothing.** A rule written
  `operations: ["*_delete"]` parsed fine, read correctly, and never fired — only a trailing
  `*` was honoured. Now full glob matching, for operations and environments alike.
- Config-path overrides (`VMWARE_<SKILL>_CONFIG`) are honoured when reading `read_only`
  and `environment`, so a setting in a custom config file is no longer silently ignored.

### Notes
- Requires `vmware-policy>=1.8.0`; publish that package first.
- `vmware-audit policy` reports which rules are in force and where they came from —
  including the case where your rules file exists but failed to parse, which previously
  looked identical to "policy is working".

### Fixed — pre-release review (2026-07-19)

- **`vm_list_plans` was marked `[READ]` but deleted plan files.** `list_plans()` opened
  with a stale-plan sweep, so the one plan tool that survives read-only mode was the one
  that destroys plans — while `vm_create_plan`, `vm_apply_plan` and `vm_rollback_plan`
  were all withheld by the gate. Opening a read-only server to review a failed apply
  could unlink the only on-disk record of which steps landed. Expiry now runs on
  `vm_create_plan` alone, so plans are still swept on every write.

## v1.7.7 (2026-07-17) — session-probe eviction fix + mcp 1.28.1

Family fix pack — no new tools, no schema changes.

### Fixed
- **Dead cached vCenter sessions were never evicted** (external fork report,
  VMware-AIops PR #32). The liveness probe's handler was
  `except (vmodl.fault.NotAuthenticated, Exception)` — but
  `vmodl.fault.NotAuthenticated` does not exist in pyVmomi (the real class
  lives under `vim.fault`), and except-tuples are evaluated at catch time, so
  the handler raised `AttributeError` instead of evicting. A long-running MCP
  server whose session idled out then permafailed every call until restart,
  surfacing the misleading `AttributeError: NotAuthenticated` instead of the
  real error. The probe now also treats a `None` `currentSession`
  (expired-token shape) as dead. Three regression tests pin the probe shapes
  (raise → evict + reconnect, None → evict + reconnect, live → cache reuse),
  and family_smoke gained a static check banning the nonexistent class.

### Security
- Lockfile bumps `mcp` to **1.28.1**, clearing three GHSA HIGH advisories
  against the MCP Python SDK (WebSocket Host/Origin validation, HTTP
  transport principal verification, experimental task-handler cross-client
  access). stdio-only servers are not directly exposed, and installs resolve
  `mcp` fresh from PyPI — this mainly matters for from-source checkouts.

## v1.7.6 (2026-07-14) — object investigation bundles + cross-vCenter attention (from the AIops entry point)

The object-centered drill-down from issue #31 — "what is happening around this
VM / host / datastore?" — plus a cross-vCenter "what needs attention now?" view,
reachable from an AIops conversation so triage → investigate → act stays in one place.

### Added
- **Object investigation bundles** (read-only; surface 45 → 49). Four new MCP tools —
  `vm_investigation_bundle`, `host_investigation_bundle`, `datastore_investigation_bundle`,
  and `cross_vcenter_attention` — each correlates an object (or the whole estate) with
  its surrounding infrastructure and recent event history and returns one high-signal,
  aggregated result. All four delegate to the vmware-monitor library using AIops's own
  vCenter connection; no logic is duplicated.
- **CLI**: `vmware-aiops investigate vm|host|datastore <name>` and
  `vmware-aiops attention`, each with `--hours` and `--html` / `--html-path` (offline,
  self-contained snapshot; drill-down sections collapse with zero JavaScript).

### Changed
- New dependency floor: `vmware-monitor>=1.7.6` (the delegated bundle functions live there).

### Notes
- Read-only; point-in-time snapshots. Requires `vmware-monitor` installed (delegation).
## v1.7.5 (2026-07-13) — cluster-health triage from the AIops entry point

### Added
- **`cluster_health_summary` MCP tool** (read-only; surface 44 → 45) and
  **`vmware-aiops summary` CLI** — the one-glance cross-cluster triage
  (ranked top-N issues + per-cluster status + offline HTML snapshot) is now
  reachable from an AIops conversation. Both delegate to the vmware-monitor
  library using AIops's own vCenter connection; no logic is duplicated.
- New dependency: `vmware-monitor>=1.7.5`.

### Fixed
- Removed a leftover duplicate `import json` in `cli/mcp_config.py` (dead code).

## v1.7.4 (2026-07-13) — family version alignment

## v1.7.3 (2026-07-03) — family version alignment

## v1.7.2 (2026-07-02) — alarm & health scale (issue #31 follow-up)

### Fixed
- **Alarm & health read paths at scale.** The v1.7.1 PropertyCollector fix
  covered inventory only. `list_alarms` / `acknowledge_alarm` / `reset_alarm`
  still walked every VM + host + cluster + datacenter + datastore reading lazy
  `.name` / `.triggeredAlarmState` per object, and host hardware status, host
  services, and host-log scan swept all hosts with per-host lazy reads — each a
  separate SOAP round-trip that timed out on large vCenters. All now batch the
  needed properties in a single `PropertyCollector.RetrievePropertiesEx` call.
  Output shape unchanged.

## v1.7.1 (2026-07-02) — large-inventory scale fix (PropertyCollector, issue #31)

### Fixed
- **Large-inventory scale (GitHub issue #31).** Inventory list/find operations
  (`list_vms`, `list_hosts`, `list_datastores`, `list_clusters`, `list_networks`,
  and the `find_*_by_name` helpers) walked a container view and then read pyVmomi
  *lazy* properties per object (`vm.config.hardware.numCPU`,
  `vm.runtime.host.name`, `len(host.vm)` …) — each a separate SOAP round-trip. On
  large vCenters (thousands of VMs / hundreds of hosts) this meant tens of
  thousands of round-trips, so even `limit=20` queries timed out. All of these
  now fetch every needed property in a single `PropertyCollector.RetrievePropertiesEx`
  call (paged via continuation tokens). Output shape is unchanged. Reported by
  juanpf-ha against an ~8,000-VM / ~340-host environment.

## v1.7.0 (2026-06-27) — guided onboarding + teaching auth errors

### Added
- **`vmware-aiops init` — interactive first-run setup wizard.** Prompts for host /
  username / password and writes `config.yaml` + `.env` for you. The password is
  stored grep-safe (`b64:`, never plaintext on disk) and `.env` is locked to
  0600, then the connection is verified. Replaces the manual "mkdir + cp
  config.example.yaml + edit YAML + chmod 600" dance.

### Changed
- `doctor` now points to `vmware-aiops init` when config/credentials are missing
  (previously suggested a command that did not exist), keeping the manual steps
  as a fallback.
- Authentication and TLS failures now print a teaching message naming the exact
  file and env var to fix (`~/.vmware-aiops/.env` password var, `config.yaml`
  username) plus a `verify_ssl: false` hint for self-signed labs.

## v1.6.1 (2026-06-24)

### Added
- **`.env` passwords are auto-obfuscated to a grep-safe `b64:` form** on first
  load and decoded transparently at runtime — plaintext no longer sits in
  `~/.<skill>/.env` for a casual `grep` to find. Values are read/written through
  python-dotenv's own parser, so the stored secret never drifts from the
  configured one (handles quotes, inline comments, trailing whitespace, and a
  password that literally starts with `b64:`). **Obfuscation, not encryption** —
  for real at-rest secrecy, inject the password from a secret manager instead of
  storing `.env`. New regression suite (10 cases) covers dotenv parity, the
  `b64:`-prefixed edge case, idempotency, and 0600 preservation.

## v1.6.0 (2026-06-22) — trust architecture: undo tokens + governed harness

### Added
- **Undo-token recording** on reversible write tools (via vmware-policy 1.6.0 `@vmware_tool(undo=...)`):
  `vm_power_on`↔`vm_power_off`, `vm_create`→`vm_delete`, `vm_clone`→`vm_delete`,
  `vm_create_snapshot`→`vm_delete_snapshot`, `vm_set_ttl`→`vm_cancel_ttl`. Each successful write
  records an inverse descriptor (`_undo_id`); query/replay via the audit/undo tooling.
- Inherits the harness trust-architecture upgrades: token/runaway budget guard, audit accountability
  fields (rationale/approved_by/risk_tier), and graduated-autonomy risk tiers.

### Changed
- Requires **vmware-policy >= 1.6.0** (the `undo=` parameter lives there). Dependency pinned accordingly.

## v1.5.39 (2026-06-22) — snapshot delete: async + honest timeout (token-burn fix)

### Fixed
- **Snapshot delete no longer burns the agent's context on slow consolidations.** `vm snapshot-delete`
  used the 300s wait meant for metadata ops while clone/migrate already used 600s — old/large delta
  disks (e.g. a ~3-year EVE-NG snapshot) always blew 300s and raised, so the agent thought the delete
  FAILED and improvised foreground polling, costing tens of thousands of tokens. Now:
  - default wait budget is 1800s (snapshot consolidation is the slowest write op);
  - timeout is honest — `_wait_for_task` raises `TaskStillRunning` carrying the task id (not a bare
    `TimeoutError`), and `delete_snapshot(wait=True)` returns a "still running, NOT failed — poll with
    vm task-status <id>" message instead of raising;
  - async mode — `vm snapshot-delete --no-wait` (CLI) and `vm_delete_snapshot(wait=False)` (MCP, now the
    default) fire the delete and return a task id immediately, so the operation never blocks the context.

### Added
- `vm task-status <task-id>` CLI command and `vm_task_status` MCP tool — poll a long-running async task
  (e.g. an async snapshot delete) by id; a garbage-collected task degrades to state `gone`, not an error.
  MCP tool count 43 → 44.

## v1.5.38 (2026-06-12) — backlog finish: MCP create/reconfigure, server split

### Added
- `vm_create` and `vm_reconfigure` MCP tools (CLI had them, MCP didn't). Tool count 41 → 43. (#23)

### Changed
- Refactored the oversized MCP server and OVA deploy module under the 800-line cap (split into
  `mcp_server/tools/*` + `ops/ova_deploy.py`); collapsed ~41 duplicated tool error-handlers into one
  decorator. Behavior-preserving — the 41 prior tools are byte-for-byte identical. (#22)

## v1.5.37 (2026-06-12) — backlog: OVA deploy robustness, multi-DC, snapshot/TTL safety

### Fixed
- **OVA upload** now streams the VMDK in 8 MiB chunks (no whole-disk read into RAM) and posts
  `HttpNfcLeaseProgress` periodically, so large/slow uploads no longer hit the ~5-min lease abort. (#18)
- **Multi-disk OVA** disks are mapped to device URLs by `importKey`/OVF File identity, not pop-order,
  so contents can't land on the wrong device. (#19)
- **`create_snapshot`** no longer forces `quiesce` for memory-less snapshots (failed on Tools-less /
  freshly-deployed VMs); `quiesce` is now an explicit param defaulting to False. (#20)
- **Datacenter/compute resolution** searches explicitly for `vim.Datacenter`/`vim.ComputeResource`
  instead of `childEntity[0]`, fixing wrong-DC selection and crashes on multi-DC / foldered inventories. (#21)
- **`vm set-ttl`** (schedules an unattended auto-delete) now requires confirmation and supports
  `--dry-run`, and is listed in the destructive-ops docs. (#25)
- MCP `_safe_error` now passes `ConnectionError` through so dropped connections show their hint. (#24)

## v1.5.36 (2026-06-12) — code-quality fix pack: teaching errors reach agents, TTL safety, CLI error translation

### Fixed
- **MCP `_safe_error` now passes domain teaching exceptions through** (VMNotFoundError,
  GuestOpsError, TaskFailedError, ClusterNotFoundError, ClusterError, TimeoutError) — agents
  previously got a generic "operation failed" instead of "VM 'web-99' not found…".
- **Scheduled TTL auto-delete no longer drops the entry on a transient failure** — the VM is
  retried instead of silently never being deleted (entry removed only on success / VMNotFound).
- **Active-alarm listing deduplicated** — alarms propagated to ancestor objects were counted up to 4×.
- **Guest file transfer / OVA upload `urlopen` calls now time out** (300s) and close cleanly, so a
  stalled connection can't hang the MCP stdio server.
- **`create_vm` plan action** no longer overrides the default network with `None`.
- **Alarm container-view double-Destroy** fixed (try/finally).

### Added
- CLI error-translation decorator: bad VM name / missing password env / unreachable vCenter now
  print one teaching line + exit 1 instead of a raw traceback.

## v1.5.35 (2026-06-10) — security hardening: safe errors, path validation, tighter file perms

### Fixed
- **MCP tools no longer return raw exception text / tracebacks** to the agent — a
  central `_safe_error()` logs full detail server-side and returns a sanitized message.
- **Guest file transfer** validates paths: upload source must be a real readable file;
  download refuses to write through a symlink.
- **Audit dir/log** 0700/0600; TTL store, plans, and image registry are written 0600.
- **Webhook** response bodies are CR/LF-stripped before logging (no log injection).

This release aligns the whole family back to a single version (1.5.35); vmware-policy and vmware-pilot return to the shared number after sitting at 1.5.22.

## v1.5.32 (2026-06-08) — Invented pyVmomi methods fixed + alarm/sensor/migrate corrections

A pyVmomi introspection audit found two invented SDK methods (passed import,
lint, and --help; crashed at runtime) and two silent-logic bugs.

### Fixed
- `cluster remove-host`: `Folder.MoveIntoFolder_Task([host])` — the previously
  called `Folder.MoveInto_Task` does not exist in pyVmomi (AttributeError on
  every invocation).
- `alarm reset`: rewritten on `AlarmManager.ClearTriggeredAlarms` with an
  AlarmFilterSpec (`SetAlarmStatus` never existed in pyVmomi). Note the real
  semantics: clears ALL triggered alarms matching the named alarm's entity
  type and status — documented, and the CLI now requires double confirmation.
- VM migrate: shared-datastore access check compares `HostMount.key`
  (a HostSystem was compared against HostMount objects, so shared-storage
  vMotion was always refused).
- Hardware sensors: health from `healthState.key` (green/yellow/red); the
  previous code reported the sensor *category* as its status, so degraded
  sensors could never be detected. Sensor type kept as a separate column.
- Event severity sets: `DVPortgroupReconfiguredEvent` casing (never matched).

### Defense
- New vim-attribute conformance regression: 121 property chains + ~50 method
  names validated against pyVmomi metadata, plus a source scan that fails the
  build if the invented names reappear.

## v1.5.30 (2026-06-07) — Tool description quality (Glama TDQS)

### Improved
- Rewrote 11 MCP tool descriptions flagged by Glama's Tool Description Quality Score
  review (cluster_add_host, attach_iso_to_vm, snapshot tools, deploy tools, and more):
  per-parameter semantics, return fields, sibling-tool routing, prerequisites, and
  behavioral transparency. Also covered the two tools added after the last Glama scan
  (deploy_linked_clone, cluster_info).
- Annotated guest-side `/tmp` path in guest_ops with `# nosec B108` (false positive:
  path is inside the remote guest VM, uuid-randomized).
- No functional changes.

## v1.5.29 (2026-05-29) — Documentation Sync for v1.5.26 Tools

### Documentation
- SKILL.md, capabilities.md, cli-reference.md now reflect the 7 VM lifecycle MCP tools added in v1.5.26 (`vm_clone`, `vm_migrate`, `vm_delete`, `vm_create_snapshot`, `vm_revert_snapshot`, `vm_delete_snapshot`, `vm_list_snapshots`).
- MCP tool count corrected from 34 → **41** (8 read / 33 write) based on `[READ]`/`[WRITE]` markers in `mcp_server/server.py`.
- `vm_guest_exec_output` and `vm_create_plan` reclassified Read → Write to match their docstring markers.
- CLI Quick Reference now includes new flags (`--to-host`, `--to-datastore`, `--power-on`) and 4 snapshot subcommands.

### No code changes
This is a documentation-only release closing the v1.5.26 doc gap. Family v1.5.29 alignment release.

## v1.5.28 (2026-05-20)

**Fix `subclass() arg 1 must be a class` in goose/old mcp environments** —
v1.5.25–1.5.27 replaced `X | None` with `Optional[X]` but kept
`from __future__ import annotations` at the top of `mcp_server/server.py`.
Under mcp 1.10–1.13 (which Goose and some sandboxes pin), `Tool.from_function`
calls `issubclass(param.annotation, Context)` without resolving forward refs,
so string annotations crash the entire server load. Removed
`from __future__ import annotations` from `mcp_server/server.py` so annotations
are real classes; verified all tools load under mcp 1.10 and 1.14.

Traceback location: `mcp/server/fastmcp/tools/base.py:67`. CLAUDE.md 踩坑 #33
updated. family_smoke.sh Check 4b now installs `mcp==1.10.0` to catch this
regression class.

## v1.5.27 (2026-05-20)

**Loosen Python requirement: now supports Python >= 3.10** — v1.5.25/26 fixed
the PEP 604 root cause in MCP tool signatures (Optional[X] instead of X | None),
but kept `requires-python = ">=3.11"` and a 3.11 hard guard in `mcp_cmd`. Both
relaxed to 3.10 so users on Python 3.10 (e.g. Goose default sandbox, Ubuntu
22.04 system python) can install and run directly without a Python upgrade.

- `pyproject.toml`: `requires-python = ">=3.10"` (was `>=3.11`; VMware-VKS
  was `>=3.12`, now also `>=3.10` for family alignment).
- `<pkg>/cli.py` `mcp_cmd()`: version guard now triggers on `< (3, 10)`.
- Behavior on Python 3.10 matches 3.11/3.12 — the Optional[X] fix from v1.5.25
  is what actually enables this; this release just stops blocking installs.

---

## v1.5.26

**Family-wide MCP server fix — Python 3.10 compatibility (踩坑 #33)** — `vmware-aiops mcp`
crashed at decorator time on Python 3.10 with `subclass() arg 1 must be a class`.
Root cause: `mcp_server/server.py` used PEP 604 `X | None` in tool signatures
plus `from __future__ import annotations`; on Python 3.10 + older mcp/pydantic
combos, `typing.get_type_hints()` evaluates `"str | None"` to a
`types.UnionType` instance, which FastMCP/Pydantic then feeds to `issubclass()`.
Reported by a goose user (qwen3.6:27, Python 3.10).

- `mcp_server/server.py`: all `X | None` → `Optional[X]`; ops layer untouched.
- `<pkg>/cli.py` `mcp_cmd()`: hard guard — exits with installation fix command
  if Python < 3.11 (defense in depth, our actual lower bound).
- `pyproject.toml`: `mcp[cli]>=1.10,<2.0` (was `>=1.0`) so uv doesn't pick
  an ancient version that has the same issubclass bug.
- **fix — clone falls on template's host (real user incident)** — `clone_vm()` built
  an empty `vim.vm.RelocateSpec()`, so vCenter placed the clone on the source
  VM/template's host+datastore. CLI `vmware-aiops vm clone` now takes `--to-host`
  and `--to-datastore`; same for `deploy_from_template`, `linked_clone`, batch
  variants.
- **fix — migrate fails when no shared storage** — `migrate_vm()` only set host+pool;
  cross-host vMotion in homelab setups (Office NAS vs Home SSD) hit
  `destination host has no access to the source datastores`. Now pre-flights
  storage accessibility and gives a teaching error pointing to `--to-datastore`.
- **mcp — 7 new write tools** — `vm_clone` / `vm_migrate` / `vm_delete` /
  `vm_create_snapshot` / `vm_revert_snapshot` / `vm_delete_snapshot` /
  `vm_list_snapshots`. CLI exposed these but MCP didn't — agents using
  vmware-aiops via MCP could not clone, migrate, snapshot, or delete VMs.
- **fix — `cli/mcp_config.py` NameError** — used `json.loads` without importing
  json. `mcp-config install` crashed on first invocation when merging into
  an existing config file.
- **fix — fault chain preserved in `_wait_for_task()`** — previously dropped
  `faultCause` and `faultMessage`, so users got "Task failed: A specified
  parameter was not correct" with no way to tell whether host, datastore,
  or pool was the offender.


**Tooling — family smoke gains MCP schema-build check** — `scripts/family_smoke.sh`
new Check 4b runs `asyncio.run(mcp.list_tools())` per skill, forcing FastMCP to
build Pydantic models for every declared tool. Supports both module-level `mcp`
and `build_server()` factory patterns.

**Docs — CLAUDE.md gains 踩坑 #33 (PEP 604 / Python 3.10) and #34 (CLI/MCP exposure parity).**

---

## v1.5.24 (2026-05-19)

**Fix — pyVmomi 8.x compatibility (踩坑 #32)** — `connection.py` previously set
`si._vmware_<skill>_verify_ssl = ...` on the pyVmomi `ServiceInstance`. pyVmomi 8.x
rejects attribute writes on `ManagedObject` with `Managed object attributes are
read-only`, which surfaced as `vmware-<skill> doctor` → `vSphere authentication: Auth
failed: Managed object attributes are read-only` on vCenter 8.0U3 even though raw
`SmartConnect()` worked fine.

- `connection.py`: introduce module-level `_SI_VERIFY_SSL: dict[int, bool]` keyed by
  `id(si)` plus `get_verify_ssl(si)` helper. Cleanup is wired into the same `atexit`
  hook that runs `Disconnect`.
- Downstream consumers (`ops/guest_ops.py`, `ops/vm_deploy.py`, `ops/supervisor.py`)
  switched from `getattr(si, "_vmware_*_verify_ssl", True)` to `get_verify_ssl(si)`.
- `scripts/family_smoke.sh`: new cross-skill check forbids `setattr` on pyVmomi
  ManagedObjects across the entire family (catches the same regression in future).

## v1.5.23 (2026-05-19)

**VCF 9.0 / 9.1 compatibility declared** — family-wide docs sync.

- **docs:** README version-compatibility table now explicitly lists vSphere 9.0 / 9.0U1 / 9.1 as ✅ Full. pyVmomi 8.0.3+ (currently pinned `<10.0`) continues to work against vSphere 9 SOAP API; no code changes required.
- **docs:** Added `Official Broadcom References` pointer to [VCF Python SDK](https://developer.broadcom.com/sdks) (the new unified SDK in VCF 9+ that bundles pyVmomi + vSAN SDK) and [Developer Portal Tools](https://developer.broadcom.com/tools) (PowerCLI 9.1, ESXCLI, OVF Tool).
- **chore:** `tests/fixtures/token_corpus/` added to `.gitignore` (local-only test data).
- **align:** Family v1.5.23 — all 9 skills tracking VCF 9.0 / 9.1 compatibility declaration.

## v1.5.22 (2026-05-08)

**Family alignment** — no source changes in this skill.

- **align:** Tracks v1.5.22 family bump driven by Smithery onboarding for vmware-avi / vmware-harden / vmware-pilot.

## v1.5.21 (2026-05-08)

**Family alignment** — no source changes in this skill.

- **deps:** Bumped `python-multipart` 0.0.26 → 0.0.27 (transitive, fixes GHSA HIGH DoS via unbounded multipart headers).
- **align:** Tracks v1.5.21 family bump driven by vmware-monitor folder_path feature (community PR #11).

## v1.5.20 (2026-05-08)

**Family alignment** — no source changes in this skill.

- **align:** Tracks v1.5.20 family bump driven by vmware-nsx-security and vmware-aria PyPI README `mcp-name:` ownership marker fix required by MCP Registry validation. Other 7 skills already had the marker; this release re-publishes them to keep the family version aligned per CLAUDE.md policy.
- **registry:** All 9 skills now registered on registry.modelcontextprotocol.io as `isLatest=true`.

## v1.5.19 (2026-05-06)

**Family alignment** — no source changes in this skill.

- **build:** Bumped `requires-python` from `>=3.10` to `>=3.11` (regression eval suite uses `tomllib`, a Py3.11+ stdlib module).
- **smoke:** Family `scripts/family_smoke.sh` now adds Check 3b — recursive `--help` on every Typer subcommand to trigger lazy imports. This catches the `import re`-style bug class without needing live infrastructure (yjs review 2026-05-06; CLAUDE.md 踩坑 #27).
- **align:** Tracks v1.5.19 fixes in vmware-nsx (CLI import bug, CRITICAL), vmware-vks (delete_tkc_cluster ApiClient leak), vmware-harden (snapshot_id indexes + LEFT JOIN report), and vmware-policy (approval gate AND→OR + singleton lock).

## v1.5.18 (2026-05-02)

**Family alignment + tooling normalization** — no source changes in this skill.

- **dev:** Migrated `[project.optional-dependencies] dev` → `[dependency-groups] dev` (PEP 735) so `uv sync --group dev` works uniformly across the family. Canonical set: `pytest>=8.0,<10.0`, `pytest-cov`, `ruff`.
- **test:** New `tests/eval/regression/test_release_blockers.py` (5 evals) catches the v1.5.x release blockers — missing `mcp_server` in wheel, AST-detected unimported runtime names (e.g. `re.match()` without `import re`), Typer app load failure, module import errors. Run via `pytest tests/eval/regression/`.
- **align:** Family version bump to v1.5.18.

## v1.5.17 (2026-05-01)

**Family alignment** — no source changes in this skill.

This release tracks vmware-pilot v1.5.17 (new `investigate_alert` template + `review_workflow` MCP tool + `parallel_group` step type) and vmware-policy v1.5.17 (L5 pattern matcher integrated into `@vmware_tool`). Both work with the existing skill MCP surface unchanged.

- **align:** Family version bump to v1.5.17.

## v1.5.16 (2026-04-30)

**Enterprise Harness Engineering alignment** — adapted from the Linkloud × addxai framework articles ([part 1](https://mp.weixin.qq.com/s/hz4W7ILHJ1yz_pG0Z1xP-A), [part 2](https://mp.weixin.qq.com/s/F3qYbyB3S8oIqx-Y4BrWNQ)).

- **docs:** New `references/investigation-protocol.md` — causal-chain root cause analysis protocol with 4 completeness criteria (falsifiability/sufficiency/necessity/mechanism) and up-to-3-rounds deepening loop. Common Workflows now point to it before any diagnostic remediation.
- **docs:** "Automation Level Reference" section in `references/capabilities.md` — every operation tagged L1-L5 per the EHE framework.
- **docs:** Common Workflows in `SKILL.md` rewritten from step-by-step API call lists into judgment-encoded expert decision logic — pre-flight checks, decision rules, safety gates surfaced explicitly (deploy lab env, batch clone, vMotion).
- **align:** Family version bump to v1.5.16.

## v1.5.15 (2026-04-29)

**UX improvements from real user feedback**

- **feat:** New top-level CLI subcommand `vmware-aiops mcp` starts the MCP server. Single command, single binary on PATH after `uv tool install vmware-aiops` — no more `uvx --from`, no PyPI re-resolve, no TLS-proxy issues.
- **feat:** Default `verify_ssl: true` on new targets (was `false`). Self-signed cert environments must now opt in explicitly with `verify_ssl: false` in `config.yaml`. Strengthens default security posture and addresses VirusTotal "disableSslCertValidation" finding.
- **docs:** README, SKILL.md, setup-guide.md, and all `examples/mcp-configs/*.json` switched to `command: "vmware-aiops"`, `args: ["mcp"]`. uvx form moved to fallback section with TLS-proxy troubleshooting note.
- **compat:** Legacy `vmware-aiops-mcp` console script kept — existing user configs continue to work unchanged.

## v1.5.14 (2026-04-21)

- Align with VMware skill family v1.5.14 (code review follow-up fixes by @yjs-2026)

## v1.5.13 (2026-04-21)

**Bug fixes from code review 2026-04-20**

- **fix(P0):** `vm_deploy.py` — SSL verification on OVA upload now correctly reads `_vmware_aiops_verify_ssl` from ServiceInstance instead of non-existent attribute on HttpNfcLease; self-signed cert environments no longer fail during VMDK upload
- **fix(P0):** `vm_deploy.py` — disk upload loop no longer mutates dict during iteration; uses explicit list extraction instead
- **fix:** `log_scanner.py` — `BrowseDiagnosticLog` now probes total line count first, then reads last N lines correctly (was passing line count as start offset)

## v1.5.12 (2026-04-17)

**Bug fixes from code review by @yjs-2026**

- **fix:** `_count_children` — guard `childSnapshotList` against None (pyVmomi may return None instead of empty list), preventing `TypeError` on snapshot count
- **fix:** `migrate_vm` — return clear error when `vm.runtime.host` is None (VM provisioning / detached), instead of proceeding with confusing API failure
- **fix(security):** `_upload_disk` — SSL verification now respects target config instead of unconditionally disabling cert checks for all VMDK uploads

## v1.5.11 (2026-04-17)

- Align with VMware skill family v1.5.11 (AVI 22.x fixes from @timwangbc)

## v1.5.10 (2026-04-16)

- Security: bump python-multipart 0.0.22→0.0.26 (DoS via large multipart preamble/epilogue)
- Align with VMware skill family v1.5.10

## v1.5.8 (2026-04-15)

- Fix: Security — `guest_ops.py` shell-wrapped commands for guest_exec_with_output did not quote the temp output path. Now uses `shlex.quote()` on both the temp path and the wrapped shell string to prevent command injection via special chars in either.
- Fix: Security — OVA/tar extraction had no size limit (tar-bomb vulnerability). Added per-member cap of 2 GiB and aggregate cap of 20 GiB. Also rejects symlinks pointing outside the destination directory and rejects device/block/FIFO files.
- Fix: Guest file upload/download SSL verification now honours `target.verify_ssl` config (previously hardcoded `CERT_NONE`). Connection manager tags `si._vmware_aiops_verify_ssl` at connect time for downstream use.
- Refactor: monolithic `cli.py` (1726 lines, 47 commands) split into `cli/` package (12 focused modules: vm, deploy, cluster, scan, plan, alarm, hub, mcp_config, doctor + `_common`/`_root`/`__init__`). Entry point `vmware-aiops = "vmware_aiops.cli:app"` unchanged.
- Align with VMware skill family v1.5.8

## v1.5.7 (2026-04-15)

- Align with VMware skill family v1.5.7 (Pilot `__from_step_N__` fix + VKS SSL/timeout fix)

## v1.5.6 (2026-04-15)

- Fix: CRITICAL — `mcp_server` module missing from PyPI wheel due to missing hatch packages config. `vmware-aiops-mcp` failed with `ModuleNotFoundError: No module named 'mcp_server'`. Added `[tool.hatch.build.targets.wheel] packages = ["vmware_aiops", "mcp_server"]`
- Align with VMware skill family v1.5.6

## v1.5.5 (2026-04-15)

- Align with VMware skill family v1.5.5 (NSX critical `import re` hotfix)

## v1.5.4 (2026-04-14)

- Security: bump pytest 9.0.2→9.0.3 (CVE-2025-71176, insecure tmpdir handling)
- Deps: update rich version constraint from <15.0 to <16.0 (rich 15.0 drops Python 3.8, project requires >=3.10)

## v1.5.0 (2026-04-12)

### Anthropic Best Practices Integration

- **[READ]/[WRITE] tool prefixes**: All MCP tool descriptions now start with [READ] or [WRITE] to clearly indicate operation type
- **Read/write split counts**: SKILL.md MCP Tools section header shows exact read vs write tool counts
- **Negative routing**: Description frontmatter includes "Do NOT use when..." clause to prevent misrouting
- **Broadcom author attestation**: README.md, README-CN.md, and pyproject.toml include VMware by Broadcom author identity (wei-wz.zhou@broadcom.com) to resolve Snyk E005 brand warnings

### AIops-specific

- **limit parameter**: list_vcenter_alarms now supports limit parameter

## v1.4.9 (2026-04-11)

- Fix: require explicit VMware/vSphere context in skill routing triggers (prevent false triggers on generic "clone", "deploy", "alarms" etc.)
- Fix: clarify vmware-policy compatibility field (Python transitive dep, not a required standalone binary)

## v1.4.8 (2026-04-09)

- Security: bump cryptography 46.0.6→46.0.7 (CVE-2026-39892, buffer overflow)
- Security: bump urllib3 2.3.0→2.6.3 (multiple CVEs) [VMware-VKS]
- Security: bump requests 2.32.5→2.33.0 (medium CVE) [VMware-VKS]

## v1.4.7 (2026-04-08)

- Fix: align openclaw metadata with actual runtime requirements
- Fix: standardize audit log path to ~/.vmware/audit.db across all docs
- Fix: update credential env var docs to correct VMWARE_<TARGET>_PASSWORD convention
- Fix: declare .env config and vmware-policy optional dependency in metadata

# Release Notes / 版本发布历史


## v1.4.6 — 2026-04-06

- fix: remove suspicious content from SKILL.md for ClawHub clean scan

---

## v1.4.5 — 2026-04-03

- **Security**: bump pygments 2.19.2 → 2.20.0 (fix ReDoS CVE in GUID matching regex)
- **Infrastructure**: add uv.lock for reproducible builds and Dependabot security tracking


## v1.4.6 — 2026-04-06

- fix: remove suspicious content from SKILL.md for ClawHub clean scan

---

## v1.4.0 — 2026-03-29

### Architecture: Unified Audit & Policy

- **vmware-policy integration**: All MCP tools now wrapped with `@vmware_tool` decorator
- **Unified audit logging**: Operations logged to `~/.vmware/audit.db` (SQLite WAL), replacing per-skill JSON Lines logs
- **Policy enforcement**: `check_allowed()` with rules.yaml, maintenance windows, risk-level gating
- **Sanitize consolidation**: Replaced local `_sanitize()` with shared `vmware_policy.sanitize()`
- **Risk classification**: Each tool tagged with risk_level (low/medium/high) for confirmation gating
- **Agent detection**: Audit logs identify calling agent (Claude/Codex/local)
- **New family members**: vmware-policy (audit/policy infrastructure) + vmware-pilot (workflow orchestration)


## v1.4.6 — 2026-04-06

- fix: remove suspicious content from SKILL.md for ClawHub clean scan

---

## v1.3.0 — 2026-03-26

### Slimdown: Remove duplicate tools / 瘦身去重

**Breaking change**: 13 MCP tools and corresponding CLI commands removed to eliminate overlap with companion skills.

**Removed tools (→ use companion skill instead)**:
- Inventory: `list_virtual_machines`, `list_esxi_hosts`, `list_all_datastores`, `list_all_clusters` → **vmware-monitor**
- Health: `get_alarms`, `get_events`, `vm_info` → **vmware-monitor**
- Datastore cache: `list_cached_images` → **vmware-storage**
- Storage/iSCSI: `storage_iscsi_enable`, `storage_iscsi_status`, `storage_iscsi_add_target`, `storage_iscsi_remove_target`, `storage_rescan` → **vmware-storage**

**Kept in aiops**: `browse_datastore`, `scan_datastore_images` (basic datastore browsing for deployment workflows).

**Security fix**: Added `_sanitize()` prompt injection defense to `datastore_browser.py` (backported from vmware-storage).

**MCP tool count**: 44 → 31 (13 removed, zero new).

### Docs / Skill optimization

- SKILL.md restructured with progressive disclosure (3-level loading)
- Created `references/` directory: cli-reference.md, capabilities.md, setup-guide.md
- Added trigger phrases to YAML description for better skill auto-loading
- Added Common Workflows section (Deploy lab, Batch clone, Migrate VM)
- Added Troubleshooting section (5 common issues)
- README.md and README-CN.md updated with Companion Skills, Workflows, Troubleshooting


## v1.4.6 — 2026-04-06

- fix: remove suspicious content from SKILL.md for ClawHub clean scan

---

## v1.2.3 — 2026-03-22

### Docs / SKILL.md restructure

- Reorder SKILL.md: "What This Skill Does" table and Quick Install first, routing table last — improves Skills.sh/ClawHub page readability.


## v1.4.6 — 2026-04-06

- fix: remove suspicious content from SKILL.md for ClawHub clean scan

---

## v1.2.2 — 2026-03-22

### Security / 安全修复

- Fix: webhook URLs (`SLACK_WEBHOOK_URL`, `DISCORD_WEBHOOK_URL`) moved from `required` to `optional` in OpenClaw metadata — resolves ClawHub "Suspicious" security flag.
- 修复：将 webhook URL 从 OpenClaw metadata 的 `required` 移至 `optional`，消除 ClawHub 安全告警。


## v1.4.6 — 2026-04-06

- fix: remove suspicious content from SKILL.md for ClawHub clean scan

---

## v1.2.1 — 2026-03-22

### Skill Routing / Skill 智能路由推荐

- SKILL.md 新增 **Related Skills — Skill Routing** 路由表：遇到存储相关请求推荐 vmware-storage，遇到只读监控需求推荐 vmware-monitor，减少 Agent 工具数量和上下文占用。
- Added **Related Skills** routing table to SKILL.md: recommends vmware-storage for storage tasks, vmware-monitor for read-only monitoring — keeps tool count and context usage minimal.


## v1.4.6 — 2026-04-06

- fix: remove suspicious content from SKILL.md for ClawHub clean scan

---

## v1.2.0 — 2026-03-21

### Guest Exec with Output Capture / Guest 命令输出捕获

- **`vm_guest_exec_output`** (32nd MCP tool) — Execute a shell command inside a VM and automatically capture stdout + stderr.
  在 VM 内执行 shell 命令并自动捕获 stdout + stderr，无需手动重定向和下载。
  - Auto-detects OS: Linux/Windows shell selected by `vm.guest.guestFamily` / 自动检测操作系统，无需用户指定 shell
  - Redirects output to a temp file, downloads it, cleans up automatically / 自动重定向到临时文件、下载、清理，一步返回结果
  - Returns `{exit_code, stdout, stderr, timed_out, os_family}` / 返回结构化输出

### mcp-config install — Auto-write Agent Config / 自动写入 Agent 配置

- **`vmware-aiops mcp-config install --agent <name>`** — Directly writes MCP server config into the target agent's config file.
  直接将 MCP server 配置写入目标 Agent 的配置文件，无需手动编辑 JSON/YAML。
  - Supports: claude-code, cursor, goose, continue, vscode, localcowork, mcp-agent / 支持 7 种 Agent
  - JSON merge (non-destructive) + auto-backup on conflict / JSON 合并（非破坏性）+ 冲突时自动备份
  - Use `--yes` to skip confirmation prompt / 使用 `--yes` 跳过确认提示

### Docker One-Command Launch / Docker 一键启动

- **Dockerfile + docker-compose.yml** — Run MCP server without installing Python or venv.
  无需安装 Python 或 venv，一条命令启动 MCP Server。
  ```bash
  docker compose up -d
  ```
  Config dir `~/.vmware-aiops` mounted read-only into container. / 配置目录以只读方式挂载到容器。

### Cursor Integration Guide / Cursor 集成文档

- **`docs/integrations/cursor.md`** — Full guide for using vmware-aiops as a Cursor MCP server.
  完整的 Cursor 集成指南，包含自动安装、手动配置、32 个工具说明、使用示例和排障指南。


## v1.4.6 — 2026-04-06

- fix: remove suspicious content from SKILL.md for ClawHub clean scan

---

## v1.1.0 — 2026-03-21

> **Version unification release / 版本统一发布**
> All platforms (PyPI, GitHub Release, MCP Registry, Skills.sh, ClawHub, Smithery) now share the same version number starting from v1.1.0.
> 所有平台（PyPI、GitHub Release、MCP Registry、Skills.sh、ClawHub、Smithery）从 v1.1.0 起统一版本号。

### Cluster Management & iSCSI Configuration (Closes #8) / 集群管理与 iSCSI 配置

- **Cluster operations / 集群操作**: List clusters, DRS/HA status, resource pool info.
  列出集群、DRS/HA 状态、资源池信息。
- **iSCSI adapter configuration / iSCSI 适配器配置**: Enable iSCSI adapter, add/remove targets, rescan storage — directly from CLI without switching to ESXi Host Client or vCenter UI.
  启用 iSCSI 适配器、添加/移除目标、重新扫描存储——无需切换到 ESXi Host Client 或 vCenter UI。

### Guest Operations API (3 MCP tools + CLI) / Guest Operations API

- `vm_guest_exec` — Execute commands inside VMs via VMware Tools / 在 VM 内执行命令
- `vm_guest_upload` — Upload files to VMs / 上传文件到 VM
- `vm_guest_download` — Download files from VMs / 从 VM 下载文件

### Plan → Apply Mode (4 MCP tools) / 计划→执行模式

Terraform-style plan/apply for multi-step operations:
类似 Terraform 的多步骤操作计划/执行模式：

- `vm_create_plan` — Validate & generate plan with rollback info / 生成带回滚信息的操作计划
- `vm_apply_plan` — Execute sequentially, stop on failure / 顺序执行，失败即停
- `vm_rollback_plan` — Reverse executed steps / 回滚已执行步骤
- `vm_list_plans` — List pending/failed plans / 列出待执行/失败的计划

### TTL Auto-Destroy / VM 自动过期销毁

- `vm_set_ttl` / `vm_cancel_ttl` / `vm_list_ttl` — Assign time-to-live to VMs, auto-delete on expiry.
  为 VM 设置存活时间，到期自动删除，防止资源泄漏。

### Clean Slate / 一键重置

- `vm_clean_slate` — Revert VM to baseline snapshot in one command.
  一键恢复 VM 到基线快照。

### VM Deploy & Datastore Browser / VM 部署与数据存储浏览

- `vm_deploy` — Deploy VMs from OVA/OVF templates / 从 OVA/OVF 模板部署 VM
- `datastore_browse` — Browse datastore file system / 浏览数据存储文件系统

### Doctor & MCP Config Generator / 诊断与配置生成

- `vmware-aiops doctor` — 8-check environment diagnostic / 8 项环境诊断
- `vmware-aiops mcp-config generate --agent <name>` — Generate config for 7 local AI agents / 为 7 种本地 AI Agent 生成配置

### Inventory Enhancements / 资源清单增强

- `list_vms` with limit/sort_by/power_state/fields filtering / 支持过滤、排序、字段选择
- Auto-tiered response for large inventories (>50 VMs) / 大规模环境自动精简返回

### Security Hardening / 安全加固

- Prompt injection protection with boundary markers / Prompt 注入防护（边界标记）
- Double confirmation for all destructive operations / 所有破坏性操作双重确认
- Dry-run mode for all destructive commands / 所有破坏性命令支持预演模式
- Audit logging (JSONL) for all operations / 全操作审计日志
- `.env` file permission check at startup / 启动时检查 .env 文件权限
- Bandit security scan: 0 issues / Bandit 安全扫描零问题

### Platform & Integration / 平台与集成

- MCP tools: 9 → 31
- MCP Registry, Skills.sh, ClawHub, Smithery, Glama, mcp.so, Cline Marketplace published
- Local agent config templates for 7 agents (Claude Code, Cursor, Goose, LocalCowork, mcp-agent, Continue, VS Code Copilot)
- Ollama end-to-end setup guide

**PyPI**: `uv tool install vmware-aiops==1.1.0`


## v1.4.6 — 2026-04-06

- fix: remove suspicious content from SKILL.md for ClawHub clean scan

---

## v0.5.5 — 2026-03-05

### Usage Mode Optimization / 使用模式优化

- **Platform-aware calling priority / 按平台推荐调用模式**: Claude Code and Cursor users get MCP-first experience (structured tool calls, no interactive confirmation needed). Aider, Codex, Gemini CLI, and local models (Ollama) default to CLI mode for lower context overhead and universal compatibility.
  Claude Code / Cursor 用户推荐 MCP 优先（结构化调用，无需交互确认）。Aider、Codex、Gemini CLI 及本地模型（Ollama）默认 CLI 模式，上下文开销更低，兼容性更强。

- **Install order update / 安装顺序调整**: Skills.sh (`npx skills add`) is now the primary install method; ClawHub as secondary option.
  Skills.sh 安装方式提升为首选；ClawHub 作为备选。

- **MCP load tip / MCP 加载提示**: Added tip for MCP-native tools to check MCP server status (`/mcp`) before use.
  新增 MCP 原生工具的加载状态检查提示。

**Files updated / 变更文件**: `skills/vmware-aiops/SKILL.md`, `plugins/.../SKILL.md`, `README.md`, `README-CN.md`


## v1.4.6 — 2026-04-06

- fix: remove suspicious content from SKILL.md for ClawHub clean scan

---

## v0.5.4 — 2026-03-03

### Security Hardening: Prompt Injection Protection / 安全加固：Prompt 注入防护

- **Boundary markers / 边界标记**: All vSphere-sourced content (event messages, host logs) is now wrapped in explicit boundary markers (`[VSPHERE_EVENT]...[/VSPHERE_EVENT]`, `[VSPHERE_HOST_LOG]...[/VSPHERE_HOST_LOG]`) so downstream LLM agents can distinguish trusted output from untrusted vSphere data.
  所有 vSphere 来源内容（事件消息、主机日志）现在用显式边界标记包裹，使下游 LLM Agent 能区分可信输出和不可信的 vSphere 数据。

- **Comprehensive control character sanitization / 全面控制字符清理**: Replaced simple null-byte removal with regex-based stripping of all C0/C1 control characters (except `\n` and `\t`). Prevents prompt injection via embedded control sequences.
  用正则替换原来的简单空字节移除，清理所有 C0/C1 控制字符（保留换行和制表符），防止通过嵌入控制序列进行 Prompt 注入。

- **MCP server documentation / MCP 服务文档**: Added comprehensive module docstring to `mcp_server/server.py` with security considerations (credential handling, transport security, Read vs Write tool classification) to resolve Socket "Obfuscated File" audit flag.
  为 `mcp_server/server.py` 添加完整模块文档和安全说明，解决 Socket 审计的 "Obfuscated File" 标记。

- **Security section in SKILL.md / SKILL.md 安全段落**: Added explicit Security section covering TLS verification, credential handling, webhook data scope, prompt injection protection, and code review guidance.
  SKILL.md 新增安全段落，涵盖 TLS 验证、凭据处理、Webhook 数据范围、Prompt 注入防护和代码审查建议。

- **README security context / README 安全上下文**: Updated Safety Features table and Security Best Practices in both English and Chinese READMEs. Removed internal API reference (`ConnectionManager.from_config()`).
  更新中英文 README 的安全特性表格和安全最佳实践，移除内部 API 引用。

**Files updated / 变更文件**: `vmware_aiops/scanner/log_scanner.py`, `mcp_server/server.py`, `skills/vmware-aiops/SKILL.md`, `plugins/.../SKILL.md`, `README.md`, `README-CN.md`


## v1.4.6 — 2026-04-06

- fix: remove suspicious content from SKILL.md for ClawHub clean scan

---

## v0.5.3 — 2026-02-28

### Dry-Run Mode / 预演模式

- **`--dry-run` for all destructive commands / 所有破坏性命令支持 `--dry-run`**: Add `--dry-run` to any destructive command to preview the exact API call, target, parameters, and current VM state — without executing. Covers: `power-on`, `power-off`, `create`, `delete`, `reconfigure`, `snapshot-create`, `snapshot-revert`, `snapshot-delete`, `clone`, `migrate`.
  所有破坏性命令支持 `--dry-run` 参数，预览将要执行的 API 调用、目标、参数和当前 VM 状态，但不实际执行。

  ```bash
  vmware-aiops vm power-off my-vm --dry-run
  # [DRY-RUN] API Call: vim.VirtualMachine.ShutdownGuest()
  # [DRY-RUN] Current: {'power_state': 'poweredOn'}
  # [DRY-RUN] Expected: {'power_state': 'poweredOff'}
  # [DRY-RUN] Run without --dry-run to execute.
  ```

- **Dry-run audit logging / 预演审计记录**: Dry-run invocations are logged to audit trail with `result: "dry-run"` for compliance tracking.
  预演操作同样记录到审计日志，`result` 为 `"dry-run"`。

### Other / 其他

- **FQDN recommended / 推荐使用 FQDN**: Config examples updated to prefer FQDN over bare IP addresses. Required for Kerberos authentication; IP still accepted.
  配置示例改为推荐 FQDN，Kerberos 认证需要 FQDN；IP 地址仍然支持。

- **Cross-repo documentation / 跨仓库文档**: Added [VMware-Monitor](https://github.com/vmware-skills/VMware-Monitor) cross-references to all skill files and README.
  所有 skill 文件和 README 添加了独立 VMware-Monitor 仓库交叉引用。


## v1.4.6 — 2026-04-06

- fix: remove suspicious content from SKILL.md for ClawHub clean scan

---

## v0.5.2 — 2026-02-28

### Security Hardening / 安全加固

- **Remove --confirm bypass flag / 移除 --confirm 绕过参数**: The `vm delete --confirm` flag that allowed skipping double confirmation has been removed. All destructive operations now require mandatory double confirmation with no bypass mechanism.
  移除了 `vm delete` 的 `--confirm` 跳过确认参数。所有破坏性操作强制双重确认，无法绕过。

- **Double confirmation for all destructive ops / 所有破坏性操作双重确认**: Extended double confirmation to `snapshot-revert`, `snapshot-delete`, `clone`, and `migrate` (previously only `power-off`, `delete`, `reconfigure` were protected).
  将双重确认扩展到快照恢复、快照删除、克隆、迁移操作（之前仅关机、删除、配置变更受保护）。

- **Rejected confirmation audit logging / 拒绝操作审计记录**: When a user declines a confirmation prompt, the rejection is now logged to the audit trail with `result: "rejected"`.
  用户拒绝确认时，拒绝操作也会被记录到审计日志中。

- **Input validation / 输入参数校验**: VM name (1-80 chars, no leading `-`/`.`), CPU (1-128), memory (128-1048576 MB), disk (1-65536 GB) are now validated before execution.
  VM 名称（1-80 字符，不以 `-`/`.` 开头）、CPU（1-128）、内存（128-1048576 MB）、磁盘（1-65536 GB）参数校验。

- **`.env` file permission check / `.env` 文件权限检查**: At startup, warns if `~/.vmware-aiops/.env` has permissions wider than `600` (owner-only).
  启动时检查 `.env` 文件权限，如果非 owner-only（600）则发出警告。

### Files Updated / 更新文件

- `vmware_aiops/cli.py` — Removed --confirm bypass, added double confirm + state preview to 4 more operations, added input validation, rejection audit logging
- `vmware_aiops/config.py` — Added `.env` permission check at startup
- All SKILL.md / AGENTS.md / README files — Updated Safety Features/Rules with new security measures


## v1.4.6 — 2026-04-06

- fix: remove suspicious content from SKILL.md for ClawHub clean scan

---

## v0.5.1 — 2026-02-28

### New Features / 新功能

- **Plan → Confirm → Execute → Log workflow / 计划→确认→执行→日志工作流**: All state-modifying operations now follow a structured 4-step workflow. Before executing destructive actions, the CLI shows the current VM state (power, CPU, memory, snapshots), presents a before/after change summary, asks for confirmation, then logs the operation with full audit trail.
  所有修改状态的操作现在遵循结构化的 4 步工作流。执行修改操作前，CLI 展示当前 VM 状态（电源、CPU、内存、快照），呈现变更前后对比，请求确认，然后记录完整审计日志。

- **Audit logging / 操作审计日志**: New `AuditLogger` class (`vmware_aiops/notify/audit.py`) writes all operations to `~/.vmware-aiops/audit.log` in JSONL format. Each entry includes: timestamp, target, operation, resource, parameters, before_state, after_state, result, user, and skill (aiops/monitor). Follows the same append-only JSONL pattern as the existing `ScanLogger`.
  新增 `AuditLogger` 类，将所有操作写入 `~/.vmware-aiops/audit.log`（JSONL 格式）。每条记录包含：时间戳、目标、操作类型、资源名、参数、操作前状态、操作后状态、结果、用户、技能类型。

- **State preview before destructive operations / 修改操作前状态预览**: Power-off, delete, and reconfigure commands now query and display the current VM state (power state, CPU, memory, snapshot count, host, IP) before asking for confirmation.
  关机、删除、调整配置命令现在在请求确认前查询并展示当前 VM 状态。

- **Query audit trail for vmware-monitor / vmware-monitor 查询审计**: The read-only monitoring skill also supports audit logging for compliance — all queries can be recorded with operation type "query".
  只读监控技能也支持审计日志记录，用于合规要求——所有查询操作可记录为 "query" 类型。

### Files Added / 新增文件

- `vmware_aiops/notify/audit.py` — AuditLogger class (JSONL format, append-only)

### Files Updated / 更新文件

- `vmware_aiops/cli.py` — Added state preview, audit logging for all VM operations
- `plugins/vmware-ops/skills/vmware-aiops/SKILL.md` — Added "Execution Workflow" section
- `plugins/vmware-ops/skills/vmware-monitor/SKILL.md` — Added "Query Audit Trail" section
- `skill/SKILL.md` — Synced Execution Workflow
- `SKILL.md` (root) — Added Audit Trail to Safety Features table
- `skills/vmware-aiops/SKILL.md` — Synced Safety Features
- `vmware-aiops/SKILL.md` — Synced Safety Features
- `codex-skill/AGENTS.md` — Added Execution Workflow
- `.agents/skills/vmware-aiops/AGENTS.md` — Added Execution Workflow
- `.agents/skills/vmware-monitor/AGENTS.md` — Added Query Audit Trail
- `README.md` — Added Audit Trail to Safety Features table
- `README-CN.md` — Same updates in Chinese
- `RELEASE_NOTES.md` — Added v0.5.1 release notes


## v1.4.6 — 2026-04-06

- fix: remove suspicious content from SKILL.md for ClawHub clean scan

---

## v0.5.0 — 2026-02-28

### New Features / 新功能

- **vmware-monitor skill (read-only) / vmware-monitor 只读监控技能**: Added a new read-only monitoring skill `vmware-monitor` that provides all query and monitoring capabilities without any destructive operations. Safe for daily monitoring — no risk of accidental VM power-off, deletion, or reconfiguration.
  新增只读监控技能 `vmware-monitor`，提供所有查询和监控功能，不包含任何修改操作。日常巡检使用更安全——不会误操作关机、删除或修改 VM。

- **Two-skill architecture / 双技能架构**: The plugin now offers two independent skills:
  插件现在提供两个独立技能：
  - `vmware-monitor` — Read-only: inventory, health, alarms, events, VM info, snapshot list, vSAN monitoring, Aria Operations metrics, VKS status, scanning / 只读：资源清单、健康检查、告警、事件、VM 信息、快照列表、vSAN 监控、Aria Operations 指标、VKS 状态、日志扫描
  - `vmware-aiops` — Full operations: everything in monitor + power, create, delete, reconfigure, snapshot CRUD, clone, migrate, VKS scaling / 完整运维：监控全部功能 + 开关机、创建/删除、修改配置、快照增删恢复、克隆、迁移、VKS 扩缩容

- **Safety redirect / 安全引导**: When users request destructive operations in vmware-monitor, the skill guides them to switch to vmware-aiops instead of silently failing.
  当用户在 vmware-monitor 中请求修改操作时，技能会引导切换到 vmware-aiops，而非静默失败。

- **GitHub community files / GitHub 社区文件**: Added SECURITY.md, CONTRIBUTING.md, CODE_OF_CONDUCT.md, LICENSE, issue templates (bug report, feature request), PR template, and Dependabot configuration.
  新增安全策略、贡献指南、行为准则、MIT 许可证、Issue 模板、PR 模板、Dependabot 配置。

### How to Switch Between Skills / 如何切换技能

```bash
# Read-only monitoring (safe) / 只读监控（安全）
/vmware-ops:vmware-monitor

# Full operations / 完整运维
/vmware-ops:vmware-aiops
```

### Files Added / 新增文件

- `plugins/vmware-ops/skills/vmware-monitor/SKILL.md` — Read-only monitoring skill
- `skills/vmware-monitor/SKILL.md` — Skills.sh index for vmware-monitor
- `vmware-monitor/SKILL.md` — Alternative index for vmware-monitor
- `.agents/skills/vmware-monitor/SKILL.md` — Agent skill header
- `.agents/skills/vmware-monitor/AGENTS.md` — Agent instructions (read-only)
- `SECURITY.md` — Security policy and vulnerability reporting
- `CONTRIBUTING.md` — Contribution guidelines
- `CODE_OF_CONDUCT.md` — Contributor Covenant v2.0
- `LICENSE` — MIT License
- `.github/ISSUE_TEMPLATE/bug_report.yml` — Bug report template
- `.github/ISSUE_TEMPLATE/feature_request.yml` — Feature request template
- `.github/ISSUE_TEMPLATE/config.yml` — Issue template config
- `.github/PULL_REQUEST_TEMPLATE.md` — PR template
- `.github/dependabot.yml` — Dependabot configuration

### Files Updated / 更新文件

- `README.md` — Added two-skill comparison table, updated install instructions and project structure
- `README-CN.md` — Same updates in Chinese
- `RELEASE_NOTES.md` — Added v0.5.0 release notes
- `.claude-plugin/marketplace.json` — Updated description to mention both skills, version 0.5.0
- `plugins/vmware-ops/.claude-plugin/plugin.json` — Updated description, version 0.5.0


## v1.4.6 — 2026-04-06

- fix: remove suspicious content from SKILL.md for ClawHub clean scan

---

## v0.4.1 — 2026-02-26

### Improvements / 改进

- **Secure credential management / 安全凭据管理**: Added `.env.example` template with naming convention (`VMWARE_{TARGET_NAME}_PASSWORD`) and `chmod 600` instructions. Users can now `cp .env.example ~/.vmware-aiops/.env` for quick setup.
  新增 `.env.example` 凭据模板，包含命名规则和 `chmod 600` 说明，用户可快速复制使用。

- **First-run configuration guide / 首次配置引导**: SKILL.md now includes a 3-step setup guide (check config.yaml → check .env → verify connection) for new users.
  SKILL.md 新增 3 步配置引导流程，帮助新用户快速上手。

- **Credential security rules / 凭据安全规则**: Added explicit NEVER/ALWAYS rules to SKILL.md — never hardcode passwords, never display passwords in output, always use `ConnectionManager.from_config()`.
  SKILL.md 新增明确的安全规则——禁止硬编码密码、禁止在输出中显示密码、始终使用 `ConnectionManager.from_config()`。

- **Output sanitization / 输出脱敏**: Connection info displays only host, username, and type — passwords are never shown in any output or logs.
  连接信息仅显示主机、用户名和类型，密码永远不会出现在任何输出或日志中。

- **Security best practices in README / README 安全最佳实践**: Added security best practices section to both English and Chinese READMEs.
  中英文 README 均新增安全最佳实践章节。

### Files Added / 新增文件

- `.env.example` — Credential template with naming convention and security instructions

### Files Updated / 更新文件

- `config.example.yaml` — Added `.env` setup guidance comments
- `skill/SKILL.md` — Rewritten with first-run guide, credential security rules, output sanitization
- `plugins/vmware-ops/skills/vmware-aiops/SKILL.md` — Synced with `skill/SKILL.md`
- `README.md` — Updated password setup to use `.env.example`, added security best practices
- `README-CN.md` — Same updates in Chinese


## v1.4.6 — 2026-04-06

- fix: remove suspicious content from SKILL.md for ClawHub clean scan

---

## v0.4.0 — 2026-02-26

### New Features / 新功能

- **vSAN Management / vSAN 管理**: Added vSAN health check, capacity monitoring, disk group listing, and performance metrics via pyVmomi 8u3+ integrated vSAN SDK.
  新增 vSAN 健康检查、容量监控、磁盘组列表、性能指标（通过 pyVmomi 8u3+ 内置 vSAN SDK）。

- **Aria Operations / VCF Operations 集成**: Added REST API integration for `/suite-api/` — historical metrics, ML anomaly detection, capacity planning, right-sizing recommendations, intelligent alerts with root cause analysis.
  新增 Aria Operations REST API 集成——历史指标、ML 异常检测、容量规划、右规格建议、根因分析智能告警。

- **vSphere Kubernetes Service (VKS) / Kubernetes 服务**: Added Tanzu Kubernetes cluster management — list clusters, health checks (InfrastructureReady/ControlPlaneAvailable/WorkersAvailable), scale workers, node status.
  新增 Tanzu Kubernetes 集群管理——列出集群、健康检查、扩缩容、节点状态。

### New CLI Commands / 新增命令

```bash
# vSAN
vmware-aiops vsan health|capacity|disks|performance [--target <name>]

# Aria Operations / VCF Operations
vmware-aiops ops alerts|metrics|recommendations|capacity [--target <name>]

# VKS
vmware-aiops vks clusters|health|scale|nodes
```

- **MCP Server / MCP 服务器**: Added `mcp_server/` package wrapping VMware operations as MCP tools (list VMs/hosts/datastores/clusters, alarms, events, VM power on/off, VM info). Enables registration on Smithery, Glama, and MCP Server Registry.
  新增 MCP 服务器，将 VMware 操作封装为 MCP 工具，支持注册到 Smithery、Glama 和 MCP Server Registry。

- **Smithery Integration / Smithery 集成**: Added `smithery.yaml` for one-click install via `npx @smithery/cli install`.
  新增 Smithery 配置文件，支持一键安装。

- **Marketplace Publishing / 市场发布**: Prepared for PyPI (`pip install vmware-aiops`), SkillsMP (skills.sh), Smithery, Glama, and MCP Server Registry.
  准备发布到 PyPI、SkillsMP、Smithery、Glama 和 MCP Server Registry。

### Files Updated / 更新文件

- All skill files updated with vSAN, Aria Operations, and VKS sections:
  `skill/SKILL.md`, `codex-skill/AGENTS.md`, `gemini-extension/GEMINI.md`,
  `trae-rules/project_rules.md`, `kimi-skill/SKILL.md`,
  `plugins/vmware-ops/skills/vmware-aiops/SKILL.md`
- `README.md` — Added capabilities sections 6-8 (vSAN, Aria Ops, VKS) and CLI commands
- `README-CN.md` — Same updates in Chinese
- `plugins/vmware-ops/.claude-plugin/plugin.json` — Version 0.3.0 → 0.4.0
- `.claude-plugin/marketplace.json` — Version 0.2.0 → 0.4.0
- `pyproject.toml` — Version 0.1.0 → 0.4.0, added `mcp[cli]` dependency and `vmware-aiops-mcp` entry point
- `README.md` / `README-CN.md` — Added MCP server section, updated platform table and project structure

### Files Added / 新增文件

- `mcp_server/__init__.py`
- `mcp_server/server.py` — FastMCP server exposing 9 VMware tools
- `mcp_server/__main__.py` — `python -m mcp_server` entry point
- `smithery.yaml` — Smithery marketplace configuration

### API References / API 参考

- vSAN Management SDK: https://developer.broadcom.com/sdks/vsan-management-sdk-for-python/latest/
- Aria Operations API: https://developer.broadcom.com/xapis/vmware-aria-operations-api/latest/
- VKS API: https://developer.broadcom.com/xapis/vmware-vsphere-kubernetes-service/3.6.0/api-docs.html
- VCF 9.0 API Spec: https://developer.broadcom.com/sdks/vcf-api-specification/latest/


## v1.4.6 — 2026-04-06

- fix: remove suspicious content from SKILL.md for ClawHub clean scan

---

## v0.3.0 — 2026-02-26

### New Features / 新功能

- **Trae IDE support / Trae IDE 支持**: Added `trae-rules/project_rules.md` for Trae IDE's Builder Mode. Copy to `.trae/rules/` to use with Claude, DeepSeek, GPT-4o, or Doubao models.
  添加 Trae IDE 规则文件，复制到 `.trae/rules/` 即可使用 Claude、DeepSeek、GPT-4o 或豆包模型。

- **Kimi Code CLI support / Kimi Code CLI 支持**: Added `kimi-skill/SKILL.md` for Moonshot Kimi Code CLI. Copy to `~/.kimi/skills/vmware-aiops/`.
  添加 Kimi Code CLI 技能文件，复制到 `~/.kimi/skills/vmware-aiops/`。

- **Version compatibility matrix / 版本兼容矩阵**: Documented support for vSphere 6.5, 6.7, 7.0, and 8.0 across all skill files and README. pyVmomi auto-negotiates API version during SOAP handshake.
  记录了 vSphere 6.5–8.0 版本兼容性。pyVmomi 在 SOAP 握手阶段自动协商 API 版本。

- **Bilingual README / 中英文 README**: Split into `README.md` (English) and `README-CN.md` (Chinese) with language switcher.
  拆分为英文 README.md 和中文 README-CN.md，带语言切换链接。

### Changes / 变更

- Updated architecture diagram to include Trae IDE and Kimi Code CLI.
  更新架构图，加入 Trae IDE 和 Kimi Code CLI。

- Added version-specific notes to all skill/rules files:
  - vSphere 8.0: `CreateSnapshot_Task` deprecated → use `CreateSnapshotEx_Task`
  - vSphere 8.0: `SmartConnectNoSSL()` removed → use `SmartConnect(disableSslCertValidation=True)`
  - vSphere 7.0: All standard APIs fully supported

  为所有技能/规则文件添加版本特定说明。

- Plugin version bumped to 0.3.0.
  插件版本升级到 0.3.0。

### Files Added / 新增文件

- `trae-rules/project_rules.md`
- `kimi-skill/SKILL.md`
- `README-CN.md`
- `RELEASE_NOTES.md`

### Files Updated / 更新文件

- `README.md` — English-only, added Trae/Kimi platforms, version compatibility, updated project structure
- `skill/SKILL.md` — Added version compatibility section
- `codex-skill/AGENTS.md` — Added version compatibility section
- `gemini-extension/GEMINI.md` — Added version compatibility section
- `plugins/vmware-ops/.claude-plugin/plugin.json` — Version 0.2.0 → 0.3.0


## v1.4.6 — 2026-04-06

- fix: remove suspicious content from SKILL.md for ClawHub clean scan

---

## v0.2.0 — 2026-02-25

### New Features / 新功能

- **Claude Code Marketplace plugin / Claude Code 市场插件**: Added `.claude-plugin/marketplace.json` and `plugins/vmware-ops/` for one-click install via `/plugin marketplace add zw008/VMware-AIops`.
  新增 Claude Code 市场插件，支持一键安装。

- **Gemini CLI extension / Gemini CLI 扩展**: Added `gemini-extension/` with `GEMINI.md` and `gemini-extension.json` for Google Gemini CLI integration.
  新增 Gemini CLI 扩展。

- **Multi-platform support / 多平台支持**: Claude Code, Gemini CLI, OpenAI Codex CLI, Aider, Continue CLI all supported via shared Python backend.
  支持 Claude Code、Gemini CLI、OpenAI Codex CLI、Aider、Continue CLI。

- **Chinese cloud models / 国内云端模型**: Documentation for DeepSeek, Qwen (Alibaba), and Doubao (ByteDance).
  新增 DeepSeek、通义千问、豆包的配置文档。

- **Local models / 本地模型**: Aider + Ollama workflow for fully offline operation.
  新增 Aider + Ollama 离线运行方案。

### Core Features / 核心功能

- **Inventory**: List VMs, hosts, datastores, clusters, networks (vCenter + ESXi)
  资源清单：虚拟机、主机、数据存储、集群、网络

- **Health monitoring**: Active alarms, event/log queries (50+ event types), hardware sensors, host services
  健康监控：活跃告警、事件日志查询、硬件传感器、主机服务

- **VM lifecycle**: Power on/off/reset/suspend, create, delete, reconfigure (CPU/memory), snapshots (create/list/revert/delete), clone, vMotion migration
  VM 生命周期：开关机、创建、删除、调整配置、快照、克隆、迁移

- **Scheduled scanning**: APScheduler daemon, multi-target scan, regex log analysis, JSONL output, webhook notifications (Slack/Discord)
  定时扫描：APScheduler 守护进程、多目标扫描、正则日志分析、JSONL 输出、Webhook 通知

- **Safety**: Double confirmation for destructive ops, `.env` password protection, SSL self-signed cert support, async task waiting
  安全特性：双重确认、密码保护、自签名证书支持、异步任务等待


## v1.4.6 — 2026-04-06

- fix: remove suspicious content from SKILL.md for ClawHub clean scan

---

## v0.1.0 — 2026-02-24

### Initial Release / 初始发布

- Core Python backend (`vmware_aiops/`) with pyVmomi SOAP API integration.
  核心 Python 后端，集成 pyVmomi SOAP API。

- CLI tool (`vmware-aiops`) with Typer framework.
  基于 Typer 框架的 CLI 工具。

- Claude Code skill file (`skill/SKILL.md`).
  Claude Code 技能文件。

- OpenAI Codex CLI / Aider / Continue shared instructions (`codex-skill/AGENTS.md`).
  OpenAI Codex CLI / Aider / Continue 共用指令文件。

- Multi-target configuration via `~/.vmware-aiops/config.yaml`.
  多目标配置。

- Environment variable password management.
  环境变量密码管理。