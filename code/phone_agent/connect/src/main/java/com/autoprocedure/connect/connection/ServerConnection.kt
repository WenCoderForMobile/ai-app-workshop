package com.autoprocedure.connect.connection

import java.io.InputStream
import java.io.OutputStream

fun interface ConnectionStateListener {
    fun onStateChanged(state: ConnectionState, detail: String?)
}

/**
 * Independent of send/receive: only opens and closes a link to the server.
 */
interface ServerConnection {
    val state: ConnectionState

    fun connect(endpoint: Endpoint): Result<Unit>

    fun disconnect()

    fun isConnected(): Boolean

    fun addStateListener(listener: ConnectionStateListener)

    fun removeStateListener(listener: ConnectionStateListener)

    fun inputStream(): InputStream?

    fun outputStream(): OutputStream?

    /**
     * Called by the message layer when the live streams reach EOF or fail.
     * Implementations must close the affected transport and publish a terminal
     * state so callers never keep treating a half-open socket as connected.
     */
    fun onTransportClosed(cause: Throwable?)
}
