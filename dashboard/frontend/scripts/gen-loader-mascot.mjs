import { readFileSync, writeFileSync } from "node:fs";

const SRC = "frontend/public/loader-orb.svg";
const OUT = "frontend/src/components/LoaderMascot.tsx";

let svg = readFileSync(SRC, "utf8").trim();

// Strip the root <svg …> wrapper; we re-declare it ourselves with a tuned
// viewBox. Everything between is kept byte-for-byte apart from the colour
// substitutions below.
const open = svg.indexOf(">");
if (!svg.startsWith("<svg") || !svg.endsWith("</svg>")) {
  throw new Error("unexpected loader-orb.svg shape");
}
let inner = svg.slice(open + 1, svg.length - "</svg>".length);

const before = inner;
// Ink: body, ears, tail, mouth stroke.
inner = inner.split('"#000000"').join('"var(--foreground)"');
// Eyes, nose, toe beans — the knockouts inside the ink.
inner = inner.split('"#ffffff"').join('"var(--background)"');
// Ground shadow: a flat mid-grey in the source, which disappears on a dark
// surface. Expressed as translucent ink instead so it reads in both themes.
const shadowHits = inner.split('fill="#bababa"').length - 1;
inner = inner.split('fill="#bababa"').join('fill="var(--foreground)" opacity="0.22"');
if (before === inner || shadowHits !== 1) {
  throw new Error(`colour substitution failed (shadow hits: ${shadowHits})`);
}
if (/#[0-9a-fA-F]{3,6}/.test(inner)) {
  throw new Error("literal colours remain: " + inner.match(/#[0-9a-fA-F]{3,6}/g).join(","));
}

const escaped = inner.replace(/\\/g, "\\\\").replace(/`/g, "\\`").replace(/\$\{/g, "\\${");

const file = `"use client";

import { forwardRef } from "react";

/**
 * The loader mascot — the cat from \`design/global_loader_1.svg\`, bundled.
 *
 * Generated from \`public/loader-orb.svg\` with its three literal colours
 * remapped onto theme tokens (ink -> --foreground, knockouts -> --background,
 * ground shadow -> translucent ink). Do not hand-edit the markup below; edit
 * the source SVG and re-run the generator.
 *
 * Why it is inlined rather than fetched: the previous GlobalLoader pulled the
 * file over the network and injected it with innerHTML behind a 300ms fade.
 * Route transitions routinely finished before that round-trip landed, so the
 * mascot reserved its box and then never appeared. Bundled markup paints on
 * the first frame, server-rendered included.
 *
 * ── Geometry ──────────────────────────────────────────────────────────────
 * The animation is a jump: the cat leaves its resting pose and travels ~180
 * user units upward. Cropping to the full travel would leave the resting cat
 * marooned at the bottom of a mostly-empty box. So the viewBox spans the whole
 * jump, and the caller sizes a short box around the *resting* pose and lets the
 * apex overflow upward. Layout is anchored to where the cat actually sits;
 * the leap borrows space that is empty anyway.
 *
 * Extents below are measured, not estimated: getBBox() on the live SVG with
 * SMIL paused at t=0 (rest) and t=0.233s (jump apex), padded by the 5-unit
 * half-stroke that getBBox() excludes.
 *
 *   at rest   x 140..376, y 296..452   (452 is the ground shadow's underside)
 *   at apex   ear tips reach y 86
 *
 * The crop spans the full jump. The resting slice is the bottom 156 units of
 * it, which is what the caller sizes its box to.
 */
export const MASCOT_VIEWBOX = "137 84 240 370";
/** viewBox aspect — callers need it to size the box without distortion. */
export const MASCOT_ASPECT = 240 / 370;
/** Height of the resting pose as a fraction of the viewBox height. */
export const MASCOT_REST_FRACTION = 156 / 370;

const MASCOT_MARKUP = \`${escaped}\`;

const LoaderMascot = forwardRef<SVGSVGElement, { className?: string }>(
  function LoaderMascot({ className }, ref) {
    return (
      <svg
        ref={ref}
        className={className}
        viewBox={MASCOT_VIEWBOX}
        preserveAspectRatio="xMidYMax meet"
        fill="none"
        xmlns="http://www.w3.org/2000/svg"
        aria-hidden="true"
        focusable="false"
        dangerouslySetInnerHTML={{ __html: MASCOT_MARKUP }}
      />
    );
  },
);

export default LoaderMascot;
`;

writeFileSync(OUT, file);
console.log(`wrote ${OUT} (${file.length} bytes, ${shadowHits} shadow)`);
