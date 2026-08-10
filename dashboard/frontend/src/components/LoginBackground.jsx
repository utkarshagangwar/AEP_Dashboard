"use client";
import { useEffect, useRef, useState } from "react";

/**
 * LoginBackground
 *
 * Decorative canvas background for the login page only:
 *  - a faint dot/line grid (terminal/circuit texture)
 *  - a soft "web" strung from the two top corners (nod to the AEP spider logo)
 *  - a handful of small bugs crawling across the screen
 *  - a green "scan" beam that periodically sweeps down and "catches" any
 *    bug it passes over (flashes red, gets an x-mark, fades out)
 *  - a glowing terminal chip, bottom-left, cycling through what the
 *    platform actually does
 *
 * Everything here is `position: fixed` to the viewport (not the page), so
 * if the login form ever grows taller than the screen and the page
 * scrolls, the background and the terminal chip stay put and in view
 * instead of scrolling away with the content.
 */

const FEATURE_LINES = [
  "generating SOW...",
  "tracking SOW impact...",
  "creating TDD...",
  "running AI-powered test execution...",
  "running visual QA audits...",
  "logging defects...",
  "tracking AI usage & cost...",
  "recording test run videos...",
  "syncing project environments...",
  "0 open defects",
];

function FeatureTicker() {
  const [index, setIndex] = useState(0);
  useEffect(() => {
    const id = setInterval(
      () => setIndex((v) => (v + 1) % FEATURE_LINES.length),
      2400,
    );
    return () => clearInterval(id);
  }, []);

  return (
    <div
      className="pointer-events-none fixed left-3 bottom-3 z-[1] flex max-w-[calc(100vw-1.5rem)] items-center gap-2 overflow-hidden rounded-md border border-emerald-400/35 px-2.5 py-2 sm:left-5 sm:bottom-4 sm:px-3"
      style={{
        background: "#081109",
        boxShadow:
          "0 0 0 1px rgba(0,0,0,0.2) inset, 0 0 14px rgba(52,211,153,0.18) inset, 0 8px 24px rgba(8,17,9,0.25)",
      }}
    >
      <span
        className="h-1.5 w-1.5 shrink-0 rounded-full"
        style={{
          background: "#34D399",
          boxShadow: "0 0 6px 1px rgba(52,211,153,0.9)",
          animation: "aep-bg-pulse 1.6s ease-in-out infinite",
        }}
        aria-hidden="true"
      />
      <span
        className="truncate whitespace-nowrap text-[11px] tracking-[0.02em] sm:text-xs"
        style={{
          fontFamily:
            "'JetBrains Mono', 'Fira Code', ui-monospace, Menlo, monospace",
          color: "#6CF5B4",
          textShadow:
            "0 0 2px rgba(108,245,180,0.9), 0 0 8px rgba(52,211,153,0.75), 0 0 18px rgba(52,211,153,0.45)",
        }}
      >
        <span style={{ color: "#34D399" }}>$ </span>
        {FEATURE_LINES[index]}
        <span
          style={{ animation: "aep-bg-blink 1s step-end infinite" }}
          aria-hidden="true"
        >
          ▍
        </span>
      </span>
    </div>
  );
}

