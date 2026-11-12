import java.util.Properties
import org.gradle.api.GradleException

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

val signingProps = Properties()
val externalSigningFile = file("${System.getProperty("user.home")}/.litegame-capture/signing/keystore.properties")
val localSigningFile = rootProject.file("keystore.properties")
val activeSigningFile = when {
    externalSigningFile.isFile -> externalSigningFile
    localSigningFile.isFile -> localSigningFile
    else -> null
}
if (activeSigningFile != null) {
    activeSigningFile.inputStream().use { signingProps.load(it) }
}

val envStorePath = System.getenv("LITEGAME_KEYSTORE_PATH")?.trim().orEmpty()
val envStorePass = System.getenv("LITEGAME_KEYSTORE_PASSWORD")?.trim().orEmpty()
val envKeyAlias = System.getenv("LITEGAME_KEYSTORE_ALIAS")?.trim().orEmpty()
val envKeyPass = System.getenv("LITEGAME_KEY_PASSWORD")?.trim().orEmpty()

val releaseStorePath = envStorePath.ifEmpty { signingProps.getProperty("storeFile")?.trim().orEmpty() }
val releaseStorePassword = envStorePass.ifEmpty { signingProps.getProperty("storePassword")?.trim().orEmpty() }
val releaseKeyAlias = envKeyAlias.ifEmpty { signingProps.getProperty("keyAlias")?.trim().orEmpty() }
val releaseKeyPassword = envKeyPass.ifEmpty {
    signingProps.getProperty("keyPassword")?.trim().orEmpty().ifEmpty { releaseStorePassword }
}

val resolvedKeystoreFile = if (releaseStorePath.isNotEmpty()) {
    val candidate = file(releaseStorePath)
    if (candidate.isAbsolute) candidate else rootProject.file(releaseStorePath)
} else {
    null
}

val hasReleaseSigning = resolvedKeystoreFile != null &&
    resolvedKeystoreFile.isFile &&
    releaseStorePassword.isNotEmpty() &&
    releaseKeyAlias.isNotEmpty() &&
    releaseKeyPassword.isNotEmpty()

android {
    namespace = "com.litegame.capture"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.litegame.capture"
        minSdk = 29
        targetSdk = 35
        versionCode = 2
        versionName = "1.0.0"
    }

    signingConfigs {
        if (hasReleaseSigning) {
            create("release") {
                storeFile = resolvedKeystoreFile
                storePassword = releaseStorePassword
                keyAlias = releaseKeyAlias
                keyPassword = releaseKeyPassword
                enableV1Signing = true
                enableV2Signing = true
                enableV3Signing = true
            }
        }
    }

    buildTypes {
        debug {
            isMinifyEnabled = false
        }
        release {
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro"
            )
            if (hasReleaseSigning) {
                signingConfig = signingConfigs.getByName("release")
            }
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
}

gradle.taskGraph.whenReady {
    val needsReleaseSigning = allTasks.any { task ->
        task.project == project &&
            task.name.contains("Release", ignoreCase = false) &&
            (task.name.startsWith("assemble") ||
                task.name.startsWith("package") ||
                task.name.startsWith("bundle"))
    }
    if (needsReleaseSigning && !hasReleaseSigning) {
        throw GradleException(
            "Release signing is not configured. Provide LITEGAME_KEYSTORE_* environment variables " +
                "or create ${externalSigningFile.absolutePath} (see android/keystore.properties.example). " +
                "Never use the debug keystore or commit signing credentials to Git."
        )
    }
}

dependencies {
    implementation("androidx.activity:activity-ktx:1.9.3")
    implementation("androidx.core:core-ktx:1.15.0")
}
