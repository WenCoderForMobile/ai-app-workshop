# phone_agent 对话界面设计

日期：2026-09-12；宿主版本：0.1.2（versionCode 3）。

## 目标与参考

参考 `auto_procedure/code/phone_agent` 的 `fragment_chat.xml`、`item_message.xml`、
`MessageAdapter.kt` 及 `bg_bubble_*`、`bg_chip_*`、`bg_input_field`。
当前实现保留 auto_procedure_2 的进程级 AgentSession、自动重连和 APK 任务交付。

| 区域 | 旧设计的问题 | 当前实现 |
|---|---|---|
| 标题和状态 | 普通状态文字占两行，连接操作在页面底部 | 单一工坊标题；简短状态；右上角连接标签显示连接、连接中、已连接、重试连接 |
| 消息 | 方形纯色块，固定 280dp，全量刷新 | 左白右蓝圆角气泡；宽度按列表可用空间计算；增量更新，长按选择文字 |
| 输入 | 固定高度输入框，多排按钮挤占空间 | 白色底栏，48dp 语音入口，圆角输入框最多四行，发送按钮；长文本在框内滚动 |
| 浏览 | 每次连接或任务通知都强制滚到底部 | 内容变化才更新；阅读历史时显示「查看新消息」按钮 |
| 导航 | 制作/下载时强制切换程序广场 | 保留用户所选页签，由用户决定何时查看程序 |
| 两个入口 | ChatActivity 和 ChatFragment 各自维护完整逻辑 | ChatActivity 直接承载同一个 ChatFragment |

## 交互规则

1. 打开应用自动尝试连接。断开/失败时点击标签连接；连接过程中禁用重复点击。
   已连接时点击查看连接操作，明确选择「断开」才断开。长按标签可查看底层错误详情。
2. 可以离线编辑需求；只有连接正常、内容非空且未在转写时允许发送。
   录音时发送按钮可结束录音并发送识别结果，网络断开则保留识别文本。
3. 首次点击语音时才申请麦克风权限，不阻挡初次文字输入。
   录音显示波形，点击语音按钮结束后可编辑；识别内容追加到现有草稿。
4. 切换页签、进入后台或离开页面时结束录音；释放识别器前等待正在执行的初始化/转写结束。
5. 在消息底部接收回复或主动发送后显示最新消息；翻看历史时不抢滚动位置。
   顶部连接状态变化和无内容变化的会话通知不刷新列表。
6. 主页面和独立对话入口使用浅色无 ActionBar 主题，避免双标题及深色系统主题下的对比度问题。
   其他插件运行 Activity 的主题保持独立。

## 实现文件

- `code/phone_agent/app/src/main/res/layout/fragment_chat.xml`：主要对话布局。
- `code/phone_agent/app/src/main/res/layout/item_message.xml`：消息文本与间距。
- `code/phone_agent/app/src/main/java/com/autoprocedure/plat/ChatFragment.kt`：连接状态、输入、语音与滚动。
- `code/phone_agent/app/src/main/java/com/autoprocedure/plat/MessageAdapter.kt`：气泡和增量列表更新。
- `code/phone_agent/app/src/main/java/com/autoprocedure/plat/ChatActivity.kt`：旧入口复用。
- `code/phone_agent/app/src/main/java/com/autoprocedure/plat/MainActivity.kt`：用户自主切换页签。

## 验收

构建命令（JDK 17）：

```sh
cd code/phone_agent
env JAVA_HOME=/Library/Java/JavaVirtualMachines/jdk-17.jdk/Contents/Home ./gradlew :app:assembleDebug :app:lintDebug
```

本次 `assembleDebug` 与 `lintDebug` 均通过；Lint 为 0 个错误、33 个警告。生成 APK 的版本为 0.1.2 / versionCode 3。

本次环境没有 adb 设备，`emulator -list-avds` 也没有返回可用模拟器，因此未执行安装或视觉验收。
以下为设备接入后需要验证的场景，不表示已测试通过：

- 首次打开、连接中、连接失败与重连；输入草稿在重连期间保留。
- 空输入/纯空白不能发送；多行输入、长中文消息、复制文本正常。
- 上滑查看历史时收到消息仍留在原处，点击新消息按钮可到底部。
- 制作/下载期间不自动切页；手动进入程序广场后程序仍可打开。
- 麦克风拒绝权限、授权、录音、转写、空语音、后台切换与转屏。
- 320dp 窄屏、横屏、字体放大、软键盘弹出后输入区可见。

## 后续可优化项

- 将 CLI 心跳和制作日志改为按任务/阶段更新的进度卡片，提供展开详情；需要服务端稳定的事件类型和任务标识，避免仅凭消息文本合并而吞掉重要回复。
- 为需求确认提供结构化操作按钮，并明确关联确认的任务版本。
- 持久化对话记录和草稿，恢复进程被系统回收前的阅读位置；当前 AgentSession 仅保存本次进程会话。
