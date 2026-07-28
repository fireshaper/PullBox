import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useQuery } from '@tanstack/react-query'
import { Search } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { get } from '../../api/client'
import { ArcCover, ArcProgress, SubscribedBadge, type ArcListItem } from '../../components/arcs'
import { Skeleton } from '../../components/ui/skeleton'

// ── Route ─────────────────────────────────────────────────────────────────────

type ArcsSearch = { filter?: 'all' | 'subscribed' }

export const Route = createFileRoute('/arcs/')({
  validateSearch: (search: Record<string, unknown>): ArcsSearch => ({
    filter: search.filter === 'subscribed' ? 'subscribed' : 'all',
  }),
  component: ArcsPage,
})

// ── Page ──────────────────────────────────────────────────────────────────────

function ArcsPage() {
  const navigate = useNavigate()
  const { filter } = Route.useSearch()
  const [query, setQuery] = useState('')
  const [debounced, setDebounced] = useState('')

  useEffect(() => {
    const t = setTimeout(() => setDebounced(query.trim()), 300)
    return () => clearTimeout(t)
  }, [query])

  const { data: arcs, isLoading } = useQuery<ArcListItem[]>({
    queryKey: ['arcs', filter],
    queryFn: () => get<ArcListItem[]>(filter === 'subscribed' ? '/arcs?subscribed=true' : '/arcs'),
  })

  // Filtering is client-side: the whole list is already loaded and the backend
  // filter would cost a round trip per keystroke.
  const visible = useMemo(() => {
    if (!arcs) return []
    if (!debounced) return arcs
    const needle = debounced.toLowerCase()
    return arcs.filter(
      (a) =>
        a.name.toLowerCase().includes(needle) ||
        (a.publisher ?? '').toLowerCase().includes(needle),
    )
  }, [arcs, debounced])

  const tabStyle = (active: boolean): React.CSSProperties => ({
    padding: '5px 12px',
    borderRadius: 6,
    fontSize: '0.8rem',
    fontWeight: 600,
    cursor: 'pointer',
    border: '1px solid var(--color-border)',
    background: active ? 'color-mix(in srgb, var(--color-accent) 15%, transparent)' : 'transparent',
    color: active ? 'var(--color-text)' : 'var(--color-muted)',
  })

  return (
    <div className="p-6">
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          marginBottom: 20,
          gap: 12,
        }}
      >
        <h1 style={{ fontSize: '1.5rem', fontWeight: 700, color: 'var(--color-text)' }}>
          Story Arcs
        </h1>
        <div style={{ display: 'flex', gap: 6 }}>
          <button
            style={tabStyle(filter !== 'subscribed')}
            onClick={() => navigate({ to: '/arcs', search: { filter: 'all' } })}
          >
            All
          </button>
          <button
            style={tabStyle(filter === 'subscribed')}
            onClick={() => navigate({ to: '/arcs', search: { filter: 'subscribed' } })}
          >
            Subscribed
          </button>
        </div>
      </div>

      <div style={{ position: 'relative', marginBottom: 20 }}>
        <Search
          size={15}
          style={{
            position: 'absolute',
            left: 12,
            top: '50%',
            transform: 'translateY(-50%)',
            color: 'var(--color-muted)',
            pointerEvents: 'none',
          }}
        />
        <input
          type="text"
          placeholder="Filter arcs by name or publisher…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          style={{
            width: '100%',
            padding: '8px 12px 8px 36px',
            background: 'var(--color-surface)',
            border: '1px solid var(--color-border)',
            borderRadius: 6,
            color: 'var(--color-text)',
            fontSize: '0.875rem',
            outline: 'none',
            boxSizing: 'border-box',
          }}
        />
      </div>

      {isLoading && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          {[1, 2, 3, 4].map((i) => (
            <Skeleton key={i} style={{ height: 90, width: '100%', borderRadius: 6 }} />
          ))}
        </div>
      )}

      {!isLoading && visible.length === 0 && (
        <div
          style={{
            textAlign: 'center',
            padding: '48px 16px',
            color: 'var(--color-muted)',
            fontSize: '0.875rem',
            lineHeight: 1.6,
          }}
        >
          {debounced ? (
            <>No arcs match “{debounced}”.</>
          ) : filter === 'subscribed' ? (
            <>
              No subscribed arcs yet.
              <br />
              Open an arc and subscribe to have PullBox fill in the issues you’re missing.
            </>
          ) : (
            <>
              No story arcs yet.
              <br />
              Arcs are discovered when a series is synced — sync a series and its issues’ arcs
              will show up here.
            </>
          )}
        </div>
      )}

      <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
        {visible.map((arc) => (
          <div
            key={arc.id}
            role="button"
            tabIndex={0}
            onClick={() => navigate({ to: '/arcs/$arcId', params: { arcId: String(arc.id) } })}
            onKeyDown={(e) => {
              if (e.key === 'Enter')
                navigate({ to: '/arcs/$arcId', params: { arcId: String(arc.id) } })
            }}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 14,
              padding: '10px 14px',
              background: 'var(--color-surface)',
              border: '1px solid var(--color-border)',
              borderRadius: 6,
              cursor: 'pointer',
            }}
          >
            <ArcCover url={arc.cover_url} name={arc.name} />
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 3 }}>
                <span
                  style={{
                    fontWeight: 600,
                    fontSize: '0.9rem',
                    color: 'var(--color-text)',
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    whiteSpace: 'nowrap',
                  }}
                >
                  {arc.name}
                </span>
                {arc.subscribed && <SubscribedBadge />}
              </div>
              <div style={{ fontSize: '0.75rem', color: 'var(--color-muted)', marginBottom: 6 }}>
                {[
                  arc.publisher,
                  `${arc.owned} issue${arc.owned === 1 ? '' : 's'} tracked`,
                  arc.series_count > 0 &&
                    `${arc.series_count} series`,
                ]
                  .filter(Boolean)
                  .join(' · ')}
              </div>
              <ArcProgress arc={arc} />
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
