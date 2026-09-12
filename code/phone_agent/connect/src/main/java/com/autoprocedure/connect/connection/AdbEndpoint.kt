package com.autoprocedure.connect.connection

/**
 * First-phase mock: phone talks to 127.0.0.1, [adb reverse] maps that
 * port to the TCP server running on the development machine.
 */
object AdbEndpoint {
    const val DEFAULT_PORT = 17890

    fun default(): Endpoint = Endpoint(host = "127.0.0.1", port = DEFAULT_PORT)
}
