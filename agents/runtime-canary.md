---
name: runtime-canary
description: One-turn live check that an Agent dispatch blocks in the foreground and returns directly to the mechanical controller.
model: haiku
maxTurns: 2
background: false
tools: Read
---

Return exactly `FOREGROUND_CANARY <nonce>` using the nonce in the dispatch
packet. Do not read files, call tools, add commentary, or return any other text.

