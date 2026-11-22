package com.flagdizero.jenny.wear;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Paint;
import android.graphics.Path;
import android.graphics.RadialGradient;
import android.graphics.RectF;
import android.graphics.Shader;
import android.graphics.drawable.Drawable;
import android.os.Handler;
import android.os.Looper;
import android.text.Layout;
import android.text.SpannableStringBuilder;
import android.text.Spanned;
import android.text.StaticLayout;
import android.text.TextPaint;
import android.text.TextUtils;
import android.text.style.ForegroundColorSpan;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.HapticFeedbackConstants;
import android.view.View;
import android.widget.FrameLayout;
import android.widget.ImageView;
import android.widget.ScrollView;
import android.widget.TextClock;
import android.widget.TextView;

/**
 * La schermata di Jenny sull'orologio. Jenny è sempre intera, mai ritagliata:
 * grande finché il testo le lascia posto, piccola solo quando c'è da leggere.
 *
 * - Pronta: «Dimmi.» e Jenny che saluta; la si tocca per parlare.
 * - Ti ascolto: il bordo dello schermo si accende con la voce, la frase si
 *   scrive in alto, lei sorride.
 * - Ci penso: mano al mento, le stelline salgono, sopra resta la domanda.
 * - Risposta: poche parole stanno grandi sopra di lei; un testo lungo prende
 *   lo schermo, scorre con la corona, e Jenny si fa piccola nella lunetta in
 *   basso, dove il cerchio è troppo stretto per le righe.
 * - Errore: la stessa Jenny, triste; toccarla riprova.
 *
 * Tastiera e voce non sono pulsanti: sono due archi sottili sul bordo, a
 * sinistra e a destra, con il segno appena dentro. L'arco della voce è rosa
 * quando è accesa.
 *
 * Le misure sono quelle del disegno, un cerchio di 466 (lo schermo del OnePlus
 * Watch 3): {@link #s} le porta agli altri.
 */
class PolsoView extends FrameLayout {
    private static final float BOARD = 466f, C = 233f;
    private static final int KEY_GREY = 0xFF6B6772, KEY_OFF = 0xFF3A3740;
    private static final long CARET_MS = 530;
    // Il saluto: quante volte cambia disegno e ogni quanto.
    private static final int WAVE_STEPS = 6;
    private static final long WAVE_MS = 240;
    /** Quanto si aspetta una voce che tarda, a schermo acceso. */
    private static final long VOICE_WAIT_MS = 20_000;

    // Dove sta la figura dentro la tela quadrata dei disegni: quanto è alta,
    // dove comincia dall'alto, dove cade il suo centro.
    private static final float FIG_H = 0.722f, FIG_TOP = 0.15f, FIG_CX = 0.523f;

    // I due archi sul bordo, in gradi (0 = ore 3, in senso orario), e i segni.
    private static final float KEY_R = 225, GLYPH_R = 190, KEY_SWEEP = 34;
    private static final float KB_START = 136, VOICE_START = 10;

    private enum Mode { READY, LISTENING, THINKING, SAYING, ERROR }

    private final float s;
    private final Handler main = new Handler(Looper.getMainLooper());
    private final Stage stage;
    private final TextClock clock;
    private final TextView label, head, sub, body, flash;
    private final ScrollView scroll;
    private final FrameLayout jenny;
    private final ImageView jennyBody, jennyFace;
    private final View kbKey, voiceKey;

    private Mode mode = Mode.READY;
    private String heard = "";
    private boolean caretOn = true;
    private boolean voiceOn;
    private boolean longText;
    // La bocca: finché è vero si apre e si chiude; `mouthUntil` la ferma da sola.
    private boolean mouthMoving, mouthOpen, voiceSpeaking, voiceComing;
    private final Runnable voiceLate = () -> voiceComing(false);
    private long mouthUntil;
    private Runnable onParla, onScrivi, onRetry;
    private VoiceToggle onToggle;

    interface VoiceToggle {
        void onToggle(boolean on);
    }

