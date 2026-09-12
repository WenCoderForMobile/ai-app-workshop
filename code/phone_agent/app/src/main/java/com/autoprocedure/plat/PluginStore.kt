package com.autoprocedure.plat

import android.content.Context
import android.util.Base64
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.io.FileInputStream
import java.security.MessageDigest
import java.util.UUID
import java.util.zip.ZipEntry
import java.util.zip.ZipFile

/**
 * The only path from an untrusted transport package to the fixed Runtime.
 *
 * Packages are JSON/data only.  They are staged, validated and then moved to a
 * private version directory.  PluginActivity only accepts [PluginInfo] from
 * this store; it never loads code, classes, scripts, or an arbitrary path.
 */
class PluginStore(private val context: Context) {
    private val root = File(context.filesDir, "plugins")

    fun install(base64: String): Result<PluginInfo> = runCatching {
        require(base64.length <= MAX_BASE64_CHARS) { "package too large" }
        val bytes = Base64.decode(base64, Base64.DEFAULT)
        require(bytes.size <= MAX_PACKAGE_BYTES) { "package too large" }
        installStaged(descriptor = null) { incoming -> incoming.writeBytes(bytes) }
    }

    /** Installs a streamed download and binds every identity field to its job descriptor. */
    fun install(packageFile: File, descriptor: ArtifactDescriptor): Result<PluginInfo> = runCatching {
        require(packageFile.isFile && packageFile.length() == descriptor.size) { "downloaded package size mismatch" }
        require(packageFile.length() in 1..MAX_PACKAGE_BYTES) { "package too large" }
        require(sha256File(packageFile) == descriptor.transportSha256) { "transport hash changed before install" }
        installStaged(descriptor) { incoming ->
            packageFile.inputStream().buffered().use { input ->
                incoming.outputStream().buffered().use { output -> input.copyTo(output) }
            }
        }
    }

    private fun installStaged(
        descriptor: ArtifactDescriptor?,
        populate: (File) -> Unit,
    ): PluginInfo {
        root.mkdirs()
        val token = UUID.randomUUID().toString()
        val staging = File(root, ".staging/$token")
        staging.mkdirs()
        val incoming = File(staging, "package.apkg")
        return try {
            populate(incoming)
            if (descriptor != null) {
                require(incoming.length() == descriptor.size) { "staged package size mismatch" }
                require(sha256File(incoming) == descriptor.transportSha256) { "staged transport hash mismatch" }
            }
            val info = validate(incoming)
            if (descriptor != null) {
                require(info.taskId == descriptor.taskId) { "task binding mismatch" }
                require(info.artifactId == descriptor.artifactId) { "artifact binding mismatch" }
                require(info.programId == descriptor.programId) { "program binding mismatch" }
                require(info.versionId == descriptor.versionId) { "version binding mismatch" }
                require(info.revision == descriptor.revision) { "revision binding mismatch" }
                require(info.manifestSha256 == descriptor.manifestSha256) { "manifest hash mismatch" }
            }
            val pluginRoot = File(root, pluginKey(info.programId))
            val versionRoot = File(pluginRoot, "versions/${info.revision}")
            versionRoot.mkdirs()
            val target = File(versionRoot, "package.apkg")
            if (target.exists()) {
                require(sha256File(target) == sha256File(incoming)) { "version already exists with different content" }
                incoming.delete()
            } else {
                require(incoming.renameTo(target)) { "atomic install failed" }
            }
            writeAtomic(File(pluginRoot, "active"), info.revision)
            info.copy(file = target)
        } catch (error: Throwable) {
            quarantine(staging, token)
            throw error
        } finally {
            staging.deleteRecursively()
        }
    }

    /** Returns one active, validated version for each installed program. */
    fun uninstall(programId: String) {
        File(root, pluginKey(programId)).deleteRecursively()
    }

