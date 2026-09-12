# Mobile App Workshop

[中文](README.md) | **English**

Describe what you want in natural language, using text or voice. An Agent on your computer checks feasibility and proposes a design. After you confirm, it generates a personal program and delivers it to the phone's host app as a plugin.

Build your own phone app: a different program made for each person, tailored to their needs.

`data/` contains the design and contract baseline; `code/` contains the current integration implementation. This README connects both to the same product design.

---

## Main Features

| Capability | Description |
|------------|-------------|
| Describe your request | Use text or offline speech recognition in Chat; the phone does not connect directly to an LLM |
| Feasibility and confirmation | The Product Agent clarifies the request, aligns the proposal, and describes the resulting experience; generation requires confirmation |
| Generate a program | After confirmation, the Program Agent generates a plugin: an internal APK by default, or a declarative `.apkg` |
| Program Square | Downloaded, verified, and installed programs appear in the list; they load when the user opens them, without automatic launch |
| Continue previous work | Resume or modify existing tasks using their original source; similar titles never justify overwriting an old program |
| Work offline | Installed, entirely local plugins can run offline; generating new programs requires a connection |

The current development default is **USB + `adb reverse` + APK plugins running inside the host process**. The target architecture still has the phone accessing only the cloud Agent's HTTP API, with declarative `.apkg` plugins as the default. USB debugging is not a production delivery channel.

In scope: local to-do lists, check-ins, lists, simple calculations, Q&A records, and local mini-games.  
Out of scope: camera access, payments, arbitrary networking, location, contacts, and general-purpose Android project generation.

---

## Architecture Overview

```text
User (text / voice)
        |
        v
+-------------------- Phone Host App --------------------+
| Chat                         Program Square            |
| ASR to text     -> Control messages: chat/job/report    |
| Download/verify -> Private files/plugins/              |
| Tap to load     -> Runtime or DexClassLoader           |
+-----------+-------------------------------^-----------+
            | Phone 127.0.0.1:17890/17891     |
            |          adb reverse          |
            v                               |
+--------------------- PC Agent -------------------------+
| ConnectPhone :17890          DownloadServer :17891     |
|        |                                              |
|        v                                              |
| Orchestrator                                          |
|   +-- Product Agent: clarify / assess / confirm / save |
|   +-- Program Agent: Codex code -> Gradle APK build    |
|   +-- LLM / Codex CLI (computer only)                  |
+-------------------------------------------------------+
```

The logical layers follow `data/01-overall-architecture.md`: the phone connects only to the Agent, never to models; the cloud side (currently a local process) orchestrates generation; the phone independently verifies packages before loading them.

```text
UserRequest + Runtime profile
  -> Clarification / feasibility
  -> Proposal (the experience after opening the program)
  -> User confirmation
  -> Artifact (plugin.apk or .apkg)
  -> Client verification -> Installation -> Activation -> User launches
```

Repository layout:

| Directory | Role |
|-----------|------|
| `data/` | Architecture, contracts, security, and roadmap |
| `code/agent/` | PC Agent |
| `code/phone_agent/` | Phone host app |
| `code/plugin_sdk/` | APK plugin entry contract |
| `code/startServer/` | Integration startup: `adb reverse` + Agent |

---

## 1. Main Interaction Flow

The user stays on their selected tab. Generation does not force a switch to Program Square.

```text
Open app -> Automatically connect to computer -> Chat
  |
  +-- Text / voice --chat--> Product Agent
  |                           +-- Ask for clarification
  |                           +-- Unsupported: explain limits and
  |                           |   alternatives; produce no package
  |                           +-- Feasible: proposal and experience
  |                                      |
  |                     Approve / revise / reject / cancel
  |                                      |
  |                          Build only after approval
  |                                      v
  |                          Program Agent generates artifact
  |                                      |
  |                          job: making -> downloading
  |                                      v
  +-- Stream over HTTP 17891 -> Verify -> installing -> ready
                                                        |
                                                        v
                              Card in Program Square; user opens it
```

Key constraints:

1. **No delivery without confirmation.** “OK, make the buttons bigger” is revision feedback, not permission to start building.
2. **Control messages do not carry whole packages.** A `job` contains only an artifact descriptor (size, SHA-256, download URL); artifacts use port 17891.
3. **Server verification does not replace phone verification.** The phone independently verifies and atomically installs packages; a failure preserves the current working version.
4. **Published packages are immutable.** Changes require a continuation linked to the original task, or a new task.

User actions follow `data/01`: `approve`, `revise` (feedback required), `reject` (another proposal without feedback, capped at 3 by default), and `cancel`.

The current APK integration state machine is `making → downloading → installing → ready | failed`. The phone manages the final two states.

---

## 2. PC Agent Design

Entry point: `code/agent/main.py`. The default is `--runtime apk`; declarative compatibility is available through `--runtime declarative`.

Normal startup:

```bash
cd code/startServer
./start-adb-server.sh
```

