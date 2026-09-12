package com.autoprocedure.plat

import android.Manifest
import android.content.pm.PackageManager
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.util.Log
import androidx.core.app.ActivityCompat
import com.k2fsa.sherpa.onnx.OfflineRecognizer
import com.k2fsa.sherpa.onnx.OfflineRecognizerConfig
import com.k2fsa.sherpa.onnx.getFeatureConfig
import com.k2fsa.sherpa.onnx.getOfflineModelConfig

/**
 * Push-to-talk ASR: click start to buffer mic audio, click stop to decode
 * the whole clip with the non-streaming OfflineRecognizer.
 */
class PushToTalkAsr(private val activity: android.content.Context) {
    companion object {
        private const val TAG = "PushToTalkAsr"
        private const val SAMPLE_RATE = 16000
        const val ASR_MODEL_TYPE = 0
    }

    private var recognizer: OfflineRecognizer? = null
    private var audioRecord: AudioRecord? = null
    private val buffer = ArrayList<Short>()

    @Volatile
    var isRecording: Boolean = false
        private set

    @Volatile
    var isReady: Boolean = false
        private set

    fun init(): Boolean {
        val config = OfflineRecognizerConfig(
            featConfig = getFeatureConfig(sampleRate = SAMPLE_RATE, featureDim = 80),
            modelConfig = getOfflineModelConfig(type = ASR_MODEL_TYPE)!!,
        )
        recognizer = OfflineRecognizer(
            assetManager = activity.assets,
            config = config,
        )
        isReady = true
        Log.i(TAG, "OfflineRecognizer ready, asrModelType=$ASR_MODEL_TYPE")
        return true
    }

    fun startRecording(): Boolean {
        if (!isReady || isRecording) {
            return false
        }
        if (ActivityCompat.checkSelfPermission(
                activity,
                Manifest.permission.RECORD_AUDIO,
            ) != PackageManager.PERMISSION_GRANTED
        ) {
            return false
        }

        val minBytes = AudioRecord.getMinBufferSize(
            SAMPLE_RATE,
            AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
        )
        val recorder = AudioRecord(
            MediaRecorder.AudioSource.MIC,
            SAMPLE_RATE,
            AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
            minBytes * 2,
        )
        if (recorder.state != AudioRecord.STATE_INITIALIZED) {
            recorder.release()
            return false
        }

        buffer.clear()
        audioRecord = recorder
        isRecording = true
        recorder.startRecording()

        Thread({
            val chunk = ShortArray(512)
            while (isRecording) {
                val n = audioRecord?.read(chunk, 0, chunk.size) ?: -1
                if (n > 0) {
                    synchronized(buffer) {
                        for (i in 0 until n) {
                            buffer.add(chunk[i])
                        }
                    }
                }
            }
        }, "asr-record").start()
        return true
    }

    fun stopAndTranscribe(): String {
        isRecording = false
        audioRecord?.stop()
        audioRecord?.release()
        audioRecord = null

        val samples: ShortArray = synchronized(buffer) {
            buffer.toShortArray()
        }
        buffer.clear()
        if (samples.isEmpty()) {
            return ""
        }

        val floats = FloatArray(samples.size) { samples[it] / 32768.0f }
        val rec = recognizer ?: return ""
        val stream = rec.createStream()
        stream.acceptWaveform(floats, SAMPLE_RATE)
        rec.decode(stream)
        val text = rec.getResult(stream).text.trim()
        stream.release()
        return text
    }

    fun release() {
        isRecording = false
        audioRecord?.release()
        audioRecord = null
        recognizer?.release()
        recognizer = null
        isReady = false
    }
}
