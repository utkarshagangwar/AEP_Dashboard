"use client";

// The app's replacement for window.confirm() / window.prompt()-as-a-yes/no.
// A promise-based bridge to ConfirmDialogHost (components/ui/confirm-dialog.tsx),
// which is mounted once in Providers.jsx -- the same "imperative call, one
// shared renderer" shape as toastSuccess()/toastError() in lib/toast.tsx.
//
// Deliberately not a hook (no useConfirm()): several call sites are plain
// async handlers, not always inside a component that wants the hook-rules
// overhead just to ask one yes/no question. A module-level listener plays
// the same role sonner's own internal store plays for toast().

export type ConfirmTone = "danger" | "neutral";

export interface ConfirmOptions {
  /** Short question, e.g. "Delete this test run?" */
  title: string;
  /** One or two sentences of consequence/detail. Optional -- some prompts
   * (e.g. a short "Continue?") are fully carried by the title. */
  body?: string;
  /** "danger" (red confirm button, alert-triangle icon) for anything
   * destructive/irreversible. "neutral" (ink confirm button, question-mark
   * icon) for a pause that isn't data loss -- e.g. "run 40 skills now?",
   * "regenerate and lose hand edits?". Defaults to "danger" since most
   * existing call sites are deletes. */
  tone?: ConfirmTone;
  confirmLabel?: string;
  cancelLabel?: string;
}

export interface ConfirmRequest extends ConfirmOptions {
  resolve: (value: boolean) => void;
}

type Listener = (request: ConfirmRequest | null) => void;

let listener: Listener | null = null;

/** Wired up by ConfirmDialogHost on mount/unmount. Internal -- call sites
 * never import this, only confirmDialog(). */
export function _registerConfirmListener(fn: Listener | null) {
  listener = fn;
}

/**
 * Shows the platform's confirm dialog and resolves the same way
 * window.confirm() did: true if the user confirmed, false if they cancelled,
 * dismissed the backdrop, or pressed Esc.
 *
 * Falls back to the real window.confirm() if ConfirmDialogHost somehow
 * hasn't mounted yet -- should never happen once Providers.jsx renders it,
 * but a silent no-op (always false) would be a worse failure than a native
 * dialog, since a caller would read that as "user cancelled" and quietly
 * skip a delete the user actually wanted.
 */
export function confirmDialog(options: ConfirmOptions): Promise<boolean> {
  return new Promise((resolve) => {
    if (!listener) {
      const message = options.body ? `${options.title}\n\n${options.body}` : options.title;
      resolve(typeof window !== "undefined" ? window.confirm(message) : false);
      return;
    }
    listener({ ...options, resolve });
  });
}
