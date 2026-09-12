package com.autoprocedure.plat

import java.io.File
import java.nio.ByteBuffer
import java.nio.ByteOrder
import org.junit.Assert.*
import org.junit.Test

class WaveEvidenceTest {
    private fun wave(samples: ByteArray, declared: Int = samples.size): ByteArray {
        val bytes = ByteBuffer.allocate(44 + samples.size).order(ByteOrder.LITTLE_ENDIAN)
        bytes.put("RIFF".toByteArray()).putInt(36 + samples.size).put("WAVEfmt ".toByteArray())
        bytes.putInt(16).putShort(1).putShort(1).putInt(16000).putInt(32000).putShort(2).putShort(16)
        bytes.put("data".toByteArray()).putInt(declared).put(samples)
        return bytes.array()
    }
    @Test fun callbackSuccessNeedsActualAudioData() {
        val file = File.createTempFile("wave-evidence-", ".wav")
        try {
            file.writeBytes(wave(byteArrayOf(1, 2, 3, 4)))
            assertTrue(WaveEvidence.hasAudio(file))
            file.writeBytes(wave(byteArrayOf()))
            assertFalse(WaveEvidence.hasAudio(file))
            file.writeBytes(wave(byteArrayOf(1, 2), declared = 100))
            assertFalse(WaveEvidence.hasAudio(file))
            file.writeBytes(ByteArray(100))
            assertFalse(WaveEvidence.hasAudio(file))
        } finally { file.delete() }
    }
}