    PolsoView(Context c, boolean voiceOn) {
        super(c);
        this.voiceOn = voiceOn;
        s = c.getResources().getDisplayMetrics().widthPixels / BOARD;
        setBackgroundColor(0xFF000000);

        stage = new Stage(c);
        addView(stage, new LayoutParams(-1, -1));

        // Il testo lungo: scorre con la corona e sfuma in alto e in basso.
        scroll = new ScrollView(c);
        scroll.setVerticalScrollBarEnabled(false);
        scroll.setOverScrollMode(OVER_SCROLL_NEVER);
        scroll.setFocusable(true);
        scroll.setFocusableInTouchMode(true);
        scroll.setVerticalFadingEdgeEnabled(true);
        scroll.setFadingEdgeLength(Polso.px(s, 64));
        body = Polso.text(c, s, "", 27, 400, Polso.INK);
        body.setLineSpacing(0, 1.3f);
        body.setLetterSpacing(-0.008f);
        body.setHyphenationFrequency(Layout.HYPHENATION_FREQUENCY_NONE);
        // Il margine sta nel testo, non nello ScrollView: così la sfumatura
        // cade sul bordo della finestra e la prima riga parte sotto l'ora.
        body.setPadding(0, Polso.px(s, 40), 0, Polso.px(s, 48));
        scroll.addView(body, new LayoutParams(-1, -2));
        scroll.setOnScrollChangeListener((v, x, y, ox, oy) -> stage.invalidate());
        addView(scroll, Polso.at(s, 70, 40, 330, 278));

        clock = new TextClock(c);
        clock.setFormat12Hour("h:mm");
        clock.setFormat24Hour("HH:mm");
        clock.setTypeface(Polso.font(c, 500));
        clock.setTextSize(TypedValue.COMPLEX_UNIT_PX, 16 * s);
        clock.setTextColor(Polso.MUTED);
        clock.setLetterSpacing(0.04f);
        clock.setIncludeFontPadding(false);
        clock.setGravity(Gravity.CENTER_HORIZONTAL);
        addView(clock, Polso.row(s, 17));

        label = Polso.centered(c, s, "", 14, 600, Polso.PINK);
        label.setLetterSpacing(0.32f);
        addView(label, Polso.row(s, 58));
        head = Polso.centered(c, s, "", 44, 600, Polso.INK);
        head.setHyphenationFrequency(Layout.HYPHENATION_FREQUENCY_NONE);
        addView(head, Polso.at(s, 60, 58, 346, -1));
        sub = Polso.centered(c, s, "", 22, 400, Polso.MUTED);
        sub.setLineSpacing(0, 1.35f);
        sub.setEllipsize(TextUtils.TruncateAt.END);
        addView(sub, Polso.at(s, 72, 90, 322, -1));

        jenny = new FrameLayout(c);
        jennyBody = new ImageView(c);
        jennyFace = new ImageView(c);
        for (ImageView v : new ImageView[]{jennyBody, jennyFace}) {
            v.setScaleType(ImageView.ScaleType.FIT_XY);
            jenny.addView(v, new LayoutParams(-1, -1));
        }
        jenny.setContentDescription(c.getString(R.string.talk));
        jenny.setOnClickListener(v -> tapJenny());
        addView(jenny);

        // Le zone da toccare stanno sopra Jenny: il suo riquadro è più largo
        // della figura e arriva fin sotto i due segni.
        kbKey = key(c, KB_START + KEY_SWEEP / 2, R.string.type);
        kbKey.setOnClickListener(v -> { if (onScrivi != null) onScrivi.run(); });
        voiceKey = key(c, VOICE_START + KEY_SWEEP / 2, R.string.voice_toggle);
        voiceKey.setOnClickListener(v -> toggleVoice());

        flash = Polso.flash(c, s);
        addView(flash);

        ready();
        caret();
    }