    fun list(): List<PluginInfo> {
        if (!root.exists()) return emptyList()
        return root.listFiles()
            ?.asSequence()
            ?.filter { it.isDirectory && !it.name.startsWith(".") }
            ?.mapNotNull { programRoot ->
                runCatching {
                    val revision = File(programRoot, "active").readText().trim()
                    require(SAFE_REVISION.matches(revision)) { "unsafe active revision" }
                    val packageFile = File(programRoot, "versions/$revision/package.apkg")
                    val info = validate(packageFile)
                    require(info.revision == revision) { "active revision binding mismatch" }
                    info.copy(file = packageFile)
                }.getOrNull()
            }
            ?.sortedBy { it.title }
            ?.toList()
            ?: emptyList()
    }

    fun load(info: PluginInfo): JSONObject {
        val verified = validate(info.file)
        require(verified.programId == info.programId && verified.revision == info.revision) { "plugin identity changed" }
        return ZipFile(info.file).use { zip ->
            zip.getInputStream(zip.getEntry("program/main.json")).bufferedReader().use { JSONObject(it.readText()) }
        }
    }

    private fun validate(file: File): PluginInfo {
        require(file.isFile && file.length() in 1..MAX_PACKAGE_BYTES) { "package is missing or too large" }
        ZipFile(file).use { zip ->
            val entries = zip.entries().asSequence().toList()
            require(entries.size in 3..MAX_ENTRIES) { "unsafe entry count" }
            val names = mutableSetOf<String>()
            var totalUncompressed = 0L
            entries.forEach { entry ->
                validateZipEntry(entry, names)
                totalUncompressed += entry.size
                require(totalUncompressed <= MAX_UNCOMPRESSED_BYTES) { "package expands too large" }
            }
            require(names.containsAll(REQUIRED_ENTRIES)) { "invalid package layout" }
            require(names.none { it.endsWith(".dex") || it.endsWith(".apk") || it.endsWith(".so") || it.endsWith(".jar") || it.endsWith(".class") || it.endsWith(".js") || it.endsWith(".sh") }) { "executable package entries are forbidden" }

            val manifestBytes = readEntryBytes(zip, "manifest.json", MAX_JSON_BYTES)
            val manifest = JSONObject(manifestBytes.toString(Charsets.UTF_8))
            requireOnly(manifest, MANIFEST_FIELDS)
            require(manifest.optInt("formatVersion") == 1) { "unsupported package format" }
            require(manifest.optString("runtime") == RUNTIME_ID) { "incompatible runtime" }
            val debugUnsigned = manifest.optBoolean("debugUnsigned", false)
            if (debugUnsigned) {
                require(BuildConfig.DEBUG) { "unsigned development package rejected by release app" }
                require(manifest.optString("algorithm") == "none" && manifest.optString("keyId") == "debug-unsigned") { "invalid debug signature marker" }
                require(!names.contains("signatures/manifest.cose")) { "debug package cannot contain a production signature" }
            } else {
                // The release trust-store/COSE verifier is intentionally a
                // hard gate. Never treat presence of a signature as validity.
                require(names.contains("signatures/manifest.cose")) { "signature is missing" }
                throw IllegalArgumentException("COSE verification is unavailable in this debug prototype")
            }
            val programId = manifest.optString("programId")
            val versionId = manifest.optString("versionId")
            val revision = manifest.optString("revision")
            val taskId = manifest.optString("taskId")
            val artifactId = manifest.optString("artifactId")
            require(SAFE_PROGRAM_ID.matches(programId) && SAFE_REVISION.matches(versionId) && SAFE_REVISION.matches(revision)) { "unsafe manifest identity" }
            require(SAFE_META_ID.matches(taskId) && SAFE_META_ID.matches(artifactId)) { "unsafe artifact identity" }
            require(manifest.optInt("securityEpoch") >= 1) { "invalid security epoch" }
            require(SHA256.matches(manifest.optString("approvedProposalDigest"))) { "invalid approval digest" }

            val files = manifest.optJSONArray("files") ?: error("manifest files missing")
            require(files.length() in 2..MAX_ENTRIES) { "invalid manifest file list" }
            val declared = mutableSetOf<String>()
            for (index in 0 until files.length()) {
                val descriptor = files.getJSONObject(index)
                requireOnly(descriptor, FILE_FIELDS)
                val path = descriptor.optString("path")
                require(path in names && path != "manifest.json" && declared.add(path)) { "invalid manifest file path" }
                require(descriptor.optLong("size") >= 0 && descriptor.optLong("size") == zip.getEntry(path).size) { "file size mismatch" }
                require(SHA256_RAW.matches(descriptor.optString("sha256"))) { "invalid file hash" }
                require(sha256Entry(zip, zip.getEntry(path)) == descriptor.getString("sha256")) { "file hash mismatch" }
            }
            require(declared == names - "manifest.json") { "package contains undeclared files" }

            val program = JSONObject(readEntry(zip, "program/main.json", MAX_JSON_BYTES))
            validateProgram(program, manifest)
            val smoke = JSONObject(readEntry(zip, "tests/smoke.json", MAX_JSON_BYTES))
            runSmoke(program, smoke)
            return PluginInfo(
                programId = programId,
                title = program.getString("title"),
                versionId = versionId,
                revision = revision,
                file = file,
                taskId = taskId,
                artifactId = artifactId,
                manifestSha256 = sha256(manifestBytes),
            )
        }
    }

