package com.autoprocedure.connect.message

fun interface IncomingMessageListener {
    fun onMessage(message: IncomingMessage)
}

/**
 * Independent of how the socket was opened. Must be [attach]ed to a live
 * [com.autoprocedure.connect.connection.ServerConnection] before send/receive.
 */
interface MessageClient {
    fun attach(connection: com.autoprocedure.connect.connection.ServerConnection)

    fun detach()

    fun send(message: OutgoingMessage): Result<Unit>

    fun setIncomingListener(listener: IncomingMessageListener?)
}
