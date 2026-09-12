package com.autoprocedure.plat.job

import android.content.Context
import com.autoprocedure.plat.plugin.PluginLoader
import com.autoprocedure.plat.PluginStore
import org.json.JSONArray
import org.json.JSONObject
import java.io.File

object JobStore {
    private val items = LinkedHashMap<String, ProgramCard>()
    private val hidden = linkedSetOf<String>()
    private val listeners = mutableListOf<() -> Unit>()
    private var file: File? = null
    private var hiddenFile: File? = null
    private var appContext: Context? = null

    @Synchronized
    fun init(context: Context) {
        if (appContext != null) return
        appContext = context.applicationContext
        file = File(context.filesDir, "jobs.json")
        hiddenFile = File(context.filesDir, "hidden_jobs.json")
        items.clear()
        hidden.clear()
        loadFile()
        loadHidden()
        for (id in hidden) items.remove(id)
        mergeIrPlugins(context)
        notifyChanged()
    }

    @Synchronized
    fun list(): List<ProgramCard> = items.values.toList()

    @Synchronized
    fun get(taskId: String): ProgramCard? = items[taskId]

    @Synchronized
    fun upsert(card: ProgramCard) {
        val key = card.taskId.ifBlank { card.programId.ifBlank { card.packageName } }
        if (key.isBlank()) return
        if (key in hidden) return
        val previous = items[key]
        if (previous?.state in setOf(ProgramCard.STATE_READY, ProgramCard.STATE_DOWNLOADING, ProgramCard.STATE_INSTALLING) &&
            card.state == ProgramCard.STATE_PENDING_SYNC && previous?.sha256 == card.sha256 &&
            previous.state != ProgramCard.STATE_READY) return
        if (previous?.state == ProgramCard.STATE_READY && card.state in setOf(ProgramCard.STATE_DOWNLOADING, ProgramCard.STATE_PENDING_SYNC) &&
            previous.sha256 == card.sha256 && File(previous.apkPath).isFile) return
        val merged = if (previous == null) {
            card
        } else {
            card.copy(
                apkPath = card.apkPath.ifBlank { previous.apkPath },
                downloadUrl = card.downloadUrl.ifBlank { previous.downloadUrl },
                packageName = card.packageName.ifBlank { previous.packageName },
                entryClass = card.entryClass.ifBlank { previous.entryClass },
                icon = card.icon.ifBlank { previous.icon },
            )
        }
        items[key] = merged
        persist()
        notifyChanged()
    }

    @Synchronized
    fun restoreForSync(card: ProgramCard) {
        if (hidden.remove(card.taskId)) persistHidden()
        upsert(card)
    }

    @Synchronized
    fun updateState(taskId: String, state: String, extra: ProgramCard.() -> ProgramCard = { this }) {
        val current = items[taskId] ?: return
        items[taskId] = extra(current.copy(state = state))
        persist()
        notifyChanged()
    }

    @Synchronized
    fun remove(card: ProgramCard) {
        val key = card.taskId.ifBlank { card.programId.ifBlank { card.packageName } }
        if (key.isBlank()) return
        items.remove(key)
        hidden.add(key)
        persist()
        persistHidden()
        val ctx = appContext
        notifyChanged()
        if (ctx != null) {
            deleteLocalFiles(ctx, card)
        }
    }

    fun addListener(listener: () -> Unit) {
        synchronized(this) { listeners.add(listener) }
    }

    fun removeListener(listener: () -> Unit) {
        synchronized(this) { listeners.remove(listener) }
    }

    private fun deleteLocalFiles(context: Context, card: ProgramCard) {
        if (card.apkPath.isNotBlank()) {
            PluginLoader.unload(File(card.apkPath))
        }
        val pluginRoot = File(context.filesDir, "apk-tasks")
        val taskDir = File(pluginRoot, taskKey(card.taskId))
        val rootPath = pluginRoot.canonicalPath + File.separator
        if (card.taskId.isNotBlank() && taskDir.canonicalPath.startsWith(rootPath)) {
            PluginLoader.unload(File(taskDir, "plugin.apk"))
            taskDir.deleteRecursively()
        }
        if (card.launchType == ProgramCard.LAUNCH_IR && card.programId.isNotBlank()) {
            PluginStore(context).uninstall(card.programId)
        }
    }

    fun mergeIrPlugins(context: Context) {
        for (program in PluginStore(context).list()) {
            val id = "ir-" + program.programId
            if (items.containsKey(id) || hidden.contains(id)) continue
            items[id] = ProgramCard(
                taskId = id,
                title = program.title,
                summary = program.versionId,
                icon = "💧",
                state = ProgramCard.STATE_READY,
                packageName = "",
                downloadUrl = "",
                launchType = ProgramCard.LAUNCH_IR,
                programId = program.programId,
                apkPath = "",
                entryClass = "",
                error = "",
            )
        }
    }

    fun taskKey(taskId: String): String = java.security.MessageDigest.getInstance("SHA-256")
        .digest(taskId.toByteArray()).joinToString("") { "%02x".format(it) }

    private fun loadFile() {
        val src = file ?: return
        if (!src.isFile) return
        try {
            val arr = JSONArray(src.readText())
            for (i in 0 until arr.length()) {
                val obj = arr.optJSONObject(i) ?: continue
                val card = ProgramCard.parse(obj)
                val key = card.taskId.ifBlank { card.programId }
                if (key.isNotBlank()) items[key] = card
            }
        } catch (_: Exception) {
        }
    }

    private fun persist() {
        val dest = file ?: return
        val arr = JSONArray()
        for (card in items.values) {
            if (card.launchType == ProgramCard.LAUNCH_IR) continue
            arr.put(card.toJson())
        }
        dest.writeText(arr.toString())
    }

    private fun loadHidden() {
        val src = hiddenFile ?: return
        if (!src.isFile) return
        try {
            val arr = JSONArray(src.readText())
            for (i in 0 until arr.length()) {
                val id = arr.optString(i)
                if (id.isNotBlank()) hidden.add(id)
            }
        } catch (_: Exception) {
        }
    }

    private fun persistHidden() {
        val dest = hiddenFile ?: return
        val arr = JSONArray()
        for (id in hidden) arr.put(id)
        dest.writeText(arr.toString())
    }

    private fun notifyChanged() {
        val copy = synchronized(this) { listeners.toList() }
        copy.forEach { it() }
    }
}
