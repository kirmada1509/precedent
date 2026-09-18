---
title: Precedent
emoji: 🛡️
colorFrom: indigo
colorTo: blue
sdk: docker
app_port: 7860
pinned: false
short_description: Check every AI agent action against past decisions in ms (Moss)
---

# Precedent

Check **every** action an AI agent proposes against a live [Moss](https://moss.dev) index of past
approved and blocked actions, in milliseconds, in-process. Human decisions are written back into
the index and apply on the very next call.

Open the app, press **Run support shift**, click the blocked refund, then the escalated
`rotate_api_key`, confirm the block, and re-run it.

Source, PRD, architecture and honest limits: see the project repository README.
Synthetic data and a fictional support desk.
