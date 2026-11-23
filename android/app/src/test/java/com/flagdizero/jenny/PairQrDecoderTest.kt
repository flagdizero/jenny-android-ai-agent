package com.flagdizero.jenny

import com.google.zxing.BarcodeFormat
import com.google.zxing.qrcode.QRCodeWriter
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * La decodifica di [QrScanActivity] su fotogrammi finti: un QR disegnato nel
 * piano Y come lo consegna la fotocamera (righe più larghe dell'immagine,
 * ultima riga corta), chiaro su scuro o scuro su chiaro.
 */
class PairQrDecoderTest {

    private val link = "jenny://pair?host=mac.example.ts.net&ip=100.64.0.9&port=18790&code=TEST-QR42"

    /** Un fotogramma `width`×`height` col QR al centro; `stride` ≥ `width`. */
    private fun frame(text: String, width: Int, height: Int, stride: Int, lightOnDark: Boolean): ByteArray {
        val side = minOf(width, height) * 2 / 3
        val matrix = QRCodeWriter().encode(text, BarcodeFormat.QR_CODE, side, side)
        val left = (width - matrix.width) / 2
        val top = (height - matrix.height) / 2
        val dark: Byte = 20
        val light: Byte = 230.toByte()
        val background = if (lightOnDark) dark else light
        val module = if (lightOnDark) light else dark
        // L'ultima riga finisce a `width`: il buffer vero di un ImageProxy è
        // lungo così, non `stride * height`.
        val bytes = ByteArray(stride * (height - 1) + width) { background }
        for (y in 0 until matrix.height) {
            for (x in 0 until matrix.width) {
                if (matrix.get(x, y)) bytes[(top + y) * stride + left + x] = module
            }
        }
        return bytes
    }

    @Test
    fun readsAQrWhoseRowsArePaddedBeyondTheImage() {
        val bytes = frame(link, 640, 480, stride = 704, lightOnDark = false)
        assertEquals(link, PairQrDecoder.decode(bytes, 704, 640, 480, inverted = false))
    }

    @Test
    fun readsALightOnDarkQrOnlyWhenInverted() {
        val bytes = frame(link, 640, 480, stride = 640, lightOnDark = true)
        assertNull(PairQrDecoder.decode(bytes, 640, 640, 480, inverted = false))
        assertEquals(link, PairQrDecoder.decode(bytes, 640, 640, 480, inverted = true))
    }

    @Test
    fun aFrameWithoutAQrIsNull() {
        val empty = ByteArray(640 * 480) { 128.toByte() }
        assertNull(PairQrDecoder.decode(empty, 640, 640, 480, inverted = false))
    }

    @Test
    fun aBufferShorterThanTheImageIsRefusedNotRead() {
        assertNull(PairQrDecoder.decode(ByteArray(100), 640, 640, 480, inverted = false))
        assertNull(PairQrDecoder.decode(ByteArray(640 * 480), 600, 640, 480, inverted = false))
    }

    @Test
    fun onlyAJennyPairLinkCounts() {
        assertTrue(PairQrDecoder.isPairLink(link))
        assertFalse(PairQrDecoder.isPairLink("https://example.com/jenny://pair"))
        assertFalse(PairQrDecoder.isPairLink("jenny://other"))
    }
}
