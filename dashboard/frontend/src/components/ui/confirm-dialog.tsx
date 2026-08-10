"use client";

import { useEffect, useState } from "react";
import { createPortal } from "react-dom";

import { Button } from "./button";
import { _registerConfirmListener, type ConfirmRequest } from "@/lib/confirm";

// Matches the CSS transition durations in app/global.css's `.confirm-*`
// rules exactly -- the exit timer below has to outlast the real transition
// or the card unmounts mid-fade and the animation visibly cuts off.
const EXIT_MS = 170;

function ConfirmIcon({ tone, entered }: { tone: "danger" | "neutral"; entered: boolean }) {
  const pathClass = `confirm-icon__path${entered ? " is-drawn" : ""}`;
  const dotClass = `confirm-icon__dot${entered ? " is-drawn" : ""}`;

  return (
    <div className={`confirm-icon confirm-icon--${tone}`} aria-hidden="true">
      {tone === "danger" && <span className="confirm-icon__ring" />}
      <svg viewBox="0 0 24 24" fill="none" className="confirm-icon__svg">
        {tone === "danger" ? (
          <>
            <path
              className={pathClass}
              d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0Z"
              stroke="currentColor"
              strokeWidth="1.8"
              strokeLinecap="round"
              strokeLinejoin="round"
              pathLength="1"
            />
            <path
              className={pathClass}
              d="M12 9v3.6"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              pathLength="1"
            />
          </>
        ) : (
          <>
            <circle
              className={pathClass}
              cx="12"
              cy="12"
              r="9"
              stroke="currentColor"
              strokeWidth="1.8"
              pathLength="1"
            />
            <path
              className={pathClass}
              d="M9.5 9a2.5 2.5 0 1 1 3.5 2.29c-.76.35-1.25.99-1.25 1.71"
              stroke="currentColor"
              strokeWidth="1.8"
              strokeLinecap="round"
              strokeLinejoin="round"
              pathLength="1"
            />
          </>
        )}
      </svg>
      {/* The dot has no meaningful path length to draw, so it fades+scales in
          on its own slightly-later delay instead -- the last beat of the
          glyph completing itself, matching the timing the stroke reveal
          already lands on. */}
      <span className={dotClass} />
    </div>
  );
}

/**
 * Renders the app's shared confirm dialog. Mount exactly once, near the
 * root (Providers.jsx, next to <Toaster />) -- every confirmDialog() call
 * anywhere in the app is served by this one instance via lib/confirm.ts's
 * module-level listener.
 *
 * Kept mounted through the exit animation on purpose: clearing `request`
 * the instant an action is picked would unmount the card before its 160ms
 * fade-out ever painted. `closing` holds the last request's content on
 * screen while `entered` flips back to false, and only clears after the CSS
 * transition's real duration.
 */
export function ConfirmDialogHost() {
  const [request, setRequest] = useState<ConfirmRequest | null>(null);
  const [entered, setEntered] = useState(false);

  useEffect(() => {
    _registerConfirmListener(setRequest);
    return () => _registerConfirmListener(null);
  }, []);

  // Two-frame handoff: mount at the closed (scale .94/opacity 0) styles
  // first, then flip to `entered` on the following frame so the browser has
  // a committed "before" state to transition from. A single rAF sometimes
  // lands in the same frame as the mount on fast machines and the card just
  // pops in with no visible transition.
  useEffect(() => {
    if (!request) return;
    setEntered(false);
    const id1 = requestAnimationFrame(() => {
      const id2 = requestAnimationFrame(() => setEntered(true));
      return () => cancelAnimationFrame(id2);
    });
    return () => cancelAnimationFrame(id1);
  }, [request]);

  useEffect(() => {
    if (!request) return;
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") settle(false);
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [request]);

  function settle(value: boolean) {
    if (!request) return;
    request.resolve(value);
    setEntered(false);
    const resolved = request;
    window.setTimeout(() => {
      // Guard against a newer request having already replaced this one
      // (e.g. a second confirmDialog() call fired before the first one's
      // exit timer finished) -- only clear if we're still showing the same
      // request we started closing.
      setRequest((current) => (current === resolved ? null : current));
    }, EXIT_MS);
  }

  if (typeof document === "undefined" || !request) return null;

  const tone = request.tone ?? "danger";
  const confirmLabel = request.confirmLabel ?? (tone === "danger" ? "Delete" : "Continue");
  const cancelLabel = request.cancelLabel ?? "Cancel";

  return createPortal(
    <div
      className={`confirm-backdrop${entered ? " is-entered" : ""}`}
      onClick={(e) => {
        if (e.target === e.currentTarget) settle(false);
      }}
    >
      <div
        className="confirm-card"
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="confirm-dialog-title"
        aria-describedby={request.body ? "confirm-dialog-body" : undefined}
      >
        <ConfirmIcon tone={tone} entered={entered} />
        <h2 id="confirm-dialog-title">{request.title}</h2>
        {request.body && <p id="confirm-dialog-body">{request.body}</p>}
        <div className="confirm-actions">
          <Button variant="outline" size="lg" onClick={() => settle(false)}>
            {cancelLabel}
          </Button>
          <Button
            variant={tone === "danger" ? "destructive" : "invert"}
            size="lg"
            onClick={() => settle(true)}
          >
            {confirmLabel}
          </Button>
        </div>
      </div>
    </div>,
    document.body
  );
}
