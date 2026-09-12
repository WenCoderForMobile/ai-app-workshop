package com.autoprocedure.connect.connection

import android.util.Log
import java.io.InputStream
import java.io.OutputStream
import java.net.InetSocketAddress
import java.net.Socket
import java.util.concurrent.CopyOnWriteArrayList

/**
 * TCP implementation used by the ADB reverse mock in phase 1.
 * Later HTTP/TLS can be another [ServerConnection] without changing MessageClient.
 */
class TcpServerConnection(
    private val connectTimeoutMs: Int = 5_000,
) : ServerConnection {
    companion object {
        private const val TAG = "TcpServerConnection"
    }

    private val listeners = CopyOnWriteArrayList<ConnectionStateListener>()
    private val lock = Any()
    @Volatile
    private var socket: Socket? = null

    @Volatile
    override var state: ConnectionState = ConnectionState.DISCONNECTED
        private set

    override fun connect(endpoint: Endpoint): Result<Unit> {
        synchronized(lock) {
            if (state == ConnectionState.CONNECTED && socket?.let(::isSocketUsable) == true) {
                return Result.success(Unit)
            }
            disconnectLocked()
            setState(ConnectionState.CONNECTING, "${endpoint.host}:${endpoint.port}")
            var candidate: Socket? = null
            return try {
                val s = Socket()
                candidate = s
                s.tcpNoDelay = true
                s.keepAlive = true
                s.connect(InetSocketAddress(endpoint.host, endpoint.port), connectTimeoutMs)
                socket = s
                setState(ConnectionState.CONNECTED, "${endpoint.host}:${endpoint.port}")
                Result.success(Unit)
            } catch (e: Exception) {
                Log.e(TAG, "connect failed", e)
                try {
                    candidate?.close()
                } catch (_: Exception) {
                }
                socket = null
                setState(ConnectionState.FAILED, e.message)
                Result.failure(e)
            }
        }
    }

    override fun disconnect() {
        synchronized(lock) {
            disconnectLocked()
            setState(ConnectionState.DISCONNECTED, null)
        }
    }

    override fun isConnected(): Boolean {
        val s = socket
        return state == ConnectionState.CONNECTED && s != null && isSocketUsable(s)
    }

    override fun addStateListener(listener: ConnectionStateListener) {
        listeners.add(listener)
        try {
            listener.onStateChanged(state, null)
        } catch (error: RuntimeException) {
            Log.w(TAG, "connection listener failed", error)
        }
    }

    override fun removeStateListener(listener: ConnectionStateListener) {
        listeners.remove(listener)
    }

    override fun inputStream(): InputStream? = synchronized(lock) { socket?.getInputStream() }

    override fun outputStream(): OutputStream? = synchronized(lock) { socket?.getOutputStream() }

    override fun onTransportClosed(cause: Throwable?) {
        synchronized(lock) {
            disconnectLocked()
            if (cause == null) {
                setState(ConnectionState.DISCONNECTED, "remote closed")
            } else {
                setState(ConnectionState.FAILED, cause.message ?: cause.javaClass.simpleName)
            }
        }
    }

    private fun disconnectLocked() {
        try {
            socket?.close()
        } catch (_: Exception) {
        }
        socket = null
    }

    private fun setState(next: ConnectionState, detail: String?) {
        state = next
        listeners.forEach { listener ->
            try {
                listener.onStateChanged(next, detail)
            } catch (error: RuntimeException) {
                Log.w(TAG, "connection listener failed", error)
            }
        }
    }

    private fun isSocketUsable(value: Socket): Boolean {
        return value.isConnected &&
            !value.isClosed &&
            !value.isInputShutdown &&
            !value.isOutputShutdown
    }
}
