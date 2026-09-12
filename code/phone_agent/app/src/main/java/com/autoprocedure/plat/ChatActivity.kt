package com.autoprocedure.plat

import android.os.Bundle
import androidx.appcompat.app.AppCompatActivity

/** Legacy entry point shares the same chat UI and process-scoped session. */
class ChatActivity : AppCompatActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_chat)
    }
}
