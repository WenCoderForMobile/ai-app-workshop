package com.autoprocedure.plugin.memoryqa;

import android.app.Activity;
import android.os.Handler;
import android.os.Looper;
import android.view.ViewGroup;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.TextView;
import com.autoprocedure.pluginapi.PluginEntry;
import java.util.ArrayList;
import java.util.Collections;

/** Offline QA fixture: four pairs, turn counting, reset, delayed mismatch cleanup. */
public class PluginMain implements PluginEntry {
    private final Handler handler = new Handler(Looper.getMainLooper());
    private final Button[] cards = new Button[8];
    private final int[] values = new int[8];
    private final boolean[] matched = new boolean[8];
    private TextView status;
    private int first = -1, turns = 0, pairs = 0;
    private boolean locked = false;

    public void onCreate(Activity host, ViewGroup container) {
        LinearLayout root = new LinearLayout(host);
        root.setOrientation(LinearLayout.VERTICAL);
        status = new TextView(host);
        status.setTextSize(22);
        root.addView(status);
        for (int row = 0; row < 2; row++) {
            LinearLayout line = new LinearLayout(host);
            for (int col = 0; col < 4; col++) {
                final int index = row * 4 + col;
                cards[index] = new Button(host);
                cards[index].setOnClickListener(v -> flip(index));
                line.addView(cards[index], new LinearLayout.LayoutParams(0, 100, 1));
            }
            root.addView(line);
        }
        Button reset = new Button(host);
        reset.setText("重新开始");
        reset.setOnClickListener(v -> reset());
        root.addView(reset);
        container.addView(root);
        reset();
    }

    private void reset() {
        handler.removeCallbacksAndMessages(null);
        ArrayList<Integer> deck = new ArrayList<>();
        for (int i = 0; i < 8; i++) deck.add(i / 2 + 1);
        Collections.shuffle(deck);
        first = -1; turns = 0; pairs = 0; locked = false;
        for (int i = 0; i < 8; i++) {
            values[i] = deck.get(i); matched[i] = false;
            cards[i].setText("?"); cards[i].setEnabled(true);
        }
        refresh();
    }

    private void flip(final int index) {
        if (locked || matched[index] || first == index) return;
        cards[index].setText(String.valueOf(values[index]));
        if (first < 0) { first = index; return; }
        final int previous = first;
        first = -1; turns++;
        if (values[previous] == values[index]) {
            matched[previous] = matched[index] = true;
            cards[previous].setEnabled(false); cards[index].setEnabled(false);
            pairs++;
        } else {
            locked = true;
            handler.postDelayed(() -> {
                cards[previous].setText("?"); cards[index].setText("?"); locked = false;
            }, 700);
        }
        refresh();
    }

    private void refresh() {
        status.setText(pairs == 4 ? "全部配对成功！步数：" + turns : "翻牌配对 " + pairs + "/4 · 步数 " + turns);
    }

    public void onDestroy() { handler.removeCallbacksAndMessages(null); }
}
