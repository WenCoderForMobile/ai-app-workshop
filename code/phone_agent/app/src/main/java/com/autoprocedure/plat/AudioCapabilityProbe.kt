package com.autoprocedure.plat

import android.content.Context
import android.media.AudioManager
import android.media.ToneGenerator
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.HandlerThread
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import java.io.File
import java.util.UUID
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import kotlin.coroutines.resume
import kotlinx.coroutines.suspendCancellableCoroutine
import org.json.JSONObject

/** Inspect the installed host and silently synthesize a fixed English sample. No user data or playback. */
class AudioCapabilityProbe(context: Context) {
    private val context = context.applicationContext
    companion object {
        private val worker = Handler(HandlerThread("audio-capability").apply { start() }.looper)
        private val deadlines = Executors.newSingleThreadScheduledExecutor { task ->
            Thread(task, "audio-capability-timeout").apply { isDaemon = true }
        }
        // A stuck vendor Binder call may outlive our timeout; never pile up new engines/threads.
        private val occupied = AtomicBoolean(false)
    }

    suspend fun run(): JSONObject {
        val report = JSONObject().put("schemaVersion", 1)
            .put("host", JSONObject().put("packageName", context.packageName)
                .put("versionName", BuildConfig.VERSION_NAME).put("versionCode", BuildConfig.VERSION_CODE)
                .put("sdk", Build.VERSION.SDK_INT))
        val audio = JSONObject().put("status", "unknown").put("audibility", "not_tested")
        runCatching {
            val manager = context.getSystemService(Context.AUDIO_SERVICE) as AudioManager
            audio.put("mediaVolume", manager.getStreamVolume(AudioManager.STREAM_MUSIC))
                .put("mediaMaxVolume", manager.getStreamMaxVolume(AudioManager.STREAM_MUSIC))
                .put("muted", manager.getStreamVolume(AudioManager.STREAM_MUSIC) == 0 ||
                    (Build.VERSION.SDK_INT >= 23 && manager.isStreamMute(AudioManager.STREAM_MUSIC)))
            // Allocate/release only; never play a surprise sound or alter system volume.
            ToneGenerator(AudioManager.STREAM_MUSIC, 0).release()
            audio.put("status", "supported").put("reasonCode", "AUDIO_API_INITIALIZED")
        }.onFailure { audio.put("reasonCode", "AUDIO_API_CHECK_FAILED") }
        report.put("audioOutput", audio)
        report.put("offlineEnglishTts", checkOfflineEnglish())
        return report
    }

    private suspend fun checkOfflineEnglish(): JSONObject = suspendCancellableCoroutine { continuation ->
        if (!occupied.compareAndSet(false, true)) {
            continuation.resume(JSONObject().put("status", "unknown").put("reasonCode", "PROBE_BUSY"))
            return@suspendCancellableCoroutine
        }
        var engine: TextToSpeech? = null // Only read/written by the probe worker.
        var sample: File? = null
        val finished = AtomicBoolean(false)
        val evidenceLock = Any()
        val evidence = JSONObject().put("status", "unknown").put("scope", "default_engine")
            .put("audibility", "not_tested")
        fun record(vararg values: Pair<String, Any>) {
            synchronized(evidenceLock) { values.forEach { evidence.put(it.first, it.second) } }
        }
        fun finish(status: String, reason: String) {
            if (!finished.compareAndSet(false, true)) return
            val result = synchronized(evidenceLock) {
                evidence.put("status", status).put("reasonCode", reason)
                JSONObject(evidence.toString())
            }
            // Return before cleanup: vendor stop/shutdown can themselves block in Binder.
            if (continuation.isActive) continuation.resume(result)
            worker.post {
                try {
                    runCatching { engine?.stop() }
                    runCatching { engine?.shutdown() }
                    sample?.delete()
                } finally { occupied.set(false) }
            }
        }
        val timeout = deadlines.schedule({ finish("unknown", "PROBE_TIMEOUT") }, 6, TimeUnit.SECONDS)
        continuation.invokeOnCancellation { timeout.cancel(false); finish("unknown", "PROBE_CANCELLED") }
        worker.post {
            if (finished.get()) return@post
            try {
                engine = TextToSpeech(context) { status ->
                    worker.post initialized@{
                        if (finished.get()) return@initialized
                        val tts = engine
                        if (status != TextToSpeech.SUCCESS || tts == null) {
                            finish("unavailable", "TTS_INIT_FAILED")
                            return@initialized
                        }
                        try {
                            record("engine" to (tts.defaultEngine ?: ""))
                            val voice = tts.voices.orEmpty()
                                .filter { it.locale.language == "en" && !it.isNetworkConnectionRequired &&
                                    !it.features.orEmpty().contains(TextToSpeech.Engine.KEY_FEATURE_NOT_INSTALLED) }
                                .sortedWith(compareBy({ it.locale.country != "US" }, { it.name }))
                                .firstOrNull()
                            if (voice == null) {
                                finish("unavailable", "NO_INSTALLED_OFFLINE_ENGLISH_VOICE")
                                return@initialized
                            }
                            record("voice" to voice.name, "locale" to voice.locale.toLanguageTag(),
                                "networkRequired" to voice.isNetworkConnectionRequired)
                            if (tts.setVoice(voice) != TextToSpeech.SUCCESS) {
                                finish("unavailable", "VOICE_SELECTION_FAILED")
                                return@initialized
                            }
                            val utterance = UUID.randomUUID().toString()
                            sample = File.createTempFile("tts-probe-", ".wav", context.cacheDir)
                            tts.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
                                override fun onStart(id: String?) {}
                                override fun onDone(id: String?) {
                                    if (id != utterance) return
                                    worker.post {
                                        if (!finished.get()) {
                                            val file = sample
                                            val bytes = file?.length() ?: 0
                                            val valid = file != null && WaveEvidence.hasAudio(file)
                                            record("synthesizedBytes" to bytes)
                                            finish(if (valid) "supported" else "unavailable",
                                                if (valid) "OFFLINE_SYNTHESIS_VERIFIED" else "EMPTY_OR_INVALID_AUDIO")
                                        }
                                    }
                                }
                                @Deprecated("Android compatibility callback")
                                override fun onError(id: String?) { failed(id) }
                                override fun onError(id: String?, errorCode: Int) { failed(id) }
                                private fun failed(id: String?) {
                                    if (id == utterance) finish("unavailable", "SYNTHESIS_FAILED")
                                }
                            })
                            if (tts.synthesizeToFile("Hello. Welcome to English learning.", Bundle(), sample!!,
                                    utterance) != TextToSpeech.SUCCESS) {
                                finish("unavailable", "SYNTHESIS_REJECTED")
                            }
                        } catch (_: Exception) { finish("unknown", "TTS_CHECK_FAILED") }
                    }
                }
            } catch (_: Exception) { finish("unknown", "TTS_CHECK_FAILED") }
        }
    }
}
