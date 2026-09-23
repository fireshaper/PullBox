import * as React from "react"
import { cva, type VariantProps } from "class-variance-authority"
import { Slot } from "radix-ui"

import { cn } from "@/lib/utils"

// Restyled from the shadcn default to PullBox's small uppercase labels.
//
// Variants:
//   default   – brand-red tint: "New Series", "Subscribed", arc chips
//   secondary – grey tint: type labels (Prowlarr, qBittorrent, …)
//   outline   – border in the current text colour; set the colour with a
//               text-* class (e.g. text-status-downloaded) for toned labels
//
// Uppercase by default; add `normal-case` for sentence-case labels.
const badgeVariants = cva(
  "inline-flex w-fit shrink-0 items-center justify-center gap-1 overflow-hidden rounded border px-1.5 py-px text-[0.65rem] font-bold tracking-wide whitespace-nowrap uppercase [&>svg]:pointer-events-none [&>svg]:size-3",
  {
    variants: {
      variant: {
        default: "border-transparent bg-accent/15 text-accent",
        secondary: "border-transparent bg-muted/20 text-muted",
        outline: "border-current bg-transparent",
      },
    },
    defaultVariants: {
      variant: "default",
    },
  }
)

function Badge({
  className,
  variant = "default",
  asChild = false,
  ...props
}: React.ComponentProps<"span"> &
  VariantProps<typeof badgeVariants> & { asChild?: boolean }) {
  const Comp = asChild ? Slot.Root : "span"

  return (
    <Comp
      data-slot="badge"
      data-variant={variant}
      className={cn(badgeVariants({ variant }), className)}
      {...props}
    />
  )
}

export { Badge, badgeVariants }
