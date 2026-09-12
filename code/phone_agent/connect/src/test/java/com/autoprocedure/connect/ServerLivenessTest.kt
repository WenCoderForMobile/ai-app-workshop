package com.autoprocedure.connect.message

import com.autoprocedure.connect.connection.ConnectionState
import com.autoprocedure.connect.connection.Endpoint
import com.autoprocedure.connect.connection.TcpServerConnection
import java.net.ServerSocket
import java.net.Socket
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger
import org.junit.Assert.*
import org.junit.Test

class ServerLivenessTest {
    private class Peer(private val reply: (Int, String) -> String?) : AutoCloseable {
        private val server = ServerSocket(0)
        val transport = TcpServerConnection()
        val closed = CountDownLatch(1)
        @Volatile private var socket: Socket? = null
        private val worker = Thread {
            try {
                server.accept().use { peer ->
                    socket = peer
                    val reader = peer.getInputStream().bufferedReader()
                    val writer = peer.getOutputStream().bufferedWriter()
                    var count = 0
                    while (true) {
                        val line = reader.readLine() ?: break
                        val message = LineProtocol.decode(line) ?: continue
                        if (message.type == "ping") {
                            val response = reply(++count, message.text)
                            if (response != null) {
                                writer.write(LineProtocol.encode(OutgoingMessage("pong", response)))
                                writer.newLine()
                                writer.flush()
                            }
                        }
                    }
                }
            } catch (_: Exception) { }
        }.apply { isDaemon = true; start() }
        init {
            transport.addStateListener { state, _ ->
                if (state == ConnectionState.FAILED) closed.countDown()
            }
            transport.connect(Endpoint("127.0.0.1", server.localPort)).getOrThrow()
        }
        fun disconnectServer() { socket?.close() }
        override fun close() {
            transport.disconnect()
            socket?.close()
            server.close()
            worker.join(1_000)
        }
    }

    private fun client() = LineMessageClient(600, 40, 250)

    @Test fun socketWithoutApplicationResponseIsNeverReady() {
        Peer { _, _ -> null }.use { peer ->
            val client = client()
            try {
                assertThrows(java.io.IOException::class.java) { client.attach(peer.transport) }
                assertFalse(client.isReady())
                assertFalse(peer.transport.isConnected())
            } finally { client.detach() }
        }
    }

    @Test fun incorrectProbeCannotConfirmConnection() {
        Peer { _, _ -> "wrong-probe" }.use { peer ->
            val client = client()
            try {
                assertThrows(java.io.IOException::class.java) { client.attach(peer.transport) }
                assertFalse(client.isReady())
            } finally { client.detach() }
        }
    }

    @Test fun healthyIdleConnectionSurvivesAndPongsAreNotChatMessages() {
        Peer { _, nonce -> nonce }.use { peer ->
            val client = client()
            val delivered = AtomicInteger()
            client.setIncomingListener { delivered.incrementAndGet() }
            try {
                client.attach(peer.transport)
                Thread.sleep(650) // More than two liveness windows, with no user messages.
                assertTrue(client.isReady())
                assertTrue(peer.transport.isConnected())
                assertEquals(0, delivered.get())
            } finally { client.detach() }
        }
    }

    @Test fun unresponsiveServerClosesPreviouslyConfirmedConnection() {
        Peer { count, nonce -> if (count == 1) nonce else null }.use { peer ->
            val client = client()
            try {
                client.attach(peer.transport)
                assertTrue(client.isReady())
                assertTrue(peer.closed.await(2, TimeUnit.SECONDS))
                assertFalse(client.isReady())
                assertFalse(peer.transport.isConnected())
            } finally { client.detach() }
        }
    }

    @Test fun serverExitClearsReadyWithoutWaitingForUserInput() {
        Peer { _, nonce -> nonce }.use { peer ->
            val client = client()
            val disconnected = CountDownLatch(1)
            try {
                client.attach(peer.transport)
                peer.transport.addStateListener { state, _ ->
                    if (state != ConnectionState.CONNECTED) disconnected.countDown()
                }
                peer.disconnectServer()
                assertTrue(disconnected.await(2, TimeUnit.SECONDS))
                assertFalse(client.isReady())
            } finally { client.detach() }
        }
    }

    @Test fun replacingConnectionRetiresOldHeartbeat() {
        Peer { _, nonce -> nonce }.use { first ->
            Peer { _, nonce -> nonce }.use { second ->
                val client = client()
                try {
                    client.attach(first.transport)
                    client.attach(second.transport)
                    Thread.sleep(650)
                    assertFalse(first.transport.isConnected())
                    assertTrue(second.transport.isConnected())
                    assertTrue(client.isReady())
                } finally { client.detach() }
            }
        }
    }

    @Test fun blockedProbeWriterDoesNotBlockTimeoutDetection() {
        val timedOut = CountDownLatch(1)
        val blocked = CountDownLatch(1)
        val health = ServerHeartbeat(100, 20, 250,
            sendPing = { try { blocked.await() } catch (_: InterruptedException) { } },
            onTimeout = { timedOut.countDown() })
        try {
            health.start()
            assertTrue(timedOut.await(1, TimeUnit.SECONDS))
            assertFalse(health.isReady())
        } finally { blocked.countDown(); health.close() }
    }
}
