# 宿主进程插件架构决策

> 2026-09-12 内部 APK 对齐：默认开发入口支持 `PluginEntry` APK 插件，声明式通道仍可用。新增 APK 任务字段 `launchType=apk`、`sha256`、`size`、`entryClass`、`downloadUrl`，状态为 `making/downloading/installing/ready/failed`（其中 installing/ready 由端侧管理）。APK 只在 Debug 下载和加载；当前摘要校验不是签名审核，不代表生产发布能力。详情见 [功能对齐与优化分析](08-procedure-alignment-and-optimizations.md)。

本文件取代 v1「所有插件只含 JSON」的限制，新增一个**仅限内部受信任分发**的
APK 插件通道。原声明式 `.apkg` 仍保留为默认、安全通道。

## 方案比较

| 方案 | 独立编译 | 下载到宿主 data 后延迟加载 | 宿主进程 | 结论 |
|---|---:|---:|---:|---|
| H5 ZIP + WebView | 是 | 是 | 部分（渲染器可能独立） | 适合不可信、表单型插件；不能交付 Android APK |
| Play Dynamic Feature | 否，必须随 App Bundle 发布 | 否，Play 会安装 split | 是 | 适合预先开发的官方模块，不适合 Agent 临时生成 |
| DexClassLoader APK 插件 | 是 | 是 | 是 | 仅限内部受信任插件；满足本项目 APK 插件要求 |
| Shadow/RePlugin 等框架 | 是 | 是 | 通常是 | 能力强但引入框架、资源与兼容性成本，不适合 MVP |
| 声明式 `.apkg` Runtime | 是（IR 编译） | 是 | 是 | 默认安全通道，不能承载任意 Android 代码 |

## 决策

MVP 同时提供两条通道：

1. **默认：声明式 `.apkg`**，用于模型生成的清单、打卡、表单等功能；固定 Runtime
   解释执行，不执行模型代码。
2. **受信任 APK 插件：`plugin.apk` + `plugin.json`**，只在内部开发/签名白名单环境
   启用。插件使用 Android SDK 独立 Gradle 编译；宿主下载它到
   `files/plugins/<pluginKey>/versions/<revision>/plugin.apk`，不调用 PackageManager
   安装。用户点击后，宿主验签/摘要、把文件设为只读，以 `DexClassLoader` 在自己的
   进程内加载入口类并创建 View。

Android 官方建议尽量避免动态代码加载；远程代码需要可信来源、完整性校验，并放在
应用内部/受限存储。因此 APK 通道不允许任意用户或未经审核的 Codex 输出直接发布。
它必须经过主 Agent 的签名、静态检查、人工批准和端侧验证。上架 Google Play 的版本
默认关闭该通道，使用声明式或 Play Dynamic Feature。

## APK 插件契约

```text
plugin.apk                       # 仅 Android DEX/resources，不能独立作为产品安装
plugin.json                      # id/version/entryClass/title/summary/icon/hash/signature
```

MVP 入口类使用稳定、无宿主私有类型的反射契约：

```kotlin
public android.view.View createView(android.content.Context context)
```

宿主不暴露 Activity、文件路径、网络客户端、Token 或任意 Service；插件不能申请新
权限。资源优先使用纯 View/Compose 和 SDK 内资源，MVP 不做运行时资源注入。

## 流程与状态

```text
手机交互页 → 产品 Agent：归档设计/实现/环境 → 用户确认
→ 程序 Agent：Codex 生成插件工程 → Gradle 独立编译 plugin.apk
→ 主 Agent：静态检查/签名/写 phone_agent/data/plugins → 通知手机
→ 手机下载到 files/plugins（未安装）→ 功能列表：下载/安装/完成/不可用
→ 用户点击完成项 → 验签 + DexClassLoader → 宿主进程内 View
```

状态：`DESIGNING`、`BUILDING`、`DOWNLOADING`、`VERIFYING`、`READY`、`FAILED`。
`FAILED` 使用灰色且不可点击；只有 `READY` 可点击加载。

## 代码位置

| 内容 | 路径 |
|---|---|
| 产品归档 | `code/agent/out/products/<productId>/` |
| Codex 生成插件工程 | `code/agent/out/products/<productId>/plugin-project/` |
| 插件 SDK | `code/plugin_sdk/` |
| 宿主下载/状态/加载器 | `code/phone_agent/app/.../ApkPluginStore.kt`、`ApkPluginLoader.kt` |
| 电脑侧插件制品镜像 | `code/phone_agent/data/plugins/<pluginId>/<revision>/plugin.apk` |
| 手机实际制品仓 | `Context.filesDir/plugins/...` |
