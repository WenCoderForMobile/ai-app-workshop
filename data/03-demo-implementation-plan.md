# 手机宿主（Demo）

Demo 是固定安装的可信 APK，也是**插件宿主**：提需求、预览、确认、下载、本地验证、自动加载、运行。始终可以访问云侧。断云只允许**已装纯本地插件**运行，不能离线生成。

## 1. 信任边界

```text
可信：APK、内置 Schema、公钥环、Runtime、组件表、Mockup 渲染、验证规则、PluginLoader
不可信：用户输入、模型、API、下载字节、包内一切字段
```

禁止用 `DexClassLoader`、反射、JNI、Shell、WebView JS 执行包内容。签名只证明来源与完整，不能把任意代码变安全。

手机**不**在本地调 LLM 生成新程序；**不**信任云侧「已验证」布尔值。

## 2. 技术基线

Kotlin、Jetpack Compose、Coroutines、OkHttp、kotlinx.serialization 严格未知字段、Room、DataStore、WorkManager（可恢复下载）、API 26+。Debug/Release 不同地址与公钥。Release 仅 HTTPS。

## 3. 模块（依赖单向）

| 模块 | 职责 |
|------|------|
| `agent-client` | 任务、预览、确认、票据 |
| `package-format` / `trust-store` | Schema、ZIP、JCS、COSE、吊销、高水位 |
| `download-manager` | 流式下载、续传、传输摘要 |
| `plugin-store` | `plugins/{id}/versions/{rev}/`、staging、journal、激活指针 |
| `program-validator` / `smoke-runner` | 11 层静态 + 隔离 Runtime 断言 |
| `runtime-core` / `runtime-ui` | 解释执行；预览与运行共用组件表 |
| `app-ui` | 需求、预览、列表、运行、离线标识、设置 |

只有 store 给出的已验证句柄才能进 Runtime。下载包不得直接加载。

## 4. 页面

### 当前 phone_agent 对话设计（2026-09-12）

当前 APK 宿主采用 Kotlin Fragment + XML View，使用「对话 / 程序广场」两个页签。
对话界面参照 `auto_procedure` 的 `fragment_chat.xml` 和消息气泡实现，详见
[对话界面设计与验收](11-phone-agent-chat-ui.md)。本段是当前对话实现基线；上文 Compose 等技术选型及下文完整页面清单保留为后续规划。

- 对话顶部放置工坊标题、简短状态和可点击连接标签，去掉重复标题栏和底部连接按钮。
- 消息区域用浅灰背景、白色助手气泡、浅蓝色用户气泡；支持长按选取复制和按可用宽度换行。
- 底部统一放置语音、最多四行的输入框和发送按钮；键盘弹出后由窗口缩放保留输入区。
- 任务制作期间留在用户选择的页签，用户自行到程序广场查看或打开程序。
- 阅读历史时保持位置，新消息提供「查看新消息」入口；位于底部或主动发送时跟随最新消息。

### 后续完整页面规划

- **功能首页**：已装/已激活插件、离线可用标记、revision、「描述新功能」、基于某版本改进（`baseVersionId`）。
- **提出需求**：自然语言或语音；提交 Runtime 画像；展示 phase。越界展示 boundary/alternatives，不只「失败」。
- **方案预览**：Proposal + Mockup。确认 / 按意见修改 / 不满意（无意见）/ 取消。预览不改已装插件。
- **下载验证**：分步文案；失败明确「旧版本未动」。
- **运行**：宿主框标明自动生成的受限程序；断云显示离线模式。
- **设置**：云侧地址、是否自动安装/激活/启动、是否允许断云。

## 5. 自动策略

| 策略 | 默认 | 含义 |
|------|------|------|
| `autoInstall` | true | 成功后下载验证并安装 |
| `autoActivate` | true | 安装后设为 active |
| `autoLaunch` | false | 不自动打开，由用户点进 |

端侧策略最终说了算。验证失败不覆盖 active。

## 6. 目录与安装事务

```text
files/plugins/<pluginKey>/staging/<artifactId>/
files/plugins/<pluginKey>/versions/<revision>/
files/plugins/<pluginKey>/quarantine/<artifactId>/
```

电脑联调可从仓库 `phone_agent/data/plugins/` 取得 Agent 编译出的 `.apkg`，再经
现有传输通道发送给手机；它不是手机上的共享目录。手机的唯一运行来源始终是上图
的 app-private `files/plugins/`，且只有安装事务返回的已验证版本可进入 Runtime。

`pluginKey` 由 `programId` 哈希得到，不用外部 id 当路径。下载写 `.part` 并算 `transportSha256`。先扫 ZIP 再按 Manifest 展开。提交：journal → 静态+smoke → 原子改名 → CAS 更新 active。崩溃：无 complete marker 则丢 staging；active 切换失败则保持旧指针。

## 7. Runtime v1（摘要）

组件：Column、Row、Text、TextInput、Button、Checkbox、List、Spacer、Dialog、Image（仅包内资源）。

状态：bool / int64 / string 及有界 list；`persistence=session|local`，local 写入程序命名空间，IR 无文件路径 API。

事件：onLoad、onClick、onChange、onSubmit。  
动作：setState、列表增删改、validateInput、navigate、showMessage、Dialog、finish。  
表达式：封闭 AST，无循环/递归。  
资源 URI 仅 `apkg:///resources/...`。MVP `capabilities=[]`。

## 8. 断云

已激活、无外部 Capability、TrustBundle/吊销/epoch 通过、设置允许 → 可启动。TrustBundle 过期后禁止装新包；已 active 可有限宽限，宽限后须联网刷新。离线设备无法获知断网后才发布的新吊销。

## 9. 实现顺序

契约与坏包向量 → Envelope/11 层验证 → Runtime 与跨端轨迹 → 安装 journal → 预览确认 UI → 任务与三策略 → 断云 → 最后接真实 LLM。
