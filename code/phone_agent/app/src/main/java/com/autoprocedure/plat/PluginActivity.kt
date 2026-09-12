package com.autoprocedure.plat

import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.text.Editable
import android.text.InputType
import android.text.TextWatcher
import android.view.View
import android.widget.Button
import android.widget.CheckBox
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import org.json.JSONArray
import org.json.JSONObject

/** Fixed interpreter for validated ProgramIR. It never loads generated code. */
class PluginActivity : AppCompatActivity() {
    private lateinit var program: JSONObject
    private lateinit var store: PluginStore
    private lateinit var pluginInfo: PluginInfo
    private lateinit var preferences: android.content.SharedPreferences
    private val state = mutableMapOf<String, Any?>()
    private var screenId = ""

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        store = PluginStore(this)
        pluginInfo = store.list().firstOrNull {
            it.programId == intent.getStringExtra(PROGRAM_ID) && it.versionId == intent.getStringExtra(VERSION_ID)
        } ?: run { finish(); return }
        program = store.load(pluginInfo)
        title = program.optString("title", pluginInfo.title)
        preferences = getSharedPreferences("program-state-${pluginInfo.programId}", MODE_PRIVATE)
        loadState()
        screenId = program.getJSONArray("screens").getJSONObject(0).getString("id")
        currentScreen().optJSONObject("onLoad")?.let { dispatch(it, rerender = false) }
        renderCurrentScreen()
    }

    private fun loadState() {
        val descriptors = program.getJSONObject("state")
        descriptors.keys().forEach { key ->
            val descriptor = descriptors.getJSONObject(key)
            val type = descriptor.getString("type")
            val local = descriptor.optString("persistence", "session") == "local"
            val initial = descriptor.get("initial")
            state[key] = if (!local || !preferences.contains(key)) initial else when (type) {
                "bool" -> preferences.getBoolean(key, initial as Boolean)
                "int64" -> preferences.getLong(key, (initial as Number).toLong())
                "string" -> preferences.getString(key, initial as String) ?: initial
                "list" -> runCatching { JSONArray(preferences.getString(key, initial.toString())) }.getOrDefault(initial)
                else -> initial
            }
        }
    }

    private fun renderCurrentScreen() {
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(24, 24, 24, 24)
        }
        renderNodes(root, currentScreen().getJSONArray("children"))
        setContentView(root)
    }

    private fun currentScreen(): JSONObject {
        val screens = program.getJSONArray("screens")
        for (index in 0 until screens.length()) {
            val screen = screens.getJSONObject(index)
            if (screen.getString("id") == screenId) return screen
        }
        return screens.getJSONObject(0)
    }

    private fun renderNodes(parent: LinearLayout, nodes: JSONArray) {
        for (index in 0 until nodes.length()) {
            val node = nodes.getJSONObject(index)
            val view: View = when (node.getString("type")) {
                "Text" -> TextView(this).apply {
                    text = node.getString("text")
                    textSize = 18f
                    setPadding(0, 12, 0, 12)
                    bindClick(node, this)
                }
                "Button" -> Button(this).apply {
                    text = node.getString("text")
                    setOnClickListener { node.optJSONObject("onClick")?.let { dispatch(it) } }
                }
                "Checkbox" -> CheckBox(this).apply {
                    text = node.getString("text")
                    val key = node.getString("stateKey")
                    isChecked = state[key] as? Boolean ?: false
                    setOnCheckedChangeListener { _, checked ->
                        setState(key, checked)
                        node.optJSONObject("onChange")?.let { dispatch(it) }
                        node.optJSONObject("onClick")?.let { dispatch(it) }
                    }
                }
                "TextInput" -> EditText(this).apply {
                    val key = node.getString("stateKey")
                    hint = node.optString("hint")
                    inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_MULTI_LINE
                    setText(state[key] as? String ?: "")
                    addTextChangedListener(object : TextWatcher {
                        override fun beforeTextChanged(s: CharSequence?, start: Int, count: Int, after: Int) = Unit
                        override fun onTextChanged(s: CharSequence?, start: Int, before: Int, count: Int) = Unit
                        override fun afterTextChanged(s: Editable?) {
                            setState(key, s?.toString() ?: "")
                            node.optJSONObject("onChange")?.let { dispatch(it, rerender = false) }
                        }
                    })
                }
                "Spacer" -> View(this).apply { minimumHeight = 16 }
                "Row", "Column", "List", "Dialog" -> LinearLayout(this).apply {
                    orientation = if (node.getString("type") == "Row") LinearLayout.HORIZONTAL else LinearLayout.VERTICAL
                    renderNodes(this, node.optJSONArray("children") ?: JSONArray())
                }
                // ProgramAgent currently rejects image resources until a
                // resource packer is added. This defensive fallback still
                // avoids interpreting a URI as an external URL.
                "Image" -> TextView(this).apply { text = "[包内图片]" }
                else -> TextView(this).apply { text = "[不支持的组件]" }
            }
            parent.addView(view)
        }
    }

    private fun bindClick(node: JSONObject, view: View) {
        node.optJSONObject("onClick")?.let { action ->
            view.isClickable = true
            view.setOnClickListener { dispatch(action) }
        }
    }

    private fun dispatch(action: JSONObject, rerender: Boolean = true) {
        when (action.getString("type")) {
            "setState" -> {
                setState(action.getString("key"), action.get("value"))
                if (rerender) renderCurrentScreen()
            }
            "showMessage" -> Toast.makeText(this, action.getString("message"), Toast.LENGTH_SHORT).show()
            "finish" -> finish()
            "navigate" -> {
                screenId = action.getString("screenId")
                if (rerender) renderCurrentScreen()
            }
        }
    }

    private fun setState(key: String, value: Any?) {
        state[key] = value
        val descriptor = program.getJSONObject("state").getJSONObject(key)
        if (descriptor.optString("persistence", "session") != "local") return
        val editor = preferences.edit()
        when (descriptor.getString("type")) {
            "bool" -> editor.putBoolean(key, value as Boolean)
            "int64" -> editor.putLong(key, (value as Number).toLong())
            "string" -> editor.putString(key, value as String)
            "list" -> editor.putString(key, value.toString())
        }
        editor.apply()
    }

    companion object {
        private const val PROGRAM_ID = "programId"
        private const val VERSION_ID = "versionId"
        fun intent(context: Context, programId: String, versionId: String) = Intent(context, PluginActivity::class.java).apply {
            putExtra(PROGRAM_ID, programId)
            putExtra(VERSION_ID, versionId)
        }
    }
}
