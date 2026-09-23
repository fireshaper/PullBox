import * as React from "react"
import { cva, type VariantProps } from "class-variance-authority"
import { Slot } from "radix-ui"

import { cn } from "@/lib/utils"

// Restyled from the shadcn default to PullBox's own button vocabulary. Every
// variant carries a 1px border (transparent where there's no visible one) so
// buttons of different variants line up at the same height side by side.
//
// Variants:
//   default     – solid brand red: the page's main action (Download, Add, Save)
//   outline     – surface + border, full-strength text: secondary actions
//   subtle      – transparent + border, muted text: low-emphasis row actions (Skip, Edit, Test)
//   danger      – red text + red-tinted border: destructive row actions (Delete)
//   destructive – solid red: confirming a destructive action
//   ghost       – no border until hover: icon buttons, back links
//   link        – inline text link
const buttonVariants = cva(
  "inline-flex shrink-0 cursor-pointer items-center justify-center gap-1.5 whitespace-nowrap border font-semibold transition-colors outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:pointer-events-none disabled:opacity-50 [&_svg]:pointer-events-none [&_svg]:shrink-0",
  {
    variants: {
      variant: {
        // Disabled primary goes neutral grey rather than faded red — PullBox's
        // existing convention, and clearer that the action isn't available yet.
        default:
          "border-transparent bg-accent text-white hover:bg-accent/85 disabled:bg-border disabled:text-text/70 disabled:opacity-100",
        outline: "border-border bg-surface text-text hover:bg-border",
        subtle:
          "border-border bg-transparent text-muted hover:bg-surface hover:text-text",
        danger:
          "border-status-failed/40 bg-transparent text-status-failed hover:bg-status-failed/10",
        destructive:
          "border-transparent bg-status-failed text-white hover:bg-status-failed/85",
        ghost:
          "border-transparent bg-transparent text-muted hover:bg-surface hover:text-text",
        link: "border-transparent bg-transparent text-accent underline-offset-4 hover:underline",
      },
      // Sized by padding (not fixed heights) to match the hand-rolled buttons
      // these replaced.
      size: {
        xs: "rounded px-2 py-[2px] text-[0.72rem]",
        sm: "rounded px-2.5 py-[3px] text-xs",
        default: "rounded-md px-3 py-[4px] text-[0.8rem]",
        lg: "rounded-md px-4 py-[5px] text-sm",
        xl: "rounded-md px-5 py-[7px] text-sm",
        icon: "size-8 rounded-md",
        "icon-sm": "size-6 rounded",
      },
    },
    // A link is inline text: drop the size padding (compound classes come last,
    // so this wins over the size's px/py).
    compoundVariants: [{ variant: "link", className: "p-0" }],
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  }
)

function Button({
  className,
  variant = "default",
  size = "default",
  asChild = false,
  ...props
}: React.ComponentProps<"button"> &
  VariantProps<typeof buttonVariants> & {
    asChild?: boolean
  }) {
  const Comp = asChild ? Slot.Root : "button"

  return (
    <Comp
      data-slot="button"
      data-variant={variant}
      data-size={size}
      className={cn(buttonVariants({ variant, size, className }))}
      {...props}
    />
  )
}

export { Button, buttonVariants }
