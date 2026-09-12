package com.autoprocedure.plat

import android.content.Context
import java.io.File
import com.autoprocedure.plat.job.JobStore
import com.autoprocedure.plat.job.ProgramCard
import com.autoprocedure.plat.plugin.PluginLoader
import android.os.Handler
import android.os.Looper
import com.autoprocedure.connect.connection.AdbEndpoint
import com.autoprocedure.connect.connection.ConnectionState
import com.autoprocedure.connect.connection.ReconnectPolicy
import com.autoprocedure.connect.connection.TcpServerConnection
import com.autoprocedure.connect.message.IncomingMessage
import com.autoprocedure.connect.message.LineMessageClient
import com.autoprocedure.connect.message.OutgoingMessage
import java.util.LinkedHashMap
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.ConcurrentHashMap
import java.util.zip.ZipException
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import org.json.JSONObject

data class AgentSessionSnapshot(
    val messages: List<ChatMessage>,
    val connectionState: ConnectionState,
    val connectionDetail: String?,
    val ready: Boolean,
    val artifactGeneration: Long,
    val showProgramSync: Boolean,
)

fun interface AgentSessionListener {
    fun onSessionChanged(snapshot: AgentSessionSnapshot)
}

/**
 * Process-lifetime owner of the control socket and artifact coordinator.
 * Activities only observe snapshots, so rotation does not tear down a job or
 * lose a downloaded artifact report.
 */
class AgentSession private constructor(context: Context) {
    private val appContext = context.applicationContext
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val mainHandler = Handler(Looper.getMainLooper())
    @Volatile private var serverConnection = TcpServerConnection()
    private val connectionMutex = Mutex()
    private val messageClient = LineMessageClient()
    private val pluginStore = PluginStore(appContext)
    private val downloader = ArtifactDownloader(appContext)
    private val listeners = CopyOnWriteArrayList<AgentSessionListener>()
    private val lock = Any()
    private val activeArtifacts = java.util.Collections.newSetFromMap(ConcurrentHashMap<String, Boolean>())
    private val capabilityProbeMutex = Mutex()
    private val pendingArtifacts = ConcurrentHashMap<String, ArtifactDescriptor>()
    private val startupCatalog = StartupCatalogCheck()
    private val catalogRequestId = java.util.UUID.randomUUID().toString().replace("-", "")
    private val pendingReports = LinkedHashMap<String, OutgoingMessage>()

    private val messages = mutableListOf(
        ChatMessage(appContext.getString(R.string.welcome), fromUser = false),
    )
    private var rawConnectionState = ConnectionState.DISCONNECTED
    private var connectionDetail: String? = null
    private var messageReady = false
    private var desiredConnected = false
    private var reconnectAttempt = 0
    private var reconnectJob: Job? = null
    private var connectionEpoch = 0L
    private var flushingReports = false
    private var artifactGeneration = 0L

    init {
        JobStore.init(appContext)
        JobStore.list().filter { it.state == ProgramCard.STATE_DOWNLOADING || it.state == ProgramCard.STATE_INSTALLING }
            .forEach { JobStore.updateState(it.taskId, it.awaitingSync().state) }
        observeTransport(serverConnection)
        messageClient.setIncomingListener(::handleIncoming)
    }

    private fun observeTransport(transport: TcpServerConnection) {
        transport.addStateListener { state, detail ->
            synchronized(lock) {
                if (serverConnection !== transport) return@addStateListener
                rawConnectionState = state
                connectionDetail = detail
                if (state != ConnectionState.CONNECTED) messageReady = false
            }
            publishSnapshot()
            if (state == ConnectionState.DISCONNECTED || state == ConnectionState.FAILED) {
                scheduleReconnect(immediate = false)
            }
        }
    }

    fun addListener(listener: AgentSessionListener) {
        listeners.addIfAbsent(listener)
        publishSnapshotTo(listener)
    }

    fun removeListener(listener: AgentSessionListener) {
        listeners.remove(listener)
    }

