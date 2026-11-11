package com.litegame.capture

import android.Manifest
import android.app.Activity
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.media.projection.MediaProjectionManager
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.util.TypedValue
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import androidx.activity.ComponentActivity
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import java.util.Locale

class MainActivity : ComponentActivity() {

    private data class RecordingProfile(
        val id: String,
        val title: String,
        val subtitle: String,
        val width: Int,
        val height: Int,
        val fps: Int,
        val defaultBitrate: Int
    )

    private val profiles = listOf(
        RecordingProfile("low", "Low Impact", "720p • 30 FPS • 4 Mbps (Low-End Devices)", 1280, 720, 30, 4_000_000),
        RecordingProfile("balanced", "Balanced", "1080p • 30 FPS • 6 Mbps (Mid-Range Devices)", 1920, 1080, 30, 6_000_000),
        RecordingProfile("high", "High Quality", "1080p • 60 FPS • 8 Mbps (Target 60 FPS)", 1920, 1080, 60, 8_000_000)
    )

    private lateinit var statusDot: View
    private lateinit var statusText: TextView
    private lateinit var timerText: TextView
    private lateinit var encoderStatusText: TextView
    private lateinit var startButton: Button
    private lateinit var stopButton: Button
    private val profileButtons = mutableMapOf<String, Button>()
    private val bitrateButtons = mutableMapOf<Int, Button>()

    private var selectedProfile = profiles[2] // Default: High Quality (1080p60 if supported)
    private var selectedBitrate = 8_000_000
    private var isRecording = false
    private var recordStartTime = 0L

    private val handler = Handler(Looper.getMainLooper())
    private val timerRunnable = object : Runnable {
        override fun run() {
            if (isRecording) {
                val elapsedSec = ((System.currentTimeMillis() - recordStartTime) / 1000L).coerceAtLeast(0L)
                val hours = elapsedSec / 3600
                val mins = (elapsedSec % 3600) / 60
                val secs = elapsedSec % 60
                timerText.text = String.format(Locale.US, "REC %02d:%02d:%02d", hours, mins, secs)
                handler.postDelayed(this, 1000)
            }
        }
    }

    private val projectionLauncher =
        registerForActivityResult(ActivityResultContracts.StartActivityForResult()) { result ->
            if (result.resultCode == Activity.RESULT_OK && result.data != null) {
                val intent = Intent(this, CaptureService::class.java).apply {
                    action = CaptureService.ACTION_START
                    putExtra(CaptureService.EXTRA_RESULT_CODE, result.resultCode)
                    putExtra(CaptureService.EXTRA_RESULT_DATA, result.data)
                    putExtra(CaptureService.EXTRA_WIDTH, selectedProfile.width)
                    putExtra(CaptureService.EXTRA_HEIGHT, selectedProfile.height)
                    putExtra(CaptureService.EXTRA_FPS, selectedProfile.fps)
                    putExtra(CaptureService.EXTRA_BITRATE, selectedBitrate)
                }
                ContextCompat.startForegroundService(this, intent)

                recordStartTime = System.currentTimeMillis()
                setRecordingUiState(
                    active = true,
                    statusMsg = "Recording ${selectedProfile.height}p${selectedProfile.fps} via USB",
                    statusColor = "#22C55E"
                )
            } else {
                setRecordingUiState(
                    active = false,
                    statusMsg = "Screen capture permission cancelled",
                    statusColor = "#EF4444"
                )
            }
        }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        requestPermissionsIfNeeded()

        if (Build.VERSION.SDK_INT < 35) {
            @Suppress("DEPRECATION")
            window.statusBarColor = Color.parseColor("#0B0F17")
            @Suppress("DEPRECATION")
            window.navigationBarColor = Color.parseColor("#0B0F17")
        }

        // Auto-pick Balanced (1080p30) on devices whose hardware encoder cannot guarantee 1080p60
        if (!EncoderChecker.checkEncoder(1920, 1080, 60.0).supportsProfile) {
            selectedProfile = profiles[1]
            selectedBitrate = selectedProfile.defaultBitrate
        }

