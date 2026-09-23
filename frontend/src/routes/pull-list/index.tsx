import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ChevronLeft, ChevronRight, RefreshCw } from 'lucide-react'
import { useEffect, useMemo } from 'react'
import { get } from '../../api/client'
import { Cover } from '../../components/cover'
import { FilterTab } from '../../components/filter-tab'
import { StatusText } from '../../components/status-text'
import { Badge } from '../../components/ui/badge'
import { Button } from '../../components/ui/button'
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
  comicvine_id: string | null
}

type WeeklyRelease = {
  id: number
  release_date: string
  pulled: boolean
  issue: ReleaseIssue
  series: ReleaseSeries
}

// ── ISO week utilities ────────────────────────────────────────────────────────

/** ISO week of a UTC-midnight date. Callers must pass UTC-normalised dates —
 *  reading local components here would shift a UTC Monday to the previous
 *  Sunday in any timezone west of UTC. */
function getISOWeek(utcDate: Date): string {
  const date = new Date(utcDate.getTime())
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
  const now = new Date()
  return getISOWeek(new Date(Date.UTC(now.getFullYear(), now.getMonth(), now.getDate())))
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

/** ComicVine volume pages live at /{title-slug}/4050-{id}/. The slug is the
 *  volume name lowercased with apostrophes dropped and every other run of
 *  non-alphanumerics collapsed to a hyphen ("Batman/Superman: World's Finest"
 *  → "batman-superman-worlds-finest"). Stored ids may already carry the 4050-
 *  prefix, so strip it before rebuilding.
 *
 *  Without an id there is still somewhere useful to go: Metron leaves cv_id
 *  null on many series, and the backend recovers those by search in the
 *  background, so a row can legitimately be unlinked for a while after it first
 *  appears. A search URL beats dead text in the meantime. */
function comicVineUrl(title: string, comicvineId: string | null): string {
  if (!comicvineId) {
    return `https://comicvine.gamespot.com/search/?i=volume&q=${encodeURIComponent(title)}`
  }
  const slug =
    title
      .toLowerCase()
      .replace(/['’]/g, '')
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/^-+|-+$/g, '') || 'volume'
  return `https://comicvine.gamespot.com/${slug}/4050-${comicvineId.replace(/^4050-/, '')}/`
}

// ── Data fetching ─────────────────────────────────────────────────────────────

const cachedReleasesKey = (week: string) => ['releases', 'weekly', week, 'cached'] as const

/** DB-only read: skips the provider refresh, so it answers in milliseconds. */
const fetchCachedReleases = (week: string) =>
  get<WeeklyRelease[]>(`/releases/weekly?week=${week}&cached=true`)

function formatReleaseDate(dateStr: string): string {
  return new Date(`${dateStr}T00:00:00Z`).toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
    timeZone: 'UTC',
  })
}

// ── Route definition ──────────────────────────────────────────────────────────

type PullFilter = 'all' | 'subscribed' | 'new'

const FILTER_TABS: { value: PullFilter; label: string }[] = [
  { value: 'all', label: 'All' },
  { value: 'subscribed', label: 'Subscribed' },
  { value: 'new', label: '#1 Issues' },
]

const EMPTY_MESSAGES: Record<PullFilter, string> = {
  all: 'No releases found for this week.',
  subscribed: 'No releases from subscribed series this week.',
  new: 'No #1 issues this week.',
}

export const Route = createFileRoute('/pull-list/')({
  validateSearch: (search: Record<string, unknown>) => ({
    week: typeof search.week === 'string' ? search.week : undefined,
    filter:
      search.filter === 'subscribed' || search.filter === 'new'
        ? (search.filter as PullFilter)
        : undefined,
  }),
  component: PullListPage,
})

// ── Sub-components ────────────────────────────────────────────────────────────

/** Both states land on the same series page — the label is what differs, because
 *  "Add to Pullbox" on a series you already follow reads as a broken button. */
function SeriesButton({ seriesId, subscribed }: { seriesId: number; subscribed: boolean }) {
  const navigate = useNavigate()
  return (
    <Button
      size="sm"
      variant={subscribed ? 'outline' : 'default'}
      onClick={() =>
        navigate({ to: '/series/$seriesId', params: { seriesId: String(seriesId) } })
      }
    >
      {subscribed ? 'Go to Series' : 'Add to Pullbox'}
    </Button>
  )
}

const SERIES_TITLE_STYLE: React.CSSProperties = {
  fontWeight: 600,
  fontSize: '0.875rem',
  color: 'var(--color-text)',
  overflow: 'hidden',
  textOverflow: 'ellipsis',
  whiteSpace: 'nowrap',
}

/** Issue numbers arrive as strings ("1", "01", "1.0"), so compare numerically. */
function isFirstIssue(issueNumber: string): boolean {
  return Number(issueNumber) === 1
}

function NewSeriesBadge() {
  return <Badge>New Series</Badge>
}

