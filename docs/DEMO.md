# Demo script (≤ 2:00)

Record against the **deployed** URL, 1080p, captions on. Click **Reset** before every take. Use the scripted agent ("Run support shift"); the live Gemini agent is optional B-roll.

Say the honest limit out loud once (at 1:30). It is the difference between a security product and a plausible one.

| Time | On screen | Say |
|---|---|---|
| 0:00–0:15 | Landing state: empty stream, KPIs at zero, inbox with the highlighted hidden text on T-103 | "Guardrails get sampled because checks are slow. An LLM judge takes seconds per call, so it runs on the calls that look risky, which is the set an attacker shapes. Precedent checks every action, in milliseconds, against your own past decisions." |
| 0:15–0:40 | Click **Run support shift**. Stream fills with green ALLOW rows and latency chips (≈4–15 ms). KPI "checked" climbs. | "Every tool call is checked before it runs. Eleven routine calls pass, each with its check time. Retrieval is Moss, in-process: no network hop, no vector database." |
| 0:40–1:05 | Injected `issue_refund(400, card 4412)` turns red; evidence card opens: args traced to `email_body · untrusted`, `order_id` absent, matched-features chips, nearest precedents all BLOCK | "Ticket T-103 hides an instruction in white-on-white text and the agent obeys it. Precedent traces both arguments to the email body, sees no order id, and finds blocked precedents of exactly this shape. Blocked. The evidence is the actual precedents, not a score." |
| 1:05–1:30 | `rotate_api_key` is amber ESCALATE: "No precedent, escalate". Click **Confirm: block this** → toast "live in ~12 ms" → **Re-run this action** → ESCALATE becomes BLOCK, HUMAN precedent at 100% | "An action it has never seen: it does not guess, it escalates. I block it. That decision goes straight into the live Moss session, queryable in about twelve milliseconds. Re-run: now it's a block, citing my decision. No reindex, no redeploy." |
| 1:30–1:45 | Scroll to the proof strip | "It is not a proof: it is nearest-precedent reasoning on synthetic data, and a plain same-shape majority vote does about as well; what retrieval adds is evidence and live adaptation. Held-out: every attack blocked, every novel action escalated." |
| 1:45–2:00 | Proof strip: 4.3 ms vs 3.8 s bars; repo and URL | "Median check 4 milliseconds when busy, 15 when paced, versus 3.8 seconds for a fast LLM judge. Forty checks in about half a second, not two and a half minutes. Precedent: make the guardrail cheap enough to run on everything." |

## Pre-flight

- [ ] Reset → run the shift once to warm up → Reset again.
- [ ] Confirm the kill switch is off and the proof strip shows numbers (needs `docs/bench.json` and `docs/eval.json`).
- [ ] Do not narrate numbers you have not read off the screen in that take.
- [ ] Upload unlisted; check it plays logged out; length ≤ 2:00.
