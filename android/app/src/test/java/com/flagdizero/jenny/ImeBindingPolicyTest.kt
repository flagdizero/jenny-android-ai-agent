package com.flagdizero.jenny

import com.flagdizero.jenny.ImeBindingPolicy.Action
import com.flagdizero.jenny.ImeBindingPolicy.Dom
import org.junit.Assert.assertEquals
import org.junit.Test

class ImeBindingPolicyTest {

    private fun policyAt(dom: Dom) = ImeBindingPolicy().apply { domFocus(dom) }

    private fun ImeBindingPolicy.unbound() = check(accepting = false, windowFocused = true)

    @Test
    fun `a bound IME is left alone`() {
        assertEquals(Action.NONE, policyAt(Dom.TEXT).check(accepting = true, windowFocused = true))
    }

    @Test
    fun `the page refocuses first, then the IME is rebound, then it gives up once`() {
        val p = policyAt(Dom.TEXT)
        assertEquals(Action.REFOCUS, p.unbound())
        assertEquals(Action.REBIND, p.unbound())
        assertEquals(Action.GIVE_UP, p.unbound())
        // Dopo la resa, silenzio: niente giri in tondo, niente righe ripetute.
        assertEquals(Action.NONE, p.unbound())
    }

    @Test
    fun `a heal that works ends the sequence`() {
        val p = policyAt(Dom.TEXT)
        assertEquals(Action.REFOCUS, p.unbound())
        assertEquals(Action.NONE, p.check(accepting = true, windowFocused = true))
    }

    @Test
    fun `a new focus on a field is a new occasion`() {
        val p = policyAt(Dom.TEXT)
        repeat(3) { p.unbound() }
        p.domFocus(Dom.NONE)
        p.domFocus(Dom.TEXT)
        assertEquals(Action.REFOCUS, p.unbound())
    }

    @Test
    fun `refocusing the same field without a blur still counts`() {
        val p = policyAt(Dom.TEXT)
        p.unbound()
        p.domFocus(Dom.TEXT)
        assertEquals(Action.REFOCUS, p.unbound())
    }

    @Test
    fun `coming back to the foreground is a new occasion with the same focus`() {
        // Il caso del tasto Home: la pagina non dice niente, il campo ha ancora
        // il fuoco, e Chromium ha lasciato l'IME senza campo.
        val p = policyAt(Dom.TEXT)
        repeat(3) { p.unbound() }
        p.occasion()
        assertEquals(Action.REFOCUS, p.unbound())
    }

    @Test
    fun `an occasion without a text field does nothing`() {
        val p = policyAt(Dom.NONE)
        p.occasion()
        assertEquals(Action.NONE, p.unbound())
    }

    @Test
    fun `nothing to do without a text field`() {
        for (dom in listOf(Dom.NONE, Dom.FRAME)) {
            assertEquals(dom.name, Action.NONE, policyAt(dom).unbound())
        }
    }

    @Test
    fun `without window focus Android rebinds by itself`() {
        assertEquals(Action.NONE, policyAt(Dom.TEXT).check(accepting = false, windowFocused = false))
    }

    @Test
    fun `the page's words are parsed strictly`() {
        assertEquals(Dom.TEXT, Dom.parse("text"))
        assertEquals(Dom.NONE, Dom.parse("none"))
        assertEquals(Dom.FRAME, Dom.parse("frame"))
        assertEquals(null, Dom.parse("TEXT"))
        assertEquals(null, Dom.parse(""))
    }
}
