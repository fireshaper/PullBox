import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ChevronLeft, ChevronRight, Download, RefreshCw } from 'lucide-react'
import { useMemo, useState } from 'react'
import { get, post } from '../../api/client'
import { Skeleton } from '../../components/ui/skeleton'

// ── Types ─────────────────────────────────────────────────────────────────────

type CalendarEntry = {
  issue_id: number
  issue_number: string
  title: string | null
  cover_url: string | null
  status: string
  job_status: string | null
  release_date: string
  date_source: string
  series_id: number
  series_title: string
  publisher: string | null
  subscribed: boolean
  auto_download: boolean
  sources: string[]
}

type CalendarResponse = {
  start: string
  end: string
  scope: string
  entries: CalendarEntry[]
  summary: {
    total: number
    pending: number
    by_status: Record<string, number>
  }
}

type View = 'month' | 'agenda'
type Scope = 'subscribed' | 'all'

// ── Date helpers ──────────────────────────────────────────────────────────────
// All arithmetic is in UTC so a local timezone can never shift a release onto
// the wrong day of the grid.

const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

function currentMonth(): string {
  const now = new Date()
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`
}

function parseMonth(monthStr: string): { year: number; month: number } {
  const [year, month] = monthStr.split('-').map(Number)
  return { year, month }
}

function offsetMonth(monthStr: string, delta: number): string {
  const { year, month } = parseMonth(monthStr)
  const d = new Date(Date.UTC(year, month - 1 + delta, 1))
  return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, '0')}`
}

function isoDay(d: Date): string {
  return d.toISOString().slice(0, 10)
}

/** The Monday-to-Sunday grid that fully contains the given month. */
function monthGrid(monthStr: string): { days: Date[]; start: string; end: string } {
  const { year, month } = parseMonth(monthStr)
  const first = new Date(Date.UTC(year, month - 1, 1))
  const last = new Date(Date.UTC(year, month, 0))

  const gridStart = new Date(first)
  gridStart.setUTCDate(first.getUTCDate() - ((first.getUTCDay() || 7) - 1))

  const gridEnd = new Date(last)
  gridEnd.setUTCDate(last.getUTCDate() + (7 - (last.getUTCDay() || 7)))

  const days: Date[] = []
  for (let d = new Date(gridStart); d <= gridEnd; d.setUTCDate(d.getUTCDate() + 1)) {
    days.push(new Date(d))
  }
  return { days, start: isoDay(gridStart), end: isoDay(gridEnd) }
}

function formatMonthLabel(monthStr: string): string {
  const { year, month } = parseMonth(monthStr)
  return new Date(Date.UTC(year, month - 1, 1)).toLocaleDateString('en-US', {
    month: 'long',
    year: 'numeric',
    timeZone: 'UTC',
  })
}

function formatDayLabel(dayStr: string): string {
  return new Date(`${dayStr}T00:00:00Z`).toLocaleDateString('en-US', {
    weekday: 'long',
    month: 'short',
    day: 'numeric',
    timeZone: 'UTC',
  })
}

function todayIso(): string {
  const now = new Date()
  return isoDay(new Date(Date.UTC(now.getFullYear(), now.getMonth(), now.getDate())))
}

// ── Status presentation ───────────────────────────────────────────────────────

/** A failing download job is the one thing Issue.status cannot express, so it
 *  takes priority over the issue's own state when picking a colour. */
function entryColor(entry: CalendarEntry): string {
  if (entry.job_status === 'failed') return 'var(--color-status-failed)'
  switch (entry.status) {
    case 'downloaded':
      return 'var(--color-status-downloaded)'
    case 'downloading':
      return 'var(--color-status-downloading)'
    case 'wanted':
      return 'var(--color-status-wanted)'
    case 'skipped':
      return 'var(--color-status-skipped)'
    default:
      return 'var(--color-muted)'
  }
}

function entryLabel(entry: CalendarEntry): string {
  if (entry.job_status === 'failed') return 'download failed'
  if (entry.status === 'unknown') return entry.auto_download ? 'will auto-download' : 'not queued'
  return entry.status
}

