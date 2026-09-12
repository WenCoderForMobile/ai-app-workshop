package com.autoprocedure.plat

import android.content.Context
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import java.security.MessageDigest

data class DownloadedArtifact(
    val file: File,
    val size: Long,
    val sha256: String,
)

/** Downloads one bounded artifact without buffering it in memory. */
class ArtifactDownloader(context: Context) {
    private val downloadRoot = File(context.cacheDir, "artifact-downloads")

    fun download(descriptor: ArtifactDescriptor): Result<DownloadedArtifact> =
        download(descriptor.downloadUrl, descriptor.size, descriptor.transportSha256)

    fun download(downloadUrl: String, size: Long, transportSha256: String): Result<DownloadedArtifact> = runCatching {
        require(size in 1..ArtifactDescriptor.MAX_ARTIFACT_BYTES) { "invalid artifact size" }
        ArtifactDescriptor.normalizedSha(transportSha256)
        val url = URL(downloadUrl)
        validateUrl(url)
        downloadRoot.mkdirs()
        val part = File.createTempFile("artifact-", ".part", downloadRoot)
        try {
            val connection = (url.openConnection() as? HttpURLConnection)
                ?: error("artifact URL is not HTTP(S)")
            try {
                connection.instanceFollowRedirects = false
                connection.connectTimeout = CONNECT_TIMEOUT_MS
                connection.readTimeout = READ_TIMEOUT_MS
                connection.requestMethod = "GET"
                connection.useCaches = false
                connection.setRequestProperty("Accept", "application/octet-stream")
                connection.setRequestProperty("Accept-Encoding", "identity")
                connection.connect()
                require(connection.responseCode == HttpURLConnection.HTTP_OK) {
                    "artifact HTTP ${connection.responseCode}"
                }
                val declaredLength = connection.getHeaderField("Content-Length")?.toLongOrNull() ?: -1L
                if (declaredLength >= 0) {
                    require(declaredLength == size) { "artifact Content-Length mismatch" }
                    require(declaredLength <= ArtifactDescriptor.MAX_ARTIFACT_BYTES) { "artifact is too large" }
                }
                val digest = MessageDigest.getInstance("SHA-256")
                var total = 0L
                connection.inputStream.use { input ->
                    part.outputStream().buffered().use { output ->
                        val buffer = ByteArray(32 * 1024)
                        while (true) {
                            val count = input.read(buffer)
                            if (count < 0) break
                            if (count == 0) continue
                            total += count
                            require(total <= size && total <= ArtifactDescriptor.MAX_ARTIFACT_BYTES) {
                                "artifact exceeded declared size"
                            }
                            digest.update(buffer, 0, count)
                            output.write(buffer, 0, count)
                        }
                    }
                }
                require(total == size) { "artifact size mismatch" }
                val sha256 = digest.digest().toHex()
                require(sha256 == transportSha256) { "artifact transport hash mismatch" }
                DownloadedArtifact(part, total, sha256)
            } finally {
                connection.disconnect()
            }
        } catch (error: Throwable) {
            part.delete()
            throw error
        }
    }

    private fun validateUrl(url: URL) {
        require(url.userInfo == null && url.ref == null) { "artifact URL contains forbidden credentials or fragment" }
        when (url.protocol.lowercase()) {
            "https" -> require(url.host.isNotBlank()) { "artifact HTTPS host is missing" }
            "http" -> require(
                BuildConfig.DEBUG && url.host == DEBUG_HOST && url.port == DEBUG_PORT,
            ) { "cleartext artifact URL is allowed only for the debug loopback server" }
            else -> throw IllegalArgumentException("artifact URL must use HTTPS")
        }
    }

    private fun ByteArray.toHex(): String = joinToString("") { "%02x".format(it) }

    companion object {
        private const val CONNECT_TIMEOUT_MS = 10_000
        private const val READ_TIMEOUT_MS = 60_000
        private const val DEBUG_HOST = "127.0.0.1"
        private const val DEBUG_PORT = 17891
    }
}
