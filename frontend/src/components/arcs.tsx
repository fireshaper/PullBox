import { Layers } from 'lucide-react'

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
  const height = Math.round(size * 1.4)
  return url ? (
    <img
      src={url}
      alt={name}
      style={{ width: size, height, objectFit: 'cover', borderRadius: 4, flexShrink: 0 }}
    />
  ) : (
    <div
      style={{
        width: size,
        height,
        borderRadius: 4,
        background: 'var(--color-border)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        flexShrink: 0,
      }}
    >
      <Layers size={Math.round(size / 3)} style={{ color: 'var(--color-muted)' }} />
    </div>
  )
}

export function SubscribedBadge() {
  return (
    <span
      style={{
        fontSize: '0.68rem',
        fontWeight: 700,
        padding: '2px 7px',
        borderRadius: 4,
        color: 'var(--color-accent)',
        background: 'color-mix(in srgb, var(--color-accent) 15%, transparent)',
        flexShrink: 0,
      }}
    >
      Subscribed
    </span>
  )
}
