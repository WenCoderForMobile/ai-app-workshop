package com.autoprocedure.plat.job

import org.json.JSONObject

data class ProgramCard(
    val taskId: String,
    val title: String,
    val summary: String,
    val icon: String,
    val state: String,
    val packageName: String,
    val downloadUrl: String,
    val launchType: String,
    val programId: String,
    val apkPath: String,
    val entryClass: String,
    val error: String,
    val sha256: String = "",
    val size: Long = 0,
) {
    val clickable: Boolean
        get() = state == STATE_READY

    val needsSync: Boolean
        get() = launchType == LAUNCH_APK && downloadUrl.isNotBlank() &&
            state in setOf(STATE_PENDING_SYNC, STATE_FAILED)

    val needsStatusCheck: Boolean
        get() = launchType == LAUNCH_APK && (state in setOf(STATE_MAKING,
            STATE_PENDING_SYNC, STATE_DOWNLOADING, STATE_INSTALLING) || needsSync)

    fun awaitingSync(): ProgramCard = if (state in setOf(STATE_DOWNLOADING, STATE_INSTALLING))
        copy(state = STATE_PENDING_SYNC) else this

    val stateLabel: String
        get() = when (state) {
            STATE_MAKING -> "正在制作"
            STATE_PENDING_SYNC -> "等待同步"
            STATE_DOWNLOADING -> "正在下载"
            STATE_INSTALLING -> "正在安装"
            STATE_READY -> "完成（可运行）"
            STATE_FAILED -> "不可运行"
            else -> state
        }

    fun toJson(): JSONObject {
        return JSONObject()
            .put("taskId", taskId)
            .put("title", title)
            .put("summary", summary)
            .put("icon", icon)
            .put("state", state)
            .put("packageName", packageName)
            .put("downloadUrl", downloadUrl)
            .put("launchType", launchType)
            .put("programId", programId)
            .put("apkPath", apkPath)
            .put("entryClass", entryClass)
            .put("error", error)
            .put("sha256", sha256)
            .put("size", size)
    }

    companion object {
        const val STATE_MAKING = "making"
        const val STATE_PENDING_SYNC = "pending_sync"
        const val STATE_DOWNLOADING = "downloading"
        const val STATE_INSTALLING = "installing"
        const val STATE_READY = "ready"
        const val STATE_FAILED = "failed"

        const val LAUNCH_APK = "apk"
        const val LAUNCH_IR = "ir"

        fun parse(text: String): ProgramCard = parse(JSONObject(text))

        fun parse(raw: JSONObject): ProgramCard {
            return ProgramCard(
                taskId = raw.optString("taskId"),
                title = raw.optString("title"),
                summary = raw.optString("summary"),
                icon = raw.optString("icon", "📦"),
                state = raw.optString("state", STATE_MAKING),
                packageName = raw.optString("packageName"),
                downloadUrl = raw.optString("downloadUrl"),
                launchType = raw.optString("launchType", LAUNCH_APK),
                programId = raw.optString("programId"),
                apkPath = raw.optString("apkPath"),
                entryClass = raw.optString("entryClass"),
                error = raw.optString("error"),
                sha256 = raw.optString("sha256"),
                size = raw.optLong("size"),
            )
        }
    }
}
