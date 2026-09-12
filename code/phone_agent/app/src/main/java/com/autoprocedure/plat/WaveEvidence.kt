package com.autoprocedure.plat

import java.io.File
import java.io.RandomAccessFile

/** Validate a WAV data chunk, rather than trusting a successful TTS callback or header-only file. */
object WaveEvidence {
    fun hasAudio(file: File): Boolean = runCatching {
        RandomAccessFile(file, "r").use { input ->
            if (input.length() < 44) return false
            fun tag(): String = ByteArray(4).also { input.readFully(it) }.toString(Charsets.US_ASCII)
            fun size(): Long = Integer.reverseBytes(input.readInt()).toLong() and 0xffffffffL
            if (tag() != "RIFF") return false
            val end = size() + 8
            if (end > input.length() || tag() != "WAVE") return false
            while (input.filePointer + 8 <= end) {
                val name = tag()
                val length = size()
                val start = input.filePointer
                if (length > end - start) return false
                if (name == "data") return length > 0
                input.seek(start + length + (length and 1))
            }
            false
        }
    }.getOrDefault(false)
}