    fun connect() {
        val oldJob = synchronized(lock) {
            desiredConnected = true
            reconnectAttempt = 0
            connectionEpoch++
            reconnectJob.also { reconnectJob = null }
        }
        oldJob?.cancel()
        scheduleReconnect(immediate = true)
    }

    fun ensureConnected() {
        val shouldStart = synchronized(lock) {
            desiredConnected = true
            if (isConnected() || reconnectJob?.isActive == true) {
                false
            } else {
                reconnectAttempt = 0
                connectionEpoch++
                true
            }
        }
        if (shouldStart) scheduleReconnect(immediate = true)
    }

    fun disconnect() {
        val oldJob = synchronized(lock) {
            desiredConnected = false
            reconnectAttempt = 0
            messageReady = false
            connectionEpoch++
            reconnectJob.also { reconnectJob = null }
        }
        oldJob?.cancel()
        messageClient.detach()
        serverConnection.disconnect()
        publishSnapshot()
    }

    fun isConnected(): Boolean = synchronized(lock) {
        messageReady && messageClient.isReady() && serverConnection.isConnected()
    }

    fun connectionState(): ConnectionState = synchronized(lock) { visibleConnectionStateLocked() }

    private fun syncCandidates(): List<ProgramCard> =
        ManualProgramSync.candidates(startupCatalog.pending(), JobStore.list())

    /** One click authorizes only the currently known versions, never future pushes or reconnects. */
    fun syncPrograms(): Boolean {
        if (!isConnected()) return false
        syncCandidates().forEach {
            JobStore.restoreForSync(it)
            installApk(it)
        }
        pendingArtifacts.values.toList().forEach { installArtifact(it) }
        return true
    }

    fun sendChat(text: String) {
        val trimmed = text.trim()
        if (trimmed.isEmpty()) return
        addMessage(ChatMessage(trimmed, fromUser = true))
        scope.launch {
            val result = messageClient.send(OutgoingMessage(text = trimmed))
            if (result.isFailure) {
                addMessage(ChatMessage(appContext.getString(R.string.status_send_failed), fromUser = false))
                scheduleReconnect(immediate = false)
            }
        }
    }

    private fun scheduleReconnect(immediate: Boolean) {
        synchronized(lock) {
            if (!desiredConnected || isConnected() || reconnectJob?.isActive == true) return
            val delayMs = if (immediate) 0L else ReconnectPolicy.delayMs(reconnectAttempt)
            val epoch = connectionEpoch
            reconnectJob = scope.launch {
                if (delayMs > 0) delay(delayMs)
                // A cancelled blocking connect/handshake finishes cleanup before a new attempt.
                connectionMutex.withLock {
                    if (!synchronized(lock) { desiredConnected && connectionEpoch == epoch }) return@withLock
                    messageClient.detach()
                    val transport = TcpServerConnection()
                    synchronized(lock) {
                        serverConnection = transport
                        messageReady = false
                    }
                    observeTransport(transport)
                    var confirmed = false
                    try {
                        transport.connect(AdbEndpoint.default()).getOrThrow()
                        currentCoroutineContext().ensureActive()
                        messageClient.attach(transport) // Wait for the server's matching pong.
                        currentCoroutineContext().ensureActive()
                        confirmed = true
                    } catch (cancelled: CancellationException) {
                        throw cancelled
                    } catch (error: Exception) {
                        transport.onTransportClosed(error)
                    } finally {
                        val current = synchronized(lock) {
                            if (connectionEpoch != epoch || !desiredConnected) false
                            else {
                                messageReady = confirmed && messageClient.isReady() && transport.isConnected()
                                reconnectAttempt = if (messageReady) 0 else (reconnectAttempt + 1).coerceAtMost(10)
                                reconnectJob = null
                                true
                            }
                        }
                        if (!current || !isConnected()) {
                            messageClient.detach()
                            if (transport.isConnected()) transport.disconnect()
                        }
                        if (current) {
                            publishSnapshot()
                            if (isConnected()) {
                                flushPendingReports()
                                if (startupCatalog.begin()) {
                                    messageClient.send(OutgoingMessage(type = "catalog_check", text = catalogRequestId))
                                }
                                // Resume known waiting tasks on every connection, without another catalog scan.
                                JobStore.list().filter { it.needsStatusCheck }.map { it.taskId }
                                    .chunked(50).forEach { ids ->
                                        val request = JSONObject().put("taskIds", org.json.JSONArray(ids))
                                        messageClient.send(OutgoingMessage(type = "task_check", text = request.toString()))
                                    }
                            } else scheduleReconnect(immediate = false)
                        }
                    }
                }
            }
        }
    }

