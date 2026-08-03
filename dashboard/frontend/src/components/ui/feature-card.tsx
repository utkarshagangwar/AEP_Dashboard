"use client"

import * as React from "react"
import { Plus } from "lucide-react"

import { cn } from "@/lib/utils"

interface FeatureCardProps extends React.ComponentProps<"div"> {
  badge?: string
  title: string
  description: string
  price?: string
  media?: React.ReactNode
  actionLabel?: string
  onAction?: () => void
}

function FeatureCard({
  className,
  badge,
  title,
  description,
  price,
  media,
  actionLabel = "View",
  onAction,
  ...props
}: FeatureCardProps) {
  return (
    <div
      data-slot="feature-card"
      className={cn(
        "group/card relative w-[190px] overflow-hidden rounded-2xl bg-white p-5 shadow-sm ring-1 ring-black/10 transition-all duration-500 [transition-timing-function:cubic-bezier(0.16,1,0.3,1)] hover:-translate-y-2.5 hover:shadow-lg motion-reduce:transition-none motion-reduce:hover:translate-y-0",
        className
      )}
      {...props}
    >
      <span
        aria-hidden="true"
        className="pointer-events-none absolute inset-0 opacity-0 transition-opacity duration-300 group-hover/card:opacity-100 motion-reduce:transition-none"
        style={{
          background:
            "linear-gradient(120deg, transparent 40%, rgba(255,255,255,0.8) 50%, transparent 60%)",
        }}
      />

      {badge && (
        <span className="absolute top-3 right-3 z-10 scale-75 rounded-full bg-emerald-500 px-2 py-0.5 text-[11px] font-semibold text-white opacity-0 transition-all delay-100 duration-300 group-hover/card:scale-100 group-hover/card:opacity-100 motion-reduce:transition-none motion-reduce:delay-0">
          {badge}
        </span>
      )}

      <div className="relative flex flex-col gap-3">
        <div className="h-[100px] w-full overflow-hidden rounded-xl bg-gradient-to-br from-primary/70 to-primary transition-transform duration-500 [transition-timing-function:cubic-bezier(0.16,1,0.3,1)] group-hover/card:-translate-y-1 group-hover/card:scale-[1.03] motion-reduce:transition-none motion-reduce:group-hover/card:translate-y-0 motion-reduce:group-hover/card:scale-100">
          {media}
        </div>

        <div className="flex flex-col gap-1">
          <p className="m-0 text-[1.05em] font-bold text-gray-900 transition-colors duration-300 group-hover/card:text-primary">
            {title}
          </p>
          <p className="m-0 text-xs text-gray-900/70">{description}</p>
        </div>

        <div className="mt-auto flex items-center justify-between">
          {price ? (
            <span className="text-sm font-bold text-gray-900 transition-colors duration-300 group-hover/card:text-primary">
              {price}
            </span>
          ) : (
            <span />
          )}
          <button
            type="button"
            onClick={onAction}
            aria-label={actionLabel}
            className="ml-auto flex size-7 scale-90 items-center justify-center rounded-full bg-primary text-primary-foreground transition-all duration-300 outline-none group-hover/card:scale-100 group-hover/card:ring-4 group-hover/card:ring-primary/20 motion-reduce:transition-none focus-visible:scale-100 focus-visible:ring-3 focus-visible:ring-ring/50"
          >
            <Plus className="size-4" />
          </button>
        </div>
      </div>
    </div>
  )
}

export { FeatureCard }
