package com.autoprocedure.connect.message

data class OutgoingMessage(
    val type: String = TYPE_CHAT,
    val text: String,
) {
    companion object {
        const val TYPE_CHAT = "chat"
    }
}

data class IncomingMessage(
    val type: String,
    val text: String,
    val programId: String? = null,
    val title: String? = null,
    val packageBase64: String? = null,
)
