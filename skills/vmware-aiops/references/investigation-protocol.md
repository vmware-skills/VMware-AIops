# Investigation Protocol — Causal Chain Root Cause Analysis

A protocol for AI agents performing diagnostic investigations on VMware infrastructure (alarms, performance regressions, availability incidents). Adopted from Enterprise Harness Engineering, drawing on 5 Whys, Google SRE, ITIL, and NASA Fault Tree Analysis.

## When to Apply

Use this protocol whenever the user asks:

- "Why is X slow / failing / down?"
- "What caused this alarm / alert / incident?"
- "Investigate / diagnose / debug …"
- Any open-ended question that requires identifying a root cause rather than just reading state.

Do NOT apply for:

- Simple state lookups ("is the VM on?", "list datastores")
- Operational requests ("clone this VM", "create this rule")
- Configuration questions ("what's the default for X?")

## The Four Criteria for Root Cause Completeness

A diagnostic conclusion is **incomplete** unless ALL four criteria are satisfied. The agent must self-check against each one before outputting a report.

### 1. Falsifiability (可证伪性)

The root cause must be independently measurable and verifiable. If you cannot test it, it is a hypothesis, not a root cause.

- ✅ "Datastore latency exceeded 50ms because IOPS hit the SAN cap of 10,000" — directly testable via `get_metrics datastore.iops`
- ❌ "Network was congested" — too vague to verify

### 2. Sufficiency (充分性)

Removing the root cause must make the symptom disappear. If the symptom persists after the supposed fix, the cause was wrong or partial.

- ✅ "Deleting the orphaned snapshot freed 200 GB and the alarm cleared within 60 seconds"
- ❌ "Restarted the VM and the issue went away" — correlation, not causation

### 3. Necessity (必要性)

The symptom must occur whenever the root cause is present. If the same condition exists elsewhere without the symptom, you have not found the true root cause.

- ✅ "Every cluster with 80%+ memory overcommit shows the same vMotion stall"
- ❌ "Only this one VM has the issue" — without explaining why this VM specifically

### 4. Mechanism (机制性)

You must explain the propagation chain: root cause → propagation → amplification → impact. A single point claim with no mechanism is a guess.

- ✅ "Snapshot delta files filled the datastore (root) → VM I/O blocked on write (propagation) → guest filesystem went read-only (amplification) → application timeout (impact)"
- ❌ "The datastore was full" — describes a state, not a chain

## Investigation Workflow — Up to Three Depth Rounds

### Round 1 — Initial Hypothesis

1. Gather symptoms via L1/L2 read tools (alarms, metrics, events, logs)
2. Form an initial causal chain hypothesis
3. Apply the four criteria

If all four pass → output report.
If any criterion fails → proceed to Round 2 with that criterion as the focus.

### Round 2 — Targeted Deepening

1. Identify which criterion failed
2. Gather additional evidence aimed specifically at that criterion (e.g. failed Necessity → compare against unaffected peers; failed Mechanism → trace next propagation step)
3. Refine the causal chain
4. Re-apply the four criteria

If all four pass → output report.
If any still fails → proceed to Round 3.

### Round 3 — Final Deepen or Escalate

1. If a deeper cause is reachable, gather final evidence and finalize the chain
2. If evidence is unavailable, system-bounded, or beyond the agent's tool surface, **escalate to a human** and explicitly label the conclusion as `⚠️ INCOMPLETE — <criterion> unsatisfied`
3. **Never** silently output a partial conclusion as if it were complete

## Output Format

Every investigation report must structure findings exactly as:

```
🔴 [ROOT CAUSE]   <falsifiable, mechanism-explained statement>
  → [PROPAGATION] <how the root cause spread to neighboring systems>
    → [AMPLIFICATION] <what made the impact worse, if applicable>
      → [IMPACT]    <observable user / business / SLA effect>

✅ Falsifiability:  <evidence — metric name, log query, command output>
✅ Sufficiency:     <evidence or stated counterfactual>
✅ Necessity:       <evidence or peer comparison>
✅ Mechanism:       <see propagation chain above>
```

If any criterion is unmet, mark it `⚠️ INCOMPLETE — <reason>` and state explicitly what additional evidence would be required to satisfy it.

## Anti-Patterns

| ❌ Pattern | Why it fails |
|---|---|
| "thanos-cn unreachable" alone | Describes symptom; does not answer **why** unreachable |
| "Datastore full" alone | No propagation, no impact chain |
| "Try restarting it" | Skips diagnosis entirely |
| "Probably the network" | Not falsifiable |
| Stopping at the first plausible cause | Skips Necessity check |
| Silent partial conclusion | Hides incompleteness from the user |

## Worked Examples

### Bad — Incomplete Diagnosis

> "VM is slow because the host is busy."

Missing:
- **Falsifiability**: which metric, what threshold?
- **Necessity**: why this VM only?
- **Mechanism**: how does host load translate into VM slowness?

### Good — Complete Diagnosis

> 🔴 [ROOT] Host `esx-03` CPU ready time exceeds 15% (validated via `get_metrics host.cpu.ready`)
>   → [PROPAGATION] vCPU contention from 4-VM reservation collision in resource pool `prod-rp`
>     → [AMPLIFICATION] DRS is in manual mode, so VMs are not rebalanced
>       → [IMPACT] Application p99 latency doubled from 200 ms to 400 ms
>
> ✅ Falsifiability: `host.cpu.ready` metric directly observable; threshold defined in vSphere docs
> ✅ Sufficiency: vMotion `vm-A` off `esx-03` reduced ready time to 3% and p99 latency back to 200 ms
> ✅ Necessity: only VMs in `prod-rp` with active reservations are affected; identical workloads in `staging-rp` are healthy
> ✅ Mechanism: cpu.ready = vCPU waiting for pCPU → guest perceives as CPU starvation → app threadpool exhaustion → tail latency

## Related Skills

A complete investigation often chains across skills:

- **vmware-aiops** (this skill): VM/host state, deployment history; can also remediate at L3+ once the investigation is complete and approved
- [vmware-aria](https://github.com/vmware-skills/VMware-Aria): metrics, alerts, anomaly detection — primary L1/L2 data source for time-series analysis
- [vmware-monitor](https://github.com/vmware-skills/VMware-Monitor): inventory, alarms, events — code-level read-only data source
- [vmware-pilot](https://github.com/vmware-skills/VMware-Pilot): orchestrate the investigation itself as a multi-step Dispatcher → Subagent workflow

The agent should treat investigation as **read-heavy first**: gather across skills, reason centrally, only invoke L3+ write tools after the four criteria are satisfied AND the user has approved a remediation plan.