    private View key(Context c, float degrees, int description) {
        View v = new View(c);
        v.setContentDescription(c.getString(description));
        double a = Math.toRadians(degrees);
        float x = C + GLYPH_R * (float) Math.cos(a), y = C + GLYPH_R * (float) Math.sin(a);
        addView(v, Polso.at(s, x - 44, y - 44, 88, 88));
        return v;
    }

    void setOnParla(Runnable r) { onParla = r; }
    void setOnScrivi(Runnable r) { onScrivi = r; }
    void setOnToggle(VoiceToggle t) { onToggle = t; }

    // --- Le schermate ---------------------------------------------------------

    /** Pronta: «Dimmi.» e Jenny che saluta. */
    void ready() {
        enter(Mode.READY);
        title(getContext().getString(R.string.hello), 44, 600, 58, 1);
        pose(R.drawable.jenny_hello, 0, 262);
        stage.sparkles(new float[][]{{340, 150, 22, 1f}, {104, 222, 13, .8f}});
        wave(0);
    }

    /**
     * Il saluto all'apertura: la mano va su e giù fra i due disegni, poi Jenny
     * si mette ferma ad aspettare. I tre disegni stanno sulla stessa tela, così
     * il cambio non la sposta.
     */
    private void wave(int step) {
        if (mode != Mode.READY) return;
        if (step >= WAVE_STEPS) {
            jennyBody.setImageResource(R.drawable.jenny_body_idle);
            jennyFace.setImageResource(R.drawable.jenny_face_normal);
            return;
        }
        jennyBody.setImageResource(step % 2 == 0 ? R.drawable.jenny_hello : R.drawable.jenny_hello2);
        main.postDelayed(() -> wave(step + 1), WAVE_MS);
    }

    /** Ti ascolto, con quello che si è capito fin qui. */
    void listening(String text) {
        heard = text == null ? "" : text;
        if (mode != Mode.LISTENING) {
            enter(Mode.LISTENING);
            caption(R.string.listening, Polso.PINK, 50);
            title("", 27, 500, 82, 3);
            head.setLineSpacing(0, 1.24f);
            pose(R.drawable.jenny_body_idle, R.drawable.jenny_face_happy, 236);
            stage.level(0.4f);
        }
        drawTranscript();
    }

    /** Ci penso, con sopra la domanda appena fatta. */
    void thinking(String question) {
        enter(Mode.THINKING);
        caption(R.string.thinking, Polso.YELLOW, 58);
        boolean has = question != null && !question.trim().isEmpty();
        note(has ? "«" + question.trim() + "»" : "", 22, 90, 2);
        pose(R.drawable.jenny_body_think, R.drawable.jenny_face_thinking, 236);
        stage.sparkles(new float[][]{{322, 236, 13, 1f}, {344, 204, 18, .7f}, {372, 166, 24, .4f}});
        twinkle(0);
    }

    /** La risposta di Jenny. */
    void reply(String text) {
        enter(Mode.SAYING);
        say(text, R.drawable.jenny_face_normal, false);
        // Senza voce la bocca si muove lo stesso per un attimo, quanto basta a
        // dire che la risposta è sua; con la voce la ferma chi parla.
        int length = text == null ? 0 : text.length();
        moveMouth(Math.min(3200, 700 + length * 45L));
    }

    /** Qualcosa non è andato: toccare Jenny chiama {@code retry}, o riascolta. */
    void error(String text, Runnable retry) {
        enter(Mode.ERROR);
        onRetry = retry;
        say(text, R.drawable.jenny_face_sad, true);
    }

