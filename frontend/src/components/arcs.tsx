import { Layers } from 'lucide-react'
import { Cover } from './cover'
import { Badge } from './ui/badge'

/** A story arc as the /api/arcs list returns it. `total` is the arc's true size
 *  from the metadata provider and is null until the arc's member list has been
 *  fetched at least once — treat it as "unknown", never as zero. */
export type ArcListItem = {
  id: number
  metron_id: string | null
  comicvine_id: string | null
  name: string
  publisher: string | null
  cover_url: string | null
  subscribed: boolean
  auto_download: boolean
  total: number | null
  owned: number
  downloaded: number
  wanted: number
  series_count: number
  detail_synced_at: string | null
}

/** How much of an arc the library holds. With no known total there is no honest
 *  denominator, so the bar falls back to progress against what's tracked. */
export function ArcProgress({ arc }: { arc: ArcListItem }) {
  const denominator = arc.total ?? arc.owned
  const pct = denominator > 0 ? Math.min(100, Math.round((arc.downloaded / denominator) * 100)) : 0
  return (
    <div style={{ minWidth: 0 }}>
      <div
        style={{
          height: 5,
          borderRadius: 3,
          background: 'var(--color-border)',
          overflow: 'hidden',
          marginBottom: 5,
        }}
      >
        <div
          style={{
            height: '100%',
            width: `${pct}%`,
            background: 'var(--color-status-downloaded)',
            transition: 'width 200ms',
          }}
        />
      </div>
      <div style={{ fontSize: '0.72rem', color: 'var(--color-muted)' }}>
        {arc.total != null
          ? `${arc.downloaded} of ${arc.total} downloaded`
          : `${arc.downloaded} of ${arc.owned} tracked downloaded`}
        {arc.wanted > 0 && ` · ${arc.wanted} wanted`}
      </div>
    </div>
  )
}

export function ArcCover({
  url,
  name,
  size = 56,
}: {
  url: string | null
  name: string
  size?: number
}) {
  return (
    <Cover
      url={url}
      alt={name}
      width={size}
      fallback={<Layers size={Math.round(size / 3)} style={{ color: 'var(--color-muted)' }} />}
    />
  )
}

export function SubscribedBadge() {
  return <Badge className="text-[0.68rem] normal-case tracking-normal">Subscribed</Badge>
}
