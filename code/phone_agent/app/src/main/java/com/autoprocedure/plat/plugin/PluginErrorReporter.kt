package com.autoprocedure.plat.plugin

import android.os.Build
import android.util.Log
import com.autoprocedure.plat.AgentSession
import com.autoprocedure.plat.job.JobStore
import com.autoprocedure.plat.job.ProgramCard
import org.json.JSONObject
import java.io.PrintWriter
import java.io.StringWriter
import java.util.concurrent.atomic.AtomicLong

/**
 * Capture plugin load/run failures and send them to the PC so the program
 * agent can patch the existing PluginMain instead of rewriting.
 */
object PluginErrorReporter {
    private const val TAG = "PluginErrorReporter"
    private const val STACK_LIMIT = 8000
    private const val DEBOUNCE_MS = 12_000L

    private val lastSentAt = AtomicLong(0L)
    private var lastKey = ""

    fun report(card: ProgramCard, phase: String, error: Throwable): Boolean {
        val key = "${card.taskId}|$phase|${error.javaClass.name}|${error.message}"
        val now = System.currentTimeMillis()
        synchronized(this) {
            if (key == lastKey && now - lastSentAt.get() < DEBOUNCE_MS) {
                return false
            }
            lastKey = key
            lastSentAt.set(now)
        }
        JobStore.updateState(card.taskId, ProgramCard.STATE_FAILED) {
            copy(error = error.message ?: phase)
        }
        val stack = stackTraceOf(error)
        Log.e(TAG, "plugin $phase failed task=${card.taskId}", error)
        val payload = JSONObject()
            .put("taskId", card.taskId)
            .put("title", card.title)
            .put("phase", phase)
            .put("entryClass", card.entryClass)
            .put("exceptionClass", error.javaClass.name)
            .put("message", error.message ?: "")
            .put("stackTrace", stack)
            .put("sdkInt", Build.VERSION.SDK_INT)
            .put("at", now)
        return try {
            val sent = AgentSession.current()?.sendRuntimeError(payload.toString())
            sent == true
        } catch (sendError: Exception) {
            Log.e(TAG, "upload runtime error failed", sendError)
            false
        }
    }

    private fun stackTraceOf(error: Throwable): String {
        val writer = StringWriter()
        error.printStackTrace(PrintWriter(writer))
        val text = writer.toString()
        return if (text.length <= STACK_LIMIT) text else text.substring(0, STACK_LIMIT)
    }
}