    private fun validateZipEntry(entry: ZipEntry, names: MutableSet<String>) {
        require(!entry.isDirectory) { "directory entries are not allowed" }
        val name = entry.name
        require(name.isNotBlank() && name == name.replace('\\', '/') && !name.startsWith("/") && !name.contains("..") && names.add(name)) { "unsafe zip path" }
        require(entry.size >= 0 && entry.compressedSize >= 0 && entry.size <= MAX_SINGLE_ENTRY_BYTES) { "invalid zip size" }
        if (entry.size > 0) require(entry.compressedSize > 0 && entry.size <= entry.compressedSize * MAX_COMPRESSION_RATIO) { "unsafe compression ratio" }
    }

    private fun validateProgram(program: JSONObject, manifest: JSONObject) {
        requireOnly(program, PROGRAM_FIELDS)
        require(program.optInt("schemaVersion") == 1) { "unsupported program schema" }
        require(program.optString("programId") == manifest.optString("programId")) { "program binding mismatch" }
        require(program.optString("runtime") == RUNTIME_ID) { "program runtime mismatch" }
        require(program.optString("approvalDigest") == manifest.optString("approvedProposalDigest")) { "approval binding mismatch" }
        require(program.optJSONArray("capabilities")?.length() == 0) { "capabilities are forbidden" }
        val state = program.optJSONObject("state") ?: error("program state missing")
        require(state.length() <= MAX_STATE_ENTRIES) { "too much state" }
        val stateTypes = mutableMapOf<String, String>()
        state.keys().forEach { key ->
            require(SAFE_IDENTIFIER.matches(key)) { "unsafe state name" }
            val value = state.getJSONObject(key)
            requireOnly(value, STATE_FIELDS)
            val type = value.optString("type")
            require(type in setOf("bool", "int64", "string", "list")) { "unknown state type" }
            require(value.has("initial") && matchesType(value.get("initial"), type)) { "invalid state initial value" }
            require(value.optString("persistence", "session") in setOf("session", "local")) { "invalid persistence" }
            stateTypes[key] = type
        }
        val screens = program.optJSONArray("screens") ?: error("screens missing")
        require(screens.length() in 1..8) { "invalid screen count" }
        val screenIds = mutableSetOf<String>()
        val nodeIds = mutableSetOf<String>()
        var count = 0
        for (index in 0 until screens.length()) {
            val screen = screens.getJSONObject(index)
            requireOnly(screen, SCREEN_FIELDS)
            require(SAFE_IDENTIFIER.matches(screen.optString("id")) && screenIds.add(screen.getString("id"))) { "invalid screen id" }
            if (screen.has("onLoad")) validateAction(screen.getJSONObject("onLoad"), stateTypes)
            count += validateNodes(screen.optJSONArray("children") ?: JSONArray(), stateTypes, nodeIds, 1)
        }
        require(count <= MAX_NODES) { "too many components" }
        val actions = collectActions(screens)
        actions.filter { it.optString("type") == "navigate" }.forEach { action -> require(screenIds.contains(action.optString("screenId"))) { "unknown navigation target" } }
    }