    private fun handleIncoming(incoming: IncomingMessage) {
        when (incoming.type) {
            "capability_probe" -> {
                val request = runCatching { JSONObject(incoming.text) }.getOrNull() ?: return
                val requestId = request.optString("requestId")
                if (!requestId.matches(Regex("[a-f0-9]{32}"))) return
                val transport = serverConnection
                scope.launch {
                    // Keep probing off the socket reader so heartbeats remain responsive.
                    if (!capabilityProbeMutex.tryLock()) return@launch
                    try {
                        val report = AudioCapabilityProbe(appContext).run().put("requestId", requestId)
                        if (serverConnection === transport && transport.isConnected()) {
                            messageClient.send(OutgoingMessage(type = "capability_report", text = report.toString()))
                        }
                    } finally { capabilityProbeMutex.unlock() }
                }
            }
            "chat" -> if (incoming.text.isNotBlank()) {
                addMessage(ChatMessage(incoming.text, fromUser = false))
            }
            "job" -> handleJob(incoming.text)
            "plugin" -> installLegacyPlugin(incoming)
            else -> if (incoming.text.isNotBlank()) {
                addMessage(ChatMessage(incoming.text, fromUser = false))
            }
        }
    }

    private fun handleJob(text: String) {
        val job = try {
            JSONObject(text)
        } catch (error: Exception) {
            addMessage(ChatMessage("任务状态无效：${safeError(error)}", fromUser = false))
            return
        }
        if (job.optString("kind") in setOf("catalog_entry", "catalog_complete")) {
            if (job.optString("requestId") != catalogRequestId) return
            if (job.optString("kind") == "catalog_entry") {
                val raw = job.optJSONObject("job") ?: return
                val card = ProgramCard.parse(raw).copy(apkPath = "").awaitingSync()
                if (card.taskId.isNotBlank() && card.taskId.length <= 160 && card.needsSync &&
                    card.sha256.matches(Regex("[a-f0-9]{64}"))) startupCatalog.receive(card)
            } else {
                val installed = JobStore.list().filter {
                    it.launchType == ProgramCard.LAUNCH_APK && it.apkPath.isNotBlank() &&
                        File(it.apkPath).isFile && it.state == ProgramCard.STATE_READY
                }.map { it.taskId to it.sha256 }.toSet()
                if (startupCatalog.complete(job.optInt("count", -1), installed)) {
                    startupCatalog.pending().forEach { JobStore.upsert(it) }
                    publishSnapshot()
                }
            }
            return
        }
        if (job.optString("kind") == "task_missing") {
            val id = job.optString("taskId")
            val current = JobStore.get(id)
            if (current?.needsStatusCheck == true) {
                startupCatalog.installed(id, current.sha256)
                JobStore.updateState(id, ProgramCard.STATE_FAILED) {
                    copy(downloadUrl = "", error = "服务端未找到此任务，请在对话中继续制作。")
                }
                publishSnapshot()
            }
            return
        }
        if (job.optString("launchType") == "apk" || job.optString("state") in setOf("making", "downloading", "failed")) {
            runCatching {
                val card = ProgramCard.parse(job).copy(apkPath = "").awaitingSync()
                require(card.taskId.isNotBlank() && card.taskId.length <= 160)
                val previous = JobStore.get(card.taskId)
                JobStore.upsert(card)
                val stored = JobStore.get(card.taskId)
                publishSnapshot()
                // Replayed metadata may be ignored for an installed or hidden program.
                // Pending downloads belong in the catalog, not in the conversation.
                if (stored != null && stored.state != ProgramCard.STATE_PENDING_SYNC &&
                    (stored.state != previous?.state || stored.error != previous.error)) {
                    addMessage(ChatMessage("${stored.title}：${stored.stateLabel}", fromUser = false))
                }
            }.onFailure { addMessage(ChatMessage("程序任务无效：${safeError(it)}", fromUser = false)) }
            return
        }
        val status = job.optString("status").ifBlank { job.optString("state") }.uppercase()
        when (status) {
            "RUNNING", "QUEUED" -> {
                val title = job.optString("title").ifBlank { "插件任务" }
                val phase = job.optString("phase").ifBlank { status }
                addMessage(ChatMessage("$title：$phase", fromUser = false))
            }
            "SUCCEEDED" -> {
                val descriptor = try {
                    ArtifactDescriptor.fromJob(job)
                } catch (error: Exception) {
                    addMessage(ChatMessage("插件描述无效：${safeError(error)}", fromUser = false))
                    sendMalformedArtifactReport(job, error)
                    return
                }
                pendingArtifacts[descriptor.artifactId] = descriptor
            }
            "FAILED", "CANCELED", "EXPIRED" -> {
                val title = job.optString("title").ifBlank { "插件任务" }
                val detail = job.optString("error").ifBlank {
                    job.optString("errorCode").ifBlank { status }
                }
                addMessage(ChatMessage("$title 失败：$detail", fromUser = false))
            }
            else -> addMessage(ChatMessage("收到未知任务状态", fromUser = false))
        }
    }

