---
name: write-plugin-program
description: >-
  Writes in-host Android plugin programs (PluginEntry / PluginMain.java) for 程序工坊.
  Use when generating, revising, compiling, or fixing a mini-program / 小程序 / 插件 APK,
  especially games with sound feedback, Canvas UI, runtime crashes, 刚才功能,
  历史程序, or 在已有程序上改.
---

# 程序编写

为「AI 程序工坊」写宿主进程内插件。遵守主 Agent 选定的任务 ID 和新建/续作/修改决策，利用该任务已有源码实现差异。

## 产物形态

- 产物是 **APK 插件**，**不安装、不独立进程、不出现在桌面**。
- 手机保存到宿主 `filesDir/apk-tasks/<任务摘要>/<APK摘要>/plugin.apk`，点击后 `DexClassLoader` 加载。
- 入口：`{package}.PluginMain` 实现 `com.autoprocedure.pluginapi.PluginEntry`。
- `onCreate(Activity host, ViewGroup container)` 里 **用 Java 代码 addView**。
- **禁止** 插件内 `R.layout`、独立 Activity、支付、任意网络、系统安装器。

## 能力范围

可以：自定义 View、Canvas / SurfaceView、Handler / Choreographer 游戏循环、触摸手势、计分、本地 SharedPreferences、合成音效。俄罗斯方块、消除、连连看等本地游戏在范围内。

不要因为「要动画 / 手势 / 计时循环」就拒绝。禁止支付、任意网络和追加系统权限。

技术约束：Java 8、minSdk 21、targetSdk 33。

## 游戏音效

生成小游戏时默认配合玩法加入短音效，并在方案与验收中体现。用户明确要求静音、不要音效时优先遵守；普通工具只在有意义的操作反馈处使用，不强加循环背景音乐。
单纯续作或运行错误修复时保留原有声音选择，不借修复改动已经确认的功能范围。

- 按玩法选择事件：贪吃蛇吃果/碰撞、翻牌配对/不匹配、消除得分/连击、胜利/失败等。触发放在状态变化处，每次事件一次，不能放在 `onDraw`、每帧或蛇每步移动中反复播放。
- 区分成功、失败与普通操作，优先使用模板中的 `com.autoprocedure.pluginsupport.GameSoundEffects`。它随插件打包，不需要修改宿主、不需要音乐素材或新增权限。
- 必须提供可见的“音效：开/关”开关，按程序保存选择。初次默认开启，遵守用户明确的无声要求。音量跟随系统媒体音量，不调整系统音量；失败时静默降级，不能影响游戏。
- 暂停游戏时调用 `stop()`，销毁时调用 `release()`；工具会在宿主 Activity 暂停/停止时停音，销毁时兜底释放。返回前台不重播之前的事件。
- 新建或补充游戏音效时，读取 [音效接入说明](references/game-sound.md)。已有合适音效保留并修正缺漏，不为换工具重写游戏。
- 验收包含事件音效、静音及重进后的偏好、快速连点不叠加、游戏暂停/切后台/退出无余音。未经设备测试不能声称已听到音效。

## 文字朗读与音频能力验证

- TTS 朗读与提示音、ASR 是不同能力。涉及朗读时读取 [朗读接入与能力证据](references/speech.md)。
- 主 Agent 提供的 `phoneCapabilities` 是当前连接宿主的自动检测报告；依据报告的引擎、voice、合成结果实现，不要求用户确认技术可行性。
- 未知或失败交回技术评估，不能把用户“可以发音”当作实测；朗读是核心功能时不可套用游戏音效的静默降级规则。

## 必须先复用历史

1. 主 Agent 先选择明确 taskId：new 是独立新任务；continue 续作已确认任务；modify 修改选定旧任务。
2. 使用传入的本任务源码（可能来自 `plugin_src`、`dev/codex_work` 或 `dev/source-draft`），保留已有功能并修复未完成处。失败草稿未经验证，不代表可直接交付。
3. 只有本任务没有源码时按 `plugin_template` 新建，不自行扫描标题相近的任务替代用户选定目标。
4. 修改保留 taskId、包名和原目录；标题可以改变。独立新任务不能覆盖旧目录，也不能修改其他任务。

保留能跑的游戏循环、绘制和触控；只改用户这次要的差异（爆炸、音效、按键大小等）。

## 产品对齐

- 用户同意即可开始：好的 / ok / 确认 / 同意 作为完整回复时算同意；带修改意见必须先更新方案。
- 「刚才功能 / 上次那个 / 再做一遍」= 找回最近历史任务再做或再改。
- 产品文档在 `code/agent/out/tasks/<小程序标题>/product/`。
- 只写 `PluginMain.java`。Gradle `assembleDebug` 由程序 Agent 执行。
- 编译失败时按 javac 错误改同一文件，最多 3 次，不要另起炉灶。
- 手机上报加载/运行错误时：按 `phase` + 堆栈改**当前任务已有** `PluginMain.java`，禁止删掉 `plugin_src` 重写。修空指针、越界、未 addView、Handler 泄漏等；保留游戏循环和触控。

## 输出

写出完整可编译的 `PluginMain.java`（含 package 与 `PluginEntry`）。不要 Markdown 包裹，不要声称已经安装到系统。
