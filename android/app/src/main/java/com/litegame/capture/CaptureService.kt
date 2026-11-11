package com.litegame.capture

import android.annotation.SuppressLint
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Intent
import android.content.pm.ServiceInfo
import android.hardware.display.DisplayManager
import android.hardware.display.VirtualDisplay
import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioPlaybackCaptureConfiguration
import android.media.AudioRecord
import android.media.AudioTimestamp
import android.media.MediaCodec
import android.media.MediaCodecInfo
import android.media.MediaFormat
import android.media.MediaRecorder
import android.media.projection.MediaProjection
import android.media.projection.MediaProjectionManager
import android.os.Build
import android.os.IBinder
import android.os.Process
import android.util.Log
import android.view.Surface
import java.io.BufferedOutputStream
import java.io.InputStream
import java.net.InetSocketAddress
import java.net.Socket
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.util.concurrent.ConcurrentLinkedQueue
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicInteger
import kotlin.concurrent.thread
import kotlin.math.abs

class CaptureService : Service() {
    companion object {
        const val ACTION_START = "com.litegame.capture.START"
        const val ACTION_STOP = "com.litegame.capture.STOP"
        const val EXTRA_RESULT_CODE = "resultCode"
        const val EXTRA_RESULT_DATA = "resultData"
        const val EXTRA_WIDTH = "width"
        const val EXTRA_HEIGHT = "height"
        const val EXTRA_FPS = "fps"
        const val EXTRA_BITRATE = "bitrate"

        private const val TAG = "LiteGameCapture"
        private const val PORT = 27183
        private const val DEFAULT_WIDTH = 1920
        private const val DEFAULT_HEIGHT = 1080
        private const val DEFAULT_FPS = 60
        private const val DEFAULT_BITRATE = 8_000_000
        private const val MAX_QUEUE_SIZE = 2000
        private const val MAX_POOL_BUFFERS = 96

        const val TRACK_VIDEO = 1
        const val TRACK_GAME_AUDIO = 2
        const val TRACK_PHONE_MIC = 3

        const val FLAG_EOS = 4

        @Volatile
        var isRecordingActive: Boolean = false
            private set

        @Volatile
        var activeRecordStartMs: Long = 0L
            private set

        @Volatile
        var activeProfileLabel: String = "1080p60"
            private set
    }

    private class Packet(
        var track: Int = 0,
        var flags: Int = 0,
        var sequence: Int = 0,
        var ptsUs: Long = 0L,
        var length: Int = 0,
        var payload: ByteArray = ByteArray(0),
        var pooled: Boolean = false
    )

    private val running = AtomicBoolean(false)
    private val packetQueue = LinkedBlockingQueue<Packet>(MAX_QUEUE_SIZE)
    private val sequenceCounter = AtomicInteger(0)

    // Reusable pools keep ART GC pauses near zero during heavy BGMI gameplay
    private val packetPool = ConcurrentLinkedQueue<Packet>()
    private val gameAudioPool = ConcurrentLinkedQueue<ByteArray>()
    private val micAudioPool = ConcurrentLinkedQueue<ByteArray>()
    private val videoBufferPool = ConcurrentLinkedQueue<ByteArray>()

    private var socket: Socket? = null
    private var output: BufferedOutputStream? = null
    private var input: InputStream? = null
    private var projection: MediaProjection? = null
    private var virtualDisplay: VirtualDisplay? = null
    private var encoder: MediaCodec? = null
    private var videoSurface: Surface? = null
    private var gameAudio: AudioRecord? = null
    private var phoneMic: AudioRecord? = null

    private var captureWidth = DEFAULT_WIDTH
    private var captureHeight = DEFAULT_HEIGHT
    private var captureFps = DEFAULT_FPS
    private var captureBitrate = DEFAULT_BITRATE

    @Volatile
    private var captureStartNs: Long = 0L

    @Volatile
    private var lastVideoPtsUs: Long = -1L

    @Volatile
    private var isVideoEOS: Boolean = false

    @Volatile
    private var writerClosing: Boolean = false

