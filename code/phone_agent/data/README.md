# 开发期插件制品镜像

程序 Agent 在用户确认需求后，将编译完成的声明式 `.apkg` 写到
`plugins/<programId>/<versionId>.apkg`。这是电脑侧的检查和制品服务根目录；Debug
联调时只有显式注册的文件可从 ADB reverse 的 17891 端口下载。它不是 Android App
可直接执行的目录，17890 控制帧也不再内嵌整包 Base64。

真机流式下载时先限制大小并核对传输摘要，随后必须做包验证和 smoke，再原子安装到 app-private
`files/plugins/`，最后由宿主内置的声明式 Runtime 解释加载。`.apkg` 不是 APK，
包内不能包含 DEX、SO、脚本或 Android 源码。