    /**
     * Sceglie quanto posto dare alle parole: una riga sta grande, due o tre
     * stanno medie sopra Jenny intera, il resto prende lo schermo e scorre.
     */
    private void say(String text, int face, boolean failed) {
        CharSequence t = Polso.highlight(text == null ? "" : text);
        float medium = failed ? 26 : 30;
        if (!failed && lines(t, 50, 600, 346) == 1) {
            title(t, 50, 600, 62, 1);
            pose(R.drawable.jenny_body_idle, face, 246);
        } else if (lines(t, medium, 500, 346) <= 3) {
            int n = lines(t, medium, 500, 346);
            title(t, medium, 500, 62, 3);
            head.setLineSpacing(0, 1.24f);
            if (failed) {
                sub.setTextColor(Polso.MUTED);
                note(getContext().getString(R.string.retry_hint), 15, 62 + n * medium * 1.3f + 14, 1);
            }
            pose(R.drawable.jenny_body_idle, face, n == 3 ? 212 : 226);
        } else {
            longText = true;
            body.setText(t);
            scroll.setVisibility(VISIBLE);
            scroll.scrollTo(0, 0);
            scroll.requestFocus();
            pose(R.drawable.jenny_body_idle, face, 126, 456);
            // Quanto è lungo il testo si sa solo dopo il layout.
            scroll.post(stage::invalidate);
        }
        // Con tre righe il testo arriva dove starebbero le stelline: si tolgono.
        if (!failed && !longText && head.getMaxLines() >= 1 && lines(t, medium, 500, 346) < 3) {
            stage.sparkles(new float[][]{{344, 160, 22, 1f}, {100, 226, 12, .8f}});
        }
    }

    private void enter(Mode m) {
        mode = m;
        mouthMoving = false;
        longText = false;
        onRetry = null;
        boolean keys = m == Mode.READY || m == Mode.SAYING || m == Mode.ERROR;
        kbKey.setVisibility(keys ? VISIBLE : GONE);
        voiceKey.setVisibility(keys ? VISIBLE : GONE);
        clock.setVisibility(m == Mode.LISTENING ? INVISIBLE : VISIBLE);
        label.setVisibility(GONE);
        head.setVisibility(GONE);
        sub.setVisibility(GONE);
        scroll.setVisibility(GONE);
        stage.sparkles(null);
        stage.invalidate();
        stayAwake();
    }

    /**
     * Lo schermo resta acceso finché si parla con Jenny: mentre ascolta, mentre
     * pensa e mentre la sua voce parla. Appena torna ferma si spegne coi tempi
     * dell'orologio, come ogni altra app.
     */
    private void stayAwake() {
        setKeepScreenOn(mode == Mode.LISTENING || mode == Mode.THINKING || voiceSpeaking || voiceComing);
    }

    /**
     * La voce sta per partire (la si sta preparando): lo schermo non si spegne
     * nell'attesa. Se la voce non arriva, dopo un po' si smette di aspettarla.
     */
    void voiceComing(boolean on) {
        post(() -> {
            main.removeCallbacks(voiceLate);
            voiceComing = on;
            if (on) main.postDelayed(voiceLate, VOICE_WAIT_MS);
            stayAwake();
        });
    }

    private void title(CharSequence t, float size, int weight, float y, int maxLines) {
        head.setText(t);
        head.setTypeface(Polso.font(getContext(), weight));
        head.setTextSize(TypedValue.COMPLEX_UNIT_PX, size * s);
        head.setLetterSpacing(-0.01f);
        head.setLineSpacing(0, 1f);
        head.setMaxLines(maxLines);
        head.setLayoutParams(Polso.at(s, 60, y, 346, -1));
        head.setVisibility(VISIBLE);
    }

    private void caption(int text, int color, float y) {
        label.setText(text);
        label.setTextColor(color);
        label.setLayoutParams(Polso.row(s, y));
        label.setVisibility(VISIBLE);
    }

    private void note(CharSequence t, float size, float y, int maxLines) {
        sub.setText(t);
        sub.setTextSize(TypedValue.COMPLEX_UNIT_PX, size * s);
        sub.setMaxLines(maxLines);
        sub.setLayoutParams(Polso.at(s, 72, y, 322, -1));
        sub.setVisibility(t.length() == 0 ? GONE : VISIBLE);
    }