    private fun validateNodes(nodes: JSONArray, stateTypes: Map<String, String>, ids: MutableSet<String>, depth: Int): Int {
        require(depth <= MAX_DEPTH) { "component nesting is too deep" }
        var count = 0
        for (index in 0 until nodes.length()) {
            val node = nodes.getJSONObject(index)
            requireOnly(node, NODE_FIELDS)
            val type = node.optString("type")
            require(type in COMPONENTS) { "unknown component" }
            require(SAFE_IDENTIFIER.matches(node.optString("id")) && ids.add(node.getString("id"))) { "invalid component id" }
            if (type in setOf("Text", "Button", "Checkbox")) require(node.optString("text").isNotBlank() && node.optString("text").length <= MAX_TEXT) { "component text missing" }
            if (type == "TextInput" || type == "Checkbox") {
                val key = node.optString("stateKey")
                val expected = if (type == "TextInput") "string" else "bool"
                require(stateTypes[key] == expected) { "input state binding mismatch" }
            }
            if (type == "Image") require(node.optString("src").startsWith("apkg:///resources/") && !node.optString("src").contains("..")) { "invalid image source" }
            val children = node.optJSONArray("children") ?: JSONArray()
            require(children.length() == 0 || type in CONTAINERS) { "component cannot contain children" }
            if (node.has("onClick")) validateAction(node.getJSONObject("onClick"), stateTypes)
            if (node.has("onChange")) validateAction(node.getJSONObject("onChange"), stateTypes)
            count += 1 + validateNodes(children, stateTypes, ids, depth + 1)
        }
        return count
    }

    private fun validateAction(action: JSONObject, stateTypes: Map<String, String>) {
        val type = action.optString("type")
        require(type in ACTIONS) { "unknown action" }
        requireOnly(action, ACTION_FIELDS.getValue(type))
        when (type) {
            "setState" -> {
                val key = action.optString("key")
                require(stateTypes.containsKey(key) && action.has("value") && matchesType(action.get("value"), stateTypes.getValue(key))) { "invalid state action" }
            }
            "showMessage" -> require(action.optString("message").isNotBlank() && action.optString("message").length <= MAX_TEXT) { "invalid message action" }
            "navigate" -> require(SAFE_IDENTIFIER.matches(action.optString("screenId"))) { "invalid navigation action" }
        }
    }

    private fun collectActions(screens: JSONArray): List<JSONObject> {
        val actions = mutableListOf<JSONObject>()
        for (index in 0 until screens.length()) {
            val screen = screens.getJSONObject(index)
            screen.optJSONObject("onLoad")?.let { actions.add(it) }
            collectNodeActions(screen.optJSONArray("children") ?: JSONArray(), actions)
        }
        return actions
    }

    private fun collectNodeActions(nodes: JSONArray, actions: MutableList<JSONObject>) {
        for (index in 0 until nodes.length()) {
            val node = nodes.getJSONObject(index)
            node.optJSONObject("onClick")?.let { actions.add(it) }
            node.optJSONObject("onChange")?.let { actions.add(it) }
            collectNodeActions(node.optJSONArray("children") ?: JSONArray(), actions)
        }
    }