Startup checks Codex login before listening on ports. While running, the Agent monitors `adb reverse` and restores ports 17890 / 17891 after USB reconnection.

### 2.1 In-Process Modules

```text
ConnectPhone (17890 NDJSON)
        |  chat / runtime_error / artifact_report
        v
Orchestrator -----------------------------> ApkDelivery
        |                                        |
        +-- ProductAgent                         | Push job to phone
        |    Intent, clarification, feasibility, |
        |    confirmation, product archive:      |
        |    out/tasks/<id>/product/             |
        |                                        |
        +-- ProgramAgent                         v
             Codex CLI writes PluginMain   DownloadServer (17891)
             Gradle assembleDebug          Read-only artifact HTTP
             Verify / register download
```

| Module | Path | Responsibility |
|--------|------|----------------|
| Phone bridge | `connect_phone/` | Transport only; no model calls |
| Orchestration | `orchestrator.py` | Create tasks only after confirmation; bounded runtime-error repair (at most 2 attempts within the same process) |
| Product Agent | `apk_product_manager/` | Understand requests, align proposals, and write archives; no code generation |
| Feasibility | `apk_product_manager/feasibility.py` | Requirements ∩ Runtime profile ∩ delivery policy; review the model's initial assessment |
| Program Agent | `program_agent/` | Generate, continue, repair, and compile plugin projects based on the archive |
| Delivery | `apk_delivery.py` + `download_server.py` | Register artifacts, push `job` messages, and replay after reconnection |
| LLM | `llm/` | OpenAI-compatible endpoint or local model; credentials come from the environment, never the repository |

Declarative mode uses a separate path: `product_manager` + `MainAgent`, producing `.apkg` files. Its state files are separate from the APK channel to avoid treating APKs as declarative packages.

### 2.2 Product Agent

User-facing replies describe the experience, without host, plugin, or Gradle implementation details. Structured states include:

- `NEED_CLARIFY`: clarify the user's goal.
- `WAITING_APPROVAL`: describe the resulting experience and wait for confirmation.
- `UNSUPPORTED`: a preliminary out-of-scope assessment, reviewed after alignment with the programming side rather than treated as a final decision.

Routing uses `new` / `continue` / `modify` / `clarify`. Continuations and modifications must reference a real `taskId` in the history directories; titles must not be used to guess IDs.

Archives live in `code/agent/out/tasks/<taskId>/product/`: `design.md`, `implementation.md`, `runtime.md`, `intent.json`, and alignment logs. Confirmed snapshots are immutable.

### 2.3 Program Agent

Work starts only after confirmation:

1. Copy `plugin_template/`.
2. Use Codex CLI (`codex exec`) to write only `{package}.PluginMain`, implementing `PluginEntry`.
3. Build `plugin.apk` with phone_agent's Gradle setup.
4. Register the artifact on port 17891 and notify the phone to download it.

Supervision includes CLI heartbeats and failure classification (transient retry, incomplete design, or user clarification). Code-generation and compilation failures each allow at most 3 attempts. Authentication, quota, and process errors require manual intervention instead of retry loops. Work takes place in the task's `dev/codex_work`; failed drafts are saved in `dev/source-draft`, with backups before overwriting.

When the phone reports `runtime_error`, repairs modify **the current task's source**, without rewriting the entire program or substituting a counter for a game that could not be implemented.

---

## 3. Phone App Design

Project: `code/phone_agent/`. The home screen has two tabs: **Chat** and **Program Square**.

The installed host, built-in validation, and Runtime are trusted. User input, model output, and downloaded bytes are untrusted. The phone does not invoke an LLM locally.

### 3.1 Speech Recognition

Offline, push-to-talk, non-streaming ASR, without VAD.

| Item | Implementation |
|------|----------------|
| Engine | Sherpa-ONNX `OfflineRecognizer` |
| Model | `assets/sherpa-onnx-paraformer-zh-2023-09-14/` (`asrModelType = 0`) |
| Capture | 16 kHz mono PCM; tap to start buffering, tap to stop and transcribe the entire recording |
| Entry point | `PushToTalkAsr.kt`; microphone permission is requested only on the first voice action |
| Interaction | A waveform appears while recording; recognized text is appended to the input field and can be edited before sending |
| Constraints | arm64 physical devices only; recording stops on tab switches, backgrounding, or leaving the page |

Speech recognition only converts audio to text. The outgoing message is an ordinary `chat`, using the same path as typed input. Text chat does not depend on ASR initialization completing.

### 3.2 Phone–Agent Communication

The phone connects only to the Agent, never directly to Ollama or cloud model APIs.

**Current development channel (USB)**

```text
App --TCP--> Phone 127.0.0.1:17890 / 17891
                       |
                  adb reverse
                       v
             PC Agent listens on the same ports
```

