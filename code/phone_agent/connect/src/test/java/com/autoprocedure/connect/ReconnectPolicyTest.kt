package com.autoprocedure.connect.connection

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class ReconnectPolicyTest {
    @Test fun backsOffWithoutAbandoningLongUsbDisconnects() {
        assertEquals(listOf(1000L, 1000L, 2000L, 4000L, 8000L, 30000L),
            (0..5).map(ReconnectPolicy::delayMs))
        for (attempt in listOf(-1, 6, 10, 100, Int.MAX_VALUE)) {
            assertTrue(ReconnectPolicy.delayMs(attempt) in 1000L..30000L)
        }
    }
}
