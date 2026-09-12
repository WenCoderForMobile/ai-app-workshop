package com.autoprocedure.pluginapi;

import android.app.Activity;
import android.view.ViewGroup;

/**
 * Host-process plugin contract. The plugin APK is not installed.
 * DexClassLoader loads this entry; UI must be built in code (no plugin R.layout).
 */
public interface PluginEntry {
    void onCreate(Activity host, ViewGroup container);

    void onDestroy();
}
