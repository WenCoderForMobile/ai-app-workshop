package com.autoprocedure.connect.message

import android.util.Log
import com.autoprocedure.connect.connection.ServerConnection
import java.io.BufferedInputStream
import java.io.BufferedWriter
import java.io.ByteArrayOutputStream
import java.io.EOFException
import java.io.IOException
import java.io.OutputStreamWriter
import java.nio.ByteBuffer
import java.nio.charset.CodingErrorAction
import java.nio.charset.StandardCharsets

/** Send and receive bounded newline-delimited JSON over a [ServerConnection]. */
class LineMessageClient(
    private val handshakeTimeoutMs: Long = 5_000,
    private val heartbeatIntervalMs: Long = 5_000,
    private val heartbeatTimeoutMs: Long = 15_000,
) : MessageClient {
    companion object {
        private const val TAG = "LineMessageClient"
        private val LEGACY_PLUGIN_PREFIX = Regex("^\\s*\\{\\s*\"type\"\\s*:\\s*\"plugin\"\\s*,")
    }

    private val writeLock = Any()
    private val sessionLock = Any()
    private var connection: ServerConnection? = null
    private var writer: BufferedWriter? = null
    private var readerThread: Thread? = null
    private var generation = 0L
    private var heartbeat: ServerHeartbeat? = null

    @Volatile
    private var listener: IncomingMessageListener? = null

    override fun attach(connection: ServerConnection) {
        synchronized(sessionLock) {
            if (this.connection === connection && writer != null && readerThread?.isAlive == true && heartbeat?.isReady() == true) {
                return
            }
        }
        detach()
        if (!connection.isConnected()) {
            throw IllegalStateException("ServerConnection is not connected")
        }
        val out = connection.outputStream() ?: throw IllegalStateException("no output stream")
        val input = connection.inputStream() ?: throw IllegalStateException("no input stream")
        val token: Long
        val thread: Thread
        val health: ServerHeartbeat
        synchronized(sessionLock) {
            generation++
            token = generation
            this.connection = connection
            writer = BufferedWriter(OutputStreamWriter(out, StandardCharsets.UTF_8))
            thread = Thread({ receiveLoop(BufferedInputStream(input), connection, token) }, "connect-recv-$token")
            readerThread = thread
            health = ServerHeartbeat(handshakeTimeoutMs, heartbeatIntervalMs, heartbeatTimeoutMs,
                sendPing = { nonce ->
                    if (isActive(connection, token)) send(OutgoingMessage(type = "ping", text = nonce))
                },
                onTimeout = {
                    if (clearSession(connection, token)) {
                        Log.w(TAG, "Server handshake/heartbeat timed out")
                        connection.onTransportClosed(IOException("服务端无响应：连接确认或心跳超时"))
                    }
                })
            heartbeat = health
        }
        thread.start()
        try {
            health.start()
            if (!health.awaitReady() || !isActive(connection, token)) {
                throw IOException("服务端未确认连接，请检查电脑端服务是否运行并已更新")
            }
            Log.i(TAG, "Server handshake confirmed")
        } catch (error: Exception) {
            if (clearSession(connection, token)) connection.onTransportClosed(error)
            throw error
        }
    }

    fun isReady(): Boolean = synchronized(sessionLock) { heartbeat?.isReady() == true }

    override fun detach() {
        val oldConnection: ServerConnection?
        val oldThread: Thread?
        synchronized(sessionLock) {
            heartbeat?.close()
            heartbeat = null
            generation++
            oldConnection = connection
            oldThread = readerThread
            connection = null
            writer = null
            readerThread = null
        }
        // A blocking socket read cannot be cancelled by Thread.interrupt().
        // Closing the transport is the only reliable way to prevent a detached
        // reader from competing with the next attachment.
        oldConnection?.onTransportClosed(null)
        if (oldThread !== Thread.currentThread()) {
            oldThread?.interrupt()
        }
    }

    override fun send(message: OutgoingMessage): Result<Unit> {
        val encoded = try {
            LineProtocol.encode(message)
        } catch (error: Exception) {
            return Result.failure(error)
        }
        val activeWriter: BufferedWriter
        val activeConnection: ServerConnection
        val token: Long
        synchronized(sessionLock) {
            activeWriter = writer ?: return Result.failure(IllegalStateException("not connected"))
            activeConnection = connection ?: return Result.failure(IllegalStateException("not connected"))
            token = generation
        }
        if (!activeConnection.isConnected()) {
            return Result.failure(IllegalStateException("not connected"))
        }
        return try {
            synchronized(writeLock) {
                require(isActive(activeConnection, token)) { "connection changed while sending" }
                activeWriter.write(encoded)
                activeWriter.write("\n")
                activeWriter.flush()
            }
            Result.success(Unit)
        } catch (error: Exception) {
            Log.e(TAG, "send failed", error)
            if (clearSession(activeConnection, token)) {
                activeConnection.onTransportClosed(error)
            }
            Result.failure(error)
        }
    }

    override fun setIncomingListener(listener: IncomingMessageListener?) {
        this.listener = listener
    }

    private fun receiveLoop(input: BufferedInputStream, activeConnection: ServerConnection, token: Long) {
        try {
            while (isActive(activeConnection, token)) {
                val line = readBoundedLine(input) ?: run {
                    if (clearSession(activeConnection, token)) {
                        activeConnection.onTransportClosed(null)
                    }
                    return
                }
                if (!isActive(activeConnection, token)) return
                val incoming = LineProtocol.decode(line) ?: continue
                if (incoming.type == "pong") {
                    synchronized(sessionLock) {
                        if (generation == token) heartbeat?.receivePong(incoming.text)
                    }
                } else listener?.onMessage(incoming)
            }
        } catch (error: Exception) {
            if (clearSession(activeConnection, token)) {
                Log.w(TAG, "receive loop ended", error)
                activeConnection.onTransportClosed(error)
            }
        }
    }

    private fun readBoundedLine(input: BufferedInputStream): String? {
        val bytes = ByteArrayOutputStream(1024)
        val controlPayloadLimit = LineProtocol.MAX_CONTROL_FRAME_BYTES - 1
        var limit = controlPayloadLimit
        while (true) {
            val value = input.read()
            if (value == -1) {
                if (bytes.size() == 0) return null
                break
            }
            if (value == '\n'.code) break
            if (bytes.size() >= limit) {
                if (limit == controlPayloadLimit && isLegacyPluginPrefix(bytes.toByteArray())) {
                    limit = LineProtocol.MAX_LEGACY_FRAME_BYTES
                } else {
                    throw IOException("NDJSON frame exceeds $limit bytes")
                }
            }
            bytes.write(value)
        }
        var raw = bytes.toByteArray()
        if (raw.isNotEmpty() && raw.last() == '\r'.code.toByte()) {
            raw = raw.copyOf(raw.size - 1)
        }
        val decoder = StandardCharsets.UTF_8.newDecoder()
            .onMalformedInput(CodingErrorAction.REPORT)
            .onUnmappableCharacter(CodingErrorAction.REPORT)
        return try {
            decoder.decode(ByteBuffer.wrap(raw)).toString()
        } catch (error: Exception) {
            throw EOFException("invalid UTF-8 frame").apply { initCause(error) }
        }
    }

    private fun isLegacyPluginPrefix(bytes: ByteArray): Boolean {
        val prefix = try {
            StandardCharsets.UTF_8.newDecoder()
                .onMalformedInput(CodingErrorAction.REPORT)
                .onUnmappableCharacter(CodingErrorAction.REPORT)
                .decode(ByteBuffer.wrap(bytes))
                .toString()
        } catch (_: Exception) {
            return false
        }
        // The retired Base64 sender always serialized `type` first. Requiring
        // that exact prefix keeps arbitrary oversized control frames rejected.
        return LEGACY_PLUGIN_PREFIX.containsMatchIn(prefix)
    }

    private fun isActive(activeConnection: ServerConnection, token: Long): Boolean {
        return synchronized(sessionLock) {
            generation == token && connection === activeConnection && writer != null
        }
    }

    private fun clearSession(activeConnection: ServerConnection, token: Long): Boolean {
        return synchronized(sessionLock) {
            if (generation != token || connection !== activeConnection) {
                false
            } else {
                heartbeat?.close()
                heartbeat = null
                generation++
                connection = null
                writer = null
                readerThread = null
                true
            }
        }
    }

}
