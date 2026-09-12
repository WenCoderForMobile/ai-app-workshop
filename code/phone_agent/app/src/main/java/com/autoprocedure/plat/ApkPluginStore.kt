package com.autoprocedure.plat

import android.content.Context
import android.util.Base64
import org.json.JSONObject
import java.io.File
import java.security.MessageDigest
import java.util.UUID
import java.util.zip.ZipFile

/** Internal-only APK-plugin repository. Packages are never PackageManager-installed. */
class ApkPluginStore(private val context: Context) {
    private val root = File(context.filesDir, "plugins/apk")

    fun install(base64: String, descriptorJson: String): Result<ApkPluginInfo> = runCatching {
        require(base64.length <= MAX_BASE64_CHARS) { "plugin APK is too large" }
        val descriptor = JSONObject(descriptorJson)
        val id = descriptor.getString("programId")
        val revision = descriptor.getString("revision")
        val entryClass = descriptor.getString("entryClass")
        require(SAFE_ID.matches(id) && SAFE_ID.matches(revision) && ENTRY_CLASS.matches(entryClass)) { "unsafe plugin descriptor" }
        val bytes = Base64.decode(base64, Base64.DEFAULT)
        require(bytes.size in 1..MAX_APK_BYTES) { "plugin APK is too large" }
        require(sha256(bytes) == descriptor.getString("sha256")) { "plugin APK hash mismatch" }
        root.mkdirs()
        val pluginRoot = File(root, sha256(id.toByteArray()).take(32))
        val staging = File(pluginRoot, "staging/${UUID.randomUUID()}").apply { mkdirs() }
        val incoming = File(staging, "plugin.apk").apply { writeBytes(bytes) }
        try {
            validateApk(incoming)
            val version = File(pluginRoot, "versions/$revision").apply { mkdirs() }
            val target = File(version, "plugin.apk")
            if (target.exists()) require(sha256(target.readBytes()) == descriptor.getString("sha256")) { "revision content conflict" }
            else require(incoming.renameTo(target)) { "failed to stage plugin APK" }
            // Android's safer DCL requires code files to be read-only before loading.
            require(target.setReadOnly()) { "cannot mark plugin APK read-only" }
            File(version, "plugin.json").writeText(descriptor.toString())
            ApkPluginInfo(id, revision, descriptor.optString("title", id), descriptor.optString("summary"), entryClass, target)
        } finally {
            staging.deleteRecursively()
        }
    }

    fun list(): List<ApkPluginInfo> = root.listFiles()?.asSequence()?.filter { it.isDirectory }?.flatMap { pluginRoot ->
        val versions = File(pluginRoot, "versions").listFiles()?.asSequence() ?: emptySequence()
        versions.mapNotNull { version -> runCatching {
            val descriptor = JSONObject(File(version, "plugin.json").readText())
            val apk = File(version, "plugin.apk")
            require(apk.isFile && !apk.canWrite()) { "APK is not immutable" }
            ApkPluginInfo(descriptor.getString("programId"), descriptor.getString("revision"), descriptor.optString("title"), descriptor.optString("summary"), descriptor.getString("entryClass"), apk)
        }.getOrNull() }
    }?.toList() ?: emptyList()

    private fun validateApk(file: File) {
        ZipFile(file).use { zip ->
            require(zip.entries().asSequence().any { it.name == "classes.dex" }) { "APK plugin has no dex" }
            require(zip.entries().asSequence().none { it.name.endsWith(".so") }) { "native libraries are forbidden" }
        }
    }

    private fun sha256(bytes: ByteArray) = MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it) }

    companion object {
        private const val MAX_APK_BYTES = 10 * 1024 * 1024
        private const val MAX_BASE64_CHARS = MAX_APK_BYTES * 2
        private val SAFE_ID = Regex("[A-Za-z0-9_-]{1,64}")
        private val ENTRY_CLASS = Regex("[A-Za-z_][A-Za-z0-9_]*(\\.[A-Za-z_][A-Za-z0-9_]*)+")
    }
}

data class ApkPluginInfo(val programId: String, val revision: String, val title: String, val summary: String, val entryClass: String, val file: File)
