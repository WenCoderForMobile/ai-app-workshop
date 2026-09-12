package com.autoprocedure.connect.message

import java.util.UUID
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

/** A socket connection is not ready until the application echoes our probe. */
internal class ServerHeartbeat(
    private val handshakeTimeoutMs: Long,
    private val intervalMs: Long,
    private val timeoutMs: Long,
    private val sendPing: (String) -> Unit,
    private val onTimeout: () -> Unit,
) {
    private val lock = Any()
    private val ready = CountDownLatch(1)
    private val timer = Executors.newScheduledThreadPool(2) { task ->
        Thread(task, "server-heartbeat").apply { isDaemon = true }
    }
    private var closed = false
    private var confirmed = false
    private var lastReply = System.nanoTime()
    private var pending: String? = null

    fun start() {
        timer.scheduleWithFixedDelay({
            val nonce = synchronized(lock) {
                if (closed || pending != null) null
                else UUID.randomUUID().toString().also { pending = it }
            }
            if (nonce != null) sendPing(nonce)
        }, 0, intervalMs, TimeUnit.MILLISECONDS)
        // Separate from sending: a blocked socket write must not block timeout detection.
        timer.scheduleWithFixedDelay({
            val expired = synchronized(lock) {
                !closed && elapsedMs() >= if (confirmed) timeoutMs else handshakeTimeoutMs
            }
            if (expired) onTimeout()
        }, 0, minOf(intervalMs, 250L), TimeUnit.MILLISECONDS)
    }

    fun receivePong(nonce: String) {
        synchronized(lock) {
            if (closed || nonce != pending || pending == null) return
            confirmed = true
            pending = null
            lastReply = System.nanoTime()
            ready.countDown()
        }
    }

    fun awaitReady(): Boolean = ready.await(handshakeTimeoutMs, TimeUnit.MILLISECONDS) && isReady()

    fun isReady(): Boolean = synchronized(lock) { !closed && confirmed && elapsedMs() < timeoutMs }

    fun close() {
        synchronized(lock) { closed = true }
        ready.countDown()
        timer.shutdownNow()
    }

    private fun elapsedMs() = TimeUnit.NANOSECONDS.toMillis(System.nanoTime() - lastReply)
}
