# 云侧 Agent

Agent 把自然语言变成**可发布的声明式候选包**。它控制生成；插件 UI 只在手机上跑。手机永远只连 Agent，不连模型。

## 1. 负责 / 不负责

负责：任务与幂等；澄清与可行性；AppSpec/Proposal/Mockup；确认与修订；ProgramIR 与测试候选；确定性编译与参考运行时；有界修复；ValidationRecord、版本预留、组包、签名请求、下载描述符；审计。

不负责：产出 APK/DEX/SO/脚本；用户未确认就发布；让模型自己判安全或持有私钥；代替手机做最终安装信任；承诺 Registry 之外的功能。

## 2. 技术基线

Python、FastAPI、开发期 SQLite / 联调 PostgreSQL、对象存储、JSON Schema、OpenAI 兼容 LLM Provider、COSE Sign1 + ES256（Signer/KMS，Worker 读不到私钥）。

Provider 至少：`generateStructured(taskType, schema, messages, limits)`，记录模型/模板版本与用量摘要。开发可用本机兼容口；评测用 Fixture Provider。

## 3. 流水线

```text
策略门禁 → 澄清（可选）→ 可行性
  ├─ 否 → FAILED + UNSUPPORTED_CAPABILITY + alternatives
  └─ 是 → AppSpec → Proposal + Mockup → WAITING_APPROVAL
            ├─ revise（有意见）→ 重新预览
            ├─ reject（无意见）→ 有界再预览（≤3）
            └─ approve → ProgramIR/测试 → 编译 → 参考运行时
                         → 修复≤2 → 发布 Saga → SUCCEEDED
```

阶段输出先落盘并校验摘要，再迁状态。重试不得重复占 revision 或重复签名。没有已批准 `approvalDigest` 禁止进入打包之后。

每次产品 Agent 与用户对齐（原始需求、澄清、方案、修改、拒绝、确认、边界拒绝）都
追加一份不可变对齐快照；产品设计、实现方案、运行环境和代码位置写入同一产品归档。
程序 Agent 对每次 Codex CLI 调用记录开始、每 5 秒心跳、完成/失败、错误分类和下一步
决策。仅暂态或候选格式错误可自动重试，**总次数最多 3 次**；认证/配额/进程错误转人工，
三次仍无有效候选则转 `design_review`，要求补全设计或询问用户，禁止无限循环。

### 3.1 Planner（LLM 候选）

把需求映射为 Feature/Capability（名称必须已在 Registry）、AppSpec 与 Proposal 文案候选。外部 capabilities 在 MVP 必须为空。越界写入不可实现原因，由确定性组件组装错误响应，**不继续出 Proposal**。

Mockup **不是**模型文件：`rendererVersion + uiRegistryVersion + AppSpec` 唯一确定。渲染失败说明 AppSpec 非法。

### 3.2 用户动作

审批带乐观并发与幂等键：`If-Match` / body 中的 digest 必须等于当前值。

- `approve`：写不可变 ApprovalRecord，进入生成。
- `revise`：feedback 必填；出新 `proposalId` 与 `proposalSequence`。
- `reject`：不计 revise；`undirectedRejectCount+1`。
- `cancel`：签名一旦完成，取消不能删除 Artifact，只能停止返回/自动安装。

### 3.3 Builder 与编译

Builder 输入：已批准 AppSpec/ApprovalBundle、Schema、Registry、预算、失败时的稳定错误码与 JSON Pointer。输出单个 JSON，拒绝 Markdown 围栏。

开发期可由 Codex CLI 充当 Builder，且只允许它在隔离工作目录中根据上述输入
产出 **ProgramIR + smoke 测试候选**。Codex 的输出与其他 LLM 输出一样不可信：
不得生成 Android 工程、APK、DEX、SO、脚本或修改宿主；候选仍必须经过本节的
确定性编译、测试和签名。编译成功的不可变 `.apkg` 同时写入制品存储和开发镜像
`phone_agent/data/plugins/`；后者只是传输/检查落点，不能绕过端侧安装验证。

编译固定次序见 [04](04-api-and-package-spec.md) 与 [01](01-overall-architecture.md) §4.4。诊断对模型给 Pointer，对用户给脱敏摘要。

测试三类：平台固定测试、从验收条目编译的测试、模型补充测试（只能加覆盖，不能放宽前两类）。参考 Runtime 与 Android Runtime 用同一输入应对齐逐步轨迹。

### 3.4 发布 Saga

1. 锁任务与 ApprovalRecord  
2. 预留 `programId/revision`（失败后号不回收）  
3. 规范化 IR/测试/资源，JCS Manifest  
4. ValidationRecord  
5. Signer 幂等签 `signatures/manifest.cose`  
6. 确定性 ZIP，算 `transportSha256`  
7. 对象存储条件创建，禁止覆盖  
8. 读回复核后任务 `SUCCEEDED`

每步有 operation id。lease 丢失则停提交，新 Worker 从 Saga 续跑。

## 4. 持久化（逻辑表）

`generation_tasks`、`task_inputs`、`app_specs`、`proposals`、`approval_records`、`programs`、`program_revisions`、`validation_records`、`artifacts`、`audit_events`。用户原文与反馈短时加密；日志只留摘要。

## 5. 错误策略

| 类型 | 策略 |
|------|------|
| 越界 / 策略拒绝 | 立即失败 + 建议，不重试 |
| 未确认 | `APPROVAL_REQUIRED`，不发布 |
| 空转超限 | `REVISION_EXHAUSTED` |
| Schema/语义/测试 | 最多 2 次全量修复 |
| 模型 429/超时 | 有界退避，尊重取消 |
| 签名/存储 | 只重试幂等步，不新开 revision |

## 6. 实现顺序

1. Fixture + 手写 AppSpec/IR，跑通状态机  
2. 冻结 Schema、Golden 包、参考 Runtime  
3. 无模型跑通确认 → 编译 → 签名  
4. 接 Planner，再接 Builder 与两次修复  
5. 与手机预览/加载/断云联调  
6. 试点前拆 Validator 与 KMS  
