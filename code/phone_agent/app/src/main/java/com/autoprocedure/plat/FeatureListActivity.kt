package com.autoprocedure.plat

import android.graphics.Color
import android.graphics.drawable.GradientDrawable
import android.os.Bundle
import android.view.View
import android.widget.LinearLayout
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/** Second sheet: every delivered program stays visible with an explicit state. */
class FeatureListActivity : AppCompatActivity() {
    private lateinit var agentSession: AgentSession
    private val sessionListener = AgentSessionListener { snapshot ->
        if (snapshot.artifactGeneration != observedArtifactGeneration) {
            observedArtifactGeneration = snapshot.artifactGeneration
            renderedKey = null
            renderPrograms()
        }
    }
    private var renderedKey: String? = null
    private var observedArtifactGeneration = -1L
    private var renderRequest = 0L

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        title = getString(R.string.program_square_title)
        agentSession = AgentSession.get(applicationContext)
        renderPrograms()
    }

    override fun onStart() {
        super.onStart()
        agentSession.addListener(sessionListener)
    }

    override fun onResume() {
        super.onResume()
        renderedKey = null
        renderPrograms()
    }

    override fun onStop() {
        agentSession.removeListener(sessionListener)
        super.onStop()
    }

    private fun renderPrograms() {
        val request = ++renderRequest
        lifecycleScope.launch {
            val (declarative, apkPlugins) = withContext(Dispatchers.IO) {
                PluginStore(this@FeatureListActivity).list() to ApkPluginStore(this@FeatureListActivity).list()
            }
            if (request != renderRequest) return@launch
            renderPrograms(declarative, apkPlugins)
        }
    }

    private fun renderPrograms(declarative: List<PluginInfo>, apkPlugins: List<ApkPluginInfo>) {
        val key = declarative.joinToString("|") { "d:${it.artifactId}:${it.revision}" } +
            apkPlugins.joinToString("|") { "a:${it.programId}:${it.revision}" }
        if (key == renderedKey) return
        renderedKey = key
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(20, 20, 20, 20)
        }
        if (declarative.isEmpty() && apkPlugins.isEmpty()) {
            root.addView(TextView(this).apply { text = "还没有程序。完成需求确认后，这里会显示制作、下载、安装和可运行状态。" })
        }
        declarative.forEach { info ->
            root.addView(card("▣", info.title, "声明式插件 · ${info.versionId}", "完成（可运行）", true) {
                startActivity(PluginActivity.intent(this, info.programId, info.versionId))
            })
        }
        apkPlugins.forEach { info ->
            root.addView(card("◆", info.title, info.summary.ifBlank { "宿主进程 APK 插件 · ${info.revision}" }, "完成（可运行）", true) {
                startActivity(ApkPluginActivity.intent(this, info.programId, info.revision))
            })
        }
        setContentView(root)
    }

    private fun card(icon: String, name: String, summary: String, status: String, runnable: Boolean, onClick: () -> Unit): View {
        val row = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            setPadding(20, 18, 20, 18)
            background = GradientDrawable().apply {
                setColor(if (runnable) Color.WHITE else Color.rgb(235, 235, 235))
                cornerRadius = 12f
                setStroke(1, Color.rgb(210, 210, 210))
            }
            isEnabled = runnable
            alpha = if (runnable) 1f else 0.45f
            setOnClickListener { if (runnable) onClick() }
        }
        row.addView(TextView(this).apply { text = icon; textSize = 28f; setPadding(0, 0, 18, 0) })
        row.addView(LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            addView(TextView(this@FeatureListActivity).apply { text = name; textSize = 17f })
            addView(TextView(this@FeatureListActivity).apply { text = summary; textSize = 13f; setTextColor(Color.DKGRAY) })
            addView(TextView(this@FeatureListActivity).apply { text = status; textSize = 13f; setTextColor(if (runnable) Color.rgb(0, 120, 70) else Color.GRAY) })
        })
        return row
    }
}
