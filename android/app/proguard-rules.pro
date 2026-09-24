# Chaquopy
-keep class com.chaquo.python.** { *; }

# Keep Python entry point module
-keep class com.flagdizero.jenny.** { *; }

# SSH (jsch + BouncyCastle).
# jsch NON referenzia le implementazioni degli algoritmi per tipo: le istanzia
# per NOME DI CLASSE, letto da stringhe di configurazione. R8 non vede quei
# riferimenti e le rimuove, e il risultato e un ClassNotFoundException che si
# manifesta SOLO in release (in debug minify e spento). Stessa logica per il
# provider BouncyCastle, che si registra e si risolve per reflection.
-keep class com.jcraft.jsch.** { *; }
-keep class org.bouncycastle.** { *; }
-dontwarn org.bouncycastle.**
-dontwarn javax.naming.**

# jsch dichiara una serie di integrazioni OPZIONALI che su Android non esistono
# e che non usiamo. R8 tratta una classe referenziata e assente come ERRORE, non
# come warning, quindi senza questi -dontwarn il build release non compila
# affatto (verificato: minifyReleaseWithR8 FAILED).
#   - JNA: supporto a Pageant, l'agent SSH di Windows
#   - log4j2 / slf4j: backend di logging alternativi, qui si usa loguru
#   - org.ietf.jgss: Kerberos GSS-API, parte della JDK ma non di Android
#   - junixsocket: socket unix per l'agent forwarding, che non esponiamo
-dontwarn com.sun.jna.**
-dontwarn org.apache.logging.log4j.**
-dontwarn org.slf4j.**
-dontwarn org.ietf.jgss.**
-dontwarn org.newsclub.net.unix.**
