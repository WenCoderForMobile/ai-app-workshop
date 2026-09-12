# 英文朗读与当前设备证据

`phoneCapabilities` 是当前连接 phone_agent 的自动观测，不是用户口头确认。
主 Agent 自动请求手机初始化音频 API，并通过默认 `TextToSpeech` 引擎选择已安装、
`isNetworkConnectionRequired=false` 的英文 voice，用固定英文静默合成 WAV、校验非空 data chunk。
报告区分 supported / unavailable / unknown，并提供 host 版本、引擎、voice、语言与原因。

- `OFFLINE_SYNTHESIS_VERIFIED`：当前引擎/voice 可合成英文；不等于所有目标单词读音正确，也不等于扬声器已被人工试听。
- 缺少离线英文 voice 或合成失败：分析报告中的资源缺口；不要改成提示音或不朗读后宣称满足需求。
- 超时、离线、旧版未返回探测：保持待核对，由程序重试/主编程 Agent 技术对齐，不让用户回答“能否发音”。
- 音量零/静音不代表不具备播放能力；跟随媒体音量，不擅自调系统音量。

插件用 `host` Context 创建 `TextToSpeech`，可以显式传入报告成功的引擎包名。
在 `OnInitListener` 成功后查询并选中报告中的 voice，运行时再次检查语言、离线要求和
`KEY_FEATURE_NOT_INSTALLED`，不能在尚未初始化时调用 speak。宿主 Manifest 已提供
`android.intent.action.TTS_SERVICE` 的 queries 声明；不需要新增运行时权限，插件自身 Manifest 查询不生效。

每条朗读用独立 utteranceId，处理 `speak()` 返回值及 `UtteranceProgressListener.onError/onDone`。
如果轮次推进取决于朗读完成，等对应 onDone 再推进，失败时保留当前词/轮次并提供重试；
不要按估计时长提前进入下一词。快速切词用 QUEUE_FLUSH 或 stop 取消旧朗读，忽略过期回调。

离开界面/切后台/游戏暂停时 stop；onDestroy 时 shutdown 并注销生命周期监听。
如果 PluginEntry 没有 onPause，使用宿主 Activity 的生命周期回调并仅处理该 Activity。
运行时失去 voice 时明确显示朗读暂不可用，保留学习进度，不能让学习流程无声跳过朗读要求。

仅需用户确定口音、自动/手动朗读等体验偏好，不要求用户验证 APK、接口或离线引擎。

依据：[TextToSpeech](https://developer.android.com/reference/android/speech/tts/TextToSpeech)、
[Voice](https://developer.android.com/reference/android/speech/tts/Voice)、
[UtteranceProgressListener](https://developer.android.com/reference/android/speech/tts/UtteranceProgressListener)。
