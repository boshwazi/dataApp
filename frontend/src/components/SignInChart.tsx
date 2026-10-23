import { useEffect, useRef } from "react";

const BARS = 40;
const BLUE = [77, 138, 240];
const YELLOW = "#facc15"; // the AI's colour: the bar you point at

/**
 * The sign-in screen's ambient chart: bars that drift slowly under a trend line, rise where the pointer is,
 * and mark the bar under it in yellow. Decorative only (hidden from screen readers). Under reduced motion it
 * draws once and redraws on hover, with no drift or entrance.
 */
export function SignInChart() {
  const ref = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = ref.current;
    const ctx = canvas?.getContext("2d");
    if (!canvas || !ctx) return;
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const lift = new Float32Array(BARS);
    const pointer = { x: 0, active: false };
    let width = 0;
    let height = 0;
    let frame = 0;
    let start = 0;

    // A gentle rising series: a trend plus a few slow waves, so it reads as data without claiming any.
    const level = (i: number, t: number) => {
      const x = i / BARS;
      return 0.3 + 0.32 * x + 0.12 * Math.sin(x * 5.1 + t * 0.35) + 0.08 * Math.sin(x * 11.3 - t * 0.22)
        + 0.05 * Math.sin(x * 23 + t * 0.5);
    };

    const draw = (t: number) => {
      ctx.clearRect(0, 0, width, height);
      const left = 40;
      const right = width - 40;
      const bottom = height - 56;
      const top = height * 0.36;
      const step = (right - left) / BARS;
      const barWidth = step * 0.62;
      const grow = reduce ? 1 : 1 - Math.pow(1 - Math.min(1, t / 1.4), 3); // the entrance: bars rise once

      ctx.strokeStyle = "rgba(255, 255, 255, 0.07)";
      ctx.lineWidth = 1;
      for (let g = 0; g <= 3; g++) {
        const y = Math.round(bottom - (bottom - top) * (g / 3)) + 0.5;
        ctx.beginPath();
        ctx.moveTo(left, y);
        ctx.lineTo(right, y);
        ctx.stroke();
      }

      let hot = -1;
      if (pointer.active) hot = Math.max(0, Math.min(BARS - 1, Math.floor((pointer.x - left) / step)));
      const tops: number[] = [];
      for (let i = 0; i < BARS; i++) {
        const cx = left + step * (i + 0.5);
        const near = pointer.active ? Math.exp(-((cx - pointer.x) ** 2) / (2 * (step * 3) ** 2)) : 0;
        lift[i] += (near - lift[i]) * (reduce ? 1 : 0.12);
        const value = Math.min(1, level(i, reduce ? 0 : t) + lift[i] * 0.2) * grow;
        const barHeight = (bottom - top) * value;
        const y = bottom - barHeight;
        tops.push(y);
        const alpha = 0.32 + 0.12 * (i / BARS) + 0.45 * lift[i];
        ctx.fillStyle = i === hot ? YELLOW : `rgba(${BLUE.join(",")}, ${alpha.toFixed(3)})`;
        ctx.beginPath();
        if (ctx.roundRect) ctx.roundRect(cx - barWidth / 2, y, barWidth, barHeight, [3, 3, 0, 0]);
        else ctx.rect(cx - barWidth / 2, y, barWidth, barHeight);
        ctx.fill();
      }

      // The trend line: a smoothed run over the bar tops, a little above them.
      const smooth = tops.map((_, i) => {
        let sum = 0;
        let n = 0;
        for (let k = Math.max(0, i - 3); k <= Math.min(BARS - 1, i + 3); k++) {
          sum += tops[k];
          n++;
        }
        return sum / n - 18;
      });
      const xs = smooth.map((_, i) => left + step * (i + 0.5));
      ctx.strokeStyle = "rgba(196, 214, 246, 0.85)";
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.moveTo(xs[0], smooth[0]);
      for (let i = 1; i < BARS - 1; i++) {
        ctx.quadraticCurveTo(xs[i], smooth[i], (xs[i] + xs[i + 1]) / 2, (smooth[i] + smooth[i + 1]) / 2);
      }
      ctx.lineTo(xs[BARS - 1], smooth[BARS - 1]);
      ctx.stroke();

      if (hot >= 0) {
        const x = Math.round(xs[hot]) + 0.5;
        ctx.strokeStyle = "rgba(250, 204, 21, 0.45)";
        ctx.setLineDash([4, 4]);
        ctx.beginPath();
        ctx.moveTo(x, top - 24);
        ctx.lineTo(x, bottom);
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.fillStyle = YELLOW;
        ctx.beginPath();
        ctx.arc(xs[hot], smooth[hot], 4.5, 0, Math.PI * 2);
        ctx.fill();
      }
    };

    const tick = (ms: number) => {
      if (!start) start = ms;
      draw((ms - start) / 1000);
      frame = requestAnimationFrame(tick);
    };

    const resize = () => {
      const rect = canvas.getBoundingClientRect();
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      width = rect.width;
      height = rect.height;
      canvas.width = Math.round(width * dpr);
      canvas.height = Math.round(height * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      if (reduce) draw(0);
    };

    const move = (event: PointerEvent) => {
      pointer.x = event.clientX - canvas.getBoundingClientRect().left;
      pointer.active = true;
      if (reduce) draw(0);
    };
    const leave = () => {
      pointer.active = false;
      if (reduce) draw(0);
    };

    const observer = new ResizeObserver(resize);
    observer.observe(canvas);
    resize();
    canvas.addEventListener("pointermove", move);
    canvas.addEventListener("pointerleave", leave);
    if (!reduce) frame = requestAnimationFrame(tick);
    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
      canvas.removeEventListener("pointermove", move);
      canvas.removeEventListener("pointerleave", leave);
    };
  }, []);

  return <canvas ref={ref} className="signin-chart" aria-hidden="true" />;
}
