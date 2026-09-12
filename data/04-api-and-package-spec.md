# API 与程序包规范

> 2026-09-12 内部 APK 对齐：默认开发入口支持 `PluginEntry` APK 插件，声明式通道仍可用。新增 APK 任务字段 `launchType=apk`、`sha256`、`size`、`entryClass`、`downloadUrl`，状态为 `making/downloading/installing/ready/failed`（其中 installing/ready 由端侧管理）。APK 只在 Debug 下载和加载；当前摘要校验不是签名审核，不代表生产发布能力。详情见 [功能对齐与优化分析](08-procedure-alignment-and-optimizations.md)。

本文是 Agent 与 Demo 的**契约唯一基线**。JSON UTF-8，时间 UTC ISO-8601。请求头：`X-AutoProc-Schema: 1.0`、`X-AutoProc-App: 0.1.0`。创建任务必须带 `Idempotency-Key`。

错误信封：

```json
{ "error": { "code": "SCHEMA_INVALID", "message": "...", "details": { "jsonPointer": "/screens/0" } } }
```

业务 4xx，内部 5xx。`code` 稳定。

## 1. Meta 与任务

| 方法 | 路径 | 用途 |
|------|------|------|
| GET | `/health` | 健康检查 |
| GET | `/v1/meta/public-key` | 发布公钥 |
| GET | `/v1/meta/schemas` | appspec/proposal/mockup/program/manifest/test/report |
| GET | `/v1/meta/features` | 服务端允许的 Feature；端侧内嵌白名单为准 |
| POST | `/v1/tasks` | 创建，`202` + taskId |
| GET | `/v1/tasks/{taskId}` | 查询状态、Proposal 引用或 Artifact |
| POST | `/v1/tasks/{taskId}/inputs` | 回答澄清（仅 WAITING_INPUT） |
| POST | `/v1/tasks/{taskId}/approval` | approve / reject / revise |
| GET | `/v1/tasks/{taskId}/proposal` | 方案正文 |
| GET | `/v1/tasks/{taskId}/mockup` | 效果图结构 |
| DELETE | `/v1/tasks/{taskId}` | 取消 |
| GET | `/v1/artifacts/{artifactId}/descriptor` | 短时下载票据 |
| POST | `/v1/artifacts/{artifactId}/reports` | 可选，脱敏验证报告 |

创建请求至少含 `prompt`、`locale`、`runtimeProfile`、`baseVersionId`。可选 `autoInstall` / `autoActivate` / `autoLaunch`。`runtimeProfile.capabilities` 在 MVP 必须为 `[]`。

`status`：`QUEUED` / `RUNNING` / `WAITING_INPUT` / `WAITING_APPROVAL` / `SUCCEEDED` / `FAILED` / `CANCELED` / `EXPIRED`。Demo 不得用 `phase` 当正确性依据。

### 确认

```json
{ "action": "approve", "proposalDigest": "sha256:..." }
{ "action": "reject", "proposalDigest": "sha256:..." }
{ "action": "revise", "proposalDigest": "sha256:...", "feedback": "增加暂停按钮" }
```

- `reject` 不得带非空 feedback（有意见走 `revise`）。
- digest 过期：`409 APPROVAL_STALE`，重新拉取。
- 空转 `reject` 满 3 次：`FAILED` + `REVISION_EXHAUSTED`。

`WAITING_APPROVAL` 响应含 `proposalDigest`、`proposalSequence`、`undirectedRejectCount` 与上限。

## 2. Download Ticket

含 `artifactId`、`programId`（= 插件 id）、`versionId`、`revision`、`securityEpoch`、HTTPS `downloadUrl`、`transportSha256`、`manifestSha256`、大小、过期时间、`keyId`。过期只重签票据，对象不变。只允许配置内制品域名。

### 2.1 ADB 联调传输 Profile

正式 API 尚未落地前，开发版使用两条互相独立的本机通道，并保持与 Download
Ticket 相同的绑定字段：

- 控制面：`adb reverse tcp:17890 tcp:17890`，UTF-8 NDJSON；每帧最多 64 KiB，
  外层固定为 `{ "type": "chat|job|artifact_report", "text": "..." }`。
- 制品面：`adb reverse tcp:17891 tcp:17891`；内容寻址的只读 HTTP 下载，仅 Debug
  宿主允许访问 `127.0.0.1:17891`，Release 仍只允许 HTTPS。
