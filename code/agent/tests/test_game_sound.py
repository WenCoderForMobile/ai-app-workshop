import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from program_agent.apk_builder import ApkBuilder

RELATIVE = Path('app/src/main/java/com/autoprocedure/pluginsupport/GameSoundEffects.java')

# Lightweight Android doubles exercise control flow; an SDK/Gradle build checks
# the real Android API surface separately. These do not simulate audible output.
STUBS = {
    'android/os/Looper.java': '''package android.os; public class Looper {
        static final Looper MAIN = new Looper();
        public static Looper getMainLooper(){return MAIN;} public static Looper myLooper(){return MAIN;}}''',
    'android/os/Bundle.java': 'package android.os; public class Bundle {}',
    'android/os/SystemClock.java': '''package android.os; public class SystemClock {
        public static long time=1000; public static long uptimeMillis(){return time;}}''',
    'android/os/Handler.java': '''package android.os; public class Handler {
        public static Runnable pending; public Handler(Looper l){}
        public void removeCallbacks(Runnable r){if(pending==r)pending=null;}
        public boolean postDelayed(Runnable r,long t){pending=r;return true;}}''',
    'android/content/SharedPreferences.java': '''package android.content; public class SharedPreferences {
        boolean enabled=true; public boolean getBoolean(String k,boolean d){return enabled;}
        public Editor edit(){return new Editor();} public class Editor {
        public Editor putBoolean(String k,boolean v){enabled=v;return this;} public void apply(){}}}''',
    'android/content/Context.java': '''package android.content; public class Context {
        public static final int MODE_PRIVATE=0; public static final String AUDIO_SERVICE="audio";}''',
    'android/app/Application.java': '''package android.app; import android.os.Bundle;
        public class Application { public int registered=0;
        public interface ActivityLifecycleCallbacks {
        void onActivityCreated(Activity a,Bundle b); void onActivityStarted(Activity a);
        void onActivityResumed(Activity a); void onActivityPaused(Activity a);
        void onActivityStopped(Activity a); void onActivityDestroyed(Activity a);
        void onActivitySaveInstanceState(Activity a,Bundle b);}
        public void registerActivityLifecycleCallbacks(ActivityLifecycleCallbacks c){registered++;}
        public void unregisterActivityLifecycleCallbacks(ActivityLifecycleCallbacks c){registered--;}}''',
    'android/app/Activity.java': '''package android.app; import android.content.*; import android.media.*;
        public class Activity extends Context { public final Application app=new Application();
        public final AudioManager audio=new AudioManager(); public boolean window=true, finishing=false;
        static final java.util.Map<String,SharedPreferences> prefs=new java.util.HashMap<>();
        public Application getApplication(){return app;} public Object getSystemService(String k){return audio;}
        public SharedPreferences getSharedPreferences(String k,int m){return prefs.computeIfAbsent(k,x->new SharedPreferences());}
        public boolean hasWindowFocus(){return window;} public boolean isFinishing(){return finishing;}}''',
    'android/media/AudioManager.java': '''package android.media; public class AudioManager {
        public static final int STREAM_MUSIC=3, AUDIOFOCUS_GAIN=1, AUDIOFOCUS_GAIN_TRANSIENT_MAY_DUCK=3,
        AUDIOFOCUS_REQUEST_GRANTED=1; public boolean allow=true; public int abandoned=0;
        public interface OnAudioFocusChangeListener {void onAudioFocusChange(int v);}
        public OnAudioFocusChangeListener listener;
        public int requestAudioFocus(OnAudioFocusChangeListener l,int stream,int gain){listener=l;return allow?1:0;}
        public int abandonAudioFocus(OnAudioFocusChangeListener l){abandoned++;return 1;}}''',
    'android/media/ToneGenerator.java': '''package android.media; public class ToneGenerator {
        public static final int TONE_PROP_ACK=1,TONE_PROP_BEEP2=2,TONE_PROP_NACK=3,TONE_DTMF_9=4,TONE_PROP_BEEP=5;
        public static int created=0, played=0, stopped=0, freed=0; public static boolean fail=false;
        public ToneGenerator(int stream,int volume){if(stream!=3||volume>40)throw new AssertionError();created++;}
        public boolean startTone(int type,int duration){if(fail)throw new RuntimeException();
        if(duration<1||duration>240)throw new AssertionError();played++;return true;}
        public void stopTone(){stopped++;} public void release(){freed++;}}''',
}