const LEGEND = [
  { color: 'var(--color-status-downloaded)', label: 'Downloaded' },
  { color: 'var(--color-status-downloading)', label: 'Downloading' },
  { color: 'var(--color-status-wanted)', label: 'Wanted' },
  { color: 'var(--color-status-failed)', label: 'Failed' },
  { color: 'var(--color-muted)', label: 'Not queued' },
]

const GRABBABLE_EXCLUDED = ['downloaded', 'downloading', 'skipped']

// ── Route definition ──────────────────────────────────────────────────────────

export const Route = createFileRoute('/calendar/')({
  validateSearch: (search: Record<string, unknown>) => ({
    month: typeof search.month === 'string' ? search.month : undefined,
    view: search.view === 'agenda' ? ('agenda' as View) : undefined,
    scope: search.scope === 'all' ? ('all' as Scope) : undefined,
  }),
  component: CalendarPage,
})

// ── Shared bits ───────────────────────────────────────────────────────────────

function NavButton({
  onClick,
  children,
  title,
}: {
  onClick: () => void
  children: React.ReactNode
  title?: string
}) {
  return (
    <button
      onClick={onClick}
      title={title}
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: '6px',
        padding: '6px 10px',
        borderRadius: '6px',
        background: 'var(--color-surface)',
        border: '1px solid var(--color-border)',
        color: 'var(--color-text)',
        cursor: 'pointer',
        fontSize: '0.8rem',
      }}
    >
      {children}
    </button>
  )
}

function Toggle<T extends string>({
  value,
  options,
  onChange,
}: {
  value: T
  options: { value: T; label: string }[]
  onChange: (v: T) => void
}) {
  return (
    <div
      style={{
        display: 'flex',
        borderRadius: '6px',
        overflow: 'hidden',
        border: '1px solid var(--color-border)',
      }}
    >
      {options.map((opt) => {
        const active = opt.value === value
        return (
          <button
            key={opt.value}
            onClick={() => onChange(opt.value)}
            style={{
              padding: '6px 12px',
              fontSize: '0.78rem',
              fontWeight: active ? 600 : 400,
              border: 'none',
              cursor: 'pointer',
              background: active
                ? 'color-mix(in srgb, var(--color-accent) 20%, transparent)'
                : 'var(--color-surface)',
              color: active ? 'var(--color-text)' : 'var(--color-muted)',
            }}
          >
            {opt.label}
          </button>
        )
      })}
    </div>
  )
}

function DownloadButton({ issueId, compact }: { issueId: number; compact?: boolean }) {
  const queryClient = useQueryClient()
  const mutation = useMutation({
    mutationFn: () => post(`/queue/enqueue/${issueId}`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['calendar'] })
      queryClient.invalidateQueries({ queryKey: ['queue'] })
    },
  })
  return (
    <button
      onClick={(e) => {
        e.stopPropagation()
        mutation.mutate()
      }}
      disabled={mutation.isPending}
      title="Send to the download queue"
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: '5px',
        fontSize: '0.72rem',
        fontWeight: 600,
        padding: compact ? '2px 6px' : '4px 10px',
        borderRadius: '5px',
        background: 'var(--color-accent)',
        color: '#fff',
        border: 'none',
        cursor: mutation.isPending ? 'default' : 'pointer',
        opacity: mutation.isPending ? 0.6 : 1,
        whiteSpace: 'nowrap',
      }}
    >
      <Download size={12} />
      {compact ? '' : mutation.isPending ? '…' : 'Download'}
    </button>
  )
}

// ── Month grid ────────────────────────────────────────────────────────────────

