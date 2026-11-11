package com.example.lowloadcapture

import android.media.MediaCodecInfo
import android.media.MediaCodecList
import android.media.MediaFormat
import android.util.Log

data class EncoderInfo(
    val encoderName: String,
    val isHardware: Boolean,
    val supports1080p60: Boolean,
    val description: String
)

object EncoderChecker {
    private const val TAG = "EncoderChecker"
    private const val MIME_TYPE = MediaFormat.MIMETYPE_VIDEO_AVC
    private const val WIDTH = 1920
    private const val HEIGHT = 1080
    private const val FPS = 60.0

    fun checkEncoder(): EncoderInfo {
        val list = MediaCodecList(MediaCodecList.REGULAR_CODECS)
        val candidate = list.codecInfos.firstOrNull { info ->
            info.isEncoder && info.supportedTypes.any { it.equals(MIME_TYPE, ignoreCase = true) }
        }

        if (candidate == null) {
            return EncoderInfo(
                encoderName = "None",
                isHardware = false,
                supports1080p60 = false,
                description = "No H.264 encoder found on this device."
            )
        }

        val hwEncoders = list.codecInfos.filter { info ->
            info.isEncoder &&
                info.supportedTypes.any { it.equals(MIME_TYPE, ignoreCase = true) } &&
                isHardwareEncoder(info)
        }

        val selected = hwEncoders.firstOrNull { supportsSurfaceAnd1080p60(it) }
            ?: list.codecInfos.firstOrNull { info ->
                info.isEncoder &&
                    info.supportedTypes.any { it.equals(MIME_TYPE, ignoreCase = true) } &&
                    supportsSurfaceAnd1080p60(info)
            }
            ?: candidate

        val isHw = isHardwareEncoder(selected)
        val supports60 = supportsSurfaceAnd1080p60(selected)

        val desc = when {
            isHw && supports60 -> "Hardware encoder ${selected.name} supports 1080p @ 60 FPS"
            supports60 -> "Software encoder ${selected.name} supports 1080p @ 60 FPS (Hardware not found)"
            else -> "Encoder ${selected.name} found, but 1080p@60 FPS surface encoding may be limited"
        }

        Log.i(TAG, desc)
        return EncoderInfo(
            encoderName = selected.name,
            isHardware = isHw,
            supports1080p60 = supports60,
            description = desc
        )
    }

    private fun isHardwareEncoder(info: MediaCodecInfo): Boolean {
        val name = info.name.lowercase()
        if (name.startsWith("omx.google.") || name.startsWith("c2.android.")) {
            return false
        }
        return true
    }

    private fun supportsSurfaceAnd1080p60(info: MediaCodecInfo): Boolean {
        return try {
            val caps = info.getCapabilitiesForType(MIME_TYPE)
            val hasSurface = caps.colorFormats.contains(MediaCodecInfo.CodecCapabilities.COLOR_FormatSurface)
            val vCaps = caps.videoCapabilities
            val sizeRate1 = vCaps.areSizeAndRateSupported(WIDTH, HEIGHT, FPS)
            val sizeRate2 = vCaps.areSizeAndRateSupported(HEIGHT, WIDTH, FPS)
            hasSurface && (sizeRate1 || sizeRate2)
        } catch (e: Exception) {
            false
        }
    }
}
