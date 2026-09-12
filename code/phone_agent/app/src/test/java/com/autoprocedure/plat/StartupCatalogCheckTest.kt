package com.autoprocedure.plat

import com.autoprocedure.plat.job.ProgramCard
import org.junit.Assert.*
import org.junit.Test

class StartupCatalogCheckTest {
    private fun card(id: String, hash: String = "v1") = ProgramCard(
        taskId = id, title = id, summary = "", icon = "", state = ProgramCard.STATE_DOWNLOADING,
        packageName = "test.plugin", downloadUrl = "http://127.0.0.1/artifact",
        launchType = ProgramCard.LAUNCH_APK, programId = "", apkPath = "",
        entryClass = "test.plugin.Main", error = "", sha256 = hash, size = 100,
    )

    @Test fun sameCatalogOrLocalExtrasHideSync() {
        val check = StartupCatalogCheck()
        assertTrue(check.begin())
        check.receive(card("snake"))
        assertTrue(check.complete(1, setOf("snake" to "v1", "local-only" to "v1")))
        assertTrue(check.pending().isEmpty())
    }

    @Test fun cloudExtrasAndNewVersionsRequireExplicitSync() {
        val check = StartupCatalogCheck()
        check.begin()
        check.receive(card("snake", "v2"))
        check.receive(card("zoo"))
        check.complete(2, setOf("snake" to "v1"))
        assertEquals(setOf("snake", "zoo"), check.pending().map { it.taskId }.toSet())
        assertTrue(check.pending().all { it.state == ProgramCard.STATE_PENDING_SYNC })
        check.installed("snake", "v1")
        assertEquals(2, check.pending().size)
        check.installed("snake", "v2")
        check.installed("zoo", "v1")
        assertTrue(check.pending().isEmpty())
    }

    @Test fun reconnectAndLatePushCannotRecheckButNewProcessCan() {
        val check = StartupCatalogCheck()
        assertTrue(check.begin())
        assertFalse(check.begin())
        assertTrue(check.complete(0, emptySet()))
        check.receive(card("late"))
        assertFalse(check.complete(1, emptySet()))
        assertFalse(check.begin())
        assertTrue(check.pending().isEmpty())
        assertTrue(StartupCatalogCheck().begin())
    }

    @Test fun partialCatalogDoesNotPublishDifference() {
        val check = StartupCatalogCheck()
        check.begin()
        check.receive(card("snake"))
        assertFalse(check.complete(2, emptySet()))
        assertTrue(check.pending().isEmpty())
        check.receive(card("zoo"))
        assertTrue(check.complete(2, emptySet()))
        assertEquals(2, check.pending().size)
    }
}