function DayCell({
  day,
  entries,
  inMonth,
  isToday,
  onOpen,
}: {
  day: Date
  entries: CalendarEntry[]
  inMonth: boolean
  isToday: boolean
  onOpen: (entry: CalendarEntry) => void
}) {
  return (
    <div
      style={{
        minHeight: 120,
        background: inMonth ? 'var(--color-surface)' : 'transparent',
        border: '1px solid var(--color-border)',
        borderRadius: '6px',
        padding: '6px',
        display: 'flex',
        flexDirection: 'column',
        gap: '4px',
        opacity: inMonth ? 1 : 0.45,
        overflow: 'hidden',
      }}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          marginBottom: '2px',
        }}
      >
        <span
          style={{
            fontSize: '0.72rem',
            fontWeight: isToday ? 700 : 500,
            color: isToday ? 'var(--color-accent)' : 'var(--color-muted)',
            background: isToday
              ? 'color-mix(in srgb, var(--color-accent) 18%, transparent)'
              : 'transparent',
            borderRadius: '4px',
            padding: isToday ? '1px 6px' : '1px 0',
          }}
        >
          {day.getUTCDate()}
        </span>
        {entries.length > 0 && (
          <span style={{ fontSize: '0.65rem', color: 'var(--color-muted)' }}>
            {entries.length}
          </span>
        )}
      </div>

      <div
        style={{
          display: 'flex',
          flexDirection: 'column',
          gap: '3px',
          overflowY: 'auto',
          maxHeight: 150,
        }}
      >
        {entries.map((entry) => (
          <div
            key={entry.issue_id}
            onClick={() => onOpen(entry)}
            title={`${entry.series_title} #${entry.issue_number}${
              entry.title ? ` — ${entry.title}` : ''
            } (${entryLabel(entry)})`}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '5px',
              padding: '3px 5px',
              borderRadius: '4px',
              background: 'var(--color-bg)',
              borderLeft: `3px solid ${entryColor(entry)}`,
              cursor: 'pointer',
              minWidth: 0,
            }}
          >
            <span
              style={{
                flex: 1,
                minWidth: 0,
                fontSize: '0.7rem',
                color: 'var(--color-text)',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
              }}
            >
              {entry.series_title}{' '}
              <span style={{ color: 'var(--color-muted)' }}>#{entry.issue_number}</span>
            </span>
            {!GRABBABLE_EXCLUDED.includes(entry.status) && (
              <DownloadButton issueId={entry.issue_id} compact />
            )}
          </div>
        ))}
      </div>
    </div>
  )
}

function MonthView({
  month,
  entriesByDay,
  onOpen,
}: {
  month: string
  entriesByDay: Record<string, CalendarEntry[]>
  onOpen: (entry: CalendarEntry) => void
}) {
  const { days } = useMemo(() => monthGrid(month), [month])
  const today = todayIso()
  const { month: monthNum } = parseMonth(month)

  return (
    <div>
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(7, 1fr)',
          gap: '6px',
          marginBottom: '6px',
        }}
      >
        {WEEKDAYS.map((w) => (
          <div
            key={w}
            style={{
              fontSize: '0.7rem',
              fontWeight: 600,
              textTransform: 'uppercase',
              letterSpacing: '0.06em',
              color: 'var(--color-muted)',
              textAlign: 'center',
            }}
          >
            {w}
          </div>
        ))}
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(7, 1fr)', gap: '6px' }}>
        {days.map((day) => {
          const key = isoDay(day)
          return (
            <DayCell
              key={key}
              day={day}
              entries={entriesByDay[key] ?? []}
              inMonth={day.getUTCMonth() + 1 === monthNum}
              isToday={key === today}
              onOpen={onOpen}
            />
          )
        })}
      </div>
    </div>
  )
}

// ── Agenda ────────────────────────────────────────────────────────────────────

