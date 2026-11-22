package com.flagdizero.jenny.wear;

import android.content.Context;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.text.SpannableString;
import android.text.Spanned;
import android.text.style.ForegroundColorSpan;
import android.util.SparseArray;
import android.util.TypedValue;
import android.view.Gravity;
import android.widget.FrameLayout;
import android.widget.TextView;

import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Colori, font e pezzi comuni della schermata dell'orologio. Le misure sono
 * quelle del disegno; chi chiama passa la scala che le porta allo schermo vero.
 *
 * Il font è Unbounded, lo stesso del quadrante: un solo file variabile, e il
 * peso si sceglie qui invece di portarsi dietro quattro file.
 */
final class Polso {
    static final int INK = 0xFFF4F1EA;
    static final int MUTED = 0xFFB4B0A8;
    static final int CARD = 0xFF1C1A22;
    static final int CHIP = 0xFF2A2830;
    static final int YELLOW = 0xFFFED89C;
    static final int PINK = 0xFFFEA5D3;
    static final int RING_OUT = 0xFF33262F;
    static final int RING_MID = 0xFF5E3F53;

    private static final SparseArray<Typeface> FONTS = new SparseArray<>();
    private static final Pattern PERCENT = Pattern.compile("\\d+%");

    private Polso() {}

    static int px(float s, float v) {
        return Math.round(v * s);
    }

    static Typeface font(Context c, int weight) {
        Typeface t = FONTS.get(weight);
        if (t == null) {
            t = new Typeface.Builder(c.getAssets(), "fonts/Unbounded.ttf")
                    .setFontVariationSettings("'wght' " + weight).build();
            FONTS.put(weight, t);
        }
        return t;
    }

    /** Posizione assoluta con le coordinate della tavola. w/h negativi = wrap. */
    static FrameLayout.LayoutParams at(float s, float x, float y, float w, float h) {
        FrameLayout.LayoutParams p = new FrameLayout.LayoutParams(
                w < 0 ? FrameLayout.LayoutParams.WRAP_CONTENT : px(s, w),
                h < 0 ? FrameLayout.LayoutParams.WRAP_CONTENT : px(s, h),
                Gravity.TOP | Gravity.START);
        p.leftMargin = px(s, x);
        p.topMargin = px(s, y);
        return p;
    }

    /** Una riga di testo che occupa tutta la larghezza, centrata: le tavole ne sono piene. */
    static FrameLayout.LayoutParams row(float s, float y) {
        FrameLayout.LayoutParams p = new FrameLayout.LayoutParams(-1, -2, Gravity.TOP);
        p.topMargin = px(s, y);
        return p;
    }

    static TextView text(Context c, float s, CharSequence t, float size, int weight, int color) {
        TextView v = new TextView(c);
        v.setText(t);
        v.setTypeface(font(c, weight));
        v.setTextSize(TypedValue.COMPLEX_UNIT_PX, size * s);
        v.setTextColor(color);
        v.setIncludeFontPadding(false);
        return v;
    }

    static TextView centered(Context c, float s, CharSequence t, float size, int weight, int color) {
        TextView v = text(c, s, t, size, weight, color);
        v.setGravity(Gravity.CENTER_HORIZONTAL);
        return v;
    }

    /** I numeri con la percentuale in giallo, come "Basilico al 34%". */
    static CharSequence highlight(String t) {
        SpannableString out = new SpannableString(t);
        Matcher m = PERCENT.matcher(t);
        while (m.find()) {
            out.setSpan(new ForegroundColorSpan(YELLOW), m.start(), m.end(), Spanned.SPAN_EXCLUSIVE_EXCLUSIVE);
        }
        return out;
    }

    static GradientDrawable round(int color, float radius) {
        GradientDrawable d = new GradientDrawable();
        d.setColor(color);
        d.setCornerRadius(radius);
        return d;
    }

    /** Un messaggio che compare per un attimo in mezzo allo schermo e sparisce. */
    static TextView flash(Context c, float s) {
        TextView f = centered(c, s, "", 20, 500, INK);
        f.setBackground(round(CHIP, 30 * s));
        f.setPadding(px(s, 24), px(s, 14), px(s, 24), px(s, 14));
        f.setAlpha(0f);
        FrameLayout.LayoutParams p = new FrameLayout.LayoutParams(-2, -2, Gravity.CENTER);
        f.setLayoutParams(p);
        return f;
    }

    static void showFlash(TextView flash, String text) {
        flash.setText(text);
        flash.animate().cancel();
        flash.setAlpha(1f);
        flash.animate().alpha(0f).setStartDelay(1100).setDuration(300).start();
    }
}