    private var videoDrainThread: Thread? = null
    private var gameAudioThread: Thread? = null
    private var phoneMicThread: Thread? = null
    private var networkWriterThread: Thread? = null
    private var controlReaderThread: Thread? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            stopCapture()
            return START_NOT_STICKY
        }
        if (intent?.action == ACTION_START && !running.get()) {
            captureWidth = intent.getIntExtra(EXTRA_WIDTH, DEFAULT_WIDTH).coerceAtLeast(640)
            captureHeight = intent.getIntExtra(EXTRA_HEIGHT, DEFAULT_HEIGHT).coerceAtLeast(360)
            captureFps = intent.getIntExtra(EXTRA_FPS, DEFAULT_FPS).coerceIn(24, 60)
            captureBitrate = intent.getIntExtra(EXTRA_BITRATE, DEFAULT_BITRATE).coerceIn(1_500_000, 20_000_000)
            activeProfileLabel = "${captureHeight}p${captureFps}"

            startForegroundCompat()
            val resultCode = intent.getIntExtra(EXTRA_RESULT_CODE, 0)

            @Suppress("DEPRECATION")
            val data = if (Build.VERSION.SDK_INT >= 33) {
                intent.getParcelableExtra(EXTRA_RESULT_DATA, Intent::class.java)
            } else {
                intent.getParcelableExtra(EXTRA_RESULT_DATA)
            }
            if (data == null) {
                Log.e(TAG, "Missing MediaProjection result intent")
                stopForeground(STOP_FOREGROUND_REMOVE)
                stopSelf()
                return START_NOT_STICKY
            }

            thread(name = "CaptureInit") {
                try {
                    startCapture(resultCode, data)
                } catch (t: Throwable) {
                    Log.e(TAG, "Capture failed to start: ${t.message}")
                    stopCapture()
                }
            }
        }
        return START_NOT_STICKY
    }

    private fun startForegroundCompat() {
        val channelId = "litegame_capture_channel"
        val nm = getSystemService(NOTIFICATION_SERVICE) as NotificationManager
        if (Build.VERSION.SDK_INT >= 26) {
            nm.createNotificationChannel(
                NotificationChannel(channelId, "LiteGame Capture", NotificationManager.IMPORTANCE_LOW)
            )
        }

        val stopIntent = Intent(this, CaptureService::class.java).apply { action = ACTION_STOP }
        val stopFlags = PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        val stopPending = PendingIntent.getService(this, 1, stopIntent, stopFlags)

        val openIntent = Intent(this, MainActivity::class.java)
        val openPending = PendingIntent.getActivity(this, 2, openIntent, stopFlags)

        val builder = if (Build.VERSION.SDK_INT >= 26) {
            Notification.Builder(this, channelId)
        } else {
            @Suppress("DEPRECATION")
            Notification.Builder(this)
        }

        val notification = builder
            .setContentTitle("LiteGame Capture Active ($activeProfileLabel)")
            .setContentText("Recording hardware stream over USB")
            .setSmallIcon(android.R.drawable.presence_video_online)
            .setOngoing(true)
            .setContentIntent(openPending)
            .addAction(
                Notification.Action.Builder(
                    null as android.graphics.drawable.Icon?, "Stop", stopPending
                ).build()
            )
            .build()

        if (Build.VERSION.SDK_INT >= 29) {
            startForeground(1, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROJECTION)
        } else {
            startForeground(1, notification)
        }
    }

    private fun startCapture(resultCode: Int, data: Intent) {
        captureStartNs = System.nanoTime()
        lastVideoPtsUs = -1L
        isVideoEOS = false

        val s = Socket()
        s.connect(InetSocketAddress("127.0.0.1", PORT), 5000)
        s.tcpNoDelay = true
        s.sendBufferSize = 1024 * 1024
        socket = s
        output = BufferedOutputStream(s.getOutputStream(), 1024 * 1024)
        input = s.getInputStream()

        val mgr = getSystemService(MEDIA_PROJECTION_SERVICE) as MediaProjectionManager
        projection = mgr.getMediaProjection(resultCode, data)
        val mp = projection ?: error("MediaProjection unavailable")

        running.set(true)
        writerClosing = false
        isRecordingActive = true
        activeRecordStartMs = System.currentTimeMillis()

        networkWriterThread = thread(name = "NetworkWriter") { networkWriterLoop() }
        controlReaderThread = thread(name = "ControlReader") { controlReaderLoop() }

        val codec = createEncoder()
        encoder = codec
        videoSurface = codec.createInputSurface()
        codec.start()

        mp.registerCallback(object : MediaProjection.Callback() {
            override fun onStop() {
                stopCapture()
            }
        }, null)

        virtualDisplay = mp.createVirtualDisplay(
            "LiteGameCapture-$activeProfileLabel",
            captureWidth,
            captureHeight,
            resources.displayMetrics.densityDpi,
            DisplayManager.VIRTUAL_DISPLAY_FLAG_AUTO_MIRROR,
            videoSurface,
            null,
            null
        )

        startAudioCapture(mp)
        videoDrainThread = thread(name = "VideoDrain") { drainVideo(codec) }
    }

    private fun createEncoder(): MediaCodec {
        val encoderInfo = EncoderChecker.checkEncoder(captureWidth, captureHeight, captureFps.toDouble())
        val baseFormat = MediaFormat.createVideoFormat(MediaFormat.MIMETYPE_VIDEO_AVC, captureWidth, captureHeight).apply {
            setInteger(MediaFormat.KEY_COLOR_FORMAT, MediaCodecInfo.CodecCapabilities.COLOR_FormatSurface)
            setInteger(MediaFormat.KEY_BIT_RATE, captureBitrate)
            setInteger(MediaFormat.KEY_FRAME_RATE, captureFps)
            setInteger(MediaFormat.KEY_I_FRAME_INTERVAL, 2)
            if (Build.VERSION.SDK_INT >= 23) setInteger(MediaFormat.KEY_PRIORITY, 0)
            if (Build.VERSION.SDK_INT >= 29) setInteger(MediaFormat.KEY_MAX_B_FRAMES, 0)
        }

        val codec = MediaCodec.createByCodecName(encoderInfo.encoderName)
        val tunedFormat = MediaFormat(baseFormat).apply {
            setInteger(MediaFormat.KEY_BITRATE_MODE, MediaCodecInfo.EncoderCapabilities.BITRATE_MODE_VBR)
            if (Build.VERSION.SDK_INT >= 23) {
                setInteger(MediaFormat.KEY_OPERATING_RATE, captureFps)
            }
        }
        try {
            codec.configure(tunedFormat, null, null, MediaCodec.CONFIGURE_FLAG_ENCODE)
        } catch (_: Exception) {
            // Some low-end vendor codecs reject optional operating-rate or VBR keys
            codec.configure(baseFormat, null, null, MediaCodec.CONFIGURE_FLAG_ENCODE)
        }
        return codec
    }

    private fun borrowVideoBuffer(minSize: Int): ByteArray {
        val reused = videoBufferPool.poll()
        return if (reused != null && reused.size >= minSize) reused else ByteArray(minSize.coerceAtLeast(65536))
    }

    private fun drainVideo(codec: MediaCodec) {
        Process.setThreadPriority(Process.THREAD_PRIORITY_DISPLAY)
        val info = MediaCodec.BufferInfo()
        val captureStartUs = captureStartNs / 1000L
        var videoPtsOffsetUs = Long.MIN_VALUE

        try {
            while (running.get()) {
                var index = try {
                    codec.dequeueOutputBuffer(info, 10_000)
                } catch (_: Exception) {
                    break
                }

                while (running.get()) {
                    when {
                        index == MediaCodec.INFO_OUTPUT_FORMAT_CHANGED -> {
                            val fmt = codec.outputFormat
                            for (key in listOf("csd-0", "csd-1")) {
                                val csd = fmt.getByteBuffer(key)
                                if (csd != null && csd.remaining() > 0) {
                                    val dup = csd.duplicate()
                                    val size = dup.remaining()
                                    val bytes = ByteArray(size)
                                    dup.get(bytes)
                                    enqueuePacket(TRACK_VIDEO, MediaCodec.BUFFER_FLAG_CODEC_CONFIG, 0L, bytes, size, pooled = false)
                                }
                            }
                        }
                        index >= 0 -> {
                            val buffer = codec.getOutputBuffer(index)
                            if (buffer != null && info.size > 0) {
                                buffer.position(info.offset)
                                buffer.limit(info.offset + info.size)
                                val buf = borrowVideoBuffer(info.size)
                                buffer.get(buf, 0, info.size)

                                val isConfig = (info.flags and MediaCodec.BUFFER_FLAG_CODEC_CONFIG) != 0
                                val ptsUs = if (isConfig) {
                                    lastVideoPtsUs.coerceAtLeast(0L)
                                } else {
                                    val nowRelUs = ((System.nanoTime() - captureStartNs) / 1000L).coerceAtLeast(0L)
                                    if (videoPtsOffsetUs == Long.MIN_VALUE) {
                                        val candidateUs = info.presentationTimeUs - captureStartUs
                                        videoPtsOffsetUs = if (
                                            candidateUs in -1_000_000L..10_000_000L &&
                                            abs(candidateUs - nowRelUs) < 2_000_000L
                                        ) {
                                            captureStartUs
                                        } else {
                                            info.presentationTimeUs - nowRelUs
                                        }
                                    }
                                    var normalized = (info.presentationTimeUs - videoPtsOffsetUs).coerceAtLeast(0L)
                                    if (lastVideoPtsUs >= 0L && normalized <= lastVideoPtsUs) {
                                        normalized = lastVideoPtsUs + 1L
                                    }
                                    lastVideoPtsUs = normalized
                                    normalized
                                }
                                enqueuePacket(TRACK_VIDEO, info.flags, ptsUs, buf, info.size, pooled = true)
                            }
                            codec.releaseOutputBuffer(index, false)
                        }
                        else -> break
                    }

                    // Drain any extra queued buffers immediately without waiting another 10ms timeout
                    index = try {
                        codec.dequeueOutputBuffer(info, 0)
                    } catch (_: Exception) {
                        MediaCodec.INFO_TRY_AGAIN_LATER
                    }
                    if (index == MediaCodec.INFO_TRY_AGAIN_LATER) break
                }
            }
        } finally {
            isVideoEOS = true
        }
    }

    @SuppressLint("MissingPermission")
    private fun startAudioCapture(mp: MediaProjection) {
        val sampleRate = 48000

        // 1. Internal Game Audio (AudioPlaybackCapture - 48kHz Stereo)
        try {
            val playbackConfig = AudioPlaybackCaptureConfiguration.Builder(mp)
                .addMatchingUsage(AudioAttributes.USAGE_GAME)
                .addMatchingUsage(AudioAttributes.USAGE_MEDIA)
                .build()

            val stereoFormat = AudioFormat.Builder()
                .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                .setSampleRate(sampleRate)
                .setChannelMask(AudioFormat.CHANNEL_IN_STEREO)
                .build()

            val gameBufferSize = (AudioRecord.getMinBufferSize(
                sampleRate, AudioFormat.CHANNEL_IN_STEREO, AudioFormat.ENCODING_PCM_16BIT
            ) * 4).coerceAtLeast(8192)

            gameAudio = AudioRecord.Builder()
                .setAudioFormat(stereoFormat)
                .setBufferSizeInBytes(gameBufferSize)
                .setAudioPlaybackCaptureConfig(playbackConfig)
                .build()

            if (gameAudio?.state == AudioRecord.STATE_INITIALIZED) {
                gameAudio?.startRecording()
                gameAudioThread = thread(name = "GameAudioDrain") {
                    drainAudioTrack(gameAudio!!, TRACK_GAME_AUDIO, 2, gameAudioPool)
                }
            } else {
                gameAudio = null
            }
        } catch (e: Exception) {
            Log.w(TAG, "Game audio init skipped: ${e.message}")
            gameAudio = null
        }

        // 2. Phone Microphone (AudioRecord MIC - 48kHz Mono)
        try {
            val monoFormat = AudioFormat.Builder()
                .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                .setSampleRate(sampleRate)
                .setChannelMask(AudioFormat.CHANNEL_IN_MONO)
                .build()

            val micBufferSize = (AudioRecord.getMinBufferSize(
                sampleRate, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT
            ) * 4).coerceAtLeast(8192)

            phoneMic = AudioRecord.Builder()
                .setAudioSource(MediaRecorder.AudioSource.MIC)
                .setAudioFormat(monoFormat)
                .setBufferSizeInBytes(micBufferSize)
                .build()

            if (phoneMic?.state == AudioRecord.STATE_INITIALIZED) {
                phoneMic?.startRecording()
                phoneMicThread = thread(name = "PhoneMicDrain") {
                    drainAudioTrack(phoneMic!!, TRACK_PHONE_MIC, 1, micAudioPool)
                }
            } else {
                phoneMic = null
            }
        } catch (e: Exception) {
            Log.w(TAG, "Phone mic init skipped: ${e.message}")
            phoneMic = null
        }
    }

    private fun drainAudioTrack(
        record: AudioRecord,
        trackId: Int,
        channels: Int,
        pool: ConcurrentLinkedQueue<ByteArray>
    ) {
        Process.setThreadPriority(Process.THREAD_PRIORITY_AUDIO)
        val sampleRate = 48000
        val chunkBytes = 2048 * channels * 2 // ~42.67ms
        var framesRead = 0L
        var expectedNextPtsUs = -1L
        val audioTs = AudioTimestamp()

        while (running.get()) {
            val buffer = pool.poll() ?: ByteArray(chunkBytes)
            val read = record.read(buffer, 0, buffer.size, AudioRecord.READ_BLOCKING)
            if (read > 0) {
                val readDoneNs = System.nanoTime()
                val aligned = read - (read % (2 * channels))
                if (aligned > 0) {
                    val frameCount = aligned / (2 * channels)
                    val chunkDurUs = (frameCount * 1_000_000L) / sampleRate
                    val wallStartUs = (((readDoneNs - captureStartNs) / 1000L) - chunkDurUs).coerceAtLeast(0L)

                    var anchoredUs = wallStartUs
                    val tsResult = try {
                        record.getTimestamp(audioTs, AudioTimestamp.TIMEBASE_MONOTONIC)
                    } catch (_: Exception) {
                        AudioRecord.ERROR_INVALID_OPERATION
                    }
                    if (tsResult == AudioRecord.SUCCESS && audioTs.nanoTime > 0L) {
                        val hwChunkStartNs =
                            audioTs.nanoTime + ((framesRead - audioTs.framePosition) * 1_000_000_000L) / sampleRate
                        val hwPtsUs = ((hwChunkStartNs - captureStartNs) / 1000L).coerceAtLeast(0L)
                        if (abs(hwPtsUs - wallStartUs) <= 150_000L) {
                            anchoredUs = hwPtsUs
                        }
                    }

                    val ptsUs = if (expectedNextPtsUs < 0L) {
                        anchoredUs
                    } else if (abs(anchoredUs - expectedNextPtsUs) <= 45_000L) {
                        expectedNextPtsUs
                    } else {
                        maxOf(anchoredUs, expectedNextPtsUs)
                    }

                    expectedNextPtsUs = ptsUs + chunkDurUs
                    framesRead += frameCount
                    enqueuePacket(trackId, 0, ptsUs, buffer, aligned, pooled = true)
                } else if (pool.size < MAX_POOL_BUFFERS) {
                    pool.offer(buffer)
                }
            } else {
                if (pool.size < MAX_POOL_BUFFERS) pool.offer(buffer)
                if (read < 0) break
            }
        }
    }

    private fun enqueuePacket(
        track: Int,
        flags: Int,
        ptsUs: Long,
        payload: ByteArray,
        length: Int,
        pooled: Boolean
    ) {
        if (!running.get()) {
            recyclePayload(track, payload, pooled)
            return
        }
        val pkt = packetPool.poll() ?: Packet()
        pkt.track = track
        pkt.flags = flags
        pkt.sequence = sequenceCounter.incrementAndGet() and 0xFFFF
        pkt.ptsUs = ptsUs
        pkt.length = length
        pkt.payload = payload
        pkt.pooled = pooled

        if (!packetQueue.offer(pkt)) {
            // Non-blocking drop recovery if USB stalls; avoids freezing encoder or BGMI
            recyclePacket(pkt)
        }
    }

    private fun recyclePayload(track: Int, payload: ByteArray, pooled: Boolean) {
        if (!pooled || payload.isEmpty()) return
        val targetPool = when (track) {
            TRACK_VIDEO -> videoBufferPool
            TRACK_GAME_AUDIO -> gameAudioPool
            TRACK_PHONE_MIC -> micAudioPool
            else -> return
        }
        if (targetPool.size < MAX_POOL_BUFFERS) {
            targetPool.offer(payload)
        }
    }

    private fun recyclePacket(pkt: Packet) {
        recyclePayload(pkt.track, pkt.payload, pkt.pooled)
        pkt.payload = ByteArray(0)
        pkt.length = 0
        pkt.pooled = false
        if (packetPool.size < MAX_POOL_BUFFERS * 2) {
            packetPool.offer(pkt)
        }
    }

    private fun networkWriterLoop() {
        val out = output ?: return
        val headerBytes = ByteArray(20)
        val headerBuf = ByteBuffer.wrap(headerBytes).order(ByteOrder.BIG_ENDIAN)
        var unwrittenCount = 0

        try {
            while ((!writerClosing && running.get()) || packetQueue.isNotEmpty()) {
                val pkt = packetQueue.poll(50, TimeUnit.MILLISECONDS) ?: continue
                headerBuf.clear()
                headerBuf.put(0x4c).put(0x43).put(0x41).put(0x50) // LCAP
                headerBuf.put(pkt.track.toByte())
                headerBuf.put(pkt.flags.toByte())
                headerBuf.putShort(pkt.sequence.toShort())
                headerBuf.putLong(pkt.ptsUs)
                headerBuf.putInt(pkt.length)

                out.write(headerBytes, 0, 20)
                if (pkt.length > 0) {
                    out.write(pkt.payload, 0, pkt.length)
                }
                recyclePacket(pkt)

                unwrittenCount++
                if (unwrittenCount >= 16 || packetQueue.isEmpty()) {
                    out.flush()
                    unwrittenCount = 0
                }
            }
        } catch (e: Exception) {
            Log.e(TAG, "USB stream error: ${e.message}")
            running.set(false)
        }
    }

    private fun controlReaderLoop() {
        Process.setThreadPriority(Process.THREAD_PRIORITY_BACKGROUND)
        val inp = input ?: return
        val cmdBuf = ByteArray(4)
        try {
            while (running.get()) {
                var readTotal = 0
                while (readTotal < 4) {
                    val n = inp.read(cmdBuf, readTotal, 4 - readTotal)
                    if (n <= 0) return
                    readTotal += n
                }
                if (cmdBuf[0] == 'S'.code.toByte() &&
                    cmdBuf[1] == 'T'.code.toByte() &&
                    cmdBuf[2] == 'O'.code.toByte() &&
                    cmdBuf[3] == 'P'.code.toByte()
                ) {
                    stopCapture()
                    return
                }
            }
        } catch (_: Exception) {
            // Socket closed during normal shutdown
        }
    }

    private fun stopCapture() {
        val wasRunning = running.getAndSet(false)
        val mp = projection
        projection = null
        isRecordingActive = false
        activeRecordStartMs = 0L

        if (!wasRunning && mp == null) {
            stopSelf()
            return
        }

        val stopPtsUs = if (captureStartNs > 0L) {
            ((System.nanoTime() - captureStartNs) / 1000L).coerceAtLeast(lastVideoPtsUs.coerceAtLeast(0L))
        } else {
            lastVideoPtsUs.coerceAtLeast(0L)
        }
        isVideoEOS = true

        val gAudio = gameAudio.also { gameAudio = null }
        val pMic = phoneMic.also { phoneMic = null }
        val vDisplay = virtualDisplay.also { virtualDisplay = null }
        val enc = encoder.also { encoder = null }
        val vSurf = videoSurface.also { videoSurface = null }
        val outStream = output.also { output = null }
        val inStream = input.also { input = null }
        val sock = socket.also { socket = null }
        val vThread = videoDrainThread.also { videoDrainThread = null }
        val gThread = gameAudioThread.also { gameAudioThread = null }
        val mThread = phoneMicThread.also { phoneMicThread = null }
        val wThread = networkWriterThread.also { networkWriterThread = null }
        controlReaderThread = null

        try { gAudio?.stop() } catch (_: Exception) {}
        try { pMic?.stop() } catch (_: Exception) {}
        try { vDisplay?.release() } catch (_: Exception) {}
        try { mp?.stop() } catch (_: Exception) {}
        try { enc?.stop() } catch (_: Exception) {}
        try { enc?.release() } catch (_: Exception) {}
        try { vSurf?.release() } catch (_: Exception) {}
        try { gAudio?.release() } catch (_: Exception) {}
        try { pMic?.release() } catch (_: Exception) {}

        thread(name = "CaptureStopDrain") {
            try { vThread?.join(300) } catch (_: Exception) {}
            try { gThread?.join(300) } catch (_: Exception) {}
            try { mThread?.join(300) } catch (_: Exception) {}
            val eosPkt = Packet(
                track = TRACK_VIDEO,
                flags = FLAG_EOS,
                sequence = sequenceCounter.incrementAndGet() and 0xFFFF,
                ptsUs = stopPtsUs,
                length = 0,
                payload = ByteArray(0),
                pooled = false
            )
            packetQueue.offer(eosPkt)
            writerClosing = true
            try { wThread?.join(1000) } catch (_: Exception) {}
            try { outStream?.flush() } catch (_: Exception) {}
            try { outStream?.close() } catch (_: Exception) {}
            try { inStream?.close() } catch (_: Exception) {}
            try { sock?.close() } catch (_: Exception) {}
        }

        stopForeground(STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    override fun onDestroy() {
        stopCapture()
        super.onDestroy()
    }
}