    fun sendRuntimeError(json: String): Boolean {
        synchronized(lock) {
            pendingReports["runtime-" + JSONObject(json).optString("taskId")] =
                OutgoingMessage(type = "runtime_error", text = json)
        }
        flushPendingReports()
        return true
    }

    private fun installApk(card: ProgramCard) {
        val key = "apk-" + card.taskId
        if (!activeArtifacts.add(key)) return
        JobStore.updateState(card.taskId, ProgramCard.STATE_DOWNLOADING)
        scope.launch {
            try {
                require(BuildConfig.DEBUG) { "APK plugins require an internal debug build" }
                require(Regex("[A-Za-z_][A-Za-z0-9_]*(\\.[A-Za-z_][A-Za-z0-9_]*)+").matches(card.entryClass)) { "invalid entry class" }
                val downloaded = downloader.download(card.downloadUrl, card.size, card.sha256).getOrThrow()
                try {
                    require(PluginLoader.isPluginApk(downloaded.file)) { "invalid plugin APK" }
                    if (JobStore.get(card.taskId)?.sha256 != card.sha256) return@launch
                    JobStore.updateState(card.taskId, ProgramCard.STATE_INSTALLING)
                    val folder = File(appContext.filesDir, "apk-tasks/${JobStore.taskKey(card.taskId)}/${card.sha256}").apply { mkdirs() }
                    val target = File(folder, "plugin.apk")
                    if (!target.exists()) {
                        require(downloaded.file.renameTo(target)) { "failed to stage APK" }
                    } else {
                        require(java.security.MessageDigest.getInstance("SHA-256").digest(target.readBytes())
                            .joinToString("") { "%02x".format(it) } == card.sha256) { "cached APK hash mismatch" }
                    }
                    require(target.setReadOnly()) { "cannot mark APK read-only" }
                    JobStore.updateState(card.taskId, ProgramCard.STATE_READY) { copy(apkPath = target.absolutePath, error = "") }
                    startupCatalog.installed(card.taskId, card.sha256)
                    publishSnapshot()
                    addMessage(ChatMessage("${card.title}已就绪，可到程序广场打开。", fromUser = false))
                } finally {
                    downloaded.file.delete()
                }
            } catch (error: Exception) {
                if (JobStore.get(card.taskId)?.sha256 == card.sha256) {
                    JobStore.updateState(card.taskId, ProgramCard.STATE_FAILED) { copy(error = safeError(error)) }
                }
                addMessage(ChatMessage("程序下载失败：${safeError(error)}", fromUser = false))
            } finally {
                activeArtifacts.remove(key)
            }
        }
    }

