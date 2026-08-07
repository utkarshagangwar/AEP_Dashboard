"use client";

import type { ReactNode } from "react";

import { Button } from "./button";

type ErrorAction = {
  label: string;
  onClick: () => void;
};

function ErrorIcon() {
  return (
    <span
      aria-hidden="true"
      className="flex size-8 shrink-0 items-center justify-center rounded-full bg-red-100 text-red-700"
    >
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="size-4">
        <circle cx="12" cy="12" r="9" />
        <path d="M12 7v6" />
        <path d="M12 16.5h.01" strokeWidth="3" strokeLinecap="round" />
      </svg>
    </span>
  );
}

/** A concise, local failure next to the action that caused it. */
export function ErrorAlert({
  title = "Something went wrong",
  message,
  action,
  onDismiss,
  className = "",
}: {
  title?: string;
  message: ReactNode;
  action?: ErrorAction;
  onDismiss?: () => void;
  className?: string;
}) {
  return (
    <div
      role="alert"
      className={`flex items-start gap-3 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-red-950 ${className}`}
    >
      <ErrorIcon />
      <div className="min-w-0 flex-1">
        <p className="m-0 text-sm font-semibold text-red-900">{title}</p>
        <div className="mt-0.5 text-sm leading-5 text-red-800">{message}</div>
        {action && (
          /* Sits on the alert's own red tint, so the tonal danger form would
             be red-on-red. Ink reads as the action against that surface, and
             the alert's border, icon, and copy already carry the severity. */
          <Button
            type="button"
            variant="invert"
            size="xs"
            onClick={action.onClick}
            className="mt-2"
          >
            {action.label}
          </Button>
        )}
      </div>
      {onDismiss && (
        <Button
          type="button"
          variant="ghost"
          size="icon-sm"
          onClick={onDismiss}
          aria-label="Dismiss error"
          className="-mr-1 -mt-1 text-red-800"
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="size-4" aria-hidden="true">
            <path d="m6 6 12 12M18 6 6 18" />
          </svg>
        </Button>
      )}
    </div>
  );
}

/** A page-level recovery state for failures that prevent the current screen from loading. */
export function ErrorPanel({
  title = "Something went wrong",
  message = "An unexpected error occurred.",
  action,
}: {
  title?: string;
  message?: ReactNode;
  action?: ErrorAction;
}) {
  return (
    <div className="flex min-h-screen items-center justify-center bg-gray-50 px-6 font-sans">
      <div className="w-full max-w-md rounded-xl bg-white p-8 text-center shadow-sm ring-1 ring-gray-200">
        <div className="mx-auto mb-4 w-fit"><ErrorIcon /></div>
        <h1 className="m-0 text-lg font-semibold text-gray-900">{title}</h1>
        <div className="mx-auto mt-2 max-w-sm text-sm leading-6 text-gray-600">{message}</div>
        {action && (
          <Button
            type="button"
            variant="invert"
            size="lg"
            onClick={action.onClick}
            className="mt-5"
          >
            {action.label}
          </Button>
        )}
      </div>
    </div>
  );
}

export function ErrorToast({
  title = "Action failed",
  message,
  onDismiss,
}: {
  title?: string;
  message: ReactNode;
  onDismiss: () => void;
}) {
  return <ErrorAlert title={title} message={message} onDismiss={onDismiss} className="w-full shadow-md" />;
}
