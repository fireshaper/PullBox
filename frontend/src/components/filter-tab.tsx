import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

/** One pill in a row of view filters (Pull List "All / Subscribed / #1 Issues",
 *  Story Arcs "All / Subscribed"). The active pill gets the brand tint. */
export function FilterTab({
  active,
  onClick,
  children,
}: {
  active: boolean
  onClick: () => void
  children: React.ReactNode
}) {
  return (
    <Button
      variant="subtle"
      aria-pressed={active}
      onClick={onClick}
      className={cn(active && 'bg-accent/15 text-text hover:bg-accent/15')}
    >
      {children}
    </Button>
  )
}
