package com.autoprocedure.pluginapi;

import android.app.Activity;
import android.view.ViewGroup;

public interface PluginEntry {
    void onCreate(Activity host, ViewGroup container);

    void onDestroy();
}
