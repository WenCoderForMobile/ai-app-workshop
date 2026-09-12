package com.autoprocedure.connect.message

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertThrows
import org.junit.Test

class LineProtocolTest {
    @Test
    fun roundTripChinese() {
        val encoded = LineProtocol.encode(OutgoingMessage(text = "我想要喝水打卡"))
        val decoded = LineProtocol.decode(encoded)
        assertNotNull(decoded)
        assertEquals("chat", decoded!!.type)
        assertEquals("我想要喝水打卡", decoded.text)
    }

    @Test
    fun escapeQuotes() {
        val encoded = LineProtocol.encode(OutgoingMessage(text = "say \"hi\""))
        val decoded = LineProtocol.decode(encoded)
        assertEquals("say \"hi\"", decoded!!.text)
    }

    @Test
    fun roundTripsEveryJsonStringEscapeAndSupplementaryUnicode() {
        val text = "quote=\" slash=/ backslash=\\ controls=\b\u000c\n\r\t emoji=😀"
        val encoded = LineProtocol.encode(OutgoingMessage(type = "artifact_report", text = text))
        val decoded = LineProtocol.decode(encoded)

        assertEquals("artifact_report", decoded!!.type)
        assertEquals(text, decoded.text)
        assertTrue(encoded.toByteArray(Charsets.UTF_8).size <= LineProtocol.MAX_CONTROL_FRAME_BYTES)
    }

    @Test
    fun decodesNestedJobJsonAndUnicodeEscapes() {
        val line = "{\"type\":\"job\",\"text\":\"{\\\"status\\\":\\\"RUNNING\\\",\\\"title\\\":\\\"\\u4f60\\u597d\\\"}\"}"
        val decoded = LineProtocol.decode(line)

        assertEquals("job", decoded!!.type)
        assertEquals("{\"status\":\"RUNNING\",\"title\":\"你好\"}", decoded.text)
    }

    @Test
    fun rejectsMalformedOrDuplicateFieldsInsteadOfExtractingStringContent() {
        assertNull(LineProtocol.decode("{\"text\":\"fake \\\"type\\\":\\\"job\\\"\"}"))
        assertNull(LineProtocol.decode("{\"type\":\"chat\",\"type\":\"job\",\"text\":\"x\"}"))
        assertNull(LineProtocol.decode("{\"type\":\"chat\",\"text\":\"unterminated}"))
    }

    @Test
    fun enforcesControlFrameByteLimit() {
        val error = assertThrows(IllegalArgumentException::class.java) {
            LineProtocol.encode(OutgoingMessage(text = "界".repeat(LineProtocol.MAX_CONTROL_FRAME_BYTES)))
        }
        assertTrue(error.message.orEmpty().contains("control frame"))

        val oversizedInbound = "{\"type\":\"job\",\"text\":\"${"x".repeat(LineProtocol.MAX_CONTROL_FRAME_BYTES)}\"}"
        assertNull(LineProtocol.decode(oversizedInbound))
    }
}
