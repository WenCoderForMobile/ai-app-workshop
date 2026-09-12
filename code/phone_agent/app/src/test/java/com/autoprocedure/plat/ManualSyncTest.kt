package com.autoprocedure.plat

import com.autoprocedure.plat.job.ProgramCard
import org.junit.Assert.*
import org.junit.Test

class ManualSyncTest {
    private fun card(state: String) = ProgramCard(
        taskId = "task", title = "Test", summary = "", icon = "", state = state,
        packageName = "test.plugin", downloadUrl = "http://127.0.0.1:17891/artifacts/test",
        launchType = ProgramCard.LAUNCH_APK, programId = "", apkPath = "",
        entryClass = "test.plugin.Main", error = "", sha256 = "version-one", size = 100,
    )

    @Test fun incomingAndInterruptedDownloadsWaitForClick() {
        for (state in listOf(ProgramCard.STATE_DOWNLOADING, ProgramCard.STATE_INSTALLING)) {
            val waiting = card(state).awaitingSync()
            assertEquals(ProgramCard.STATE_PENDING_SYNC, waiting.state)
            assertEquals("等待同步", waiting.stateLabel)
            assertTrue(waiting.needsSync)
            assertFalse(waiting.clickable)
        }
    }

    @Test fun syncSkipsInstalledActiveAndUnfinishedPrograms() {
        for (state in listOf(ProgramCard.STATE_READY, ProgramCard.STATE_DOWNLOADING,
                ProgramCard.STATE_INSTALLING, ProgramCard.STATE_MAKING)) {
            assertFalse(card(state).needsSync)
        }
        assertTrue(card(ProgramCard.STATE_FAILED).needsSync)
        assertFalse(card(ProgramCard.STATE_FAILED).copy(downloadUrl = "").needsSync)
        assertEquals(ProgramCard.STATE_READY, card(ProgramCard.STATE_READY).awaitingSync().state)
    }

    @Test fun reconnectChecksWaitingTasksWithoutAuthorizingDownload() {
        val making = card(ProgramCard.STATE_MAKING).copy(downloadUrl = "")
        assertTrue(making.needsStatusCheck)
        assertFalse(making.needsSync)
        val completed = card(ProgramCard.STATE_DOWNLOADING).awaitingSync()
        assertTrue(completed.needsStatusCheck)
        assertTrue(completed.needsSync)
        assertEquals(ProgramCard.STATE_PENDING_SYNC, completed.state)
        assertFalse(card(ProgramCard.STATE_READY).needsStatusCheck)
        assertFalse(card(ProgramCard.STATE_FAILED).copy(downloadUrl = "").needsStatusCheck)
    }

    @Test fun taskCompletedAfterStartupBecomesAvailableForManualSync() {
        val current = card(ProgramCard.STATE_PENDING_SYNC)
        assertEquals(listOf(current), ManualProgramSync.candidates(emptyList(), listOf(current)))
        assertTrue(ManualProgramSync.candidates(emptyList(),
            listOf(current.copy(state = ProgramCard.STATE_MAKING))).isEmpty())
    }

    @Test fun liveVersionAndInstalledStateOverrideStaleStartupSnapshot() {
        val old = card(ProgramCard.STATE_PENDING_SYNC)
        val latest = old.copy(sha256 = "version-two")
        assertEquals(listOf(latest), ManualProgramSync.candidates(listOf(old), listOf(latest)))
        assertTrue(ManualProgramSync.candidates(listOf(old),
            listOf(latest.copy(state = ProgramCard.STATE_READY))).isEmpty())
    }
}