function AgendaView({
  entriesByDay,
  onOpen,
}: {
  entriesByDay: Record<string, CalendarEntry[]>
  onOpen: (entry: CalendarEntry) => void
}) {
  const days = useMemo(
    () => Object.keys(entriesByDay).sort((a, b) => a.localeCompare(b)),
    [entriesByDay],
  )
  const today = todayIso()

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '22px' }}>
      {days.map((day) => (
        <div key={day}>
          <h2
            style={{
              fontSize: '0.75rem',
              fontWeight: 600,
              textTransform: 'uppercase',
              letterSpacing: '0.08em',
              color: day === today ? 'var(--color-accent)' : 'var(--color-muted)',
              borderBottom: '1px solid var(--color-border)',
              paddingBottom: '6px',
              marginBottom: '10px',
            }}
          >
            {formatDayLabel(day)}
            {day === today ? ' · Today' : ''}
          </h2>

          <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
            {entriesByDay[day].map((entry) => (
              <div
                key={entry.issue_id}
                onClick={() => onOpen(entry)}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '14px',
                  background: 'var(--color-surface)',
                  border: '1px solid var(--color-border)',
                  borderLeft: `4px solid ${entryColor(entry)}`,
                  borderRadius: '8px',
                  padding: '10px 14px',
                  cursor: 'pointer',
                }}
              >
                {entry.cover_url ? (
                  <img
                    src={entry.cover_url}
                    alt={entry.series_title}
                    style={{
                      width: 40,
                      height: 56,
                      objectFit: 'cover',
                      borderRadius: '4px',
                      flexShrink: 0,
                    }}
                  />
                ) : (
                  <div
                    style={{
                      width: 40,
                      height: 56,
                      borderRadius: '4px',
                      background: 'var(--color-border)',
                      flexShrink: 0,
                    }}
                  />
                )}

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
                    {entry.series_title}{' '}
                    <span style={{ color: 'var(--color-muted)', fontWeight: 400 }}>
                      #{entry.issue_number}
                    </span>
                  </div>
                  <div
                    style={{ fontSize: '0.75rem', color: 'var(--color-muted)', marginTop: '2px' }}
                  >
                    {entry.title ?? '—'}
                    {entry.publisher ? ` · ${entry.publisher}` : ''}
                    {entry.date_source === 'cover' ? ' · cover date' : ''}
                  </div>
                </div>

                <div
                  style={{ display: 'flex', alignItems: 'center', gap: '10px', flexShrink: 0 }}
                >
                  {entry.sources.includes('arc') && !entry.subscribed && (
                    <span
                      style={{
                        fontSize: '0.65rem',
                        fontWeight: 600,
                        textTransform: 'uppercase',
                        color: 'var(--color-muted)',
                        border: '1px solid var(--color-border)',
                        borderRadius: '4px',
                        padding: '1px 5px',
                      }}
                    >
                      Arc
                    </span>
                  )}
                  <span
                    style={{
                      fontSize: '0.7rem',
                      fontWeight: 600,
                      textTransform: 'capitalize',
                      color: entryColor(entry),
                    }}
                  >
                    {entryLabel(entry)}
                  </span>
                  {!GRABBABLE_EXCLUDED.includes(entry.status) && (
                    <DownloadButton issueId={entry.issue_id} />
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────

function CalendarPage() {
  const navigate = useNavigate({ from: Route.fullPath })
  const search = Route.useSearch()
  const month = search.month ?? currentMonth()
  const view: View = search.view ?? 'month'
  const scope: Scope = search.scope ?? 'subscribed'
  const [notice, setNotice] = useState<string | null>(null)
  const queryClient = useQueryClient()

  const { start, end } = useMemo(() => monthGrid(month), [month])

  const { data, isLoading } = useQuery<CalendarResponse>({
    queryKey: ['calendar', start, end, scope],
    queryFn: () =>
      get<CalendarResponse>(`/calendar?start=${start}&end=${end}&scope=${scope}`),
  })

  const entriesByDay = useMemo(() => {
    const groups: Record<string, CalendarEntry[]> = {}
    for (const entry of data?.entries ?? []) {
      ;(groups[entry.release_date] ??= []).push(entry)
    }
    return groups
  }, [data])

  // The calendar itself never calls the metadata provider (see the router
  // docstring); this is the explicit way to pull in newly-announced releases.
  const refresh = useMutation({
    mutationFn: () => post('/releases/refresh'),
    onSuccess: () => {
      setNotice('Refresh started — new releases appear as the provider responds.')
      setTimeout(() => queryClient.invalidateQueries({ queryKey: ['calendar'] }), 5000)
    },
    onError: () => setNotice('Could not start the refresh.'),
  })

  const setSearch = (next: Partial<{ month: string; view: View; scope: Scope }>) =>
    navigate({
      search: {
        month: next.month ?? month,
        view: (next.view ?? view) === 'month' ? undefined : (next.view ?? view),
        scope: (next.scope ?? scope) === 'subscribed' ? undefined : (next.scope ?? scope),
      },
    })

  const openEntry = (entry: CalendarEntry) =>
    navigate({ to: '/series/$seriesId', params: { seriesId: String(entry.series_id) } })

  return (
    <div className="p-6">
      {/* Header */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '10px',
          flexWrap: 'wrap',
          marginBottom: '14px',
        }}
      >
        <NavButton onClick={() => setSearch({ month: offsetMonth(month, -1) })} title="Previous month">
          <ChevronLeft size={16} />
        </NavButton>
        <NavButton onClick={() => setSearch({ month: currentMonth() })}>Today</NavButton>
        <NavButton onClick={() => setSearch({ month: offsetMonth(month, 1) })} title="Next month">
          <ChevronRight size={16} />
        </NavButton>

        <h1
          className="text-2xl font-bold"
          style={{ color: 'var(--color-text)', margin: 0, flex: 1 }}
        >
          {formatMonthLabel(month)}
        </h1>

        <Toggle<Scope>
          value={scope}
          onChange={(v) => setSearch({ scope: v })}
          options={[
            { value: 'subscribed', label: 'Subscribed' },
            { value: 'all', label: 'All known' },
          ]}
        />
        <Toggle<View>
          value={view}
          onChange={(v) => setSearch({ view: v })}
          options={[
            { value: 'month', label: 'Month' },
            { value: 'agenda', label: 'Agenda' },
          ]}
        />
        <NavButton onClick={() => refresh.mutate()} title="Fetch newly-announced releases">
          <RefreshCw size={14} />
          {refresh.isPending ? 'Refreshing…' : 'Refresh'}
        </NavButton>
      </div>

      {/* Summary + legend */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '16px',
          flexWrap: 'wrap',
          marginBottom: '18px',
          fontSize: '0.75rem',
          color: 'var(--color-muted)',
        }}
      >
        {data && (
          <span>
            {data.summary.total} issue{data.summary.total === 1 ? '' : 's'} ·{' '}
            <span style={{ color: 'var(--color-status-wanted)' }}>
              {data.summary.pending} outstanding
            </span>
          </span>
        )}
        <span style={{ display: 'flex', alignItems: 'center', gap: '12px', flexWrap: 'wrap' }}>
          {LEGEND.map((l) => (
            <span key={l.label} style={{ display: 'flex', alignItems: 'center', gap: '5px' }}>
              <span
                style={{
                  width: 8,
                  height: 8,
                  borderRadius: '2px',
                  background: l.color,
                  display: 'inline-block',
                }}
              />
              {l.label}
            </span>
          ))}
        </span>
      </div>

      {notice && (
        <div
          style={{
            marginBottom: '16px',
            padding: '8px 12px',
            borderRadius: '6px',
            background: 'var(--color-surface)',
            border: '1px solid var(--color-border)',
            color: 'var(--color-text)',
            fontSize: '0.8rem',
          }}
        >
          {notice}
        </div>
      )}

      {isLoading && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(7, 1fr)', gap: '6px' }}>
          {Array.from({ length: 35 }).map((_, i) => (
            <Skeleton key={i} style={{ height: '120px', width: '100%' }} />
          ))}
        </div>
      )}

      {!isLoading && data && data.entries.length === 0 && (
        <div
          style={{
            textAlign: 'center',
            padding: '64px 0',
            color: 'var(--color-muted)',
            fontSize: '0.95rem',
          }}
        >
          {scope === 'subscribed'
            ? 'Nothing scheduled this month for the series and arcs you follow. Try "All known", or subscribe to a series.'
            : 'No issues are dated in this month yet.'}
        </div>
      )}

      {!isLoading && data && data.entries.length > 0 && view === 'month' && (
        <MonthView month={month} entriesByDay={entriesByDay} onOpen={openEntry} />
      )}
      {!isLoading && data && data.entries.length > 0 && view === 'agenda' && (
        <AgendaView entriesByDay={entriesByDay} onOpen={openEntry} />
      )}
    </div>
  )
}