    private fun installArtifact(descriptor: ArtifactDescriptor) {
        if (!activeArtifacts.add(descriptor.artifactId)) return
        scope.launch {
            var phase = "DOWNLOADING"
            try {
                val alreadyInstalled = pluginStore.list().any {
                    it.artifactId == descriptor.artifactId &&
                        it.manifestSha256 == descriptor.manifestSha256 &&
                        it.programId == descriptor.programId &&
                        it.revision == descriptor.revision
                }
                if (!alreadyInstalled) {
                    addMessage(ChatMessage("正在下载插件…", fromUser = false))
                    val downloaded = downloader.download(descriptor).getOrThrow()
                    try {
                        phase = "VERIFYING"
                        pluginStore.install(downloaded.file, descriptor).getOrThrow()
                    } finally {
                        downloaded.file.delete()
                    }
                }
                synchronized(lock) { artifactGeneration++ }
                addMessage(ChatMessage("插件已安装：${descriptor.programId}", fromUser = false))
                queueArtifactReport(descriptor, "PASSED", "INSTALLED", null)
                pendingArtifacts.remove(descriptor.artifactId, descriptor)
            } catch (error: Throwable) {
                addMessage(ChatMessage("插件安装失败：${safeError(error)}", fromUser = false))
                queueArtifactReport(descriptor, classifyArtifactFailure(error), phase, error)
            } finally {
                activeArtifacts.remove(descriptor.artifactId)
            }
        }
    }

    private fun installLegacyPlugin(incoming: IncomingMessage) {
        val packageBase64 = incoming.packageBase64
        if (packageBase64 == null) {
            addMessage(ChatMessage("旧版插件帧缺少 packageBase64", fromUser = false))
            return
        }
        scope.launch {
            val result = pluginStore.install(packageBase64)
            val label = incoming.title ?: incoming.programId ?: "未命名插件"
            if (result.isSuccess) synchronized(lock) { artifactGeneration++ }
            addMessage(
                ChatMessage(
                    if (result.isSuccess) "插件已安装：$label"
                    else "插件安装失败：${safeError(result.exceptionOrNull())}",
                    fromUser = false,
                ),
            )
        }
    }

    private fun queueArtifactReport(
        descriptor: ArtifactDescriptor,
        result: String,
        phase: String,
        error: Throwable?,
    ) {
        val payload = JSONObject()
            .put("result", result)
            .put("phase", phase)
            .put("taskId", descriptor.taskId)
            .put("artifactId", descriptor.artifactId)
            .put("programId", descriptor.programId)
            .put("versionId", descriptor.versionId)
            .put("revision", descriptor.revision)
            .put("transportSha256", descriptor.transportSha256)
        if (error != null) {
            payload.put("errorCode", errorCode(error))
            payload.put("errorMessage", safeError(error))
        }
        enqueueReport(descriptor.artifactId, payload)
    }

    private fun sendMalformedArtifactReport(job: JSONObject, error: Throwable) {
        val taskId = job.optString("taskId")
        val artifactId = job.optString("artifactId")
        if (taskId.isBlank() || artifactId.isBlank()) return
        val payload = JSONObject()
            .put("result", "REJECTED")
            .put("phase", "VERIFYING")
            .put("taskId", taskId.take(160))
            .put("artifactId", artifactId.take(160))
            .put("errorCode", "INVALID_DESCRIPTOR")
            .put("errorMessage", safeError(error))
        enqueueReport(artifactId.take(160), payload)
    }

    private fun enqueueReport(key: String, payload: JSONObject) {
        val message = OutgoingMessage(type = "artifact_report", text = payload.toString())
        synchronized(lock) { pendingReports[key] = message }
        flushPendingReports()
    }