        val rootLayout = ScrollView(this).apply {
            setBackgroundColor(Color.parseColor("#0B0F17"))
            isFillViewport = true
        }

        val mainLayout = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(20), dp(22), dp(20), dp(24))
        }

        // 1. Header
        val titleText = TextView(this).apply {
            text = "LiteGame Capture"
            textSize = 24f
            setTypeface(null, Typeface.BOLD)
            setTextColor(Color.parseColor("#F8FAFC"))
        }

        val subtitleText = TextView(this).apply {
            text = "Lightweight Game Recording. Less Impact. More Gameplay."
            textSize = 13f
            setTextColor(Color.parseColor("#94A3B8"))
            setPadding(0, dp(4), 0, dp(18))
        }

        // 2. Status & Timer Card
        val statusCard = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            background = makeRoundedBg("#131A26", "#232F43", dp(16))
            setPadding(dp(16), dp(16), dp(16), dp(16))
        }

        val statusHeader = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
        }

        statusDot = View(this).apply {
            background = makeRoundedBg("#F59E0B", null, dp(5))
            layoutParams = LinearLayout.LayoutParams(dp(10), dp(10)).apply { marginEnd = dp(8) }
        }

        statusText = TextView(this).apply {
            text = "Ready (Open LiteGame Capture on Windows)"
            textSize = 14f
            setTypeface(null, Typeface.BOLD)
            setTextColor(Color.parseColor("#F59E0B"))
        }

        statusHeader.addView(statusDot)
        statusHeader.addView(statusText)

        timerText = TextView(this).apply {
            text = "REC 00:00:00"
            textSize = 28f
            typeface = Typeface.MONOSPACE
            setTypeface(typeface, Typeface.BOLD)
            setTextColor(Color.parseColor("#EF4444"))
            gravity = Gravity.CENTER_HORIZONTAL
            visibility = View.GONE
            setPadding(0, dp(12), 0, dp(4))
        }

        encoderStatusText = TextView(this).apply {
            textSize = 12.5f
            setTextColor(Color.parseColor("#94A3B8"))
            setPadding(0, dp(8), 0, 0)
        }

        statusCard.addView(statusHeader)
        statusCard.addView(timerText)
        statusCard.addView(encoderStatusText)

        // 3. Quality Profile Selector Card
        val profileCard = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            background = makeRoundedBg("#131A26", "#232F43", dp(16))
            setPadding(dp(16), dp(14), dp(16), dp(14))
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
            ).apply { topMargin = dp(14) }
        }

        val profileHeader = TextView(this).apply {
            text = "RECORDING PROFILE"
            textSize = 11f
            setTypeface(null, Typeface.BOLD)
            letterSpacing = 0.08f
            setTextColor(Color.parseColor("#38BDF8"))
            setPadding(0, 0, 0, dp(8))
        }
        profileCard.addView(profileHeader)

        for ((idx, profile) in profiles.withIndex()) {
            val btn = Button(this).apply {
                text = "${profile.title} — ${profile.subtitle}"
                textSize = 13f
                isAllCaps = false
                gravity = Gravity.START or Gravity.CENTER_VERTICAL
                setPadding(dp(14), dp(10), dp(14), dp(10))
                layoutParams = LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT,
                    ViewGroup.LayoutParams.WRAP_CONTENT
                ).apply { if (idx > 0) topMargin = dp(8) }
                setOnClickListener {
                    if (!isRecording) {
                        selectedProfile = profile
                        selectedBitrate = profile.defaultBitrate
                        updateProfileUi()
                        checkEncoderCapabilities()
                    }
                }
            }
            profileButtons[profile.id] = btn
            profileCard.addView(btn)
        }

        // 4. Bitrate Chips Card
        val bitrateCard = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            background = makeRoundedBg("#131A26", "#232F43", dp(16))
            setPadding(dp(16), dp(14), dp(16), dp(14))
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
            ).apply { topMargin = dp(14) }
        }

        val bitrateTitle = TextView(this).apply {
            text = "BITRATE"
            textSize = 11f
            setTypeface(null, Typeface.BOLD)
            letterSpacing = 0.08f
            setTextColor(Color.parseColor("#38BDF8"))
            setPadding(0, 0, 0, dp(8))
        }

        val bitrateRow = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
        for ((index, option) in listOf(4_000_000 to "4 Mbps", 6_000_000 to "6 Mbps", 8_000_000 to "8 Mbps", 12_000_000 to "12 Mbps").withIndex()) {
            val (bps, label) = option
            val chip = Button(this).apply {
                text = label
                textSize = 12.5f
                isAllCaps = false
                setTypeface(null, Typeface.BOLD)
                setPadding(0, dp(8), 0, dp(8))
                layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply {
                    if (index > 0) marginStart = dp(6)
                }
                setOnClickListener {
                    if (!isRecording) {
                        selectedBitrate = bps
                        updateProfileUi()
                    }
                }
            }
            bitrateButtons[bps] = chip
            bitrateRow.addView(chip)
        }
        bitrateCard.addView(bitrateTitle)
        bitrateCard.addView(bitrateRow)

        // 5. Action Buttons
        val buttonLayout = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
            ).apply { topMargin = dp(18) }
        }

        startButton = Button(this).apply {
            text = "START RECORDING"
            textSize = 15f
            setTypeface(null, Typeface.BOLD)
            background = makeRoundedBg("#16A34A", "#22C55E", dp(14))
            setTextColor(Color.WHITE)
            setPadding(0, dp(15), 0, dp(15))
            setOnClickListener { startRecording() }
        }

        stopButton = Button(this).apply {
            text = "STOP RECORDING"
            textSize = 15f
            setTypeface(null, Typeface.BOLD)
            background = makeRoundedBg("#DC2626", "#EF4444", dp(14))
            setTextColor(Color.WHITE)
            setPadding(0, dp(15), 0, dp(15))
            isEnabled = false
            alpha = 0.45f
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
            ).apply { topMargin = dp(12) }
            setOnClickListener { stopRecording() }
        }

        buttonLayout.addView(startButton)
        buttonLayout.addView(stopButton)

        val audioNote = TextView(this).apply {
            text = "Internal game audio + phone mic stream over USB.\nLive preview & audio toggles stay on Windows PC."
            textSize = 12f
            setTextColor(Color.parseColor("#64748B"))
            gravity = Gravity.CENTER_HORIZONTAL
            setPadding(0, dp(14), 0, 0)
        }

        mainLayout.addView(titleText)
        mainLayout.addView(subtitleText)
        mainLayout.addView(statusCard)
        mainLayout.addView(profileCard)
        mainLayout.addView(bitrateCard)
        mainLayout.addView(buttonLayout)
        mainLayout.addView(audioNote)

        rootLayout.addView(mainLayout)
        setContentView(rootLayout)

        updateProfileUi()
        checkEncoderCapabilities()
        handleRemoteIntent(intent)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        handleRemoteIntent(intent)
    }

    private fun handleRemoteIntent(intent: Intent?) {
        if (intent == null) return
        if (intent.getBooleanExtra("auto_stop", false) && isRecording) {
            stopRecording()
        } else if (intent.getBooleanExtra("auto_start", false) && !isRecording && !CaptureService.isRecordingActive) {
            handler.postDelayed({
                if (!isRecording && !CaptureService.isRecordingActive) {
                    startRecording()
                }
            }, 150)
        }
    }

    override fun onResume() {
        super.onResume()
        if (CaptureService.isRecordingActive && !isRecording) {
            recordStartTime = CaptureService.activeRecordStartMs.takeIf { it > 0L } ?: System.currentTimeMillis()
            setRecordingUiState(
                active = true,
                statusMsg = "Recording ${CaptureService.activeProfileLabel} via USB",
                statusColor = "#22C55E"
            )
        } else if (!CaptureService.isRecordingActive && isRecording) {
            setRecordingUiState(active = false, statusMsg = "Recording Stopped", statusColor = "#F59E0B")
        }
    }

    override fun onDestroy() {
        handler.removeCallbacks(timerRunnable)
        super.onDestroy()
    }

    private fun updateProfileUi() {
        profileButtons.forEach { (id, btn) ->
            val selected = id == selectedProfile.id
            btn.background = if (selected) {
                makeRoundedBg("#0F2942", "#38BDF8", dp(10))
            } else {
                makeRoundedBg("#1B2434", "#2D3B52", dp(10))
            }
            btn.setTextColor(Color.parseColor(if (selected) "#F8FAFC" else "#94A3B8"))
            btn.isEnabled = !isRecording
            btn.alpha = if (isRecording && !selected) 0.5f else 1.0f
        }
        bitrateButtons.forEach { (bps, btn) ->
            val selected = bps == selectedBitrate
            btn.background = if (selected) {
                makeRoundedBg("#0F2942", "#38BDF8", dp(10))
            } else {
                makeRoundedBg("#1B2434", "#2D3B52", dp(10))
            }
            btn.setTextColor(Color.parseColor(if (selected) "#38BDF8" else "#CBD5E1"))
            btn.isEnabled = !isRecording
            btn.alpha = if (isRecording && !selected) 0.5f else 1.0f
        }
    }

    private fun setRecordingUiState(active: Boolean, statusMsg: String, statusColor: String) {
        isRecording = active
        handler.removeCallbacks(timerRunnable)
        if (active) {
            timerText.visibility = View.VISIBLE
            handler.post(timerRunnable)
        } else {
            timerText.visibility = View.GONE
        }

        statusDot.background = makeRoundedBg(statusColor, null, dp(5))
        statusText.text = statusMsg
        statusText.setTextColor(Color.parseColor(statusColor))

        startButton.isEnabled = !active
        startButton.alpha = if (active) 0.45f else 1.0f
        stopButton.isEnabled = active
        stopButton.alpha = if (active) 1.0f else 0.45f
        updateProfileUi()
    }

    private fun makeRoundedBg(fillHex: String, strokeHex: String?, radiusPx: Int): GradientDrawable {
        return GradientDrawable().apply {
            shape = GradientDrawable.RECTANGLE
            cornerRadius = radiusPx.toFloat()
            setColor(Color.parseColor(fillHex))
            if (strokeHex != null) {
                setStroke(dp(1), Color.parseColor(strokeHex))
            }
        }
    }

    private fun dp(value: Int): Int {
        return TypedValue.applyDimension(
            TypedValue.COMPLEX_UNIT_DIP,
            value.toFloat(),
            resources.displayMetrics
        ).toInt()
    }

    private fun checkEncoderCapabilities() {
        val enc = EncoderChecker.checkEncoder(
            selectedProfile.width,
            selectedProfile.height,
            selectedProfile.fps.toDouble()
        )
        encoderStatusText.text = enc.description
        encoderStatusText.setTextColor(
            Color.parseColor(if (enc.supportsProfile) "#7DD3FC" else "#FB923C")
        )
    }

    private fun startRecording() {
        val mgr = getSystemService(MEDIA_PROJECTION_SERVICE) as MediaProjectionManager
        projectionLauncher.launch(mgr.createScreenCaptureIntent())
    }

    private fun stopRecording() {
        startService(Intent(this, CaptureService::class.java).apply {
            action = CaptureService.ACTION_STOP
        })
        setRecordingUiState(active = false, statusMsg = "Recording Stopped", statusColor = "#F59E0B")
    }

    private fun requestPermissionsIfNeeded() {
        val permissions = mutableListOf<String>()
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO)
            != PackageManager.PERMISSION_GRANTED) {
            permissions.add(Manifest.permission.RECORD_AUDIO)
        }
        if (Build.VERSION.SDK_INT >= 33 &&
            ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS)
            != PackageManager.PERMISSION_GRANTED) {
            permissions.add(Manifest.permission.POST_NOTIFICATIONS)
        }

        if (permissions.isNotEmpty()) {
            ActivityCompat.requestPermissions(this, permissions.toTypedArray(), 101)
        }
    }
}
