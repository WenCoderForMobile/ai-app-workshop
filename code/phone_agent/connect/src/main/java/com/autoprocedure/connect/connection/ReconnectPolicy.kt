package com.autoprocedure.connect.connection

/** Quick transient recovery, then low-frequency retries until explicitly disconnected. */
object ReconnectPolicy {
    fun delayMs(attempt: Int): Long = when {
        attempt >= 5 -> 30_000L
        else -> (1L shl (attempt - 1).coerceIn(0, 3)) * 1_000L
    }
}
