# 程序 Agent：宿主进程内 APK 插件

插件是 **APK 文件**，但 **不安装、不独立进程**。宿主下载到自己的 `filesDir`，点击后再 `DexClassLoader` 加载，在宿主 Activity 里跑。

## 为什么选这个方案

| 方案 | 做法 | 不选原因 / 选用原因 |
|------|------|---------------------|
| H5 / WebView | 下发 HTML/JS，WebView 打开 | 不是 APK；能力弱，和程序 Agent 编 Java 对不齐 |
| Shadow / RePlugin / VirtualAPK | 完整插件化，hook 系统、代理 Activity | 隐藏 API、新系统易碎，工程过重 |
| 动态功能模块 (Play Feature) | Google 官方拆包 | 必须走 Play，不能自己下到 data |
| 独立安装 APK | PackageInstaller + 新进程 | **不是宿主内插件** |
| **宿主契约 + DexClassLoader（选用）** | APK 只含 `PluginEntry` 实现，代码构建 UI | 官方 ClassLoader；不 hook；不安装；点按再加载 |

插件 **禁止** 插件包内的 `R.layout`（避免 `AssetManager.addAssetPath`）。  
**可以** 用宿主 `Activity` 代码构建任意 View：Canvas / SurfaceView、Handler / Choreographer 游戏循环、GestureDetector / 触摸、计时计分。俄罗斯方块这类本地游戏在范围内。

小游戏默认提供匹配玩法的短音效和静音开关，用户明确要求无声时除外。模板内置 `com.autoprocedure.pluginsupport.GameSoundEffects`，随插件 APK 打包；旧项目编译前也会同步该工具，不需要重新安装 phone-agent。使用与验收规则见程序编写 skill 的 `references/game-sound.md`。

英文朗读使用系统 `TextToSpeech`，与游戏音效及 ASR 区分。主 Agent 会在 `phoneCapabilities`
中提供当前宿主的音频 API、媒体音量、默认 TTS 引擎/离线英文 voice 及静默合成 WAV 的探测结果。
应依据成功探测的引擎/voice 实现，不让用户确认“能否发音”；未知继续技术核对，失败说明证据缺口。
接入与生命周期要求见程序编写 skill 的 `references/speech.md`。

## 产品归档

`code/agent/out/tasks/<taskId>/product/`：`design.md`、`implementation.md`、`runtime.md`、`code-locations.md`、`intent.json`。初始任务 ID 来自标题，同名独立新任务加后缀；之后改标题不改 ID、包名和目录。用户确认后的诉求快照按任务 ID 存储。
每次与用户对齐（澄清、方案、拒绝、确认）追加 `alignment-log.md`、`alignments.jsonl`；确认后还有 `aligned.json`。

## 编译

1. 复制 `plugin_template/`
2. 写 `{package}.PluginMain` 实现 `com.autoprocedure.pluginapi.PluginEntry`
3. `phone_agent/gradlew -p plugin_src assembleDebug`
4. `out/tasks/<小程序标题>/dist/plugin.apk`

入口类：`{packageName}.PluginMain`。

## 端上加载

1. 下载到 `filesDir/plugins/<taskId>/plugin.apk`（不调用系统安装）
2. 校验 zip 内有 `classes.dex` → 状态 ready
3. 点击：`PluginContainerActivity` → `DexClassLoader(apk, codeCacheDir, null, hostClassLoader)` → `PluginEntry.onCreate(host, container)`
4. 加载或运行失败：手机抓异常（`load` / `onCreate` / `runtime`）发 `{"type":"runtime_error","text":"{...}"}`。云端**不重写**，按堆栈改当前任务 `plugin_src/**/PluginMain.java`，Gradle 再编，push 新 APK。

## 主 Agent → 手机

`{"type":"job","text":"{...}"}`，`state`：making → downloading → installing（写入 data）→ ready。

## Codex CLI

```bash
export AUTO_PROCEDURE_USE_CODEX_CLI=1
```

开发 Agent 用 `codex exec --json` 读 CLI 事件，约每 8 秒推一次真实状态（运行中 / 已结束 / 失败 / 超时强杀），写入 `out/tasks/<id>/dev/cli-progress.log` 和 `attempts.jsonl`，并同步到手机对话。单次先等 **5 分钟**；若 CLI 一直没报错，再续等 **5 分钟**（最长 10 分钟）。有 `Connection lost` 等报错则 5 分钟到点即停。
失败则判断：`retry`（瞬时/编译问题）/ `design_incomplete` / `ask_user`。写代码与 **Gradle 编译失败** 都最多 **3** 次；编译失败会把 javac 错误回喂 CLI 改 `PluginMain` 再编。
CLI 只写 `PluginMain.java`，Gradle 负责编译。写代码前先读 [write-plugin-program](../skills/write-plugin-program/SKILL.md)：遵守主 Agent 选定的 taskId，仅复用本任务的源码，不自行按相似标题切换任务。

主 LLM 判断 new / continue / modify / clarify。单纯继续已确认任务复用原方案；功能修改先确认更新后的方案。工作目录为任务内 `dev/codex_work`，失败草稿保存在 `dev/source-draft`，覆盖前备份到 `dev/source-checkpoints`。`build-state.json` 记录状态和错误，`generation-state.json` 记录代码生成是否完成及对应需求摘要；只有生成完成且需求未变的正式源码，续作时才跳过生成直接尝试编译。


开发 CLI 统一为 Codex：`codex exec --sandbox workspace-write`。server 启动时检查登录，交互终端未登录时先运行 `codex login`，成功后才启动服务；生成和重试阶段复用登录状态。模型沿用本机 Codex 配置。
