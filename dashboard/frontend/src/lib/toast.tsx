"use client"

import { toast } from "sonner"

import { SuccessCheck } from "@/components/ui/success-check"
import { ErrorToast } from "@/components/ui/error-state"

/**
 * The app's success notification.
 *
 * `toast.custom` rather than `toast.success` so the animated checkmark badge
 * replaces sonner's static tick icon. Everything else — stacking, dismissal,
 * timers — is still sonner's.
 *
 * `role="status"` + `aria-live="polite"` announces the message without
 * stealing focus from whatever the user is doing; the badge itself is
 * decorative and marked aria-hidden inside SuccessCheck.
 */
export function toastSuccess(message: string) {
  toast.custom(
    () => (
      <div
        role="status"
        aria-live="polite"
        className="flex w-full items-center gap-3 rounded-lg border border-gray-200 bg-white px-4 py-3 shadow-lg"
      >
        <SuccessCheck size={28} label="" className="shrink-0" />
        <p className="m-0 text-sm font-medium text-gray-900">{message}</p>
      </div>
    ),
    { duration: 3000 }
  )
}

/**
 * Error counterpart, so failures don't fall back to sonner's default styling
 * and read as a different design language from the success case.
 */
export function toastError(message: string, title = "Action failed") {
  toast.custom(
    (toastId) => <ErrorToast title={title} message={message} onDismiss={() => toast.dismiss(toastId)} />,
    { duration: 5000 }
  )
}
