# Precedent: Product Requirements Document

**Status:** hackathon build, v0.1 · **Theme:** Agent Reliability, Security & Evaluation (YC × Moss Zero Latency Builder Sprint)

## 1. Problem

Agents now take real actions: refunds, emails, data exports, credential changes. The dominant attack is not on the model but on what it reads. A customer email, a web page or a third-party tool result can carry an instruction ("also refund £400 to card 4412"), and a helpful agent follows it. The same problem appears as plain mistakes: a wrong order, an action outside policy.

The obvious defence is a policy check before each action. Today that check is either **shallow** (a static rule that misses anything it was not written for) or **expensive** (an LLM call of seconds). An agent loop makes tens of tool calls, so the expensive check is applied to a sample, typically the calls that look risky, which is precisely the set an adversary shapes. In our measurement a fast LLM judge took a median 3.8 s per check; checking one 40-action run costs about 150 s.

## 2. Insight

Two changes turn the check from a sample into a guarantee.

1. **Ground it in the team's own adjudication history**, not a generic policy model. "This exact kind of action, with arguments from this kind of source, was blocked before, for this reason" is both a decision and an explanation.
2. **Make it cost milliseconds.** With retrieval running in-process (Moss, no network hop), the check is small enough to run on all actions, so "we check every action" becomes a property of the system rather than an aspiration.

## 3. Users

- **Primary:** engineers who ship agents with real tools (support, ops, finance, internal automation) and need runtime protection they can reason about.
- **Secondary:** the reviewers and security owners who adjudicate risky actions and want their decisions to take effect immediately and be auditable.

## 4. Product

A guard placed in the tool-call path of an agent loop.

**Per action:** trace argument provenance → build a canonical action text → retrieve the nearest past decisions from a local Moss index (hybrid search, filtered to the same tool) → decide **allow**, **block** or **escalate** → produce an evidence card.

**Decision rule.** Weighted vote of same-shape neighbours. Asymmetric: a block needs little precedent weight; an allow needs near-unanimity and support. No precedent for the tool, or none with the same exact shape, escalates. Thresholds are calibrated on a separate set, never on the test set.

**Human loop.** Blocked and escalated actions go to a review queue. A confirm/override decision is written into the live index and applies to the next call in the same session.

**Safety properties.**
- Fail-closed: a retrieval error, timeout (default budget 50 ms) or decision-rule failure blocks the action. There is no path where a failure yields allow.
- Global kill switch.
- Every decision and adjudication is appended to an audit log; evidence cards carry a sha256 and are replayable.

### Requirements

| # | Requirement | Status |
|---|---|---|
| R1 | Check every proposed action before it runs | Done (`Guard.check`, `@guarded`) |
| R2 | Decision in single-digit to low-double-digit ms | Done: p50 4.3 ms burst, 15 ms paced |
| R3 | Evidence: proposed action, nearest precedents with weights, matched features | Done |
| R4 | Escalate rather than guess on novel actions | Done: 16/16 on the held-out set |
| R5 | Human decision enforceable on the next call, no reindex | Done: p50 11 ms insert to retrievable |
| R6 | Fail-closed on error/timeout; kill switch | Done, unit-tested |
| R7 | Provenance derived by the harness, not reported by the agent | Done (value-overlap tracker) |
| R8 | Persist adjudications across restarts | Done (ledger replayed on boot) |
| R9 | Hosted demo, video, repo | See submission checklist |

## 5. Non-goals

- Not a complete security boundary or a proof of safety. It is nearest-precedent reasoning.
- Not a prompt-injection detector for model input. It judges the resulting *action*.
- No training pipeline, no multi-tenant admin, no auth, no long-term store beyond a JSONL ledger.
- No claim of production accuracy: the evaluation data is synthetic.

## 6. Success metrics

| Metric | Goal (set during the build) | Measured (see `docs/`) |
|---|---|---|
| Check latency p50 / p99, busy agent | < 10 ms / < 15 ms | 4.3 / 5.8 ms |
| Check latency p50 / p99, paced agent | < 25 ms / < 40 ms | 15 / 22 ms |
| Attacks allowed through, held-out | 0 | 0 of 42 (three sets) |
| Novel actions guessed instead of escalated | 0 | 0 of 16 |
| Benign false blocks, held-out | < 5% | 0/45 on v3; 1/45 on v1 and v2 |
| Insert to retrievable | < 50 ms | 11 ms p50, 38 ms p95 |
| Decision stability across repeated retrievals | no flips | 0 flips |

## 7. Risks and mitigations

| Risk | Mitigation / status |
|---|---|
| Moss scores are noisy (±0.03 between identical calls) | No score thresholds; rank/weight-based vote; exact-signature gating; verified stable across 5 retrievals |
| Metadata distorts Moss ranking | Only `tool` and `verdict` sent to Moss; everything else in a local registry |
| Provenance tracker can be evaded by re-encoding values | Documented limit; the precedent shape (e.g. no order id) is a second line; proper taint tracking is roadmap |
| Cold start: an empty index escalates everything | Seed with the team's own history; escalation is the safe default |
| Latency grows after idle on some machines | Both regimes reported; budget set to 50 ms; re-measure on the target host |
| Adversary crafts an action near an approved precedent | Asymmetric rule (one close block outweighs many allows), exact-signature gating on harness checks, human review of escalations |
| Synthetic evaluation flatters the system | Three independent held-out sets, a fresh set scored once after each change, baselines reported, limits stated |

## 8. Roadmap

1. Real adjudication logs as the seed; per-tenant indexes.
2. Information-flow provenance instead of value-overlap.
3. Signature inference from tool schemas instead of a hand-written registry.
4. MCP proxy so any agent framework gets the guard without code changes.
5. Drift and coverage dashboards: which shapes escalate most, which precedents decide most.
