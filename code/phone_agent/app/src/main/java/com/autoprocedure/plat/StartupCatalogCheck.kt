package com.autoprocedure.plat

import com.autoprocedure.plat.job.ProgramCard

/** Process-local snapshot. Reconnects and live task updates never reopen this check. */
class StartupCatalogCheck {
    private var started = false
    private var completed = false
    private val cloud = linkedMapOf<String, ProgramCard>()
    private val missing = linkedMapOf<String, ProgramCard>()

    @Synchronized fun begin(): Boolean {
        if (started) return false
        started = true
        return true
    }

    @Synchronized fun receive(card: ProgramCard) {
        if (started && !completed) cloud[card.taskId] = card.awaitingSync()
    }

    @Synchronized fun complete(count: Int, installed: Set<Pair<String, String>>): Boolean {
        if (!started || completed || cloud.size != count) return false
        missing.putAll(cloud.filterValues { (it.taskId to it.sha256) !in installed })
        completed = true
        cloud.clear()
        return true
    }

    @Synchronized fun pending(): List<ProgramCard> = missing.values.toList()

    @Synchronized fun installed(taskId: String, sha256: String) {
        if (missing[taskId]?.sha256 == sha256) missing.remove(taskId)
    }
}
