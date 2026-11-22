pluginManagement {
    repositories {
        google()
        mavenCentral()
        maven { url = uri("https://chaquo.com/maven") }
    }
}

dependencyResolutionManagement {
    repositories {
        google()
        mavenCentral()
        maven { url = uri("https://chaquo.com/maven") }
    }
}

rootProject.name = "jenny"
include(":app")
// Jenny sull'orologio (Wear OS): un APK a sé, con lo stesso applicationId
// dell'app del telefono, perché il Data Layer collega solo app con id e firma
// uguali. V. `wear/build.gradle.kts`.
include(":wear")
