package com.autoprocedure.plat

import org.json.JSONObject

/** Immutable identity and transport contract for one declarative .apkg. */
data class ArtifactDescriptor(
    val taskId: String,
    val artifactId: String,
    val programId: String,
    val versionId: String,
    val revision: String,
    val downloadUrl: String,
    val transportSha256: String,
    val manifestSha256: String,
    val size: Long,
) {
    companion object {
        const val MAX_ARTIFACT_BYTES = 10L * 1024L * 1024L

        private val META_ID = Regex("[A-Za-z0-9][A-Za-z0-9._:-]{0,159}")
        private val PROGRAM_ID = Regex("[a-z][a-z0-9-]{0,47}")
        private val VERSION_ID = Regex("[A-Za-z0-9-]{1,64}")
        private val SHA256 = Regex("[0-9a-f]{64}")

        /** Accept both the canonical nested descriptor and the earlier flat job. */
        fun fromJob(job: JSONObject): ArtifactDescriptor {
            val source = job.optJSONObject("descriptor") ?: job
            val descriptor = ArtifactDescriptor(
                taskId = requiredString(source, "taskId", 160),
                artifactId = requiredString(source, "artifactId", 160),
                programId = requiredString(source, "programId"),
                versionId = requiredString(source, "versionId"),
                revision = requiredString(source, "revision"),
                downloadUrl = requiredString(source, "downloadUrl", 2_048),
                transportSha256 = normalizedSha(requiredString(source, "transportSha256")),
                manifestSha256 = normalizedSha(requiredString(source, "manifestSha256")),
                size = requiredLong(source, "size"),
            )
            require(META_ID.matches(descriptor.taskId)) { "invalid taskId" }
            require(META_ID.matches(descriptor.artifactId)) { "invalid artifactId" }
            require(PROGRAM_ID.matches(descriptor.programId)) { "invalid programId" }
            require(VERSION_ID.matches(descriptor.versionId)) { "invalid versionId" }
            require(VERSION_ID.matches(descriptor.revision)) { "invalid revision" }
            require(descriptor.size in 1..MAX_ARTIFACT_BYTES) { "invalid artifact size" }
            return descriptor
        }

        fun normalizedSha(value: String): String {
            val normalized = value.removePrefix("sha256:")
            require(SHA256.matches(normalized)) { "invalid SHA-256" }
            return normalized
        }

        private fun requiredString(source: JSONObject, key: String, maxLength: Int = 128): String {
            val value = source.opt(key)
            require(value is String && value.isNotBlank() && value.length <= maxLength) { "invalid $key" }
            return value
        }

        private fun requiredLong(source: JSONObject, key: String): Long {
            val value = source.opt(key)
            return when (value) {
                is Int -> value.toLong()
                is Long -> value
                else -> throw IllegalArgumentException("invalid $key")
            }
        }
    }
}
