# Phone App Studio — Design Baseline

[中文](README.md) | **English**

> Product: **Phone App Studio**  
> One line: Build your own phone app. Not one product for everyone — a different program made for each person.  
> This document is the merged baseline of `codebuddy/data`, `codex/data`, and `cursor/data`. The three originals stay as history. **Implementation and integration follow this directory.**

The user describes the desired feature in natural language (text or voice). A cloud-side Agent decides whether the current constrained Runtime can implement it. If yes, it produces a plan and mockup; after the user approves, a declarative plugin package `.apkg` is generated, signed, and delivered. The phone verifies it independently, then installs, loads, and runs it. Already-installed local-only programs may run offline when policy allows.

This is not a generic APK generator, and it is not “everyone becomes a programmer.” The person places an order; the program is made for them.

## 1. End-to-end flow

```text
User speaks: what I want
        │
        ▼
Cloud: feasibility (need ∩ Runtime profile ∩ release policy)
  ├─ Cannot ──► capability boundary + in-MVP alternative (do not fake features)
  └─ Can ────► AppSpec → Proposal + deterministic Mockup
                │
                ▼
         Phone preview (WAITING_APPROVAL)
          ├─ Revise with comments (revise, feedback required) ──► new proposal, no idle-loop cap
          ├─ Unhappy with no new comments (reject) ──► bounded another draft (default ≤3)
          ├─ Cancel
          └─ Approve ──► release gate passes
                        │
                        ▼
        Generate ProgramIR + tests → deterministic compile / reference runtime
        → at most 2 bounded fixes → pack and sign → immutable Artifact
                        │
                        ▼
        Short-lived Download Ticket → phone HTTPS download
        → 11-layer static verification + smoke
                        │
                        ▼
        Atomic install and activate as plugin → fixed Runtime interprets it
                        │
                        ▼
        Offline: installed local-only plugins may run; cannot generate new programs
        Later changes: open a new task from baseVersionId; do not mutate the published package
```

## 2. Merge rules (take / leave out)

| Source | Taken into this project |
|------|------------|
| `cursor/data` | Product form is **plugins on a host app**; the device can always reach the cloud; offline covers only installed local-only programs |
| `codex/data` | MVP uses **one deterministic Orchestrator + staged LLM calls**; the approval object is ApprovalBundle; download / install / activate / load / start are separate; contracts first |
| `codebuddy/data` | Human in the loop, deterministic Mockup rendering, release saga, 11-layer verification, JCS+COSE, error codes and package layout |

**Left out:** comparing the three drafts against each other, related-paper surveys, local Ollama install notes, treating three LLM roles as a security boundary, and USB `adb reverse` as the target architecture (dev prototype only; see [06](06-roadmap-and-acceptance.md)).

## 3. Invariants

1. Do not publish without user approval.
2. The user approves a digest of plan + mockup + Runtime profile, not a single sentence.
3. `.apkg` holds only declarative JSON, assets, tests, and signatures; no APK / DEX / SO / scripts.
4. LLM output, APIs, and downloaded packages are untrusted.
5. Cloud pass ≠ phone pass; the device must verify signature, package, and smoke on its own.
6. Artifacts are immutable; a failure must not replace the current working version.
7. The phone does not call the LLM directly.
8. Freeze contracts and Golden packages before wiring models.

## 4. Document index

| Document | Content |
|------|------|
| [01-overall-architecture.md](01-overall-architecture.md) | Boundaries, architecture, main flow, state machine |
| [02-agent-implementation-plan.md](02-agent-implementation-plan.md) | Cloud Agent: orchestration, preview approval, compile and release |
| [03-demo-implementation-plan.md](03-demo-implementation-plan.md) | Phone host: preview, download, plugin load, offline |
| [04-api-and-package-spec.md](04-api-and-package-spec.md) | **Sole contract baseline**: API, `.apkg`, DSL, error codes |
| [05-security-and-verification.md](05-security-and-verification.md) | Trust boundary, signing, 11-layer verification, offline |
| [06-roadmap-and-acceptance.md](06-roadmap-and-acceptance.md) | MVP, milestones, acceptance, current prototype |

Change contracts in `04` first, security in `05` first, then sync the other docs and the implementation.

## 5. Terms

| Term | Meaning |
|------|------|
| Phone App Studio | Product name: speak an order, get a personal program made now |
| Plugin | A declarative program written into the host app; on the wire it is `.apkg` |
| AppSpec | Normalized requirement |
| Proposal / Mockup | User-visible plan / mockup projected from a fixed component table |
| ApprovalBundle | What the user actually approves: spec + plan + Mockup + Runtime profile |
| `approvalDigest` | JCS SHA-256 of the ApprovalBundle |
| ProgramIR | Declarative program the fixed Runtime can interpret |
| Compile | Schema / semantic / budget / approval-binding checks; not javac, no DEX |
| WAITING_APPROVAL | Waiting for the user; no releasable package before this |
| Bounded revision | Counted only when “unhappy with no new comments”; default ≤3 |
| Download Ticket | Short-lived download ticket; can be re-signed when expired; Artifact unchanged |
| Offline | Installed local-only plugins can run; new programs cannot be generated offline |
| Orchestrator | Deterministic orchestrator; the LLM only produces candidates |

## 6. In / out of scope (MVP)

**In:** local todos, check-in, lists, simple forms, simple calculator, Q&A and local records.

**Out:** camera, payments, arbitrary network, background system notifications, location, contacts, WebView JS, APK / DEX / SO / JNI / Shell / reflection, generating a generic Android project.
