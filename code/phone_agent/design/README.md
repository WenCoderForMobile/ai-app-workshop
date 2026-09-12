# AI 程序工厂图标

深蓝背景、青绿色工厂、白色 AI 芯片和代码括号。原生矢量设计。

- `ai-program-factory-icon.svg`：可编辑设计稿。
- `ai-program-factory-icon.png`：512px 普通预览。
- `ai-program-factory-icon-round.png`：512px 圆形预览。
- Application 的 `icon` / `roundIcon` 明确引用五档尺寸的 `ic_factory_app.png` / `ic_factory_app_round.png`，APK 图标读取工具无需解释自适应 XML。
- MainActivity 的桌面图标引用 `ic_factory_launcher`：Android 26+ 使用自适应矢量图标，其余版本使用 PNG。
- 插件工程模板同步导出 PNG 并绑定 Manifest。编程 Agent 在编译旧工程前也补齐资源和缺失的图标声明，已有自定义图标声明保留。
- 已发布的旧插件 APK 不会直接修改，重新构建才会带上图标。

重新导出：`python3 code/phone_agent/tools/generate_launcher_icon.py`（需要 Pillow）。

## 0.1.1 验证

宿主及翻牌插件样例的 `assembleDebug` 均通过。使用 aapt 检查 APK，Application 图标在各 density 下均指向 PNG；宿主 APK 内 10 张应用图标与生成源文件逐字节一致。

`test_apk_icons.py` 的两项回归通过：旧工程补齐图标、自定义图标声明保留。

已在连接的 PLG110 手机上通过 `adb install -r` 覆盖安装，版本为 `versionCode=2 / versionName=0.1.1`，启动返回 `Status: ok`；桌面新图标见 [实机截图](verification/launcher-0.1.1.png)。
