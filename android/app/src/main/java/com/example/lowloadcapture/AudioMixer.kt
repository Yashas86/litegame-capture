package com.example.lowloadcapture

import java.nio.ByteBuffer
import java.nio.ByteOrder

object AudioMixer {
    /**
     * Mixes game audio (16-bit PCM stereo, 48kHz) and phone mic audio (16-bit PCM mono, 48kHz)
     * into a single 16-bit PCM stereo output buffer.
     */
    fun mixStereoAndMono(
        gamePcm: ByteArray,
        gameLength: Int,
        micPcm: ByteArray,
        micLength: Int
    ): ByteArray {
        val gameSampleCount = gameLength / 2 // total 16-bit samples in game (L R L R ...)
        val gameFrameCount = gameSampleCount / 2 // total stereo frames
        val micSampleCount = micLength / 2 // total 16-bit samples in mic (M M M ...)

        val frameCount = maxOf(gameFrameCount, micSampleCount)
        if (frameCount == 0) return ByteArray(0)

        val outputBytes = ByteArray(frameCount * 4) // 2 channels * 2 bytes = 4 bytes per stereo frame
        val gameBb = ByteBuffer.wrap(gamePcm, 0, gameLength).order(ByteOrder.LITTLE_ENDIAN)
        val micBb = ByteBuffer.wrap(micPcm, 0, micLength).order(ByteOrder.LITTLE_ENDIAN)
        val outBb = ByteBuffer.wrap(outputBytes).order(ByteOrder.LITTLE_ENDIAN)

        for (i in 0 until frameCount) {
            val gameL = if (i * 2 < gameSampleCount) gameBb.short.toInt() else 0
            val gameR = if (i * 2 + 1 < gameSampleCount) gameBb.short.toInt() else 0
            val micM = if (i < micSampleCount) micBb.short.toInt() else 0

            // Mix mic into both L and R channels with gain scaling
            val mixedL = (gameL + micM).coerceIn(-32768, 32767).toShort()
            val mixedR = (gameR + micM).coerceIn(-32768, 32767).toShort()

            outBb.putShort(mixedL)
            outBb.putShort(mixedR)
        }

        return outputBytes
    }
}
