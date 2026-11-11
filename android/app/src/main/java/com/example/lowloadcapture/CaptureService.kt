package com.example.lowloadcapture

import android.annotation.SuppressLint
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Intent
import android.content.pm.ServiceInfo
import android.hardware.display.DisplayManager
import android.hardware.display.VirtualDisplay
import android.media.*
import android.media.projection.MediaProjection
import android.media.projection.MediaProjectionManager
import android.os.Build
import android.os.IBinder
import android.util.Log
import android.view.Surface
import java.io.BufferedOutputStream
import java.io.DataOutputStream
import java.net.InetSocketAddress
import java.net.Socket
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicInteger
import kotlin.concurrent.thread
import kotlin.math.abs

class CaptureService : Service() {
    companion object {
        const val ACTION_START = "com.example.lowloadcapture.START"
        const val ACTION_STOP = "com.example.lowloadcapture.STOP"
        const val EXTRA_RESULT_CODE = "resultCode"
        const val EXTRA_RESULT_DATA = "resultData"
        const val EXTRA_BITRATE = "bitrate"
        
        private const val TAG = "LowLoadCapture"
        private const val PORT = 27183
        private const val DEFAULT_WIDTH = 1920
        private const val DEFAULT_HEIGHT = 1080
        private const val FPS = 60
        private const val DEFAULT_BITRATE = 8_000_000
        private const val MAX_QUEUE_SIZE = 2000

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
    }

    private class Packet(
        val track: Int,
        val flags: Int,
        val sequence: Int,
        val ptsUs: Long,
        val payload: ByteArray
    )

    private val running = AtomicBoolean(false)
    private val packetQueue = LinkedBlockingQueue<Packet>(MAX_QUEUE_SIZE)
    private val sequenceCounter = AtomicInteger(0)

    private var socket: Socket? = null
    private var output: DataOutputStream? = null
    private var projection: MediaProjection? = null
    private var virtualDisplay: VirtualDisplay? = null
    private var encoder: MediaCodec? = null
    private var videoSurface: Surface? = null
    private var gameAudio: AudioRecord? = null
    private var phoneMic: AudioRecord? = null

    private var captureWidth = DEFAULT_WIDTH
    private var captureHeight = DEFAULT_HEIGHT
    private var captureBitrate = DEFAULT_BITRATE

