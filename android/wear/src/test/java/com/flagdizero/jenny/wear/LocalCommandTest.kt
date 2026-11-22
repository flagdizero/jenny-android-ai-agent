package com.flagdizero.jenny.wear

import com.flagdizero.jenny.wear.LocalCommand.Alarm
import com.flagdizero.jenny.wear.LocalCommand.FindPlace
import com.flagdizero.jenny.wear.LocalCommand.Navigate
import com.flagdizero.jenny.wear.LocalCommand.OpenApp
import com.flagdizero.jenny.wear.LocalCommand.Timer
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class LocalCommandTest {
    private fun parse(s: String) = LocalCommand.parse(s)

    @Test
    fun timersInItalian() {
        assertEquals(Timer(600, null), parse("Timer di 10 minuti"))
        assertEquals(Timer(600, null), parse("imposta un timer di 10 minuti."))
        assertEquals(Timer(300, null), parse("Metti un timer da cinque minuti"))
        assertEquals(Timer(60, null), parse("timer di un minuto"))
        assertEquals(Timer(3600, null), parse("timer di un'ora"))
        assertEquals(Timer(3600, null), parse("timer di un’ora"))
        assertEquals(Timer(5400, null), parse("timer di un'ora e mezza"))
        assertEquals(Timer(5400, null), parse("timer di 1 ora e 30"))
        assertEquals(Timer(4500, null), parse("timer di un'ora e un quarto"))
        assertEquals(Timer(1800, null), parse("avvia un timer di mezz'ora"))
        assertEquals(Timer(900, null), parse("timer di un quarto d'ora"))
        assertEquals(Timer(150, null), parse("timer di 2 minuti e 30 secondi"))
        assertEquals(Timer(45, null), parse("fai partire un timer di 45 secondi"))
        assertEquals(Timer(480, "la pasta"), parse("timer di 8 minuti per la pasta"))
        assertEquals(Timer(1200, null), parse("conto alla rovescia di venti minuti"))
    }

    @Test
    fun timersInEnglish() {
        assertEquals(Timer(600, null), parse("Set a timer for 10 minutes"))
        assertEquals(Timer(5400, null), parse("timer for an hour and a half"))
        assertEquals(Timer(90, null), parse("start a timer for 1 minute and 30 seconds"))
        assertEquals(Timer(300, "tea"), parse("timer for five minutes for tea"))
    }

    @Test
    fun alarms() {
        assertEquals(Alarm(7, 0, null), parse("Sveglia alle 7"))
        assertEquals(Alarm(7, 30, null), parse("metti la sveglia alle 7:30"))
        assertEquals(Alarm(7, 30, null), parse("svegliami alle 7 e mezza"))
        assertEquals(Alarm(7, 15, null), parse("sveglia alle sette e un quarto"))
        assertEquals(Alarm(7, 45, null), parse("sveglia alle 8 meno un quarto"))
        assertEquals(Alarm(18, 45, null), parse("imposta una sveglia alle 18.45"))
        assertEquals(Alarm(18, 0, null), parse("sveglia alle 6 di sera"))
        assertEquals(Alarm(1, 0, null), parse("sveglia all'una"))
        assertEquals(Alarm(12, 0, null), parse("sveglia a mezzogiorno"))
        assertEquals(Alarm(6, 20, null), parse("sveglia alle 6 e 20"))
        assertEquals(Alarm(7, 30, null), parse("Set an alarm for 7:30"))
        assertEquals(Alarm(19, 0, null), parse("wake me up at 7 pm"))
    }

    @Test
    fun openingAnApp() {
        assertEquals(OpenApp("spotify"), parse("Apri Spotify"))
        assertEquals(OpenApp("meteo"), parse("apri il meteo"))
        assertEquals(OpenApp("youtube music"), parse("apri l'app YouTube Music"))
        assertEquals(OpenApp("torcia"), parse("Accendi la torcia"))
        assertEquals(OpenApp("maps"), parse("open Maps"))
        // Il nome è solo quello detto: se non è un'app, chi esegue lo lascia a Jenny.
        assertEquals(OpenApp("cancello"), parse("apri il cancello"))
        assertNull(parse("apri la porta del garage e accendi le luci"))
        assertEquals(3, LocalCommand.appMatch("Meteo", "meteo"))
        assertEquals(2, LocalCommand.appMatch("YouTube Music", "youtube"))
        assertEquals(1, LocalCommand.appMatch("YouTube Music", "music"))
        assertEquals(0, LocalCommand.appMatch("Messaggi", "meteo"))
        assertEquals(0, LocalCommand.appMatch("Mappe", "map"))
    }

    @Test
    fun goingSomewhere() {
        assertEquals(Navigate("casa"), parse("Portami a casa"))
        assertEquals(Navigate("via roma 3"), parse("portami in via Roma 3"))
        assertEquals(Navigate("supermercato"), parse("portami al supermercato"))
        assertEquals(Navigate("dentista"), parse("portami dal dentista"))
        assertEquals(Navigate("stazione centrale"), parse("indicazioni per la stazione centrale"))
        assertEquals(Navigate("piazza duomo"), parse("naviga verso piazza Duomo"))
        assertEquals(Navigate("airport"), parse("take me to the airport"))
    }

    @Test
    fun findingAPlace() {
        assertEquals(FindPlace("farmacia"), parse("Dov'è una farmacia?"))
        assertEquals(FindPlace("farmacia"), parse("dov’è una farmacia qui vicino"))
        assertEquals(FindPlace("benzinaio"), parse("dov'è il benzinaio più vicino"))
        assertEquals(FindPlace("bancomat"), parse("cerca un bancomat qui vicino"))
        assertEquals(FindPlace("pizzeria"), parse("trova una pizzeria nei dintorni"))
        assertEquals(FindPlace("gas station"), parse("where is the nearest gas station"))
        assertEquals(FindPlace("pharmacy"), parse("find a pharmacy near me"))
    }

    @Test
    fun anythingElseGoesToJenny() {
        assertNull(parse("dov'è il mio telefono"))
        assertNull(parse("dov'è Marco"))
        assertNull(parse("cerca una ricetta per la carbonara"))
        assertNull(parse("trova il file del preventivo"))
        assertNull(parse("portami un caffè"))
        assertNull(parse("come stanno le piante sul balcone"))
        assertNull(parse("metti un timer e ricordami la pasta"))
        assertNull(parse("quanto manca al timer"))
        assertNull(parse("timer di 10 minuti e poi spegni la luce"))
        assertNull(parse("sveglia alle 7 e ricordami la riunione"))
        assertNull(parse("sveglia alle 25"))
        assertNull(parse("timer di 30 ore"))
        assertNull(parse("timer"))
        assertNull(parse("a che ora ho messo la sveglia"))
        assertNull(parse("ricordami tra 10 minuti di chiamare"))
    }
}
