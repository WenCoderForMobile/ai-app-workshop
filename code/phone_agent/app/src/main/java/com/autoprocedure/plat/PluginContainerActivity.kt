package com.autoprocedure.plat

import android.os.Bundle
import android.os.Looper
import android.widget.FrameLayout
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import com.autoprocedure.pluginapi.PluginEntry
import com.autoprocedure.plat.job.JobStore
import com.autoprocedure.plat.job.ProgramCard
import com.autoprocedure.plat.plugin.PluginErrorReporter
import com.autoprocedure.plat.plugin.PluginLoader
import java.io.File
import java.util.concurrent.atomic.AtomicBoolean

class PluginContainerActivity : AppCompatActivity() {
    companion object {
        const val EXTRA_TASK_ID = "taskId"
    }

    private var entry: PluginEntry? = null
    private var card: ProgramCard? = null
    private var previousHandler: Thread.UncaughtExceptionHandler? = null
    private val reported = AtomicBoolean(false)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_plugin_container)
        AgentSession.get(applicationContext)
        val taskId = intent.getStringExtra(EXTRA_TASK_ID).orEmpty()
        val loadedCard = JobStore.get(taskId)
        if (loadedCard == null || !loadedCard.clickable) {
            Toast.makeText(this, R.string.plugin_missing, Toast.LENGTH_SHORT).show()
            finish()
            return
        }
        val apk = File(loadedCard.apkPath)
        if (!apk.isFile) {
            Toast.makeText(this, R.string.plugin_file_missing, Toast.LENGTH_SHORT).show()
            finish()
            return
        }
        card = loadedCard
        title = loadedCard.title
        installCrashGuard(loadedCard)
        val container = findViewById<FrameLayout>(R.id.plugin_container)
        try {
            val plugin = PluginLoader.load(this, apk, loadedCard.entryClass)
            try {
                plugin.onCreate(this, container)
                entry = plugin
            } catch (e: Exception) {
                failAndReport(loadedCard, "onCreate", e)
            }
        } catch (e: Exception) {
            failAndReport(loadedCard, "load", e)
        }
    }

    override fun onDestroy() {
        try {
            entry?.onDestroy()
        } catch (e: Exception) {
            val current = card
            if (current != null && reported.compareAndSet(false, true)) {
                PluginErrorReporter.report(current, "onDestroy", e)
            }
        }
        Thread.setDefaultUncaughtExceptionHandler(previousHandler)
        entry = null
        super.onDestroy()
    }

    private fun installCrashGuard(current: ProgramCard) {
        previousHandler = Thread.getDefaultUncaughtExceptionHandler()
        Thread.setDefaultUncaughtExceptionHandler { thread, error ->
            if (reported.compareAndSet(false, true)) {
                PluginErrorReporter.report(current, "runtime", error)
            }
            // A crashed main looper cannot safely resume. Preserve Android's handler.
            previousHandler?.uncaughtException(thread, error)
        }
    }

    private fun failAndReport(current: ProgramCard, phase: String, error: Exception) {
        if (reported.compareAndSet(false, true)) {
            val uploaded = PluginErrorReporter.report(current, phase, error)
            Toast.makeText(
                this,
                getString(
                    if (uploaded) R.string.plugin_error_uploaded else R.string.plugin_load_failed,
                    error.message ?: "",
                ),
                Toast.LENGTH_LONG,
            ).show()
        }
        finish()
    }
}
