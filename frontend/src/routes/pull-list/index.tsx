import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useQuery } from '@tanstack/react-query'
import { ChevronLeft, ChevronRight } from 'lucide-react'
import { useMemo } from 'react'
import { get } from '../../api/client'
import { Skeleton } from '../../components/ui/skeleton'

// ── Types ─────────────────────────────────────────────────────────────────────

type ReleaseIssue = {
  id: number
  issue_number: string
  title: string | null
  cover_url: string | null
  status: string
}

type ReleaseSeries = {
  id: number
  title: string
  publisher: string | null
  subscribed: boolean
}

type WeeklyRelease = {
  id: number
  release_date: string
  pulled: boolean
  issue: ReleaseIssue
  series: ReleaseSeries
}

// ── ISO week utilities ────────────────────────────────────────────────────────

function getISOWeek(d: Date): string {
  const date = new Date(Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()))
  const day = date.getUTCDay() || 7
  date.setUTCDate(date.getUTCDate() + 4 - day)
  const yearStart = new Date(Date.UTC(date.getUTCFullYear(), 0, 1))
  const week = Math.ceil((((date.getTime() - yearStart.getTime()) / 86400000) + 1) / 7)
  return `${date.getUTCFullYear()}-${String(week).padStart(2, '0')}`
}

function getWeekMonday(weekStr: string): Date {
  const [year, week] = weekStr.split('-').map(Number)
  const jan4 = new Date(Date.UTC(year, 0, 4))
  const day = jan4.getUTCDay() || 7
  const week1Monday = new Date(jan4)
  week1Monday.setUTCDate(jan4.getUTCDate() - day + 1)
  const monday = new Date(week1Monday)
  monday.setUTCDate(week1Monday.getUTCDate() + (week - 1) * 7)
  return monday
}

function getCurrentWeek(): string {
  return getISOWeek(new Date())
}

function offsetWeek(weekStr: string, delta: number): string {
  const monday = getWeekMonday(weekStr)
  monday.setUTCDate(monday.getUTCDate() + delta * 7)
  return getISOWeek(monday)
}

function formatWeekLabel(weekStr: string): string {
  const monday = getWeekMonday(weekStr)
  return `Week of ${monday.toLocaleDateString('en-US', {
    month: 'long',
    day: 'numeric',
    year: 'numeric',
    timeZone: 'UTC',
  })}`
}

function formatReleaseDate(dateStr: string): string {
  return new Date(`${dateStr}T00:00:00Z`).toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
    timeZone: 'UTC',
  })
}

// ── Route definition ──────────────────────────────────────────────────────────

export const Route = createFileRoute('/pull-list/')({
  validateSearch: (search: Record<string, unknown>) => ({
    week: typeof search.week === 'string' ? search.week : undefined,
  }),
  component: PullListPage,
})

// ── Sub-components ────────────────────────────────────────────────────────────

const STATUS_COLORS: Record<string, string> = {
  wanted: 'var(--color-status-wanted)',
  downloading: 'var(--color-status-downloading)',
  downloaded: 'var(--color-status-downloaded)',
  skipped: 'var(--color-status-skipped)',
  failed: 'var(--color-status-failed)',
  unknown: 'var(--color-muted)',
}

/** Both states land on the same series page — the label is what differs, because
 *  "Add to Pullbox" on a series you already follow reads as a broken button. */
function SeriesButton({ seriesId, subscribed }: { seriesId: number; subscribed: boolean }) {
  const navigate = useNavigate()
  return (
    <button
      onClick={() =>
        navigate({ to: '/series/$seriesId', params: { seriesId: String(seriesId) } })
      }
      style={{
        fontSize: '0.75rem',
        padding: '4px 10px',
        borderRadius: '4px',
        background: subscribed ? 'var(--color-surface)' : 'var(--color-accent)',
        color: subscribed ? 'var(--color-text)' : '#fff',
        border: subscribed ? '1px solid var(--color-border)' : 'none',
        cursor: 'pointer',
        whiteSpace: 'nowrap',
      }}
    >
      {subscribed ? 'Go to Series' : 'Add to Pullbox'}
    </button>
  )
}

