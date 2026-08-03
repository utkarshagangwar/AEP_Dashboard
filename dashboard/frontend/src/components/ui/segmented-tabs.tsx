"use client"

import * as React from "react"
import { Tabs as TabsPrimitive } from "@base-ui/react/tabs"

import { cn } from "@/lib/utils"

function SegmentedTabs({ className, ...props }: TabsPrimitive.Root.Props) {
  return (
    <TabsPrimitive.Root
      data-slot="segmented-tabs"
      className={cn(className)}
      {...props}
    />
  )
}

function SegmentedTabsList({ className, ...props }: TabsPrimitive.List.Props) {
  return (
    <TabsPrimitive.List
      data-slot="segmented-tabs-list"
      className={cn(
        "relative inline-flex items-center gap-1 rounded-full bg-card p-1.5 shadow-[0_0_1px_0_rgba(24,94,224,0.15),0_6px_12px_0_rgba(24,94,224,0.15)]",
        className
      )}
      {...props}
    />
  )
}

function SegmentedTabsIndicator({
  className,
  ...props
}: TabsPrimitive.Indicator.Props) {
  return (
    <TabsPrimitive.Indicator
      data-slot="segmented-tabs-indicator"
      className={cn(
        "absolute rounded-full bg-muted transition-all duration-300 ease-out motion-reduce:transition-none",
        className
      )}
      style={{
        left: "var(--active-tab-left)",
        top: "var(--active-tab-top)",
        width: "var(--active-tab-width)",
        height: "var(--active-tab-height)",
      }}
      {...props}
    />
  )
}

function SegmentedTabsTab({
  className,
  children,
  badge,
  ...props
}: TabsPrimitive.Tab.Props & { badge?: React.ReactNode }) {
  return (
    <TabsPrimitive.Tab
      data-slot="segmented-tabs-tab"
      className={cn(
        "group relative z-[1] flex h-[30px] w-[50px] items-center justify-center rounded-full text-[13px] font-medium text-muted-foreground outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring/50 aria-selected:text-foreground",
        className
      )}
      {...props}
    >
      {children}
      {badge !== undefined && (
        <span className="ml-1.5 flex size-[14px] items-center justify-center rounded-full bg-background text-[10px] text-muted-foreground transition-colors group-aria-selected:bg-primary group-aria-selected:text-primary-foreground">
          {badge}
        </span>
      )}
    </TabsPrimitive.Tab>
  )
}

function SegmentedTabsPanel({ className, ...props }: TabsPrimitive.Panel.Props) {
  return (
    <TabsPrimitive.Panel
      data-slot="segmented-tabs-panel"
      className={cn("outline-none", className)}
      {...props}
    />
  )
}

export {
  SegmentedTabs,
  SegmentedTabsList,
  SegmentedTabsIndicator,
  SegmentedTabsTab,
  SegmentedTabsPanel,
}
