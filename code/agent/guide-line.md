# procedure_2 启动顺序

开发 CLI 使用 Codex，产品需求对话使用 `code/agent/llm/config.json` 的模型配置。

1. 执行 `codex login`，然后用 `codex login status` 检查登录。
2. 准备产品模型：使用本地模型时启动 Ollama；使用远端模型时配置对应环境变量。
3. USB 连接手机，执行 `adb devices`，确保设备状态为 `device`。
4. 在项目根目录运行 `cd code/startServer && ./start-adb-server.sh`。
5. 打开手机 App，描述需求、确认后，在程序广场查看制作／下载状态并打开。

启动脚本检查 Codex 认证，建立 17890 控制端口与 17891 下载端口的 ADB reverse。
默认生成宿主进程内 APK 插件。`AUTO_PROCEDURE_RUNTIME=declarative` 可切回声明式插件。

`AUTO_PROCEDURE_USE_CODEX_CLI=1` 为默认 APK 开发方式。设为 `0` 才显式改用产品模型生成代码。
`CODEX_CLI` 可指定 Codex 可执行文件路径。模型选型沿用本机 Codex 配置，本项目不强制指定模型。

Codex 通过 `exec --sandbox workspace-write --json --output-last-message ...` 在候选工作目录生成源码；
声明式候选使用只读沙箱。两条路径均使用 `--skip-git-repo-check` 支持当前无 Git 的工作目录。
Gradle 编译由 Python 程序 Agent 执行。日志位于 `code/agent/out/tasks/<任务>/dev/`。

调用参数参考：[Codex 非交互模式](https://learn.chatgpt.com/docs/non-interactive-mode)。
