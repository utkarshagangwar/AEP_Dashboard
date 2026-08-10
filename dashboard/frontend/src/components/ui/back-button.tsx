"use client"

import * as React from "react"

import { cn } from "@/lib/utils"

type BackButtonProps = {
  /**
   * The full label, e.g. "Back to SOW". Split on whitespace so each word can
   * be staggered independently, and carried whole in a visually-hidden span
   * for the accessible name.
   */
  label: string
  className?: string
} & (
  | ({ href: string; onClick?: never } & Omit<
      React.ComponentProps<"a">,
      "href" | "className"
    >)
  | ({ href?: never; onClick: React.MouseEventHandler<HTMLButtonElement> } & Omit<
      React.ComponentProps<"button">,
      "onClick" | "className"
    >)
)

/**
 * The back / breadcrumb control.
 *
 * Its own design rather than a variant of the shared Button, at the owner's
 * request: it carries the supplied hover animation (label swap, underline
 * wipe, icon turn) and deliberately not the white button's orbiting rim,
 * bloom and tilt. Two full hover choreographies on one control read as noise,
 * and this one is about leaving the page, not acting on it. Size, colour,
 * radius and border still come from the platform — see `.btn-back` in
 * app/global.css.
 *
 * Renders an <a> when given `href` and a <button> when given `onClick`, so a
 * navigation and a state reset both get the same affordance. `href` stays a
 * plain anchor rather than next/link on purpose: every call site it replaced
 * was already a plain anchor, and swapping in client-side routing would be a
 * behaviour change smuggled in under a design task.
 */
function BackButton({ label, className, ...props }: BackButtonProps) {
  const words = label.trim().split(/\s+/)

  const content = (
    <>
      <svg
        className="btn-back__icon"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden="true"
      >
        <polyline points="15 18 9 12 15 6" />
      </svg>
      {/* Both label copies are decorative — the accessible name is the
          sr-only span below, so the words are announced once and unsplit. */}
      <span className="btn-back__label" aria-hidden="true">
        <span className="btn-back__text">
          {words.map((word, i) => (
            <span key={`t-${i}`}>{word}</span>
          ))}
        </span>
        <span className="btn-back__clone">
          {words.map((word, i) => (
            <span key={`c-${i}`}>{word}</span>
          ))}
        </span>
      </span>
      <span className="sr-only">{label}</span>
    </>
  )

  if ("href" in props && props.href !== undefined) {
    const { href, ...anchorProps } = props as { href: string } & React.ComponentProps<"a">
    return (
      <a href={href} className={cn("btn-back", className)} {...anchorProps}>
        {content}
      </a>
    )
  }

  const { type = "button", ...buttonProps } = props as React.ComponentProps<"button">
  return (
    <button type={type} className={cn("btn-back", className)} {...buttonProps}>
      {content}
    </button>
  )
}

export { BackButton }
