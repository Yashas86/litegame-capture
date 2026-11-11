package com.example.lowloadcapture

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

    private lateinit var statusDot: View
    private lateinit var statusText: TextView
    private lateinit var timerText: TextView
    private lateinit var encoderStatusText: TextView
    private lateinit var audioStatusText: TextView
    private lateinit var startButton: Button
    private lateinit var stopButton: Button
    private val bitrateButtons = mutableMapOf<Int, Button>()

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
                handler.postDelayed(this, 500)
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
                    putExtra(CaptureService.EXTRA_BITRATE, selectedBitrate)
                }
                ContextCompat.startForegroundService(this, intent)

                recordStartTime = System.currentTimeMillis()
                setRecordingUiState(active = true, statusMsg = "Recording Active -> USB PC Stream", statusColor = "#22C55E")
            } else {
                setRecordingUiState(active = false, statusMsg = "Screen capture permission denied", statusColor = "#EF4444")
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

        val rootLayout = ScrollView(this).apply {
            setBackgroundColor(Color.parseColor("#0B0F17"))
            isFillViewport = true
        }

        val mainLayout = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(20), dp(24), dp(20), dp(24))
        }

        // 1. Header Row with v2.1 Pill Badge
        val headerRow = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
        }

        val titleText = TextView(this).apply {
            text = "LowLoadCapture"
            textSize = 24f
            setTypeface(null, Typeface.BOLD)
            setTextColor(Color.parseColor("#F8FAFC"))
        }

        val versionBadge = TextView(this).apply {
            text = "v2.1"
            textSize = 12f
            setTypeface(null, Typeface.BOLD)
            setTextColor(Color.parseColor("#38BDF8"))
            background = makeRoundedBg("#0F2942", "#1E4D7B", dp(12))
            setPadding(dp(10), dp(3), dp(10), dp(3))
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
            ).apply { marginStart = dp(10) }
        }

        headerRow.addView(titleText)
        headerRow.addView(versionBadge)

        val subtitleText = TextView(this).apply {
            text = "1080p60 USB Android-to-PC Hardware Game Recorder"
            textSize = 13f
            setTextColor(Color.parseColor("#94A3B8"))
            setPadding(0, dp(4), 0, dp(18))
        }

        // 2. Live Status & Timer Card
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
            layoutParams = LinearLayout.LayoutParams(dp(10), dp(10)).apply {
                marginEnd = dp(8)
            }
        }

        statusText = TextView(this).apply {
            text = "Ready (Ensure Windows App is open)"
            textSize = 14f
            setTypeface(null, Typeface.BOLD)
            setTextColor(Color.parseColor("#F59E0B"))
        }

        statusHeader.addView(statusDot)
        statusHeader.addView(statusText)

        timerText = TextView(this).apply {
            text = "REC 00:00:00"
            textSize = 30f
            typeface = Typeface.MONOSPACE
            setTypeface(typeface, Typeface.BOLD)
            setTextColor(Color.parseColor("#EF4444"))
            gravity = Gravity.CENTER_HORIZONTAL
            visibility = View.GONE
            setPadding(0, dp(14), 0, dp(4))
        }

        statusCard.addView(statusHeader)
        statusCard.addView(timerText)

        // 3. Hardware & Stream Specs Card
        val infoBox = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            background = makeRoundedBg("#131A26", "#232F43", dp(16))
            setPadding(dp(16), dp(16), dp(16), dp(16))
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
            ).apply { topMargin = dp(14) }
        }

        val specTitle = TextView(this).apply {
            text = "CAPTURE PIPELINE"
            textSize = 11f
            setTypeface(null, Typeface.BOLD)
            letterSpacing = 0.08f
            setTextColor(Color.parseColor("#38BDF8"))
            setPadding(0, 0, 0, dp(10))
        }

        val videoSpecText = TextView(this).apply {
            text = "Video: 1920 x 1080 @ 60 FPS Target (H.264 VFR)"
            textSize = 13.5f
            setTextColor(Color.parseColor("#E2E8F0"))
        }

        encoderStatusText = TextView(this).apply {
            textSize = 13.5f
            setTextColor(Color.parseColor("#7DD3FC"))
            setPadding(0, dp(8), 0, 0)
        }

        audioStatusText = TextView(this).apply {
            text = "Audio: Dual-Track 48 kHz (Game Stereo + Mic Mono)"
            textSize = 13.5f
            setTextColor(Color.parseColor("#E2E8F0"))
            setPadding(0, dp(8), 0, 0)
        }

        val usbStatusText = TextView(this).apply {
            text = "Transport: ADB USB Reverse (127.0.0.1:27183)"
            textSize = 13.5f
            setTextColor(Color.parseColor("#E2E8F0"))
            setPadding(0, dp(8), 0, 0)
        }

        infoBox.addView(specTitle)
        infoBox.addView(videoSpecText)
        infoBox.addView(encoderStatusText)
        infoBox.addView(audioStatusText)
        infoBox.addView(usbStatusText)

        // 4. Bitrate Selector Card
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
            text = "VIDEO BITRATE"
            textSize = 11f
            setTypeface(null, Typeface.BOLD)
            letterSpacing = 0.08f
            setTextColor(Color.parseColor("#38BDF8"))
            setPadding(0, 0, 0, dp(10))
        }

        val bitrateRow = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
        }

        for ((index, option) in listOf(6_000_000 to "6 Mbps", 8_000_000 to "8 Mbps", 12_000_000 to "12 Mbps").withIndex()) {
            val (bps, label) = option
            val chip = Button(this).apply {
                text = label
                textSize = 13f
                isAllCaps = false
                setTypeface(null, Typeface.BOLD)
                setPadding(0, dp(8), 0, dp(8))
                layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply {
                    if (index > 0) marginStart = dp(8)
                }
                setOnClickListener {
                    if (!isRecording) {
                        selectedBitrate = bps
                        updateBitrateChips()
                    }
                }
            }
            bitrateButtons[bps] = chip
            bitrateRow.addView(chip)
        }
        updateBitrateChips()

        bitrateCard.addView(bitrateTitle)
        bitrateCard.addView(bitrateRow)

        // 5. Action Buttons
        val buttonLayout = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
            ).apply { topMargin = dp(20) }
        }

        startButton = Button(this).apply {
            text = "START RECORDING"
            textSize = 15f
            setTypeface(null, Typeface.BOLD)
            background = makeRoundedBg("#16A34A", "#22C55E", dp(14))
            setTextColor(Color.WHITE)
            setPadding(0, dp(15), 0, dp(15))
            setOnClickListener {
                startRecording()
            }
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
            setOnClickListener {
                stopRecording()
            }
        }

        buttonLayout.addView(startButton)
        buttonLayout.addView(stopButton)

        mainLayout.addView(headerRow)
        mainLayout.addView(subtitleText)
        mainLayout.addView(statusCard)
        mainLayout.addView(infoBox)
        mainLayout.addView(bitrateCard)
        mainLayout.addView(buttonLayout)

        rootLayout.addView(mainLayout)
        setContentView(rootLayout)

        checkEncoderCapabilities()
    }

    override fun onResume() {
        super.onResume()
        if (CaptureService.isRecordingActive && !isRecording) {
            recordStartTime = CaptureService.activeRecordStartMs.takeIf { it > 0L } ?: System.currentTimeMillis()
            setRecordingUiState(active = true, statusMsg = "Recording Active -> USB PC Stream", statusColor = "#22C55E")
        } else if (!CaptureService.isRecordingActive && isRecording) {
            setRecordingUiState(active = false, statusMsg = "Recording Stopped", statusColor = "#F59E0B")
        }
    }

    override fun onDestroy() {
        handler.removeCallbacks(timerRunnable)
        super.onDestroy()
    }

    private fun updateBitrateChips() {
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
        updateBitrateChips()
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
        val enc = EncoderChecker.checkEncoder()
        encoderStatusText.text = "Encoder: ${enc.description}"
        if (!enc.supports1080p60) {
            encoderStatusText.setTextColor(Color.parseColor("#FB923C"))
        }
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
