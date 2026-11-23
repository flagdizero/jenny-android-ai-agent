package com.flagdizero.jenny

import com.google.zxing.BinaryBitmap
import com.google.zxing.LuminanceSource
import com.google.zxing.PlanarYUVLuminanceSource
import com.google.zxing.ReaderException
import com.google.zxing.common.HybridBinarizer
import com.google.zxing.qrcode.QRCodeReader

/**
 * La parte di [QrScanActivity] che non tocca Android: dal piano di luminanza di
 * un fotogramma al testo del QR. Fuori dall'activity perché un test JVM la
 * possa provare (`PairQrDecoderTest`) senza fotocamera.
 */
object PairQrDecoder {
    const val PAIR_PREFIX = "jenny://pair"

    fun isPairLink(text: String): Boolean = text.startsWith(PAIR_PREFIX)

    /**
     * Il testo del QR nel piano Y di un fotogramma YUV, o `null`.
     *
     * [rowStride] è la lunghezza vera di una riga nel buffer, che può essere
     * più larga dell'immagine: la si passa a ZXing come larghezza dei dati, e
     * l'ultima riga può essere corta (finisce a [width]). Con [inverted] legge
     * un QR chiaro su fondo scuro, come quello di un terminale col tema scuro.
     */
    fun decode(
        luminance: ByteArray,
        rowStride: Int,
        width: Int,
        height: Int,
        inverted: Boolean,
        reader: QRCodeReader = QRCodeReader(),
    ): String? {
        if (width <= 0 || height <= 0 || rowStride < width) return null
        if (luminance.size < rowStride * (height - 1) + width) return null
        val plain = PlanarYUVLuminanceSource(luminance, rowStride, height, 0, 0, width, height, false)
        val source: LuminanceSource = if (inverted) plain.invert() else plain
        return try {
            reader.decode(BinaryBitmap(HybridBinarizer(source))).text
        } catch (e: ReaderException) {
            null
        } finally {
            reader.reset()
        }
    }
}
