# Keep line numbers for clear crash stack traces while allowing full R8 shrinking/obfuscation.
-keepattributes SourceFile,LineNumberTable
-renamesourcefileattribute SourceFile

# Manifest-referenced entry points are also kept by AAPT2, kept explicitly here for clarity.
-keep class com.litegame.capture.MainActivity { *; }
-keep class com.litegame.capture.CaptureService { *; }