    private int lines(CharSequence t, float size, int weight, float width) {
        TextPaint p = new TextPaint(Paint.ANTI_ALIAS_FLAG);
        p.setTypeface(Polso.font(getContext(), weight));
        p.setTextSize(size * s);
        p.setLetterSpacing(-0.01f);
        return StaticLayout.Builder.obtain(t, 0, t.length(), p, Polso.px(s, width))
                .setAlignment(Layout.Alignment.ALIGN_CENTER).build().getLineCount();
    }

    private void pose(int bodyRes, int faceRes, float height) {
        pose(bodyRes, faceRes, height, 446);
    }

    /** Jenny alta {@code height}, con i piedi a {@code feet} (misure del disegno). */
    private void pose(int bodyRes, int faceRes, float height, float feet) {
        jennyBody.setImageResource(bodyRes);
        if (faceRes == 0) jennyFace.setImageDrawable(null);
        else jennyFace.setImageResource(faceRes);
        float size = height / FIG_H;
        jenny.setLayoutParams(Polso.at(s, C - FIG_CX * size, feet - height - FIG_TOP * size, size, size));
        stage.shadow(feet, height);
        jenny.animate().cancel();
        jenny.setScaleX(.94f);
        jenny.setScaleY(.94f);
        jenny.setPivotX(FIG_CX * size * s);
        jenny.setPivotY((FIG_TOP + FIG_H) * size * s);
        jenny.animate().scaleX(1f).scaleY(1f).setDuration(180).start();
    }

    private void tapJenny() {
        if (mode == Mode.LISTENING || mode == Mode.THINKING) return;
        jenny.performHapticFeedback(HapticFeedbackConstants.CONTEXT_CLICK);
        Runnable r = mode == Mode.ERROR && onRetry != null ? onRetry : onParla;
        if (r != null) r.run();
    }

    void showFlash(String t) {
        Polso.showFlash(flash, t);
    }

    /** Il bordo segue il livello della voce (dB del riconoscitore, circa -2..10). */
    void level(float rmsdB) {
        stage.level(Math.max(0f, Math.min(1f, (rmsdB + 2f) / 10f)));
    }

    /** Fine della voce: il bordo torna fermo. */
    void rest() {
        stage.level(0.4f);
    }

    /** Mentre la voce di Jenny parla, la bocca si muove; poi si chiude. */
    void speaking(boolean on) {
        post(() -> {
            voiceSpeaking = on;
            main.removeCallbacks(voiceLate);
            voiceComing = false;
            stayAwake();
            if (mode != Mode.SAYING) return;
            if (on) moveMouth(Long.MAX_VALUE);
            else mouthUntil = 0;
        });
    }

    /** Apre e chiude la bocca per {@code millis}, a passi un po' irregolari come chi parla. */
    private void moveMouth(long millis) {
        long now = android.os.SystemClock.uptimeMillis();
        mouthUntil = millis == Long.MAX_VALUE ? Long.MAX_VALUE : now + millis;
        if (mouthMoving) return;
        mouthMoving = true;
        mouthOpen = false;
        mouthStep();
    }

    private void mouthStep() {
        if (!mouthMoving || mode != Mode.SAYING) return;
        boolean done = android.os.SystemClock.uptimeMillis() >= mouthUntil && !voiceSpeaking;
        mouthOpen = !done && !mouthOpen;
        jennyFace.setImageResource(mouthOpen ? R.drawable.jenny_face_talk : R.drawable.jenny_face_normal);
        if (done) {
            mouthMoving = false;
            return;
        }
        main.postDelayed(this::mouthStep, 110 + (long) (Math.random() * 110));
    }

    private void toggleVoice() {
        voiceOn = !voiceOn;
        voiceKey.performHapticFeedback(HapticFeedbackConstants.CONTEXT_CLICK);
        showFlash(getContext().getString(voiceOn ? R.string.voice_on : R.string.voice_off));
        stage.invalidate();
        if (onToggle != null) onToggle.onToggle(voiceOn);
    }

    /** Il cursore rosa in fondo alla trascrizione, che lampeggia. */
    private void caret() {
        caretOn = !caretOn;
        if (mode == Mode.LISTENING) drawTranscript();
        main.postDelayed(this::caret, CARET_MS);
    }

