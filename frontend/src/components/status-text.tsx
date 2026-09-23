import { cn } from '@/lib/utils'
import { statusColor } from '@/lib/status'

/** An issue or job status word in its status colour ("Wanted", "Downloading", …).
 *  Pass `className` for layout (width, alignment) or to change the size. */
export function StatusText({
  status,
  children,
  className,
}: {
  status: string | null | undefined
  /** Label to show instead of the status word itself. */
  children?: React.ReactNode
  className?: string
}) {
  return (
    <span
      className={cn('shrink-0 text-[0.7rem] font-semibold whitespace-nowrap capitalize', className)}
      style={{ color: statusColor(status) }}
    >
      {children ?? status}
    </span>
  )
}