function NavButton({
  onClick,
  children,
}: {
  onClick: () => void
  children: React.ReactNode
}) {
  return (
    <button
      onClick={onClick}
      style={{
        display: 'flex',
        alignItems: 'center',
        padding: '6px 10px',
        borderRadius: '6px',
        background: 'var(--color-surface)',
        border: '1px solid var(--color-border)',
        color: 'var(--color-text)',
        cursor: 'pointer',
      }}
    >
      {children}
    </button>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────

function PullListPage() {
  const navigate = useNavigate({ from: Route.fullPath })
  const { week: weekParam } = Route.useSearch()
  const week = weekParam ?? getCurrentWeek()

  const { data: releases, isLoading } = useQuery<WeeklyRelease[]>({
    queryKey: ['releases', 'weekly', week],
    queryFn: () => get<WeeklyRelease[]>(`/releases/weekly?week=${week}`),
    staleTime: 0,
  })

  const grouped = useMemo(() => {
    if (!releases) return {}
    const groups: Record<string, WeeklyRelease[]> = {}
    for (const r of releases) {
      const pub = r.series.publisher ?? 'Unknown Publisher'
      if (!groups[pub]) groups[pub] = []
      groups[pub].push(r)
    }
    return groups
  }, [releases])

  const sortedPublishers = useMemo(
    () => Object.keys(grouped).sort((a, b) => a.localeCompare(b)),
    [grouped],
  )

  return (
    <div className="p-6">
      {/* Week navigation header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '24px' }}>
        <NavButton onClick={() => navigate({ search: { week: offsetWeek(week, -1) } })}>
          <ChevronLeft size={16} />
        </NavButton>

        <h1
          className="text-2xl font-bold"
          style={{ color: 'var(--color-text)', margin: 0, flex: 1, textAlign: 'center' }}
        >
          {formatWeekLabel(week)}
        </h1>

        <NavButton onClick={() => navigate({ search: { week: offsetWeek(week, 1) } })}>
          <ChevronRight size={16} />
        </NavButton>
      </div>

      {/* Loading skeleton */}
      {isLoading && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '24px' }}>
          {[1, 2, 3].map((i) => (
            <div key={i}>
              <Skeleton style={{ height: '16px', width: '120px', marginBottom: '8px' }} />
              <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                <Skeleton style={{ height: '90px', width: '100%' }} />
                <Skeleton style={{ height: '90px', width: '100%' }} />
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Empty state */}
      {!isLoading && releases && releases.length === 0 && (
        <div
          style={{
            textAlign: 'center',
            padding: '64px 0',
            color: 'var(--color-muted)',
            fontSize: '0.95rem',
          }}
        >
          No releases found for this week.
        </div>
      )}

      {/* Releases grouped by publisher */}
      {!isLoading && releases && releases.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '32px' }}>
          {sortedPublishers.map((publisher) => (
            <div key={publisher}>
              <h2
                style={{
                  fontSize: '0.75rem',
                  fontWeight: 600,
                  textTransform: 'uppercase',
                  letterSpacing: '0.08em',
                  color: 'var(--color-muted)',
                  borderBottom: '1px solid var(--color-border)',
                  paddingBottom: '6px',
                  marginBottom: '10px',
                }}
              >
                {publisher}
              </h2>

              <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                {grouped[publisher].map((release) => (
                  <div
                    key={release.id}
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: '14px',
                      background: 'var(--color-surface)',
                      border: '1px solid var(--color-border)',
                      borderRadius: '8px',
                      padding: '10px 14px',
                    }}
                  >
                    {/* Cover thumbnail */}
                    {release.issue.cover_url ? (
                      <img
                        src={release.issue.cover_url}
                        alt={release.series.title}
                        style={{
                          width: 50,
                          height: 70,
                          objectFit: 'cover',
                          borderRadius: '4px',
                          flexShrink: 0,
                        }}
                      />
                    ) : (
                      <div
                        style={{
                          width: 50,
                          height: 70,
                          borderRadius: '4px',
                          background: 'var(--color-border)',
                          flexShrink: 0,
                        }}
                      />
                    )}

                    {/* Issue details */}
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div
                        style={{
                          fontWeight: 600,
                          fontSize: '0.875rem',
                          color: 'var(--color-text)',
                          overflow: 'hidden',
                          textOverflow: 'ellipsis',
                          whiteSpace: 'nowrap',
                        }}
                      >
                        {release.series.title}
                      </div>
                      <div
                        style={{
                          fontSize: '0.75rem',
                          color: 'var(--color-muted)',
                          marginTop: '2px',
                        }}
                      >
                        Issue #{release.issue.issue_number}
                        {release.issue.title ? ` — ${release.issue.title}` : ''}
                      </div>
                      <div
                        style={{
                          fontSize: '0.75rem',
                          color: 'var(--color-muted)',
                          marginTop: '2px',
                        }}
                      >
                        {formatReleaseDate(release.release_date)}
                      </div>
                    </div>

                    {/* Status badge + action */}
                    <div
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: '10px',
                        flexShrink: 0,
                      }}
                    >
                      <span
                        style={{
                          fontSize: '0.7rem',
                          fontWeight: 600,
                          textTransform: 'capitalize',
                          color: STATUS_COLORS[release.issue.status] ?? 'var(--color-muted)',
                        }}
                      >
                        {release.issue.status}
                      </span>
                      <SeriesButton
                        seriesId={release.series.id}
                        subscribed={release.series.subscribed}
                      />
                    </div>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
