package com.autoprocedure.plat.plugin

import android.content.Context
import com.autoprocedure.pluginapi.PluginEntry
import dalvik.system.DexClassLoader
import java.io.File
import java.util.zip.ZipFile

/**
 * Load an uninstalled plugin APK from the host private data dir into this process.
 */
object PluginLoader {
    private data class Loaded(
        val loader: DexClassLoader,
        val opt: File,
    )

    private val loaders = HashMap<String, Loaded>()

    fun isPluginApk(file: File): Boolean {
        if (!file.isFile || file.length() < 32) return false
        return try {
            ZipFile(file).use { zip ->
                zip.getEntry("classes.dex") != null && !zip.entries().asSequence().any { it.name.endsWith(".so") }
            }
        } catch (_: Exception) {
            false
        }
    }

    fun load(context: Context, apk: File, entryClass: String): PluginEntry {
        require(com.autoprocedure.plat.BuildConfig.DEBUG) { "APK plugins are enabled only in internal debug builds" }
        require(!apk.canWrite()) { "plugin APK must be read-only" }
        if (!isPluginApk(apk)) {
            throw IllegalStateException("not a plugin apk: ${apk.path}")
        }
        val key = apk.absolutePath
        val loaded = loaders.getOrPut(key) {
            val opt = File(
                context.codeCacheDir,
                "plugins/${apk.nameWithoutExtension}-${apk.lastModified()}",
            )
            opt.mkdirs()
            Loaded(
                DexClassLoader(apk.absolutePath, opt.absolutePath, null, context.classLoader),
                opt,
            )
        }
        val cls = loaded.loader.loadClass(entryClass)
        val instance = cls.getDeclaredConstructor().newInstance()
        return instance as PluginEntry
    }

    fun unload(apk: File) {
        val removed = loaders.remove(apk.absolutePath)
        removed?.opt?.deleteRecursively()
    }
}
