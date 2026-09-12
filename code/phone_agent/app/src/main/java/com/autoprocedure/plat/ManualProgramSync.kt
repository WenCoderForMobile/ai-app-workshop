package com.autoprocedure.plat

import com.autoprocedure.plat.job.ProgramCard

object ManualProgramSync {
    fun candidates(startup: List<ProgramCard>, current: List<ProgramCard>): List<ProgramCard> {
        // Live task state supersedes the startup snapshot, including installed and making states.
        val byTask = startup.associateBy { it.taskId }.toMutableMap()
        current.forEach { byTask[it.taskId] = it }
        return byTask.values.filter { it.needsSync }
    }
}