HARNESS = '''import android.app.*; import android.media.*; import android.os.*;
import com.autoprocedure.pluginsupport.GameSoundEffects;
import com.autoprocedure.pluginsupport.GameSoundEffects.Event;
public class SoundTest {
  static void check(boolean b){if(!b)throw new AssertionError();}
  public static void main(String[] args){
    Activity host=new Activity(); GameSoundEffects sound=new GameSoundEffects(host,"one");
    check(sound.isEnabled()); check(sound.play(Event.TAP)); check(!sound.play(Event.COLLECT));
    SystemClock.time+=100; check(sound.play(Event.COLLECT)); check(ToneGenerator.created==1);
    sound.setEnabled(false); check(!sound.play(Event.WIN)); check(Handler.pending==null);
    sound.release(); sound.release(); check(host.app.registered==0); check(ToneGenerator.freed==1);
    GameSoundEffects again=new GameSoundEffects(host,"one"); check(!again.isEnabled());
    GameSoundEffects other=new GameSoundEffects(host,"two"); check(other.isEnabled()); other.release();
    again.setEnabled(true); check(again.play(Event.WIN));
    again.onActivityPaused(new Activity()); check(again.play(Event.SUCCESS));
    again.onActivityPaused(host); check(!again.play(Event.WIN)); check(Handler.pending==null);
    again.onActivityStopped(host); int created=ToneGenerator.created;
    again.onActivityResumed(host); check(ToneGenerator.created==created); check(again.play(Event.WIN));
    host.audio.listener.onAudioFocusChange(-1); check(Handler.pending==null);
    host.audio.allow=false; check(!again.play(Event.FAILURE)); host.audio.allow=true;
    host.window=false; check(!again.play(Event.WIN)); host.window=true;
    check(again.play(Event.WIN)); Handler.pending.run(); check(Handler.pending==null);
    ToneGenerator.fail=true; check(!again.play(Event.FAILURE)); check(!again.play(Event.WIN));
    again.onActivityDestroyed(host); check(host.app.registered==0); check(!again.play(Event.WIN));
    System.out.println("sound lifecycle, mute, focus, rate limit and graceful failure: OK");
  }
}'''


class GameSoundTest(unittest.TestCase):
    def test_support_is_backfilled_without_changing_existing_plugin(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp)
            plugin = src / 'app/src/main/java/game/PluginMain.java'
            plugin.parent.mkdir(parents=True)
            plugin.write_text('original game code')
            builder = ApkBuilder(ROOT)
            builder._sync_plugin_support(src)
            self.assertEqual((src / RELATIVE).read_bytes(), (builder.template / RELATIVE).read_bytes())
            self.assertEqual(plugin.read_text(), 'original game code')

    @unittest.skipUnless(shutil.which('javac') and shutil.which('java'), 'JDK required')
    def test_audio_control_and_lifecycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for relative, content in STUBS.items():
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content)
            (root / 'SoundTest.java').write_text(HARNESS)
            sources = [str(p) for p in root.rglob('*.java')]
            sources.append(str(ROOT / 'program_agent/plugin_template' / RELATIVE))
            subprocess.run(['javac', '-d', str(root / 'classes')] + sources, check=True, capture_output=True, text=True)
            run = subprocess.run(['java', '-cp', str(root / 'classes'), 'SoundTest'], check=True, capture_output=True, text=True)
            self.assertIn('graceful failure: OK', run.stdout)
