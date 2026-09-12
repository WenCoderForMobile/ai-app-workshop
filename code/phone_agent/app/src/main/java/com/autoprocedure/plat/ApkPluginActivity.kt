package com.autoprocedure.plat

import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.view.View
import androidx.appcompat.app.AppCompatActivity
import dalvik.system.DexClassLoader

/** Loads a previously validated, read-only internal APK plugin only after tap. */
class ApkPluginActivity : AppCompatActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (!BuildConfig.DEBUG) { finish(); return }
        val info = ApkPluginStore(this).list().firstOrNull {
            it.programId == intent.getStringExtra(PROGRAM_ID) && it.revision == intent.getStringExtra(REVISION)
        } ?: run { finish(); return }
        title = info.title
        runCatching {
            val loader = DexClassLoader(info.file.absolutePath, codeCacheDir.absolutePath, null, classLoader)
            val instance = loader.loadClass(info.entryClass).getDeclaredConstructor().newInstance()
            val createView = instance.javaClass.getMethod("createView", Context::class.java)
            createView.invoke(instance, this) as View
        }.onSuccess { setContentView(it) }.onFailure { finish() }
    }

    companion object {
        private const val PROGRAM_ID = "programId"
        private const val REVISION = "revision"
        fun intent(context: Context, programId: String, revision: String) = Intent(context, ApkPluginActivity::class.java).apply {
            putExtra(PROGRAM_ID, programId)
            putExtra(REVISION, revision)
        }
    }
}
