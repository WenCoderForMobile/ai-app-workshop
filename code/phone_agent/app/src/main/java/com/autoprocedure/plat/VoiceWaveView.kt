package com.autoprocedure.plat

import android.animation.ValueAnimator
import android.content.Context
import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.Path
import android.util.AttributeSet
import android.view.View
import android.view.animation.LinearInterpolator
import androidx.core.content.ContextCompat
import kotlin.math.PI
import kotlin.math.sin

/** Animated sine waves shown in place of the text field while recording. */
class VoiceWaveView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
) : View(context, attrs) {
    private val wavePaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeWidth = 4f
        strokeCap = Paint.Cap.ROUND
        color = ContextCompat.getColor(context, R.color.purple_500)
    }
    private val wavePaintSoft = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeWidth = 3f
        strokeCap = Paint.Cap.ROUND
        color = ContextCompat.getColor(context, R.color.recording)
        alpha = 140
    }
    private val path = Path()
    private var phase = 0f
    private val animator = ValueAnimator.ofFloat(0f, (2 * PI).toFloat()).apply {
        duration = 900
        repeatCount = ValueAnimator.INFINITE
        interpolator = LinearInterpolator()
        addUpdateListener {
            phase = it.animatedValue as Float
            invalidate()
        }
    }

    fun start() {
        if (!animator.isStarted) {
            animator.start()
        }
    }

    fun stop() {
        animator.cancel()
        phase = 0f
        invalidate()
    }

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)
        val w = width.toFloat()
        val h = height.toFloat()
        if (w <= 0f || h <= 0f) {
            return
        }
        val mid = h / 2f
        drawWave(canvas, wavePaintSoft, w, mid, h * 0.32f, 2.2f, phase)
        drawWave(canvas, wavePaint, w, mid, h * 0.22f, 3.1f, -phase * 1.4f)
    }

    private fun drawWave(
        canvas: Canvas,
        paint: Paint,
        width: Float,
        mid: Float,
        amp: Float,
        cycles: Float,
        shift: Float,
    ) {
        path.reset()
        var x = 0f
        path.moveTo(0f, mid)
        while (x <= width) {
            val y = mid + (sin((x / width) * cycles * 2.0 * PI + shift) * amp).toFloat()
            path.lineTo(x, y)
            x += 3f
        }
        canvas.drawPath(path, paint)
    }

    override fun onDetachedFromWindow() {
        stop()
        super.onDetachedFromWindow()
    }
}
