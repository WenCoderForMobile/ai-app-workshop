# 手机程序作坊 — 设计基线

> 2026-09-12 内部 APK 对齐：默认开发入口支持 `PluginEntry` APK 插件，声明式通道仍可用。新增 APK 任务字段 `launchType=apk`、`sha256`、`size`、`entryClass`、`downloadUrl`，状态为 `making/downloading/installing/ready/failed`（其中 installing/ready 由端侧管理）。APK 只在 Debug 下载和加载；当前摘要校验不是签名审核，不代表生产发布能力。详情见 [功能对齐与优化分析](08-procedure-alignment-and-optimizations.md)。

**中文** | [English](README.en.md)

> 产品：**手机程序作坊**  
> 一句话：做你自己的手机 App。不为大众做一个同款，为每一个人现做不一样的程序。  
> 本文档是 `codebuddy/data`、`codex/data`、`cursor/data` 的合并基线。三套原稿保留作历史参考，**实现与联调以本目录为准**。

用户用自然语言（文字或语音）描述想要的功能。云侧 Agent 判断能否由当前受限 Runtime 实现；能则给出方案与效果图，用户确认后生成声明式插件包 `.apkg`，签名下发；手机独立验证后安装、加载、运行。已安装的纯本地程序可在策略允许时离线使用。

这不是通用 APK 生成器，也不是「人人去当程序员」。人是下单的人，程序是为他现做的。

## 1. 端到端流程

```text
用户开口：我想要什么功能
        │
        ▼
云侧：可行性（需求 ∩ Runtime 画像 ∩ 发布策略）
  ├─ 不能做 ──► 能力边界 + 可落在 MVP 内的替代建议（不伪造功能）
  └─ 能做 ────► AppSpec → Proposal + 确定性 Mockup
                │
                ▼
         手机预览（WAITING_APPROVAL）
          ├─ 按意见修改（revise，反馈必填）──► 新方案，不计空转上限
          ├─ 不满意且无新意见（reject）──► 有界再出一版（默认 ≤3）
          ├─ 取消
          └─ 确认（approve）──► 发布门禁通过
                        │
                        ▼
        生成 ProgramIR + 测试 → 确定性编译/参考运行时
        → 最多 2 次有界修复 → 打包签名 → 不可变 Artifact
                        │
                        ▼
        短时 Download Ticket → 手机 HTTPS 下载
        → 11 层静态验证 + smoke
                        │
                        ▼
        原子安装并激活为插件 → 固定 Runtime 解释执行
                        │
                        ▼
        断云：已装纯本地插件可离线跑；离线不能生成新程序
        使用后要改：以 baseVersionId 开新任务，不改已发布包
```

## 2. 合并原则（吸收 / 不带入）

| 来源 | 带入本课题 |
|------|------------|
| `cursor/data` | 产品形态是**给宿主 App 加插件**；端侧始终可访云；断云只覆盖已装纯本地程序 |
| `codex/data` | MVP 用**一个确定性 Orchestrator + LLM 分阶段调用**；批准对象是 ApprovalBundle；下载/安装/激活/加载/启动分开；契约优先 |
| `codebuddy/data` | 人工在环、Mockup 确定性渲染、发布 Saga、11 层验证、JCS+COSE、错误码与包布局 |

**不带入：** 三套方案互相评比、相关论文调研、本机 Ollama 装机说明、把三个 LLM 角色当成安全隔离、USB `adb reverse` 作为目标架构（仅作为当前开发原型，见 [06](06-roadmap-and-acceptance.md)）。

## 3. 不可变原则

1. 未经用户确认不得发布。
2. 用户确认的是方案 + 效果图 + Runtime 画像组成的批准摘要，不是单独一句话。
3. 默认 `.apkg` 只含声明式 JSON、资源、测试和签名；禁止 APK/DEX/SO/脚本。仅
   [07](07-host-process-plugin-architecture.md) 定义的内部受信任 APK 插件通道可加载
   经签名审核的 `plugin.apk`，上架版默认关闭该通道。
4. LLM 输出、API、下载包一律不可信。
5. 云侧通过不等于手机通过；端侧必须独立验签、验包、跑 smoke。
6. Artifact 不可覆盖；失败不得替换当前可用版本。
7. 手机不直连大模型。
8. 先冻结合约与 Golden 包，再接模型。

## 4. 文档索引

| 文档 | 内容 |
|------|------|
| [01-overall-architecture.md](01-overall-architecture.md) | 边界、架构、主流程、状态机 |
| [02-agent-implementation-plan.md](02-agent-implementation-plan.md) | 云侧 Agent：编排、预览确认、编译发布 |
| [03-demo-implementation-plan.md](03-demo-implementation-plan.md) | 手机宿主：预览、下载、插件加载、断云 |
| [04-api-and-package-spec.md](04-api-and-package-spec.md) | **契约唯一基线**：API、`.apkg`、DSL、错误码 |
| [05-security-and-verification.md](05-security-and-verification.md) | 信任边界、签名、11 层验证、断云 |
| [06-roadmap-and-acceptance.md](06-roadmap-and-acceptance.md) | MVP、里程碑、验收、当前原型 |
| [07-host-process-plugin-architecture.md](07-host-process-plugin-architecture.md) | H5 / Dynamic Feature / APK 插件比较与宿主进程加载决策 |
| [12-audio-capability-verification.md](12-audio-capability-verification.md) | 宿主音频 / 离线英文朗读实测与 Agent 决策 |
| [11-phone-agent-chat-ui.md](11-phone-agent-chat-ui.md) | 当前 phone_agent 对话界面、参考映射与验收 |
| [ngrok-demo.md](ngrok-demo.md) | Ngrok 端云交互 Demo：公网隧道、回显、搭建过程 |

契约变更先改 `04`，安全变更先改 `05`，再同步其余文档与实现。

## 5. 术语

| 术语 | 含义 |
|------|------|
| 手机程序作坊 | 本课题产品名：开口点活，现做个人程序 |
| 插件 | 写入宿主 App 的一份声明式程序，传输形态为 `.apkg` |
| AppSpec | 归一化需求 |
| Proposal / Mockup | 用户可见方案 / 由固定组件表投影出的效果图 |
| ApprovalBundle | 用户实际批准的规格+方案+Mockup+Runtime 画像 |
| `approvalDigest` | ApprovalBundle 的 JCS SHA-256 |
| ProgramIR | 固定 Runtime 可解释的声明式程序 |
| 编译 | Schema/语义/预算/批准绑定校验，不是 javac、不产出 DEX |
| WAITING_APPROVAL | 等用户确认；此前不得生成可发布包 |
| 有界修订 | 仅「不满意且无新意见」计数，默认 ≤3 |
| Download Ticket | 短时下载票据，过期可重签，Artifact 不变 |
| 断云 | 已装纯本地插件离线可运行；不能离线生成 |
| Orchestrator | 确定性编排器；LLM 只产候选 |

## 6. 范围内 / 范围外（MVP）

**做：** 本地待办、打卡、清单、登记表、简单计算器、问答与本地记录。

**不做：** 相机、支付、任意网络、后台系统通知、定位、联系人、WebView JS、APK/DEX/SO/JNI/Shell/反射、通用 Android 工程生成。