- Neither a shared Wi-Fi network nor the computer's LAN IP is required.
- Control channel, port 17890: one UTF-8 JSON object per line, `{"type":"...","text":"..."}`, with a maximum frame size of 64 KiB.
- Artifact channel, port 17891: read-only HTTP; enforce size limits and calculate SHA-256 while downloading. Write to `.part`, then install only after verification.
- Application heartbeat: exchange `ping`/`pong` after connecting, then probe every 5 seconds. A timeout disconnects and triggers reconnection. A TCP connection alone does not mean “Connected.”
- After reconnection, the server replays recent tasks. Retries stop only when the user manually selects “Disconnect.”

Message types: `chat` (conversation), `job` (task/artifact descriptor), `artifact_report` (download, verification, and installation result), `runtime_error` (plugin crash report), and `ping`/`pong` (internal messages, excluded from chat and model calls).

**Target production channel**

The phone uses an HTTPS Task API and short-lived Download Tickets. Release builds prohibit loopback downloads; see `data/04-api-and-package-spec.md`. The USB profile is for integration testing only.

### 3.3 Plugin Loading Options

The host must run programs generated independently on the computer and downloaded to its private directory, minimizing host reinstalls. `data/07-host-process-plugin-architecture.md` compares these approaches.

| Approach | Mechanism | Role in this project |
|----------|-----------|----------------------|
| **A. Declarative `.apkg` + fixed Runtime** | Packages contain only JSON, assets, tests, and signatures; the host interprets them without running model-generated code | Default security approach. `--runtime declarative`. `PluginStore` + built-in component registry. Intended for store releases |
| **B. In-process APK (PluginEntry)** | Gradle builds `plugin.apk` independently, without the system installer; a user tap loads it through `DexClassLoader`, calling `onCreate` inside the host Activity | **Current Debug default.** `PluginLoader` → `PluginContainerActivity`. Supports Canvas mini-games |
| **C. Legacy createView reflection entry** | `public View createView(Context)` | Compatibility for older APKs via `ApkPluginActivity`. Newly generated plugins use B |
| D. H5 ZIP + WebView | Deliver HTML/JS | Evaluated: suitable for untrusted forms, but does not deliver native APKs; not the primary channel |
| E. Play Dynamic Feature | Play delivers splits as part of an App Bundle | Evaluated: requires store delivery and cannot serve packages generated on demand by the Agent |
| F. Independently installed APK | PackageInstaller, separate process | Not adopted: this would no longer be an in-host plugin |
| G. Shadow / RePlugin and similar | System hooks and proxy Activities | Evaluated: too heavy, with compatibility concerns; excluded from the MVP |

Current default loading path (B):

```text
Download from 17891 -> Size / SHA-256 verification
  -> Save read-only in a private directory (classes.dex required; .so forbidden)
  -> Program Square card becomes ready
  -> User taps the card
  -> PluginLoader (Debug only)
  -> DexClassLoader(apk, codeCacheDir, null, hostClassLoader)
  -> PluginEntry.onCreate(host, container)
```

The contract lives in `code/plugin_sdk/`: plugins depend only on the Android SDK, not private host classes; they request no new permissions, include no native libraries, and should not access the network themselves. Failures are captured at `load`, `onCreate`, or `runtime` and sent to the computer, where the source is repaired and rebuilt using the stack trace. Repairs are not executed on the phone.

Installation, activation, and session states are separate: downloaded ≠ activated ≠ running. Defaults are `autoInstall=true`, `autoActivate=true`, and `autoLaunch=false`.

APK digest verification is not production signature approval; packages from forged sources must not be treated as published capabilities. Google Play releases should disable B and retain only A (or official Dynamic Feature delivery).

---

## Documentation and Code Index

| Document | Contents |
|----------|----------|
| [data/README.en.md](data/README.en.md) | Design baseline, terminology, and immutable principles |
| [data/01-overall-architecture.md](data/01-overall-architecture.md) | Boundaries, main flow, and state machines |
| [data/02-agent-implementation-plan.md](data/02-agent-implementation-plan.md) | Cloud / PC Agent orchestration |
| [data/03-demo-implementation-plan.md](data/03-demo-implementation-plan.md) | Phone host, preview, and offline behavior |
| [data/04-api-and-package-spec.md](data/04-api-and-package-spec.md) | API and package contracts |
| [data/05-security-and-verification.md](data/05-security-and-verification.md) | Trust boundaries and package verification |
| [data/07-host-process-plugin-architecture.md](data/07-host-process-plugin-architecture.md) | Comparison of plugin loading approaches |
| [data/08-procedure-alignment-and-optimizations.md](data/08-procedure-alignment-and-optimizations.md) | Implementation alignment and startup |
| [data/11-phone-agent-chat-ui.md](data/11-phone-agent-chat-ui.md) | Chat interaction design |
| [code/startServer/README.md](code/startServer/README.md) | ADB connection, heartbeat, and troubleshooting |
| [code/agent/program_agent/FRAMEWORK.md](code/agent/program_agent/FRAMEWORK.md) | APK plugin framework conventions |

Most detailed documents linked above are currently in Chinese.

For contract changes, update `data/04` first; for security changes, update `data/05` first. Then synchronize the remaining documentation and `code/`.
