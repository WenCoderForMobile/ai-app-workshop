# 总体架构与全部流程

产品：**手机程序作坊**。固定安装的 Android 宿主按用户需求扩充**声明式插件**，不重装 APK、不热加载代码。

## 1. 目标

证明六件事：

1. **可分析**：说得清能不能做；不能做给替代建议。
2. **可预览**：方案 + 效果图；用户确认前不发布。
3. **可生成**：确认后变成受约束 ProgramIR 并确定性编译、签名。
4. **可扩充**：插件落到宿主内，出现在功能列表。
5. **可断云**：已装纯本地插件离线可跑。
6. **可验证**：坏包不能变成可运行插件；失败保留旧版。

## 2. 架构

```text
用户 ──► 手机宿主 App
           │  需求 / 确认 / 下载
           ▼
        云侧 Task API
           │
           ▼
        确定性 Orchestrator
           ├─ LLM（规划 / 实现候选，可换 Provider）
           ├─ 确定性 Renderer（AppSpec → Mockup）
           ├─ Compiler + 参考 Runtime + 测试
           └─ Signer/KMS → 不可变对象存储
           │
           ▼
        手机：验签验包 → 插件目录 → 固定 Runtime
```

逻辑上可把 LLM 调用分成 Planner（AppSpec/Proposal）和 Builder（ProgramIR/测试）。独立的 Designer/Developer/Tester 三个会话角色是后续质量选项，**不是信任边界**。发布裁决只来自 Schema、编译器、测试、Signer 和端侧验证。

## 3. 对象血缘

```text
UserRequest + RuntimeProfile
  → AppSpec
  → Proposal
  → Mockup（确定性投影，模型不画位图）
  → ApprovalBundle → approvalDigest
  → ProgramIR + tests
  → ValidationRecord
  → Manifest + COSE → .apkg Artifact
  → 端侧 InstalledProgram（active revision）
```

ApprovalBundle 至少包含：`appSpecDigest`、`proposalDigest`、`mockupDigest`、`runtimeProfileDigest`、`uiRegistryVersion`、`rendererVersion`、`proposalSequence`。

`approvalDigest = SHA-256(JCS(ApprovalBundle))`。批准记录、ProgramIR、ValidationRecord、Manifest 绑定同一摘要。

## 4. 主流程（逐步）

### 4.1 提需求

手机提交 `prompt`、`locale`、当前 `runtimeProfile`、可选 `baseVersionId`。服务端做长度/编码/策略门禁，返回 `202` + `taskId`。客户端轮询 `GET /v1/tasks/{id}`。

### 4.2 澄清与可行性

信息不够：最多 3 个短问题，`WAITING_INPUT`。

可行性是集合运算，不是模型说「可以」：

```text
requestedFeatures
  ∩ 服务端发布策略
  ∩ runtimeProfile.supportedFeatures
  ∩ Schema 兼容
```

MVP `capabilities=[]`。依赖相机、任意网络、后台系统通知、支付等则 `UNSUPPORTED_CAPABILITY`，带 `boundary` 与可落在 Registry 内的 `alternatives`。不生成 Proposal。

### 4.3 预览

可实现则生成 AppSpec，再生成用户语言的 Proposal。Mockup 由 Renderer 用与 Runtime **同一套组件表**从 AppSpec 投影。模型不能单独提交做不出来的界面。

任务进入 `WAITING_APPROVAL`。手机必须展示方案与效果图。

用户动作：

| 动作 | 条件 | 效果 |
|------|------|------|
| `approve` | digest 与当前方案一致 | 进入正式生成 |
| `revise` | `feedback` 去空白后非空 | 并入需求出新方案；**不计**空转上限 |
| `reject` | 无有效 feedback | 空转再出一版；计数 +1，默认上限 3 |
| `cancel` | 非终态 | 结束任务 |

空转耗尽：`FAILED` + `REVISION_EXHAUSTED`。用户可接受当前方案（视为 approve）、放弃、或改为 `revise`。

### 4.4 生成与发布

确认后：

1. 生成 ProgramIR 与 smoke 测试候选。
2. 固定次序编译：解码 → Schema → 类型引用 → 控制流 → 表达式 → Feature/Capability → 预算 → 内容安全 → 测试覆盖 → 批准摘要绑定。
3. 参考 Runtime 跑平台测试 + 验收测试 + 补充测试。
4. 可修复错误最多 **2 次全量重生成**。
5. 预留不可复用 revision → 组包 → ValidationRecord → 幂等签名 → 条件写入对象存储。
6. 任务 `SUCCEEDED`，返回 Artifact 描述符。

「编译」不生成 Android 原生代码。

### 4.5 下载、验证、加载

默认策略建议：`autoInstall=true`，`autoActivate=true`，`autoLaunch=false`（装好出现在列表，由用户点开）。

手机：取 Ticket → HTTPS 流式下载 → 11 层静态验证 → 新 Runtime 跑 smoke → 原子安装 → 按策略激活。任一步失败隔离，不改当前 active。

### 4.6 运行与断云

固定组件 + 白名单动作 + 步数/状态预算。无网络 Capability 的已激活插件，在吊销检查与 `securityEpoch` 通过且用户允许离线时，可断云启动。离线禁止：创建任务、确认方案、下载新插件。

使用后要改：新任务带 `baseVersionId`，走完整流程；已发布包永不原地修改。

## 5. 状态机

云侧任务：

```text
QUEUED → RUNNING
          ├─ WAITING_INPUT → RUNNING
          ├─ WAITING_APPROVAL → RUNNING（revise/reject/approve）
          └─ SUCCEEDED | FAILED | CANCELED
QUEUED / 等待态也可 CANCELED | EXPIRED
```

`status` 是协议；`phase` 只展示进度（如 INTAKE、FEASIBILITY、PREVIEWING、GENERATING、COMPILING、PUBLISHING）。

端侧三套状态分开：安装（DISCOVERED…INSTALLED/REJECTED）、激活（INACTIVE/ACTIVE/DISABLED）、会话（STARTING/RUNNING/STOPPED/FAILED）。已安装 ≠ 已激活 ≠ 正在跑。

## 6. 部署

开发：Agent 单进程 + SQLite + 本地对象目录；LLM 为 OpenAI 兼容口（云或本机）；开发签名钥只给 Debug APK。生产：独立 Worker、PostgreSQL、对象存储、Validation Service 与 KMS 分离。

若 Validator、Publisher、Signer 同进程，只能声称挡住模型胡写，不能声称挡住生成服务被攻陷。

## 7. 成功标准

范围内样例能从需求走到端侧运行；范围外样例给边界不产包；预览组件与运行 UI 可追踪；坏包/回滚/摘要不匹配不改 active；杀进程与断网可恢复；人工等待不计入生成耗时。
