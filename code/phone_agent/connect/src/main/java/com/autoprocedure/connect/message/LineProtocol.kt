package com.autoprocedure.connect.message

import java.nio.charset.StandardCharsets

/**
 * Strict newline-delimited JSON framing shared by chat and control messages.
 *
 * The outer envelope intentionally stays flat (`type` + string fields) so an
 * older bridge can forward it. Structured job/report payloads are JSON encoded
 * inside `text`. Legacy Base64 plugin frames are the sole large-frame exception.
 */
object LineProtocol {
    const val MAX_CONTROL_FRAME_BYTES = 64 * 1024
    const val MAX_LEGACY_FRAME_BYTES = 16 * 1024 * 1024

    private val TYPE = Regex("[a-z][a-z0-9_]{0,31}")

    fun encode(message: OutgoingMessage): String {
        require(TYPE.matches(message.type)) { "invalid message type" }
        val encoded = "{\"type\":\"${escape(message.type)}\",\"text\":\"${escape(message.text)}\"}"
        require(encoded.toByteArray(StandardCharsets.UTF_8).size + 1 <= MAX_CONTROL_FRAME_BYTES) {
            "control frame exceeds $MAX_CONTROL_FRAME_BYTES bytes"
        }
        return encoded
    }

    fun decode(line: String): IncomingMessage? {
        val encodedBytes = line.toByteArray(StandardCharsets.UTF_8).size
        if (encodedBytes == 0 || encodedBytes > MAX_LEGACY_FRAME_BYTES) {
            return null
        }
        val fields = try {
            StringObjectParser(line).parse()
        } catch (_: IllegalArgumentException) {
            return null
        }
        val type = fields["type"] ?: return null
        if (!TYPE.matches(type)) {
            return null
        }
        if (type != "plugin" && encodedBytes + 1 > MAX_CONTROL_FRAME_BYTES) {
            return null
        }
        return IncomingMessage(
            type = type,
            text = fields["text"].orEmpty(),
            programId = fields["programId"],
            title = fields["title"],
            packageBase64 = fields["packageBase64"],
        )
    }

    private fun escape(value: String): String {
        val output = StringBuilder(value.length + 16)
        var index = 0
        while (index < value.length) {
            val char = value[index]
            when (char) {
                '"' -> output.append("\\\"")
                '\\' -> output.append("\\\\")
                '\b' -> output.append("\\b")
                '\u000c' -> output.append("\\f")
                '\n' -> output.append("\\n")
                '\r' -> output.append("\\r")
                '\t' -> output.append("\\t")
                else -> when {
                    char.code < 0x20 -> output.append("\\u%04x".format(char.code))
                    Character.isHighSurrogate(char) -> {
                        require(index + 1 < value.length && Character.isLowSurrogate(value[index + 1])) {
                            "unpaired high surrogate"
                        }
                        output.append(char).append(value[index + 1])
                        index++
                    }
                    Character.isLowSurrogate(char) -> throw IllegalArgumentException("unpaired low surrogate")
                    else -> output.append(char)
                }
            }
            index++
        }
        return output.toString()
    }

    /** Parser for a JSON object whose values must be strings or null. */
    private class StringObjectParser(private val source: String) {
        private var index = 0

        fun parse(): Map<String, String?> {
            skipWhitespace()
            expect('{')
            skipWhitespace()
            val values = linkedMapOf<String, String?>()
            if (consume('}')) {
                finish()
                return values
            }
            while (true) {
                val key = readString()
                require(!values.containsKey(key)) { "duplicate JSON key" }
                skipWhitespace()
                expect(':')
                skipWhitespace()
                val value = if (peek() == '"') {
                    readString()
                } else {
                    expectWord("null")
                    null
                }
                values[key] = value
                skipWhitespace()
                if (consume('}')) {
                    finish()
                    return values
                }
                expect(',')
                skipWhitespace()
            }
        }

        private fun readString(): String {
            expect('"')
            val output = StringBuilder()
            while (index < source.length) {
                val char = source[index++]
                when {
                    char == '"' -> return output.toString()
                    char == '\\' -> readEscape(output)
                    char.code < 0x20 -> throw IllegalArgumentException("unescaped control character")
                    Character.isHighSurrogate(char) -> {
                        require(index < source.length && Character.isLowSurrogate(source[index])) {
                            "unpaired high surrogate"
                        }
                        output.append(char).append(source[index++])
                    }
                    Character.isLowSurrogate(char) -> throw IllegalArgumentException("unpaired low surrogate")
                    else -> output.append(char)
                }
            }
            throw IllegalArgumentException("unterminated JSON string")
        }

        private fun readEscape(output: StringBuilder) {
            require(index < source.length) { "truncated JSON escape" }
            when (val escaped = source[index++]) {
                '"', '\\', '/' -> output.append(escaped)
                'b' -> output.append('\b')
                'f' -> output.append('\u000c')
                'n' -> output.append('\n')
                'r' -> output.append('\r')
                't' -> output.append('\t')
                'u' -> {
                    val first = readHexCodeUnit()
                    when {
                        Character.isHighSurrogate(first) -> {
                            require(index + 1 < source.length && source[index] == '\\' && source[index + 1] == 'u') {
                                "high surrogate without low surrogate"
                            }
                            index += 2
                            val second = readHexCodeUnit()
                            require(Character.isLowSurrogate(second)) { "invalid low surrogate" }
                            output.append(first).append(second)
                        }
                        Character.isLowSurrogate(first) -> throw IllegalArgumentException("unpaired low surrogate")
                        else -> output.append(first)
                    }
                }
                else -> throw IllegalArgumentException("unsupported JSON escape")
            }
        }

        private fun readHexCodeUnit(): Char {
            require(index + 4 <= source.length) { "truncated unicode escape" }
            var value = 0
            repeat(4) {
                val digit = source[index++].digitToIntOrNull(16)
                    ?: throw IllegalArgumentException("invalid unicode escape")
                value = value * 16 + digit
            }
            return value.toChar()
        }

        private fun expectWord(word: String) {
            require(source.regionMatches(index, word, 0, word.length)) { "expected $word" }
            index += word.length
        }

        private fun expect(expected: Char) {
            require(index < source.length && source[index] == expected) { "expected $expected" }
            index++
        }

        private fun consume(expected: Char): Boolean {
            if (index < source.length && source[index] == expected) {
                index++
                return true
            }
            return false
        }

        private fun peek(): Char? = source.getOrNull(index)

        private fun skipWhitespace() {
            while (index < source.length && source[index] in charArrayOf(' ', '\t', '\r', '\n')) {
                index++
            }
        }

        private fun finish() {
            skipWhitespace()
            require(index == source.length) { "trailing JSON data" }
        }
    }
}