function NavButton({
  onClick,
  children,
}: {
  onClick: () => void
  children: React.ReactNode
}) {
  return (
    <Button variant="outline" size="icon" onClick={onClick}>
      {children}
    </Button>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────

function PullListPage() {
  const navigate = useNavigate({ from: Route.fullPath })
  const { week: weekParam, filter: filterParam } = Route.useSearch()
  const week = weekParam ?? getCurrentWeek()
  const filter: PullFilter = filterParam ?? 'all'

  // 'all' is the default and stays out of the URL so plain /pull-list links keep working.
  const setSearch = (next: { week?: string; filter?: PullFilter }) =>
    navigate({
      search: {
        week: next.week ?? week,
        filter: (next.filter ?? filter) === 'all' ? undefined : (next.filter ?? filter),
      },
    })

  const queryClient = useQueryClient()

  // Two reads per week. The cached one is a plain DB read and renders at once;
  // the live one refreshes from the metadata provider (seconds of HTTP) and
  // replaces it when it lands, so the page never sits on a skeleton waiting for
  // Metron/ComicVine.
  const cached = useQuery<WeeklyRelease[]>({
    queryKey: cachedReleasesKey(week),
    queryFn: () => fetchCachedReleases(week),
  })

  const live = useQuery<WeeklyRelease[]>({
    queryKey: ['releases', 'weekly', week],
    queryFn: async () => {
      const data = await get<WeeklyRelease[]>(`/releases/weekly?week=${week}`)
      // Keep the cached copy current so the next visit opens on this data.
      queryClient.setQueryData(cachedReleasesKey(week), data)
      return data
    },
    staleTime: 0,
  })

  // Warm the neighbouring weeks' cached reads so the arrows switch instantly.
  // Only the cheap DB read — prefetching live would triple provider calls.
  useEffect(() => {
    for (const delta of [-1, 1]) {
      const w = offsetWeek(week, delta)
      queryClient.prefetchQuery({
        queryKey: cachedReleasesKey(w),
        queryFn: () => fetchCachedReleases(w),
      })
    }
  }, [week, queryClient])

  const releases = live.data ?? cached.data
  const refreshing = live.isFetching
  // An unseen week has no DB rows yet, so an empty cached read means "not known
  // yet" rather than "no releases" until the live refresh has answered.
  const isLoading = !releases || (releases.length === 0 && refreshing)

  // Filtering is client-side: the week is already loaded and the three views
  // are just different slices of the same list.
  const visible = useMemo(() => {
    if (!releases) return undefined
    if (filter === 'subscribed') return releases.filter((r) => r.series.subscribed)
    if (filter === 'new') return releases.filter((r) => isFirstIssue(r.issue.issue_number))
    return releases
  }, [releases, filter])

  const grouped = useMemo(() => {
    if (!visible) return {}
    const groups: Record<string, WeeklyRelease[]> = {}
    for (const r of visible) {
      const pub = r.series.publisher ?? 'Unknown Publisher'
      if (!groups[pub]) groups[pub] = []
      groups[pub].push(r)
    }
    return groups
  }, [visible])

  const sortedPublishers = useMemo(
    () => Object.keys(grouped).sort((a, b) => a.localeCompare(b)),
    [grouped],
  )

  return (
    <div className="p-6">
      {/* Week navigation header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '24px' }}>
        <NavButton onClick={() => setSearch({ week: offsetWeek(week, -1) })}>
          <ChevronLeft size={16} />
        </NavButton>

        <h1
          className="text-2xl font-bold"
          style={{ color: 'var(--color-text)', margin: 0, flex: 1, textAlign: 'center' }}
        >
          {formatWeekLabel(week)}
        </h1>

        <NavButton onClick={() => setSearch({ week: offsetWeek(week, 1) })}>
          <ChevronRight size={16} />
        </NavButton>
      </div>

      {/* Filter tabs */}
      <div
        style={{
          position: 'relative',
          display: 'flex',
          justifyContent: 'center',
          gap: '6px',
          marginBottom: '24px',
        }}
      >
        {FILTER_TABS.map((tab) => (
          <FilterTab
            key={tab.value}
            active={filter === tab.value}
            onClick={() => setSearch({ filter: tab.value })}
          >
            {tab.label}
          </FilterTab>
        ))}

        {/* Live-refresh indicator. Absolutely placed so it never shifts the list,
            and hidden while the skeleton already says "loading". */}
        {refreshing && !isLoading && (
          <span
            style={{
              position: 'absolute',
              right: 0,
              top: '50%',
              transform: 'translateY(-50%)',
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
              fontSize: '0.72rem',
              color: 'var(--color-muted)',
            }}
          >
            <RefreshCw size={12} className="animate-spin" />
            Checking for updates…
          </span>
        )}
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
      {!isLoading && visible && visible.length === 0 && (
        <div
          style={{
            textAlign: 'center',
            padding: '64px 0',
            color: 'var(--color-muted)',
            fontSize: '0.95rem',
          }}
        >
          {EMPTY_MESSAGES[filter]}
        </div>
      )}

      {/* Releases grouped by publisher */}
      {!isLoading && visible && visible.length > 0 && (
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
                    <Cover url={release.issue.cover_url} alt={release.series.title} width={50} />


                    {/* Issue details */}
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div
                        style={{
                          display: 'flex',
                          alignItems: 'center',
                          gap: '8px',
                          minWidth: 0,
                        }}
                      >
                        <a
                          href={comicVineUrl(release.series.title, release.series.comicvine_id)}
                          target="_blank"
                          rel="noreferrer"
                          title={
                            release.series.comicvine_id
                              ? 'Open on ComicVine'
                              : 'Search ComicVine for this series'
                          }
                          className="no-underline hover:underline"
                          style={SERIES_TITLE_STYLE}
                        >
                          {release.series.title}
                        </a>
                        {isFirstIssue(release.issue.issue_number) && <NewSeriesBadge />}
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
                      <StatusText status={release.issue.status} />
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