    private void drawTranscript() {
        String t = heard;
        // Tre righe al massimo: di una frase lunga si vede la fine, che è la
        // parte che si sta dicendo.
        if (t.length() > 54) t = "…" + t.substring(t.length() - 53).replaceFirst("^\\S*\\s", "");
        SpannableStringBuilder b = new SpannableStringBuilder(t);
        b.append("|");
        b.setSpan(new ForegroundColorSpan(caretOn ? Polso.PINK : 0x00000000),
                b.length() - 1, b.length(), Spanned.SPAN_EXCLUSIVE_EXCLUSIVE);
        head.setText(b);
    }

    /** Le tre stelline di Ci penso si accendono una dopo l'altra, dal basso. */
    private void twinkle(int step) {
        if (mode != Mode.THINKING) return;
        stage.lit(step % 4);
        main.postDelayed(() -> twinkle(step + 1), 280);
    }

    @Override
    protected void onDetachedFromWindow() {
        main.removeCallbacksAndMessages(null);
        super.onDetachedFromWindow();
    }

    /**
     * Tutto quello che è disegnato e non è testo né Jenny: il bordo acceso di
     * Ti ascolto, l'ombra rosa sotto i piedi, le stelline, i due archi con i
     * loro segni e l'arco di scorrimento del testo lungo.
     */
    private class Stage extends View {
        private final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);
        private final Paint stroke = new Paint(Paint.ANTI_ALIAS_FLAG);
        private final RectF oval = new RectF();
        private final Path star = new Path();
        private final Drawable keyboard, voice, voiceOff;
        private float[][] sparkles;
        private int lit = 3;
        private float level, shadowY, shadowW, shadowH;

        Stage(Context c) {
            super(c);
            stroke.setStyle(Paint.Style.STROKE);
            stroke.setStrokeCap(Paint.Cap.ROUND);
            keyboard = c.getDrawable(R.drawable.ic_tastiera).mutate();
            voice = c.getDrawable(R.drawable.ic_voce).mutate();
            voiceOff = c.getDrawable(R.drawable.ic_voce_spenta).mutate();
        }

        void sparkles(float[][] list) {
            sparkles = list;
            lit = 3;
            invalidate();
        }

        void lit(int count) {
            lit = count;
            invalidate();
        }

        void level(float v) {
            level += (v - level) * 0.5f;
            invalidate();
        }

        void shadow(float feet, float height) {
            shadowY = feet - 3;
            shadowW = height * 0.61f;
            shadowH = height * 0.084f;
            invalidate();
        }

        @Override
        protected void onDraw(Canvas canvas) {
            if (mode == Mode.LISTENING) drawBezel(canvas);
            drawShadow(canvas);
            if (sparkles != null) {
                for (int i = 0; i < sparkles.length; i++) {
                    float[] k = sparkles[i];
                    float alpha = mode == Mode.THINKING ? (i < lit ? k[3] + .3f : k[3] * .35f) : k[3];
                    drawSparkle(canvas, k[0], k[1], k[2], Math.min(1f, alpha));
                }
            }
            if (kbKey.getVisibility() == VISIBLE) drawKeys(canvas);
            if (longText) drawScroll(canvas);
        }

        private void ring(float radius) {
            oval.set((C - radius) * s, (C - radius) * s, (C + radius) * s, (C + radius) * s);
        }

        private void drawBezel(Canvas canvas) {
            float r = getWidth() / 2f;
            // Un velo, non un faro: a voce ferma si vede appena, e cresce poco.
            int glow = Math.round(255 * (0.05f + 0.13f * level));
            paint.setShader(new RadialGradient(r, r, r,
                    new int[]{0x00FEA5D3, 0x00FEA5D3, (glow << 24) | 0xFEA5D3},
                    new float[]{0f, 0.80f, 1f}, Shader.TileMode.CLAMP));
            canvas.drawCircle(r, r, r, paint);
            paint.setShader(null);
            stroke.setColor(Polso.PINK);
            stroke.setAlpha(Math.round(255 * (0.55f + 0.30f * level)));
            stroke.setStrokeWidth(3 * s);
            canvas.drawCircle(r, r, r - 1.5f * s, stroke);
            stroke.setAlpha(255);
        }

