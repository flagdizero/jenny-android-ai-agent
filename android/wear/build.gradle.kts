import java.util.Properties

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

// Jenny sull'orologio. Primo passo: una schermata sola — "Parla", la domanda va
// al telefono via Data Layer, torna la risposta. Niente Chaquopy e niente agente
// qui: Jenny è una sola e vive sul telefono, l'orologio le fa da orecchio e bocca.
//
// **Stesso `applicationId` e stessa firma dell'app del telefono**, e non è un
// dettaglio: il Data Layer consegna i messaggi solo fra app con lo stesso id e
// la stessa chiave. In debug va da sé (le build dello stesso Mac condividono il
// keystore di debug); in release la chiave è la stessa di `:app`, letta dalle
// stesse variabili e dallo stesso `keystore.properties` — il blocco qui sotto è
// lo specchio di quello in `app/build.gradle.kts`, e i due devono restare uguali.
//
// Il telefono parla con questo modulo solo nel flavor `play` (il Data Layer è
// Google): un orologio associato a una Jenny `foss` non trova nessuno.

val keystorePropsFile = rootProject.file("keystore.properties")
val keystoreProps = Properties().apply {
    if (keystorePropsFile.exists()) {
        keystorePropsFile.inputStream().use { load(it) }
    }
}

fun signingCredential(envName: String, propName: String): String? =
    (System.getenv(envName) ?: keystoreProps.getProperty(propName))?.takeIf { it.isNotBlank() }

val releaseStoreFile = signingCredential("JENNY_KEYSTORE_PATH", "storeFile")
val releaseStorePassword = signingCredential("JENNY_KEYSTORE_PASSWORD", "storePassword")
val releaseKeyAlias = signingCredential("JENNY_KEY_ALIAS", "keyAlias")
val releaseKeyPassword = signingCredential("JENNY_KEY_PASSWORD", "keyPassword")

val hasReleaseSigning = releaseStoreFile != null &&
    releaseStorePassword != null &&
    releaseKeyAlias != null &&
    releaseKeyPassword != null &&
    file(releaseStoreFile!!).exists()

android {
    // Il namespace (il pacchetto di R e BuildConfig) è diverso da quello del
    // telefono per non confonderli nel codice; l'applicationId no.
    namespace = "com.flagdizero.jenny.wear"
    compileSdk = 36

    defaultConfig {
        applicationId = "com.flagdizero.jenny"
        // Wear OS 3: il primo con le app per orologio che si installano da sé,
        // e il minimo della sonda che ha girato sul Pixel Watch 3.
        minSdk = 30
        targetSdk = 35
        // Numerazione propria: i due APK si aggiornano separatamente, e il
        // telefono non guarda questo numero. Quello che telefono e orologio
        // confrontano è la versione del protocollo (`WearProtocol.VERSION`).
        versionCode = 1
        versionName = "0.1.0"
    }

    signingConfigs {
        if (hasReleaseSigning) {
            create("release") {
                storeFile = file(releaseStoreFile!!)
                storePassword = releaseStorePassword
                keyAlias = releaseKeyAlias
                keyPassword = releaseKeyPassword
                enableV1Signing = false
                enableV2Signing = true
                enableV3Signing = true
            }
        }
    }

    buildTypes {
        release {
            // Spento: il primo passo è piccolo, e R8 sulle classi Java copiate
            // dalla sonda andrebbe provato a parte.
            isMinifyEnabled = false
            signingConfig = signingConfigs.findByName("release")
        }
    }

    buildFeatures {
        buildConfig = true
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_11
        targetCompatibility = JavaVersion.VERSION_11
    }

    kotlinOptions {
        jvmTarget = "11"
    }

    sourceSets {
        getByName("main") {
            // Il protocollo con il telefono, scritto una volta e compilato anche
            // dal flavor `play` di `:app`.
            java.srcDir("../wear-protocol")
        }
    }
}

dependencies {
    // Il Data Layer: le domande al telefono e le risposte da lui.
    implementation("com.google.android.gms:play-services-wearable:19.0.0")
    // La complicazione: Jenny sul quadrante, il tocco apre la domanda.
    implementation("androidx.wear.watchface:watchface-complications-data-source:1.3.0")

    // Le frasi che l'orologio capisce da solo (LocalCommand) si provano sul Mac.
    testImplementation("junit:junit:4.13.2")
}