    private fun flushPendingReports() {
        synchronized(lock) {
            if (flushingReports || !messageReady || pendingReports.isEmpty()) return
            flushingReports = true
        }
        scope.launch {
            try {
                while (isConnected()) {
                    val next = synchronized(lock) { pendingReports.entries.firstOrNull()?.toPair() } ?: break
                    if (messageClient.send(next.second).isFailure) break
                    synchronized(lock) {
                        if (pendingReports[next.first] === next.second) pendingReports.remove(next.first)
                    }
                }
            } finally {
                val retry = synchronized(lock) {
                    flushingReports = false
                    messageReady && pendingReports.isNotEmpty()
                }
                if (retry) {
                    scope.launch {
                        delay(REPORT_RETRY_MS)
                        flushPendingReports()
                    }
                }
            }
        }
    }

    private fun addMessage(message: ChatMessage) {
        synchronized(lock) {
            messages.add(message)
            while (messages.size > MAX_MESSAGES) messages.removeAt(0)
        }
        publishSnapshot()
    }

    private fun publishSnapshot() {
        mainHandler.post {
            val snapshot = snapshot()
            listeners.forEach { listener -> runCatching { listener.onSessionChanged(snapshot) } }
        }
    }

    private fun publishSnapshotTo(listener: AgentSessionListener) {
        mainHandler.post {
            if (listeners.contains(listener)) runCatching { listener.onSessionChanged(snapshot()) }
        }
    }

    private fun snapshot(): AgentSessionSnapshot = synchronized(lock) {
        AgentSessionSnapshot(
            messages = messages.toList(),
            connectionState = visibleConnectionStateLocked(),
            connectionDetail = connectionDetail,
            ready = messageReady && messageClient.isReady() && serverConnection.isConnected(),
            artifactGeneration = artifactGeneration,
            showProgramSync = syncCandidates().isNotEmpty(),
        )
    }

    private fun visibleConnectionStateLocked(): ConnectionState {
        return if (rawConnectionState == ConnectionState.CONNECTED && !isConnected()) {
            ConnectionState.CONNECTING
        } else {
            rawConnectionState
        }
    }

    private fun classifyArtifactFailure(error: Throwable): String {
        val message = error.message.orEmpty().lowercase()
        return when {
            error is ZipException -> "CORRUPTED"
            "hash" in message || "size" in message || "too large" in message -> "CORRUPTED"
            "quarantine" in message -> "QUARANTINED"
            "incompatible" in message || "unsupported" in message || "signature" in message ||
                "unsigned" in message || "runtime" in message -> "INCOMPATIBLE"
            "binding" in message || "unsafe" in message || "invalid package" in message || "smoke" in message -> "REJECTED"
            else -> "FAILED"
        }
    }

    private fun errorCode(error: Throwable): String {
        val message = error.message.orEmpty().lowercase()
        return when {
            "hash" in message -> "HASH_MISMATCH"
            "size" in message || "too large" in message -> "SIZE_INVALID"
            "http" in message || "url" in message -> "DOWNLOAD_FAILED"
            else -> "INSTALL_FAILED"
        }
    }

    private fun safeError(error: Throwable?): String {
        val raw = error?.message ?: error?.javaClass?.simpleName ?: "未知错误"
        return raw.replace(Regex("https?://\\S+"), "[url]")
            .replace(Regex("(?:/[A-Za-z0-9._-]+){2,}"), "[path]")
            .replace('\u0000', ' ')
            .take(300)
    }

    companion object {
        private const val MAX_MESSAGES = 200
        private const val REPORT_RETRY_MS = 2_000L

        @Volatile
        private var instance: AgentSession? = null

        fun current(): AgentSession? = instance

        fun get(context: Context): AgentSession {
            return instance ?: synchronized(this) {
                instance ?: AgentSession(context.applicationContext).also { instance = it }
            }
        }
    }
}