    private fun runSmoke(program: JSONObject, smoke: JSONObject) {
        requireOnly(smoke, SMOKE_FIELDS)
        val events = smoke.optJSONArray("events") ?: error("smoke events missing")
        val assertions = smoke.optJSONArray("assertions") ?: error("smoke assertions missing")
        require(events.length() in 1..64 && assertions.length() in 1..64) { "invalid smoke budget" }
        val state = mutableMapOf<String, Any?>()
        val descriptors = program.getJSONObject("state")
        descriptors.keys().forEach { state[it] = descriptors.getJSONObject(it).get("initial") }
        val nodes = mutableMapOf<String, JSONObject>()
        collectScreenNodes(program.getJSONArray("screens"), nodes)
        for (index in 0 until events.length()) {
            val event = events.getJSONObject(index)
            when (event.optString("type")) {
                "open" -> {
                    require(event.length() == 1) { "invalid open event" }
                    program.getJSONArray("screens").getJSONObject(0).optJSONObject("onLoad")?.let { applySmokeAction(it, state) }
                }
                "click" -> {
                    require(event.length() == 2)
                    val node = nodes[event.optString("nodeId")] ?: error("invalid click target")
                    require(node.has("onClick")) { "click target has no action" }
                    applySmokeAction(node.getJSONObject("onClick"), state)
                }
                "change" -> {
                    require(event.length() == 3)
                    val node = nodes[event.optString("nodeId")] ?: error("invalid change target")
                    val key = node.optString("stateKey")
                    require(key.isNotBlank()) { "change target is not input" }
                    state[key] = event.get("value")
                    if (node.has("onChange")) applySmokeAction(node.getJSONObject("onChange"), state)
                }
                else -> error("unknown smoke event")
            }
        }
        for (index in 0 until assertions.length()) {
            val assertion = assertions.getJSONObject(index)
            requireOnly(assertion, ASSERTION_FIELDS)
            require(assertion.has("equals")) { "assertion value missing" }
            val actual: Any? = when (val path = assertion.optString("path")) {
                "/programId" -> program.getString("programId")
                "/title" -> program.getString("title")
                else -> if (path.startsWith("/state/")) state[path.removePrefix("/state/")] else error("unsupported assertion path")
            }
            require(jsonEquals(actual, assertion.get("equals"))) { "smoke assertion failed" }
        }
    }

    private fun applySmokeAction(action: JSONObject, state: MutableMap<String, Any?>) {
        if (action.optString("type") == "setState") state[action.getString("key")] = action.get("value")
    }

    private fun collectScreenNodes(screens: JSONArray, output: MutableMap<String, JSONObject>) {
        for (screenIndex in 0 until screens.length()) collectNodes(screens.getJSONObject(screenIndex).getJSONArray("children"), output)
    }
    private fun collectNodes(nodes: JSONArray, output: MutableMap<String, JSONObject>) {
        for (index in 0 until nodes.length()) {
            val node = nodes.getJSONObject(index)
            output[node.getString("id")] = node
            collectNodes(node.optJSONArray("children") ?: JSONArray(), output)
        }
    }

    private fun quarantine(staging: File, token: String) {
        if (!staging.exists()) return
        val directory = File(root, ".quarantine")
        directory.mkdirs()
        staging.renameTo(File(directory, token))
    }

