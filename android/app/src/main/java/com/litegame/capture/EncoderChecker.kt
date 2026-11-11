package com.litegame.capture

import android.media.MediaCodecInfo
import android.media.MediaCodecList
import android.media.MediaFormat
import android.os.Build

data class EncoderInfo(
    val encoderName: String,
    val isHardware: Boolean,
    val supportsProfile: Boolean,
    val description: String
)

object EncoderChecker {
    private const val MIME_TYPE = MediaFormat.MIMETYPE_VIDEO_AVC

    fun checkEncoder(
        width: Int = 1920,
        height: Int = 1080,
        fps: Double = 60.0
    ): EncoderInfo {
        val list = MediaCodecList(MediaCodecList.REGULAR_CODECS)
        val candidates = list.codecInfos.filter { info ->
            info.isEncoder && info.supportedTypes.any { it.equals(MIME_TYPE, ignoreCase = true) }
        }

        if (candidates.isEmpty()) {
            return EncoderInfo(
                encoderName = "None",
                isHardware = false,
                supportsProfile = false,
                description = "No H.264 encoder available on this device"
            )
        }

        val hwEncoders = candidates.filter { isHardwareEncoder(it) }
        val selected = hwEncoders.firstOrNull { supportsSurfaceAndProfile(it, width, height, fps) }
            ?: hwEncoders.firstOrNull { supportsSurface(it) }
            ?: candidates.firstOrNull { supportsSurfaceAndProfile(it, width, height, fps) }
            ?: candidates.first()

        val isHw = isHardwareEncoder(selected)
        val supports = supportsSurfaceAndProfile(selected, width, height, fps)
        val resLabel = "${height}p${fps.toInt()}"

        val desc = when {
            isHw && supports -> "Hardware H.264 ready ($resLabel)"
            supports -> "Software H.264 fallback ($resLabel)"
            isHw -> "Hardware H.264 present ($resLabel may drop frames)"
            else -> "Limited encoder ($resLabel not guaranteed)"
        }

        return EncoderInfo(
            encoderName = selected.name,
            isHardware = isHw,
            supportsProfile = supports,
            description = desc
        )
    }

    private fun isHardwareEncoder(info: MediaCodecInfo): Boolean {
        val name = info.name.lowercase()
        if (name.startsWith("omx.google.") || name.startsWith("c2.android.")) {
            return false
        }
        return if (Build.VERSION.SDK_INT >= 29) info.isHardwareAccelerated else true
    }

    private fun supportsSurface(info: MediaCodecInfo): Boolean {
        return try {
            val caps = info.getCapabilitiesForType(MIME_TYPE)
            caps.colorFormats.contains(MediaCodecInfo.CodecCapabilities.COLOR_FormatSurface)
        } catch (_: Exception) {
            false
        }
    }

    private fun supportsSurfaceAndProfile(
        info: MediaCodecInfo,
        width: Int,
        height: Int,
        fps: Double
    ): Boolean {
        return try {
            val caps = info.getCapabilitiesForType(MIME_TYPE)
            val hasSurface = caps.colorFormats.contains(MediaCodecInfo.CodecCapabilities.COLOR_FormatSurface)
            val vCaps = caps.videoCapabilities
            val sizeRateLandscape = vCaps.areSizeAndRateSupported(width, height, fps)
            val sizeRatePortrait = vCaps.areSizeAndRateSupported(height, width, fps)
            hasSurface && (sizeRateLandscape || sizeRatePortrait)
        } catch (_: Exception) {
            false
        }
    }
}
