package com.flagdizero.jenny

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class AlertConversationTest {

    @Test
    fun `a notebook tag names its notebook`() {
        assertEquals("project:Casa", AlertConversation.ofTag("project:Casa|message"))
        assertEquals("project:Casa", AlertConversation.ofTag("project:Casa|cron:Piante"))
    }

    @Test
    fun `every other tag is the personal chat`() {
        for (tag in listOf("message", "cron:spesa", "heartbeat", "update", "chat", "reply-failed")) {
            assertNull(tag, AlertConversation.ofTag(tag))
        }
        assertNull(AlertConversation.ofTag(null))
        // Senza separatore, o senza nome: non è un tag scritto da Python.
        assertNull(AlertConversation.ofTag("project:Casa"))
        assertNull(AlertConversation.ofTag("project:|message"))
    }

    @Test
    fun `the personal chat asks openChat with null`() {
        assertTrue(AlertConversation.openChatJs(null).contains("app.openChat(null)"))
    }

    @Test
    fun `a notebook name cannot break out of the JS string`() {
        val js = AlertConversation.openChatJs("project:a'b\"c\\d</script>")
        assertTrue(js.contains("app.openChat(\"project\\u003aa\\u0027b\\u0022c\\u005cd"))
        for (bad in listOf("'", "\\\"c", "</script>")) assertFalse(bad, js.contains("a$bad"))
    }

    @Test
    fun `js strings escape everything but letters and digits`() {
        assertEquals("\"Casa\\u002d1\"", AlertConversation.jsString("Casa-1"))
    }
}
