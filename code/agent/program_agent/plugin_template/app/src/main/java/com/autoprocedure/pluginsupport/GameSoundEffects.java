package com.autoprocedure.pluginsupport;

import android.app.Activity;
import android.app.Application;
import android.content.Context;
import android.content.SharedPreferences;
import android.media.AudioManager;
import android.media.ToneGenerator;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;

/** Short, optional game feedback. All methods are called on the UI thread. */
public final class GameSoundEffects implements Application.ActivityLifecycleCallbacks {
    public enum Event { TAP, COLLECT, SUCCESS, FAILURE, WIN }

    private Activity host;
    private final Application application;
    private final SharedPreferences preferences;
    private final AudioManager audioManager;
    private final Handler handler = new Handler(Looper.getMainLooper());
    private ToneGenerator tones;
    private boolean enabled;
    private boolean foreground = true;
    private boolean released;
    private boolean unavailable;
    private boolean focused;
    private long lastPlayed = -1000;
    private final Runnable finishTone = this::stop;
    private final AudioManager.OnAudioFocusChangeListener focusListener = change -> {
        // Do not replay an old effect when focus returns.
        if (change != AudioManager.AUDIOFOCUS_GAIN) stop();
    };

    public GameSoundEffects(Activity activity, String programId) {
        host = activity;
        application = activity.getApplication();
        preferences = activity.getSharedPreferences("game_sound_" + programId, Context.MODE_PRIVATE);
        enabled = preferences.getBoolean("enabled", true);
        audioManager = (AudioManager) activity.getSystemService(Context.AUDIO_SERVICE);
        application.registerActivityLifecycleCallbacks(this);
    }

    public boolean isEnabled() { return enabled; }

    public void setEnabled(boolean value) {
        if (released) return;
        enabled = value;
        preferences.edit().putBoolean("enabled", value).apply();
        if (!value) stop();
    }

    /** Returns false when muted, backgrounded, rate-limited or audio unavailable. */
    @SuppressWarnings("deprecation")
    public boolean play(Event event) {
        if (Looper.myLooper() != Looper.getMainLooper() || event == null || released || !enabled
                || unavailable || !foreground || host == null || host.isFinishing()
                || !host.hasWindowFocus() || audioManager == null) return false;
        long now = SystemClock.uptimeMillis();
        // Coalesce high-frequency movement/score callbacks. Important outcome
        // sounds may replace the current short tone instead of piling up.
        if ((event == Event.TAP || event == Event.COLLECT) && now - lastPlayed < 80) return false;
        int tone;
        int duration;
        switch (event) {
            case COLLECT: tone = ToneGenerator.TONE_PROP_ACK; duration = 100; break;
            case SUCCESS: tone = ToneGenerator.TONE_PROP_BEEP2; duration = 140; break;
            case FAILURE: tone = ToneGenerator.TONE_PROP_NACK; duration = 180; break;
            case WIN: tone = ToneGenerator.TONE_DTMF_9; duration = 240; break;
            default: tone = ToneGenerator.TONE_PROP_BEEP; duration = 45;
        }
        try {
            if (!focused) {
                focused = audioManager.requestAudioFocus(focusListener, AudioManager.STREAM_MUSIC,
                        AudioManager.AUDIOFOCUS_GAIN_TRANSIENT_MAY_DUCK) == AudioManager.AUDIOFOCUS_REQUEST_GRANTED;
                if (!focused) return false;
            }
            if (tones == null) tones = new ToneGenerator(AudioManager.STREAM_MUSIC, 25);
            handler.removeCallbacks(finishTone);
            if (!tones.startTone(tone, duration)) {
                stop();
                return false;
            }
            lastPlayed = now;
            handler.postDelayed(finishTone, duration + 30L);
            return true;
        } catch (RuntimeException error) {
            // Native audio failures must not break gameplay or trigger code repair.
            unavailable = true;
            stop();
            disposeTone();
            return false;
        }
    }

    @SuppressWarnings("deprecation")
    public void stop() {
        handler.removeCallbacks(finishTone);
        if (tones != null) {
            try { tones.stopTone(); } catch (RuntimeException ignored) { }
        }
        if (focused && audioManager != null) {
            focused = false;
            try { audioManager.abandonAudioFocus(focusListener); } catch (RuntimeException ignored) { }
        }
    }

    /** Idempotent; call from PluginEntry.onDestroy even though Activity cleanup is automatic. */
    public void release() {
        if (released) return;
        released = true;
        stop();
        disposeTone();
        application.unregisterActivityLifecycleCallbacks(this);
        host = null;
    }

    private void disposeTone() {
        if (tones != null) {
            try { tones.release(); } catch (RuntimeException ignored) { }
            tones = null;
        }
    }

    @Override public void onActivityPaused(Activity activity) {
        if (activity == host) {
            foreground = false;
            stop();
        }
    }
    @Override public void onActivityStopped(Activity activity) {
        if (activity == host) {
            foreground = false;
            stop();
            disposeTone();
        }
    }
    @Override public void onActivityResumed(Activity activity) {
        if (activity == host && !released) foreground = true;
    }
    @Override public void onActivityDestroyed(Activity activity) {
        if (activity == host) release();
    }
    @Override public void onActivityCreated(Activity activity, Bundle state) { }
    @Override public void onActivityStarted(Activity activity) { }
    @Override public void onActivitySaveInstanceState(Activity activity, Bundle state) { }
}