    // Single shared clock baseline for both video and audio streams
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

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            stopCapture()
            return START_NOT_STICKY
        }
        if (intent?.action == ACTION_START && !running.get()) {
            startForegroundCompat()
            val resultCode = intent.getIntExtra(EXTRA_RESULT_CODE, 0)
            captureBitrate = intent.getIntExtra(EXTRA_BITRATE, DEFAULT_BITRATE)
            
            @Suppress("DEPRECATION")
            val data = if (Build.VERSION.SDK_INT >= 33) {
                intent.getParcelableExtra(EXTRA_RESULT_DATA, Intent::class.java)
            } else {
                intent.getParcelableExtra(EXTRA_RESULT_DATA)
            }
            if (data == null) {
                Log.e(TAG, "Missing result data intent")
                stopSelf()
                return START_NOT_STICKY
            }

            thread(name = "CaptureInit") {
                try {
                    startCapture(resultCode, data)
                } catch (t: Throwable) {
                    Log.e(TAG, "Capture failed to start", t)
                    stopCapture()
                }
            }
        }
        return START_NOT_STICKY
    }

    private fun startForegroundCompat() {
        val channelId = "lowloadcapture_v21"
        val nm = getSystemService(NOTIFICATION_SERVICE) as NotificationManager
        if (Build.VERSION.SDK_INT >= 26) {
            nm.createNotificationChannel(
                NotificationChannel(channelId, "LowLoadCapture v2.1", NotificationManager.IMPORTANCE_LOW)
            )
        }
        val notification = if (Build.VERSION.SDK_INT >= 26) {
            Notification.Builder(this, channelId)
        } else {
            @Suppress("DEPRECATION")
            Notification.Builder(this)
        }.setContentTitle("LowLoadCapture v2.1 Active")
            .setContentText("Streaming 1080p60 capture & dual audio to Windows PC")
            .setSmallIcon(android.R.drawable.presence_video_online)
            .setOngoing(true)
            .build()

        if (Build.VERSION.SDK_INT >= 29) {
            startForeground(1, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROJECTION)
        } else {
            startForeground(1, notification)
        }
    }

    private fun startCapture(resultCode: Int, data: Intent) {
        // Set single shared clock baseline for synchronized video & audio timestamps
        captureStartNs = System.nanoTime()
        lastVideoPtsUs = -1L
        isVideoEOS = false

        // Connect to ADB-reversed localhost socket on Windows
        Log.i(TAG, "Connecting to socket localhost:$PORT...")
        val s = Socket()
        s.connect(InetSocketAddress("127.0.0.1", PORT), 5000)
        s.tcpNoDelay = true
        s.sendBufferSize = 1024 * 1024
        socket = s
        output = DataOutputStream(BufferedOutputStream(s.getOutputStream(), 1024 * 1024))
        Log.i(TAG, "Socket connected successfully")

        val mgr = getSystemService(MEDIA_PROJECTION_SERVICE) as MediaProjectionManager
        projection = mgr.getMediaProjection(resultCode, data)
        val mp = projection ?: error("MediaProjection unavailable")

        running.set(true)
        writerClosing = false
        isRecordingActive = true
        activeRecordStartMs = System.currentTimeMillis()

        // Start network writer thread first
        networkWriterThread = thread(name = "NetworkWriter") { networkWriterLoop() }

        val codec = createEncoder()
        encoder = codec
        videoSurface = codec.createInputSurface()
        codec.start()

        mp.registerCallback(object : MediaProjection.Callback() {
            override fun onStop() {
                Log.i(TAG, "MediaProjection callback onStop()")
                stopCapture()
            }
        }, null)

        virtualDisplay = mp.createVirtualDisplay(
            "LowLoadCapture-1080p60",
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
        val encoderInfo = EncoderChecker.checkEncoder()
        val format = MediaFormat.createVideoFormat(MediaFormat.MIMETYPE_VIDEO_AVC, captureWidth, captureHeight).apply {
            setInteger(MediaFormat.KEY_COLOR_FORMAT, MediaCodecInfo.CodecCapabilities.COLOR_FormatSurface)
            setInteger(MediaFormat.KEY_BIT_RATE, captureBitrate)
            setInteger(MediaFormat.KEY_FRAME_RATE, FPS)
            setInteger(MediaFormat.KEY_I_FRAME_INTERVAL, 1)
            if (Build.VERSION.SDK_INT >= 23) setInteger(MediaFormat.KEY_PRIORITY, 0)
            if (Build.VERSION.SDK_INT >= 29) setInteger(MediaFormat.KEY_MAX_B_FRAMES, 0)
        }

        Log.i(TAG, "Initializing hardware video encoder: ${encoderInfo.encoderName}")
        return MediaCodec.createByCodecName(encoderInfo.encoderName).apply {
            configure(format, null, null, MediaCodec.CONFIGURE_FLAG_ENCODE)
        }
    }

    private fun drainVideo(codec: MediaCodec) {
        val info = MediaCodec.BufferInfo()
        val captureStartUs = captureStartNs / 1000L
        var videoPtsOffsetUs = Long.MIN_VALUE

        try {
            while (running.get()) {
                val index = try {
                    codec.dequeueOutputBuffer(info, 10_000)
                } catch (e: Exception) {
                    Log.w(TAG, "Video dequeue exception", e)
                    break
                }

                when {
                    index == MediaCodec.INFO_OUTPUT_FORMAT_CHANGED -> {
                        val fmt = codec.outputFormat
                        for (key in listOf("csd-0", "csd-1")) {
                            val csd = fmt.getByteBuffer(key)
                            if (csd != null && csd.remaining() > 0) {
                                val duplicate = csd.duplicate()
                                val bytes = ByteArray(duplicate.remaining())
                                duplicate.get(bytes)
                                enqueuePacket(TRACK_VIDEO, MediaCodec.BUFFER_FLAG_CODEC_CONFIG, 0L, bytes)
                            }
                        }
                        Log.i(TAG, "Encoder output format changed: $fmt")
                    }
                    index >= 0 -> {
                        val buffer = codec.getOutputBuffer(index)
                        if (buffer != null && info.size > 0) {
                            buffer.position(info.offset)
                            buffer.limit(info.offset + info.size)
                            val bytes = ByteArray(info.size)
                            buffer.get(bytes)

                            val isConfig = (info.flags and MediaCodec.BUFFER_FLAG_CODEC_CONFIG) != 0
                            val ptsUs = if (isConfig) {
                                lastVideoPtsUs.coerceAtLeast(0L)
                            } else {
                                val nowRelUs = ((System.nanoTime() - captureStartNs) / 1000L).coerceAtLeast(0L)
                                if (videoPtsOffsetUs == Long.MIN_VALUE) {
                                    // Prefer MediaCodec Surface presentationTimeUs if it uses the CLOCK_MONOTONIC epoch
                                    val candidateUs = info.presentationTimeUs - captureStartUs
                                    videoPtsOffsetUs = if (candidateUs in -1_000_000L..10_000_000L && abs(candidateUs - nowRelUs) < 2_000_000L) {
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
                            enqueuePacket(TRACK_VIDEO, info.flags, ptsUs, bytes)
                        }
                        codec.releaseOutputBuffer(index, false)
                    }
                }
            }
        } finally {
            isVideoEOS = true
            Log.i(TAG, "Video drain finished at lastVideoPtsUs=$lastVideoPtsUs")
        }
    }

    @SuppressLint("MissingPermission")
    private fun startAudioCapture(mp: MediaProjection) {
        val sampleRate = 48000

        // 1. Game Audio (AudioPlaybackCapture - 48kHz Stereo)
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
                Log.i(TAG, "Game audio capture initialized successfully")
                gameAudioThread = thread(name = "GameAudioDrain") {
                    drainAudioTrack(gameAudio!!, TRACK_GAME_AUDIO, 2)
                }
            } else {
                Log.w(TAG, "Game audio record state not initialized")
                gameAudio = null
            }
        } catch (e: Exception) {
            Log.w(TAG, "Failed to initialize Game Audio capture: ${e.message}")
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
                Log.i(TAG, "Phone mic capture initialized successfully")
                phoneMicThread = thread(name = "PhoneMicDrain") {
                    drainAudioTrack(phoneMic!!, TRACK_PHONE_MIC, 1)
                }
            } else {
                Log.w(TAG, "Phone mic record state not initialized")
                phoneMic = null
            }
        } catch (e: Exception) {
            Log.w(TAG, "Failed to initialize Phone Mic capture: ${e.message}")
            phoneMic = null
        }
    }

    private fun drainAudioTrack(record: AudioRecord, trackId: Int, channels: Int) {
        val sampleRate = 48000
        val buffer = ByteArray(2048 * channels * 2) // ~42.67ms chunk
        var framesRead = 0L
        var expectedNextPtsUs = -1L
        val audioTs = AudioTimestamp()

        while (running.get()) {
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
                        val hwChunkStartNs = audioTs.nanoTime + ((framesRead - audioTs.framePosition) * 1_000_000_000L) / sampleRate
                        val hwPtsUs = ((hwChunkStartNs - captureStartNs) / 1000L).coerceAtLeast(0L)
                        if (abs(hwPtsUs - wallStartUs) <= 150_000L) {
                            anchoredUs = hwPtsUs
                        }
                    }

                    val ptsUs = if (expectedNextPtsUs < 0L) {
                        anchoredUs
                    } else if (abs(anchoredUs - expectedNextPtsUs) <= 45_000L) {
                        // Keep contiguous sample timing across minor thread scheduling jitter
                        expectedNextPtsUs
                    } else {
                        // Re-anchor cleanly if AudioRecord experienced an overrun or stall gap
                        maxOf(anchoredUs, expectedNextPtsUs)
                    }

                    expectedNextPtsUs = ptsUs + chunkDurUs
                    framesRead += frameCount
                    enqueuePacket(trackId, 0, ptsUs, buffer.copyOf(aligned))
                }
            } else if (read < 0) {
                Log.w(TAG, "AudioRecord read error track=$trackId code=$read")
                break
            }
        }
    }

    private fun enqueuePacket(track: Int, flags: Int, ptsUs: Long, payload: ByteArray) {
        if (!running.get()) return
        val seq = sequenceCounter.incrementAndGet() and 0xFFFF
        val pkt = Packet(track, flags, seq, ptsUs, payload)

        try {
            packetQueue.put(pkt)
        } catch (_: InterruptedException) {
            // Service stopping
        }
    }

    private fun networkWriterLoop() {
        val out = output ?: return
        var unwrittenCount = 0
        try {
            while ((!writerClosing && running.get()) || packetQueue.isNotEmpty()) {
                val pkt = packetQueue.poll(50, TimeUnit.MILLISECONDS) ?: continue

                // LCAP v2 Header: 20 bytes
                // 4B magic, 1B track, 1B flags, 2B sequence, 8B ptsUs, 4B size
                out.write(byteArrayOf(0x4c, 0x43, 0x41, 0x50)) // LCAP
                out.writeByte(pkt.track)
                out.writeByte(pkt.flags)
                out.writeShort(pkt.sequence)
                out.writeLong(pkt.ptsUs)
                out.writeInt(pkt.payload.size)
                out.write(pkt.payload)

                unwrittenCount++
                if (unwrittenCount >= 20 || packetQueue.isEmpty()) {
                    out.flush()
                    unwrittenCount = 0
                }
            }
        } catch (e: Exception) {
            Log.e(TAG, "USB stream network write error: ${e.message}")
            running.set(false)
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

        Log.i(TAG, "Stopping capture service...")
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
        val sock = socket.also { socket = null }
        val vThread = videoDrainThread.also { videoDrainThread = null }
        val gThread = gameAudioThread.also { gameAudioThread = null }
        val mThread = phoneMicThread.also { phoneMicThread = null }
        val wThread = networkWriterThread.also { networkWriterThread = null }

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
            val seq = sequenceCounter.incrementAndGet() and 0xFFFF
            packetQueue.offer(Packet(TRACK_VIDEO, FLAG_EOS, seq, stopPtsUs, ByteArray(0)))
            writerClosing = true
            try { wThread?.join(1000) } catch (_: Exception) {}
            try { outStream?.flush() } catch (_: Exception) {}
            try { outStream?.close() } catch (_: Exception) {}
            try { sock?.close() } catch (_: Exception) {}
            Log.i(TAG, "Capture service stopped completely at stopPtsUs=$stopPtsUs")
        }

        stopForeground(STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    override fun onDestroy() {
        stopCapture()
        super.onDestroy()
    }
}