    private fun readEntryBytes(zip: ZipFile, path: String, limit: Int): ByteArray = zip.getInputStream(zip.getEntry(path)).use { input ->
        val bytes = input.readBytes()
        require(bytes.size <= limit) { "JSON entry too large" }
        bytes
    }
    private fun readEntry(zip: ZipFile, path: String, limit: Int): String = readEntryBytes(zip, path, limit).toString(Charsets.UTF_8)
    private fun sha256Entry(zip: ZipFile, entry: ZipEntry): String = zip.getInputStream(entry).use { input -> sha256(input.readBytes()) }
    private fun sha256File(file: File): String = FileInputStream(file).use { input ->
        val digest = MessageDigest.getInstance("SHA-256")
        val buffer = ByteArray(32 * 1024)
        while (true) {
            val count = input.read(buffer)
            if (count < 0) break
            if (count > 0) digest.update(buffer, 0, count)
        }
        digest.digest().joinToString("") { "%02x".format(it) }
    }
    private fun sha256(bytes: ByteArray): String = MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it) }
    private fun pluginKey(programId: String): String = sha256(programId.toByteArray()).take(32)
    private fun writeAtomic(file: File, text: String) {
        file.parentFile?.mkdirs()
        val temporary = File(file.parentFile, ".${file.name}.${UUID.randomUUID()}.tmp")
        temporary.writeText(text)
        require(temporary.renameTo(file)) { "failed to update active version" }
    }
    private fun requireOnly(objectValue: JSONObject, allowed: Set<String>) {
        val keys = objectValue.keys().asSequence().toSet()
        require(keys.all { it in allowed }) { "unknown JSON field" }
    }
    private fun matchesType(value: Any?, type: String): Boolean = when (type) {
        "bool" -> value is Boolean
        "int64" -> value is Int || value is Long
        "string" -> value is String && value.length <= MAX_TEXT
        "list" -> value is JSONArray && value.length() <= 100
        else -> false
    }
    private fun jsonEquals(left: Any?, right: Any?): Boolean = left.toString() == right.toString() && left?.javaClass == right?.javaClass

    companion object {
        private const val RUNTIME_ID = "declarative-v1"
        private const val MAX_PACKAGE_BYTES = 10 * 1024 * 1024
        private const val MAX_BASE64_CHARS = MAX_PACKAGE_BYTES * 2
        private const val MAX_UNCOMPRESSED_BYTES = 10 * 1024 * 1024
        private const val MAX_SINGLE_ENTRY_BYTES = 2 * 1024 * 1024
        private const val MAX_COMPRESSION_RATIO = 100L
        private const val MAX_ENTRIES = 64
        private const val MAX_JSON_BYTES = 512 * 1024
        private const val MAX_STATE_ENTRIES = 32
        private const val MAX_NODES = 200
        private const val MAX_DEPTH = 16
        private const val MAX_TEXT = 500
        private val REQUIRED_ENTRIES = setOf("manifest.json", "program/main.json", "tests/smoke.json")
        private val COMPONENTS = setOf("Text", "Image", "Button", "TextInput", "Checkbox", "Row", "Column", "List", "Spacer", "Dialog")
        private val CONTAINERS = setOf("Row", "Column", "List", "Dialog")
        private val ACTIONS = setOf("setState", "showMessage", "finish", "navigate")
        private val MANIFEST_FIELDS = setOf("formatVersion", "artifactId", "taskId", "programId", "versionId", "revision", "securityEpoch", "approvedProposalDigest", "runtime", "uiRegistryVersion", "keyId", "algorithm", "debugUnsigned", "files")
        private val FILE_FIELDS = setOf("path", "mime", "size", "sha256")
        private val PROGRAM_FIELDS = setOf("schemaVersion", "programId", "title", "runtime", "approvalDigest", "capabilities", "state", "screens")
        private val STATE_FIELDS = setOf("type", "initial", "persistence")
        private val SCREEN_FIELDS = setOf("id", "children", "onLoad")
        private val NODE_FIELDS = setOf("id", "type", "text", "children", "onClick", "onChange", "stateKey", "hint", "src")
        private val SMOKE_FIELDS = setOf("schemaVersion", "events", "assertions")
        private val ASSERTION_FIELDS = setOf("path", "equals")
        private val ACTION_FIELDS = mapOf("setState" to setOf("type", "key", "value"), "showMessage" to setOf("type", "message"), "finish" to setOf("type"), "navigate" to setOf("type", "screenId"))
        private val SAFE_PROGRAM_ID = Regex("[a-z][a-z0-9-]{0,47}")
        private val SAFE_REVISION = Regex("[a-zA-Z0-9-]{1,64}")
        private val SAFE_META_ID = Regex("[A-Za-z0-9][A-Za-z0-9._:-]{0,159}")
        private val SAFE_IDENTIFIER = Regex("[A-Za-z][A-Za-z0-9_-]{0,63}")
        private val SHA256 = Regex("sha256:[0-9a-f]{64}")
        private val SHA256_RAW = Regex("[0-9a-f]{64}")
    }
}

data class PluginInfo(
    val programId: String,
    val title: String,
    val versionId: String,
    val revision: String,
    val file: File,
    val taskId: String,
    val artifactId: String,
    val manifestSha256: String,
)