- `job` 的 `text` 是 JSON。`status` 只能使用云侧规范状态；`phase` 仅用于显示。
  当 `status=SUCCEEDED` 时必须包含 `taskId`、`artifactId`、`programId`、
  `versionId`、`revision`、`downloadUrl`、`transportSha256`、
  `manifestSha256` 和 `size`。
- 手机必须先把制品流式写入 `.part`，同时执行大小与 `transportSha256` 校验，
  再进入包验证、smoke 与原子激活；不得从控制帧直接加载 Base64 制品。
- 手机用 `artifact_report` 回传 `passed` / `failed` / `corrupted` /
  `quarantined` 等脱敏结果。重连后服务端可重放最新 job；安装必须保持幂等。

此 Profile 只是 USB/ADB 联调，不代表公网云连接，也不替代正式 HTTPS Ticket、鉴权、
签名和吊销机制。

## 3. `.apkg`

不可变签名 ZIP，正斜杠相对路径，无 `..`、无符号链接、无加密 Entry。仅 Manifest 声明的文件。

```text
manifest.json
program/main.json
tests/smoke.json
resources/
signatures/manifest.cose  # COSE Sign1，payload = JCS(manifest)
```

`signatures/manifest.cose` 是唯一的签名路径；`cosignature/` 不是有效包布局。开发联调时，云侧/电脑 Agent 可以把已生成的 `.apkg` 镜像写到仓库的
`phone_agent/data/plugins/`，方便 ADB/TCP 传输和人工检查；该目录**不是**
Android 的执行目录。真机仍须把收到的字节验证、安装到 app-private 的
`files/plugins/` 后，才可由固定 Runtime 加载。

Manifest 必含：格式版本、task/program/version/revision/`securityEpoch`、`approvedProposalDigest`、目标 Runtime、排序文件表（路径/MIME/大小/SHA-256）、`keyId`、`algorithm=ES256`。未知字段拒绝。

### DSL 白名单

组件：Text、Image（包内）、Button、TextInput、Checkbox、Row、Column、List、Spacer、Dialog。  
动作：setState、validateInput、navigate、showMessage、finish 及有界列表改写。  
禁止：循环、递归、动态类名、任意 URI、HTML/脚本、网络、文件、Intent、Android 权限。未知组件/动作直接失败。

`tests/smoke.json`：确定性事件与断言；每条核心验收至少一条断言。

## 4. 验证报告（可选）

`result`：`rejected` / `failed` / `passed` / `incompatible` / `corrupted` / `quarantined`。含分层结果与脱敏诊断。禁止 Token、密码、完整用户输入、签名材料。

## 5. 错误码

`INPUT_AMBIGUOUS`、`POLICY_DENIED`、`UNSUPPORTED_CAPABILITY`、`APPROVAL_REQUIRED`、`APPROVAL_STALE`、`REVISION_EXHAUSTED`、`REPAIR_EXHAUSTED`、`SCHEMA_INVALID`、`SEMANTIC_INVALID`、`TEST_FAILED`、`TASK_STATE_CONFLICT`、`ZIP_UNSAFE`、`SIG_INVALID`、`HASH_MISMATCH`、`INCOMPATIBLE`、`ROLLBACK_BLOCKED`、`APPROVAL_MISMATCH`、`ASSERT_FAILED`、`BUDGET_EXCEEDED`、`MODEL_TIMEOUT`、`SIGNING_FAILED`、`INTERNAL_ERROR`。

越界时 `details` 含 `boundary`、`alternatives`。

实现阶段 Schema 单一来源为 `agent/schemas/`，构建时同步到 Demo。


## 内部 ADB 控制通道补充：服务在线探测（2026-09-12）

当前 phone_agent 0.1.4 使用有界 NDJSON 控制通道，新增 `ping` / `pong` 类型，
外层仍为字符串字段 `type` 与 `text`。手机使用随机探针作为 `text`，服务端仅对非空且
不超过 128 字符的探针立即原样应答，不入 LLM/编译任务队列。首次匹配应答才允许进入
已连接状态，5 秒握手超时；后续探测间隔 5 秒，有效应答超时 15 秒。
该扩展只适用于内部 ADB 开发通道，不替代正式 API 的身份验证、授权或发布确认。
