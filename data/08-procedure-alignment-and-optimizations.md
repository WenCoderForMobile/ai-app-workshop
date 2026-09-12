# procedure_2 功能对齐与优化分析

对照：同级目录 `auto_procedure`。本次范围：需求对话、历史任务、APK 插件、小型本地游戏、手机程序广场。参考实现来源见 `alignment-imports.txt`。

## 本次功能对齐

| 功能 | 参考版 | procedure_2 本次实现 |
|---|---|---|
| 需求确认 | 对话复述、同意后制作 | APK 产品 Agent；确认、修改、取消分开，带修改意见不能误触发制作 |
| 本地小游戏 | Canvas、触控、游戏循环、计分 | 引入 APK 编程 Agent、Java 工程模板、PluginEntry 生命周期；支持由开发模型生成本地游戏 |
| 历史复用 | 历史任务索引、相似源码查找 | 保留最近任务索引、恢复历史需求；已有任务保留源码，在原代码上修改 |
| 开发监督 | CLI 心跳、失败分类、重试、向用户提问 | 引入 supervisor、编译错误修复与运行错误修复；同一服务进程内每任务自动运行修复最多两次 |
| 手机首页 | 对话／程序广场双页 | 引入双页、正方形卡片、状态显示、打开、删除；对话沿用进程级 AgentSession |
| APK 下载 | 下载 URL，覆盖同名文件 | 显式注册的制品服务；10 MiB 上限、大小和 SHA-256 校验、禁重定向；摘要目录保存，只读后加载 |
| 任务恢复 | 参考版缺少完整断线恢复 | 服务端任务落盘并重放；服务重启中断的制作标记失败；下载制品重新注册当前端口 |
| 插件运行 | PluginEntry.onCreate/onDestroy | 同一接口、宿主进程内运行，不走系统安装器；捕获加载和运行错误回传 |
| 声明式插件 | 旧版存在另一套 IR | 保留 procedure_2 原有 `.apkg` 编译、验证、安装链路，通过 `--runtime declarative` 启动 |

APK 与声明式通道使用不同产品 Agent 和发布状态文件，避免把 APK 当作声明式包安装。当前默认是 APK 内部开发模式，APK 下载和新接口加载只在 Debug 开启。现有 `createView` APK 入口属于旧接口，新生成的插件统一使用 PluginEntry。

## 已修正的参考版问题

- “确认后再加计时”“好的，把按键放大”作为修改意见，不直接开始制作。
- 模型没有返回完整新方案时，不沿用上一次方案等待批准。
- 离线修改保留原方案和意见，等待恢复模型后重新评估。
- APK 生成失败明确报错，不把小游戏或其他需求伪装成“完成一次”计数器，也不直接交付未经本次修改的相似历史源码。
- 当前任务已有源码时不删除工程重新生成。
- 接收 APK 时忽略远端 `apkPath`，本地路径由宿主计算；任务目录用摘要命名。
- APK 更新使用不可变制品，不覆盖正在使用的旧 APK；服务端不暴露任意目录。
- 不吞掉 Android 主线程的未捕获异常；上报后交给原异常处理器，避免主 Looper 已退出但进程假存活。
- Gradle 模板对齐 7.4.2；编程 Agent 在 macOS 未指定 JAVA_HOME 时尝试 JDK 17；统一使用 `codex exec`。

## 后续优化建议

| 优先级 | 功能与证据 | 优化方向 | 验收标准 |
|---|---|---|---|
| P0 | APK 目前是内部 Debug 下载执行，摘要不能证明发布者身份 | 实现受信发布者签名、验签、受限域名、票据和鉴权后再开放发布通道 | 伪造签名和过期票据被拒绝，旧可用版本保留 |
| P0 | 能编译不等于游戏操作正确，本次无设备实测条件 | 增加模拟器验收：启动、触控、输赢、重开、返回、旋转、断网 | 翻牌和俄罗斯方块均有真实设备测试记录；Handler 无离页残留 |
| P1 | 模型、CLI 及编译串行占用单个消息工作线程 | 独立任务队列、明确 taskId、可取消构建；取消信号传递给子进程 | 构建中仍可响应状态查询和取消，不继续发布已取消任务 |
| P1 | 运行错误报告待发队列仍在进程内；服务修复次数未跨重启保存 | 报告持久化、ACK、幂等 reportId、按任务版本持久化修复预算 | 杀进程后报告重发且只修复一次，不无限循环 |
| P1 | 删除与重连重放、同名重建之间缺少统一版本语义 | 删除墓碑按任务版本保存，提供明确重建／恢复入口 | 删除后重放不复活，新版本显式重建仍可显示 |
| P1 | 语音资源约 232 MiB，JNI 约 25 MiB；当前进页面就加载语音模型 | 模型按需下载、点击语音时延迟加载；录音设时长上限、用原始数组缓冲 | 文本对话不等待 ASR，首包显著缩小；持续录音内存有界 |
| P1 | 关键词式可行性判定易误拒绝“无需联网”等否定描述 | 固定能力画像与结构化能力申请；模型只建议，宿主判定 | 本地游戏通过，额外系统权限／网络请求不能绕过策略 |
| P2 | 两套产品流程、APK 历史入口、旧 IR 编译器并存 | 保持对外兼容期间提取统一会话和任务模型，逐步淘汰无调用旧入口 | 单一状态机、明确迁移测试，已有本地程序可继续打开 |
| P2 | 程序卡片使用全量刷新，多个状态更新会反复切页 | ListAdapter/DiffUtil；只对用户新确认任务自动切页 | 大量卡片更新无闪烁，历史重放不抢焦点 |