export default function LoginBackground() {
  const canvasRef = useRef(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    const ctx = canvas.getContext("2d");
    let width = 0;
    let height = 0;
    let dpr = 1;
    let animationId;

    const reduceMotion = window.matchMedia(
      "(prefers-reduced-motion: reduce)",
    ).matches;

    const COLORS = {
      grid: "rgba(15, 23, 42, 0.05)",
      web: "rgba(15, 23, 42, 0.08)",
      bug: "rgba(15, 23, 42, 0.32)",
      bugCaught: "rgba(224, 62, 87, 0.95)",
      scanCore: "rgba(52, 211, 153, 0.32)",
      scanEdge: "rgba(52, 211, 153, 0)",
    };

    function resize() {
      dpr = Math.min(window.devicePixelRatio || 1, 2);
      // Sized to the viewport, not the (possibly taller/scrollable) page,
      // since the canvas is fixed-positioned.
      width = window.innerWidth;
      height = window.innerHeight;
      canvas.width = width * dpr;
      canvas.height = height * dpr;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }
    resize();
    window.addEventListener("resize", resize);

    function webAnchors() {
      return [
        { x: 0, y: 0, aStart: 0.08, aEnd: Math.PI / 2 - 0.08 },
        { x: width, y: 0, aStart: Math.PI / 2 + 0.08, aEnd: Math.PI - 0.08 },
      ];
    }

    function spawnBug() {
      return {
        x: Math.random() * width,
        y: Math.random() * height,
        angle: Math.random() * Math.PI * 2,
        speed: 0.15 + Math.random() * 0.25,
        wobble: Math.random() * Math.PI * 2,
        size: 3 + Math.random() * 2.5,
        state: "alive", // alive | caught | gone
        caughtAt: 0,
      };
    }

    const NUM_BUGS = 6;
    let bugs = Array.from({ length: NUM_BUGS }, spawnBug);

    let scanY = -150;
    const scanHeight = 90;
    const scanSpeed = 0.5;
    let scanPause = 600;

    function drawGrid() {
      ctx.strokeStyle = COLORS.grid;
      ctx.lineWidth = 1;
      const step = 46;
      for (let x = 0; x < width; x += step) {
        ctx.beginPath();
        ctx.moveTo(x + 0.5, 0);
        ctx.lineTo(x + 0.5, height);
        ctx.stroke();
      }
      for (let y = 0; y < height; y += step) {
        ctx.beginPath();
        ctx.moveTo(0, y + 0.5);
        ctx.lineTo(width, y + 0.5);
        ctx.stroke();
      }
    }

    function drawWeb() {
      ctx.strokeStyle = COLORS.web;
      ctx.lineWidth = 1;
      const anchors = webAnchors();
      const len = Math.hypot(width, height) * 0.95;
      anchors.forEach((a) => {
        const strands = 6;
        for (let i = 0; i <= strands; i++) {
          const ang = a.aStart + ((a.aEnd - a.aStart) / strands) * i;
          ctx.beginPath();
          ctx.moveTo(a.x, a.y);
          ctx.lineTo(a.x + Math.cos(ang) * len, a.y + Math.sin(ang) * len);
          ctx.stroke();
        }
        for (let r = 90; r < len; r += 110) {
          ctx.beginPath();
          ctx.arc(a.x, a.y, r, a.aStart, a.aEnd);
          ctx.stroke();
        }
      });
    }

    function drawBug(bug, alpha, color) {
      ctx.save();
      ctx.globalAlpha = alpha;
      ctx.translate(bug.x, bug.y);
      ctx.rotate(bug.angle);
      const s = bug.size;
      ctx.fillStyle = color;
      ctx.strokeStyle = color;
      ctx.lineWidth = 0.8;
      ctx.beginPath();
      ctx.ellipse(0, 0, s, s * 0.7, 0, 0, Math.PI * 2);
      ctx.fill();
      ctx.beginPath();
      ctx.arc(s * 1.1, 0, s * 0.45, 0, Math.PI * 2);
      ctx.fill();
      for (let i = -1; i <= 1; i++) {
        ctx.beginPath();
        ctx.moveTo(i * s * 0.4, 0);
        ctx.lineTo(i * s * 0.4 + Math.sin(bug.wobble + i) * s * 0.6, s * 1.4);
        ctx.stroke();
        ctx.beginPath();
        ctx.moveTo(i * s * 0.4, 0);
        ctx.lineTo(
          i * s * 0.4 + Math.sin(bug.wobble + i) * s * 0.6,
          -s * 1.4,
        );
        ctx.stroke();
      }
      if (bug.state === "caught") {
        const r = s * 1.9;
        ctx.strokeStyle = COLORS.bugCaught;
        ctx.lineWidth = 1.2;
        ctx.beginPath();
        ctx.moveTo(-r, -r);
        ctx.lineTo(r, r);
        ctx.moveTo(-r, r);
        ctx.lineTo(r, -r);
        ctx.stroke();
      }
      ctx.restore();
    }

    let lastTime = performance.now();
    function tick(now) {
      const dt = Math.min(now - lastTime, 40);
      lastTime = now;

      ctx.clearRect(0, 0, width, height);
      drawGrid();
      drawWeb();

      bugs.forEach((bug) => {
        if (bug.state === "alive") {
          bug.wobble += 0.03;
          bug.angle += Math.sin(bug.wobble) * 0.01;
          bug.x += Math.cos(bug.angle) * bug.speed * (dt / 16);
          bug.y += Math.sin(bug.angle) * bug.speed * (dt / 16);
          if (bug.x < -10) bug.x = width + 10;
          if (bug.x > width + 10) bug.x = -10;
          if (bug.y < -10) bug.y = height + 10;
          if (bug.y > height + 10) bug.y = -10;

          if (
            !reduceMotion &&
            scanPause <= 0 &&
            bug.y > scanY &&
            bug.y < scanY + scanHeight
          ) {
            bug.state = "caught";
            bug.caughtAt = now;
          }
          drawBug(bug, 1, COLORS.bug);
        } else if (bug.state === "caught") {
          const elapsed = now - bug.caughtAt;
          if (elapsed < 300) {
            drawBug(bug, 1, COLORS.bugCaught);
          } else if (elapsed < 600) {
            drawBug(bug, 1 - (elapsed - 300) / 300, COLORS.bugCaught);
          } else {
            bug.state = "gone";
          }
        }
      });
      bugs = bugs.filter((b) => b.state !== "gone");
      while (bugs.length < NUM_BUGS) bugs.push(spawnBug());

      if (!reduceMotion) {
        if (scanPause > 0) {
          scanPause -= dt;
        } else {
          scanY += scanSpeed * (dt / 16);
          if (scanY > height + 150) {
            scanY = -150;
            scanPause = 1800;
          }
        }
        const grad = ctx.createLinearGradient(0, scanY, 0, scanY + scanHeight);
        grad.addColorStop(0, COLORS.scanEdge);
        grad.addColorStop(0.5, COLORS.scanCore);
        grad.addColorStop(1, COLORS.scanEdge);
        ctx.fillStyle = grad;
        ctx.fillRect(0, scanY, width, scanHeight);
      }

      animationId = requestAnimationFrame(tick);
    }
    animationId = requestAnimationFrame(tick);

    return () => {
      cancelAnimationFrame(animationId);
      window.removeEventListener("resize", resize);
    };
  }, []);

  return (
    <>
      <style>{`
        @keyframes aep-bg-blink { 50% { opacity: 0; } }
        @keyframes aep-bg-pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.35; } }
      `}</style>
      {/* Fixed to the viewport (not the document) so it never scrolls with
          the login card, and stays full-bleed at every screen size. */}
      <canvas
        ref={canvasRef}
        className="pointer-events-none fixed inset-0 z-0 h-screen w-screen"
        aria-hidden="true"
      />
      <FeatureTicker />
    </>
  );
}