        private void drawShadow(Canvas canvas) {
            if (shadowW <= 0) return;
            float cx = C * s, cy = shadowY * s, rx = shadowW / 2 * s;
            canvas.save();
            canvas.scale(1f, shadowH / shadowW, cx, cy);
            paint.setShader(new RadialGradient(cx, cy, rx, 0x6BFEA5D3, 0x00FEA5D3, Shader.TileMode.CLAMP));
            canvas.drawCircle(cx, cy, rx, paint);
            paint.setShader(null);
            canvas.restore();
        }

        /** La stellina a quattro punte del quadrante, con i fianchi curvi. */
        private void drawSparkle(Canvas canvas, float x, float y, float size, float alpha) {
            float k = size * s / 24f, ox = x * s, oy = y * s;
            star.reset();
            star.moveTo(ox + 12 * k, oy);
            star.quadTo(ox + 13.2f * k, oy + 10.8f * k, ox + 24 * k, oy + 12 * k);
            star.quadTo(ox + 13.2f * k, oy + 13.2f * k, ox + 12 * k, oy + 24 * k);
            star.quadTo(ox + 10.8f * k, oy + 13.2f * k, ox, oy + 12 * k);
            star.quadTo(ox + 10.8f * k, oy + 10.8f * k, ox + 12 * k, oy);
            star.close();
            paint.setColor(Polso.YELLOW);
            paint.setAlpha(Math.round(255 * alpha));
            canvas.drawPath(star, paint);
            paint.setAlpha(255);
        }

        private void drawKeys(Canvas canvas) {
            ring(KEY_R);
            stroke.setStrokeWidth(5 * s);
            stroke.setColor(KEY_GREY);
            canvas.drawArc(oval, KB_START, KEY_SWEEP, false, stroke);
            stroke.setColor(voiceOn ? Polso.PINK : KEY_OFF);
            canvas.drawArc(oval, VOICE_START, KEY_SWEEP, false, stroke);
            glyph(canvas, keyboard, KB_START + KEY_SWEEP / 2, Polso.INK);
            glyph(canvas, voiceOn ? voice : voiceOff, VOICE_START + KEY_SWEEP / 2, voiceOn ? Polso.PINK : Polso.MUTED);
        }

        private void glyph(Canvas canvas, Drawable d, float degrees, int color) {
            double a = Math.toRadians(degrees);
            float x = (C + GLYPH_R * (float) Math.cos(a)) * s, y = (C + GLYPH_R * (float) Math.sin(a)) * s;
            int half = Polso.px(s, 15);
            d.setBounds(Math.round(x) - half, Math.round(y) - half, Math.round(x) + half, Math.round(y) + half);
            d.setTint(color);
            d.draw(canvas);
        }

        /** Dove sei nel testo lungo: un arco in alto a destra, sopra quello della voce. */
        private void drawScroll(Canvas canvas) {
            View content = scroll.getChildAt(0);
            if (content == null) return;
            int view = scroll.getHeight();
            int range = content.getHeight() - view;
            if (range <= 0) return;
            ring(224);
            float start = -36f, sweep = 32f;
            stroke.setStrokeWidth(4 * s);
            stroke.setColor(Polso.CHIP);
            canvas.drawArc(oval, start, sweep, false, stroke);
            float thumb = Math.max(8f, sweep * view / content.getHeight());
            float pos = Math.max(0f, Math.min(1f, (float) scroll.getScrollY() / range));
            stroke.setColor(Polso.INK);
            canvas.drawArc(oval, start + (sweep - thumb) * pos, thumb, false, stroke);
        }
    }
}