这些优先级基于本地代码检查；未进行网络产品推荐或性能测量，不把体积统计当作启动性能结论。

## 启动与验证

```bash
cd code/startServer
./start-adb-server.sh
```

默认 `AUTO_PROCEDURE_RUNTIME=apk`。脚本默认使用 Codex CLI；如使用配置中的开发模型，可设置 `AUTO_PROCEDURE_USE_CODEX_CLI=0`。直接启动可运行 `python3 code/agent/main.py --runtime apk`。保留声明式模式：`python3 code/agent/main.py --runtime declarative`。凭据仍通过现有环境配置提供。

Android 构建使用 JDK 17：

```bash
cd code/phone_agent
JAVA_HOME=/Library/Java/JavaVirtualMachines/jdk-17.jdk/Contents/Home ./gradlew :connect:testDebugUnitTest :app:assembleDebug :app:lintDebug
```

Python 回归：`python3 -m unittest discover -s code/agent/tests`。

`code/agent/tests/fixtures/MemoryMatch.java` 是用于验证 APK 工程模板及 PluginEntry 编译的翻牌游戏测试样例，包含配对、计步、重新开始和销毁时移除延迟回调。它不是通用代码生成失败时的替代交付。

本次未连接真实开发模型发起收费生成，也没有 Android 设备或已配置 AVD，因此不宣称完成游戏触控、语音和安装运行实测。具体构建和回归结果见本次交付说明。

已编译的宿主 Debug APK 约 231 MiB，翻牌样例 APK 约 12 KiB。下载上限 10 MiB 针对插件制品，不针对宿主安装包。

## 本次验证结果

- Python 回归：60 项通过。
- Android：`:connect:testDebugUnitTest :app:assembleDebug :app:lintDebug` 通过。
- Lint：0 个错误，29 个警告；主要是依赖版本、未使用资源和界面刷新建议。
- 翻牌小游戏测试 APK：使用生产插件工程模板，`assembleDebug` 通过。
- Python 语法编译和启动脚本语法检查通过。
- 未进行设备运行、触控和真实模型生成端到端验收。

## CLI 更正：procedure_2 使用 Codex

- 开发 CLI 统一为 `codex exec`，APK 生成／修复、可行性检查及声明式生成均已切换。
- 默认 `AUTO_PROCEDURE_USE_CODEX_CLI=1`；APK 模式仅显式设为 0 时改用模型代码生成。
- 可用 `CODEX_CLI` 指定可执行文件路径；server 入口检查 `codex login status`，复用既有登录。
- `codex_cli.py` 统一参数；源码生成使用 workspace-write，声明式候选使用 read-only；不传绕过沙箱参数。
- supervisor 解析 Codex 的 thread/turn/item 事件，并使用 `--output-last-message` 读取最终消息。失败退出和超时不接受残留源码。
- 本机确认安装 codex-cli 0.151.0，登录状态为已通过 ChatGPT 登录。67 项回归通过，包含模拟 Codex 子进程的成功／失败验证；未发起真实模型生成。
- [官方非交互文档](https://learn.chatgpt.com/docs/non-interactive-mode)。

补充验证：Codex 专项 8 项测试通过；缺少 Codex 或运行修复失败时明确报错，不静默切换到产品模型。Python 源码语法和 Bash 启动脚本语法检查通过。

## Codex 模型与 CLI 版本兼容修复

- 贪吃蛇任务的 HTTP 400 完整错误为 `The 'gpt-6-astra' model requires a newer version of Codex. Please upgrade to the latest app or CLI and try again.`；本机 ChatGPT 登录有效。
- 将 `/opt/homebrew/bin/codex` 对应的 npm 安装从 0.151.0 更新到 0.154.0。保留 gpt-6-astra，使用 `codex exec --sandbox read-only --ephemeral --json -m gpt-6-astra` 实测返回 OK、turn.completed、exit=0。
- APK supervisor 对版本不兼容返回 `upgrade_required`，首次失败即停止；不要求修改产品设计。声明式通道同样停止无效重试。
- 解析 `error.message` 内嵌的 JSON，先提取实际错误再截断；错误提示保留升级操作。启动脚本显示实际 CLI 路径和版本，便于发现 PATH 或 CODEX_CLI 指向旧安装。
- Codex 专项 13 项回归通过，覆盖嵌套错误、首次停止、最终尝试的错误分类、声明式通道以及不重开产品设计。
- 完整 Python 回归 75 项通过；启动脚本 Bash 语法检查通过。网络测试在允许本机回环端口的环境执行。
- CLI 更新会用于后续子进程调用；已启动的 Python 服务需重启才能加载新的错误处理代码。此次模型实测仅验证连通性，不代表贪吃蛇 APK 已生成。

## server 启动优先登录 Codex

- 登录检查统一到 `main.py` 的初始化入口，在创建模型客户端、手机连接和下载服务之前执行。启动脚本与直接运行 Python 都走此入口。
- 已登录时直接复用；交互终端未登录时执行 `codex login`，成功后复查再启动。非交互环境未登录、登录失败或超时均退出，不接收制作任务。
- `CODEX_API_KEY` 保留为显式凭据来源；显式关闭 CLI 时跳过检查。启动时固定实际 CLI 路径，后续调用使用同一安装。
- 已移除开发 supervisor 每次生成和重试前的 `login status` 调用。模拟 CLI 子进程只接受 exec，以回归验证开发阶段不会调用登录命令。
- 本机启动检查成功，复用 Codex CLI 0.154.0 的现有登录。完整 Python 回归 83 项通过，Bash 启动脚本语法检查通过。
