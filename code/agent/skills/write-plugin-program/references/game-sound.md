# 游戏音效接入

模板已经包含 `com.autoprocedure.pluginsupport.GameSoundEffects`，编译旧项目时也会自动补入。只需在 `PluginMain.java` 引用，不复制成另一个同名类，不读取宿主不存在的 `R.raw` 或 `assets`。

```java
import com.autoprocedure.pluginsupport.GameSoundEffects;

// PluginMain 成员；在 onCreate 中以本插件包名为键初始化。
private GameSoundEffects sound;

// onCreate(Activity host, ViewGroup container) 中：
sound = new GameSoundEffects(host, "当前 PluginMain 的实际包名");
Button mute = new Button(host);
mute.setText(sound.isEnabled() ? "音效：开" : "音效：关");
mute.setOnClickListener(v -> {
    sound.setEnabled(!sound.isEnabled());
    mute.setText(sound.isEnabled() ? "音效：开" : "音效：关");
});
// 将 mute 加到可见布局；不要只创建而忘记 addView。

// 在具体事件成功改变游戏状态后调用一次，例如吃到食物：
sound.play(GameSoundEffects.Event.COLLECT);

// 用户暂停游戏：sound.stop();
// PluginMain.onDestroy() 中：
if (sound != null) { sound.release(); sound = null; }
```

所有接口在 UI 线程调用。用户明确要求全程无声时不创建播放器、不插入音效；不要因这份默认规则违背用户需求。恢复运行不播放积压声音。

| 事件 | 建议场景 |
| --- | --- |
| TAP | 开始、确认等少量有意义的操作；不必每次触摸都响 |
| COLLECT | 吃果、拾取、加分 |
| SUCCESS | 匹配、消除、完成子目标 |
| FAILURE | 碰撞、失败、无效配对；避免在结束状态每帧重复触发 |
| WIN | 完成整局、通关 |

工具使用系统媒体音量和适中的内部音量，申请短暂音频焦点；焦点被拒绝/丢失时停音，不自动重播。快速普通事件会合并，不创建无限线程或播放器；单个音效最长 240ms。`play()` 返回 false 时继续正常游戏，不报程序错误。

实现依据：[Android ToneGenerator](https://developer.android.com/reference/android/media/ToneGenerator)、[Activity 生命周期回调](https://developer.android.com/reference/android/app/Application.ActivityLifecycleCallbacks)与[音频焦点](https://developer.android.com/media/optimize/audio-focus)。

源码：[GameSoundEffects.java](../../../program_agent/plugin_template/app/src/main/java/com/autoprocedure/pluginsupport/GameSoundEffects.java)。若自行运行 javac 检查，需要把该文件与 PluginEntry 一起加入编译输入；正式打包由程序 Agent 的 Gradle 流程完成。

这里提供短合成提示音，不是 MP3 播放器、配乐素材库或后台音乐服务。用户要求特定音乐风格、真实乐器或歌曲时，再评估素材与播放实现，不能用蜂鸣声宣称已实现指定音乐。
