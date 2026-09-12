# 当前宿主音频与离线朗读能力自动验证

## 原因

旧运行环境只提供源码、设备型号、SDK、ABI，没有当前安装宿主的音频或 TTS 实测。
主 Agent 虽然被要求“问效果、不问技术”，仍可能把未知条件转换成“请用户确认能否发音”。
用户回答“可以发音”不能区分音效、媒体文件、在线 TTS 和离线英文朗读，也不证明生成插件能调用。

## 当前实现

手机连接后即在后台自动检测，并将实测写入 `code/agent/out/phone-capabilities.json`（诊断快照，不能替代当前连接查询）。
每次产品理解前，RuntimeEnvironment 向当前连接手机请求能力报告。并发调用共享一次探测；相同连接内最多缓存
60 秒；换连接或超时后不沿用旧证据。报告只包含固定检查结果，不发送录音、用户文本或音频文件。

- 音频：在宿主进程分配/释放 ToneGenerator，检查媒体音量与静音；不实际播放声音，不调整音量。
- 英文 TTS：使用当前默认引擎，查询已安装且不要求网络的英文 voice，优先 en-US。
- 用选中的 voice 将固定英文静默合成为临时 WAV，收到完成回调后检查有效且非空的 data chunk。
- 结果区分 `supported` / `unavailable` / `unknown`；超时不冒充不支持。
- TTS Binder 调用在独立线程执行，6 秒独立超时；报告先返回再清理，厂商 stop/shutdown 阻塞不能阻塞界面或报告。
  同一时间最多一个探测引擎，挂住时不会不断创建线程或引擎；恢复后继续释放。
- 正常完成删除临时文件；引擎 Binder 永不返回时待释放文件可能留在应用 cache 中，不会上传。

APK 的 `TTS_SERVICE` queries 在宿主 Manifest 声明，使 Android 11+ 可查询 TTS 服务。
它不是新增运行时权限。插件使用 host Context 调用系统 TextToSpeech；插件自身 Manifest 不会注册服务或增加查询能力。

## 控制协议

- PC → 手机：`type=capability_probe`，text 为 JSON，包含随机 requestId。
- 手机 → PC：`type=capability_report`，text 为 JSON，包含相同 requestId、schemaVersion=1、host、audioOutput、offlineEnglishTts。
- 设备报告直接由控制读取线程分发，绕过业务队列；主 Agent 等待报告不会与自己死锁。
- PC 校验 requestId、当前连接、字段类型和证据完整性。没有匹配请求的报告被丢弃。
- TTS supported 必须含成功合成标记、英文 locale、离线标记、引擎、voice 和非空文件字节数。
- 不匹配、格式错误、旧客户端无报告、断线、超时返回 unknown。

## 决策规则

1. 音频 API 可用不代表离线英文 TTS 可用；ASR 也不能作为 TTS 的证据。
2. 媒体静音不代表程序不支持音频；不为通过测试擅自提高音量。
3. 离线英文合成成功时直接规划朗读功能，不再问用户手机“能否发音”。
4. 引擎/语音资源缺失时，依据具体证据评估解决路径；不将英文朗读改成提示音或静默。
5. 未知时分类为 technical_verification，自动进入编程 Agent 对齐；只向用户说明检测尚未完成，不让用户代做技术验证。
6. 用户只确认口音、朗读触发方式、学习轮次等产品偏好。
7. 检测只覆盖默认引擎和固定英文样本；合成成功不是扬声器实听，也不证明所有目标词的发音质量。

主 Agent、最终可行性复核和程序编写 skill 使用相同原则。实际报告写入
intent.runtimeCapabilities 和 product/runtime.md，让代码生成能使用成功检测的引擎与 voice。
生成程序仍需在运行时初始化并校验 TTS、处理失败和回调，暂停 stop、销毁 shutdown，保存学习进度。

## 验证入口

- `code/agent/tests/test_phone_capabilities.py`：证据完整性、缓存/连接隔离、业务线程请求无死锁、两级模型及归档证据传递。
- `code/phone_agent/app/src/test/java/com/autoprocedure/plat/WaveEvidenceTest.kt`：有效音频、空 data、截断文件与无效文件。
- 使用真实手机进行不播放声音的探测，并查看 actual host version、engine/voice、reasonCode。

API 依据：[TextToSpeech](https://developer.android.com/reference/android/speech/tts/TextToSpeech)、
[Voice](https://developer.android.com/reference/android/speech/tts/Voice)、
[UtteranceProgressListener](https://developer.android.com/reference/android/speech/tts/UtteranceProgressListener)。
