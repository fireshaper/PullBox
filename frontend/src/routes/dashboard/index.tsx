import { createFileRoute, Link, useNavigate } from '@tanstack/react-router'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AlertTriangle,
  CheckCircle2,
  Clock,
  Database,
  Download,
  HardDrive,
  Layers,
  Library,
  RefreshCw,
  XCircle,
} from 'lucide-react'
import { get, post } from '../../api/client'
import { Skeleton } from '../../components/ui/skeleton'

// ── Types (mirror backend dashboard schemas) ──────────────────────────────────

type IssueRef = {
  id: number
  issue_number: string
  title: string | null
  cover_url: string | null
  status: string
  series_id: number
  series_title: string
}

type Job = {
  id: number
  status: string
  attempts: number
  source_type: string
  result_title: string | null
  download_client_type: string | null
  last_attempt_at: string | null
  next_attempt_at: string | null
  updated_at: string
  issue: IssueRef | null
}

type QueueHealth = {
  queued: number
  searching: number
  pending: number
  downloading: number
  failed: number
}

type ActivityResponse = {
  queue_health: QueueHealth
  active_downloads: Job[]
  recent_completed: Job[]
  recent_failed: Job[]
}

type SyncInfo = {
  last_run_at: string | null
  success: boolean | null
  message: string | null
}

type OverviewResponse = {
  library_stats: {
    total_series: number
    total_issues: number
    downloaded_issues: number
    storage_bytes: number
  }
  sync_status: {
    calendar: SyncInfo
    backfill: SyncInfo
    next_backfill_at: string | null
    import_pending: number
  }
  recent_library: {
    id: number
    issue_number: string
    title: string | null
    cover_url: string | null
    series_id: number
    series_title: string
    updated_at: string
  }[]
  stuck_series: {
    series_id: number
    series_title: string
    publisher: string | null
    wanted_count: number
    max_attempts: number
  }[]
}

type Release = {
  issue_id: number
  issue_number: string
  title: string | null
  cover_url: string | null
  status: string
  release_date: string
  series_id: number
  series_title: string
  publisher: string | null
  subscribed: boolean
}

type PullResponse = {
  week: string
  this_week: Release[]
  upcoming: Release[]
}

// ── Helpers ───────────────────────────────────────────────────────────────────

const STATUS_COLORS: Record<string, string> = {
  wanted: 'var(--color-status-wanted)',
  downloading: 'var(--color-status-downloading)',
  downloaded: 'var(--color-status-downloaded)',
  skipped: 'var(--color-status-skipped)',
  failed: 'var(--color-status-failed)',
  searching: 'var(--color-status-downloading)',
  pending: 'var(--color-status-wanted)',
  queued: 'var(--color-muted)',
  completed: 'var(--color-status-downloaded)',
  unknown: 'var(--color-muted)',
}

function formatBytes(bytes: number): string {
  if (!bytes) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  const i = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1)
  const value = bytes / Math.pow(1024, i)
  return `${value.toFixed(value >= 100 || i === 0 ? 0 : 1)} ${units[i]}`
}

function formatDateTime(iso: string | null): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('en-US', {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  })
}

function formatRelative(iso: string | null): string {
  if (!iso) return 'Never'
  const diff = Date.now() - new Date(iso).getTime()
  const mins = Math.round(diff / 60000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins}m ago`
  const hours = Math.round(mins / 60)
  if (hours < 24) return `${hours}h ago`
  const days = Math.round(hours / 24)
  return `${days}d ago`
}

function formatReleaseDate(dateStr: string): string {
  return new Date(`${dateStr}T00:00:00Z`).toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    timeZone: 'UTC',
  })
}

// ── Route ─────────────────────────────────────────────────────────────────────

export const Route = createFileRoute('/dashboard/')({
  component: DashboardPage,
})

// ── Shared UI atoms ───────────────────────────────────────────────────────────

function Card({
  title,
  icon: Icon,
  action,
  children,
  span,
}: {
  title: string
  icon?: React.ComponentType<{ size?: number }>
  action?: React.ReactNode
  children: React.ReactNode
  span?: boolean
}) {
  return (
    <section
      style={{
        gridColumn: span ? '1 / -1' : undefined,
        background: 'var(--color-surface)',
        border: '1px solid var(--color-border)',
        borderRadius: '10px',
        padding: '16px',
        display: 'flex',
        flexDirection: 'column',
        minWidth: 0,
      }}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          marginBottom: '12px',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          {Icon && (
            <span style={{ color: 'var(--color-muted)', display: 'flex' }}>
              <Icon size={15} />
            </span>
          )}
          <h2
            style={{
              fontSize: '0.72rem',
              fontWeight: 600,
              textTransform: 'uppercase',
              letterSpacing: '0.08em',
              color: 'var(--color-muted)',
              margin: 0,
            }}
          >
            {title}
          </h2>
        </div>
        {action}
      </div>
      {children}
    </section>
  )
}

function EmptyRow({ text }: { text: string }) {
  return (
    <div style={{ color: 'var(--color-muted)', fontSize: '0.8rem', padding: '8px 0' }}>
      {text}
    </div>
  )
}

function Cover({ url, size = 40 }: { url: string | null; size?: number }) {
  const w = size
  const h = Math.round(size * 1.4)
  return url ? (
    <img
      src={url}
      alt=""
      style={{ width: w, height: h, objectFit: 'cover', borderRadius: '3px', flexShrink: 0 }}
    />
  ) : (
    <div
      style={{
        width: w,
        height: h,
        borderRadius: '3px',
        background: 'var(--color-border)',
        flexShrink: 0,
      }}
    />
  )
}

function StatusDot({ status }: { status: string }) {
  return (
    <span
      style={{
        fontSize: '0.68rem',
        fontWeight: 600,
        textTransform: 'capitalize',
        color: STATUS_COLORS[status] ?? 'var(--color-muted)',
        whiteSpace: 'nowrap',
      }}
    >
      {status}
    </span>
  )
}

// ── Queue health strip ────────────────────────────────────────────────────────

function QueueHealthStrip({ health }: { health: QueueHealth }) {
  const queryClient = useQueryClient()
  const retryAll = useMutation({
    mutationFn: () => post<{ retried: number }>('/queue/retry-failed'),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['dashboard', 'activity'] }),
  })

  const tiles = [
    { label: 'Queued', value: health.queued, color: 'var(--color-muted)' },
    {
      label: 'Downloading',
      value: health.downloading + health.pending + health.searching,
      color: 'var(--color-status-downloading)',
    },
    { label: 'Failed', value: health.failed, color: 'var(--color-status-failed)' },
  ]

  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'stretch',
        gap: '12px',
        flexWrap: 'wrap',
      }}
    >
      {tiles.map((t) => (
        <div
          key={t.label}
          style={{
            flex: '1 1 140px',
            background: 'var(--color-surface)',
            border: '1px solid var(--color-border)',
            borderRadius: '10px',
            padding: '14px 18px',
            display: 'flex',
            flexDirection: 'column',
            gap: '2px',
          }}
        >
          <span style={{ fontSize: '1.75rem', fontWeight: 700, color: t.color, lineHeight: 1.1 }}>
            {t.value}
          </span>
          <span
            style={{
              fontSize: '0.72rem',
              textTransform: 'uppercase',
              letterSpacing: '0.06em',
              color: 'var(--color-muted)',
            }}
          >
            {t.label}
          </span>
        </div>
      ))}

      {health.failed > 0 && (
        <button
          onClick={() => retryAll.mutate()}
          disabled={retryAll.isPending}
          style={{
            flex: '0 0 auto',
            alignSelf: 'center',
            display: 'flex',
            alignItems: 'center',
            gap: '8px',
            padding: '10px 16px',
            borderRadius: '8px',
            background: 'var(--color-accent)',
            color: '#fff',
            border: 'none',
            fontSize: '0.82rem',
            fontWeight: 600,
            cursor: retryAll.isPending ? 'default' : 'pointer',
            opacity: retryAll.isPending ? 0.6 : 1,
          }}
        >
          <RefreshCw size={15} />
          {retryAll.isPending ? 'Retrying…' : `Retry All Failed (${health.failed})`}
        </button>
      )}
    </div>
  )
}

// ── Download activity ─────────────────────────────────────────────────────────

function ActiveDownloadRow({ job }: { job: Job }) {
  const isDownloading = job.status === 'downloading'
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: '10px', padding: '6px 0' }}>
      <Cover url={job.issue?.cover_url ?? null} size={32} />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div
          style={{
            fontSize: '0.82rem',
            fontWeight: 600,
            color: 'var(--color-text)',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}
        >
          {job.issue?.series_title ?? 'Unknown'}{' '}
          <span style={{ color: 'var(--color-muted)', fontWeight: 400 }}>
            #{job.issue?.issue_number}
          </span>
        </div>
        <div
          style={{
            position: 'relative',
            height: '5px',
            borderRadius: '3px',
            background: 'var(--color-border)',
            overflow: 'hidden',
            marginTop: '5px',
          }}
        >
          {isDownloading ? (
            <span className="pb-indeterminate-bar" />
          ) : (
            <span
              style={{
                position: 'absolute',
                inset: 0,
                width: '30%',
                borderRadius: 'inherit',
                background: STATUS_COLORS[job.status] ?? 'var(--color-muted)',
                opacity: 0.6,
              }}
            />
          )}
        </div>
      </div>
      <StatusDot status={job.status} />
    </div>
  )
}

function CompactJobRow({
  job,
  right,
}: {
  job: Job
  right: React.ReactNode
}) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: '10px', padding: '5px 0' }}>
      <Cover url={job.issue?.cover_url ?? null} size={28} />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div
          style={{
            fontSize: '0.8rem',
            color: 'var(--color-text)',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}
        >
          {job.issue?.series_title ?? 'Unknown'}{' '}
          <span style={{ color: 'var(--color-muted)' }}>#{job.issue?.issue_number}</span>
        </div>
      </div>
      {right}
    </div>
  )
}

function ActivityCard({ data }: { data: ActivityResponse }) {
  return (
    <Card title="Download Activity" icon={Download} span>
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))',
          gap: '20px',
        }}
      >
        {/* Active */}
        <div>
          <SubHeading text={`Active (${data.active_downloads.length})`} />
          {data.active_downloads.length === 0 ? (
            <EmptyRow text="Nothing downloading right now." />
          ) : (
            data.active_downloads.map((j) => <ActiveDownloadRow key={j.id} job={j} />)
          )}
        </div>

        {/* Recently completed */}
        <div>
          <SubHeading text="Recently Completed" />
          {data.recent_completed.length === 0 ? (
            <EmptyRow text="No completed grabs yet." />
          ) : (
            data.recent_completed.map((j) => (
              <CompactJobRow
                key={j.id}
                job={j}
                right={
                  <span
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: '4px',
                      fontSize: '0.72rem',
                      color: 'var(--color-status-downloaded)',
                    }}
                  >
                    <CheckCircle2 size={13} />
                    {formatRelative(j.updated_at)}
                  </span>
                }
              />
            ))
          )}
        </div>

        {/* Failed needing attention */}
        <div>
          <SubHeading text="Needs Attention" />
          {data.recent_failed.length === 0 ? (
            <EmptyRow text="No failed jobs. 🎉" />
          ) : (
            data.recent_failed.map((j) => (
              <CompactJobRow
                key={j.id}
                job={j}
                right={
                  <span
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: '4px',
                      fontSize: '0.72rem',
                      color: 'var(--color-status-failed)',
                    }}
                  >
                    <XCircle size={13} />
                    {j.attempts} tries
                  </span>
                }
              />
            ))
          )}
        </div>
      </div>
    </Card>
  )
}

function SubHeading({ text }: { text: string }) {
  return (
    <div
      style={{
        fontSize: '0.68rem',
        fontWeight: 600,
        textTransform: 'uppercase',
        letterSpacing: '0.06em',
        color: 'var(--color-muted)',
        marginBottom: '6px',
        paddingBottom: '4px',
        borderBottom: '1px solid var(--color-border)',
      }}
    >
      {text}
    </div>
  )
}

// ── This week's pull list ─────────────────────────────────────────────────────

function DownloadButton({ issueId }: { issueId: number }) {
  const queryClient = useQueryClient()
  const mutation = useMutation({
    mutationFn: () => post(`/queue/enqueue/${issueId}`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['dashboard', 'pull'] })
      queryClient.invalidateQueries({ queryKey: ['dashboard', 'activity'] })
    },
  })
  return (
    <button
      onClick={() => mutation.mutate()}
      disabled={mutation.isPending}
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: '5px',
        fontSize: '0.72rem',
        fontWeight: 600,
        padding: '4px 10px',
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
      {mutation.isPending ? '…' : 'Download'}
    </button>
  )
}

function ReleaseRow({ release }: { release: Release }) {
  // Wanted / not-yet-grabbed issues get a one-click Download; only issues that
  // are already downloaded, in flight, or deliberately skipped hide the button.
  const grabbable = !['downloaded', 'downloading', 'skipped'].includes(release.status)
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: '10px', padding: '6px 0' }}>
      <Cover url={release.cover_url} size={32} />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div
          style={{
            fontSize: '0.82rem',
            fontWeight: 600,
            color: 'var(--color-text)',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}
        >
          {release.series_title}{' '}
          <span style={{ color: 'var(--color-muted)', fontWeight: 400 }}>
            #{release.issue_number}
          </span>
        </div>
        <div style={{ fontSize: '0.72rem', color: 'var(--color-muted)' }}>
          {formatReleaseDate(release.release_date)}
        </div>
      </div>
      {grabbable ? (
        <DownloadButton issueId={release.issue_id} />
      ) : (
        <StatusDot status={release.status} />
      )}
    </div>
  )
}

// ── Library stats ─────────────────────────────────────────────────────────────

function StatTile({
  icon: Icon,
  value,
  label,
}: {
  icon: React.ComponentType<{ size?: number }>
  value: string | number
  label: string
}) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
      <span
        style={{
          color: 'var(--color-accent)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          width: 34,
          height: 34,
          borderRadius: '8px',
          background: 'color-mix(in srgb, var(--color-accent) 15%, transparent)',
          flexShrink: 0,
        }}
      >
        <Icon size={17} />
      </span>
      <div style={{ minWidth: 0 }}>
        <div style={{ fontSize: '1.15rem', fontWeight: 700, color: 'var(--color-text)' }}>
          {value}
        </div>
        <div style={{ fontSize: '0.7rem', color: 'var(--color-muted)' }}>{label}</div>
      </div>
    </div>
  )
}

// ── Sync status ───────────────────────────────────────────────────────────────

function SyncRow({ label, info }: { label: string; info: SyncInfo }) {
  const ok = info.success
  const Icon = ok === null ? Clock : ok ? CheckCircle2 : AlertTriangle
  const color =
    ok === null
      ? 'var(--color-muted)'
      : ok
        ? 'var(--color-status-downloaded)'
        : 'var(--color-status-failed)'
  return (
    <div style={{ display: 'flex', alignItems: 'flex-start', gap: '8px', padding: '4px 0' }}>
      <span style={{ color, display: 'flex', marginTop: '2px' }}>
        <Icon size={14} />
      </span>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: '8px' }}>
          <span style={{ fontSize: '0.8rem', color: 'var(--color-text)' }}>{label}</span>
          <span style={{ fontSize: '0.75rem', color: 'var(--color-muted)' }}>
            {formatRelative(info.last_run_at)}
          </span>
        </div>
        {info.message && (
          <div
            style={{
              fontSize: '0.72rem',
              color: ok === false ? 'var(--color-status-failed)' : 'var(--color-muted)',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            }}
          >
            {info.message}
          </div>
        )}
      </div>
    </div>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────

function DashboardPage() {
  const activity = useQuery<ActivityResponse>({
    queryKey: ['dashboard', 'activity'],
    queryFn: () => get<ActivityResponse>('/dashboard/activity'),
    refetchInterval: 15_000,
  })
  const overview = useQuery<OverviewResponse>({
    queryKey: ['dashboard', 'overview'],
    queryFn: () => get<OverviewResponse>('/dashboard/overview'),
    refetchInterval: 60_000,
  })
  const pull = useQuery<PullResponse>({
    queryKey: ['dashboard', 'pull'],
    queryFn: () => get<PullResponse>('/dashboard/pull'),
    refetchInterval: 120_000,
  })

  return (
    <div className="p-6" style={{ maxWidth: 1200, margin: '0 auto' }}>
      <h1
        className="text-2xl font-bold"
        style={{ color: 'var(--color-text)', marginBottom: '20px' }}
      >
        Dashboard
      </h1>

      {/* Queue health strip */}
      <div style={{ marginBottom: '20px' }}>
        {activity.data ? (
          <QueueHealthStrip health={activity.data.queue_health} />
        ) : (
          <Skeleton style={{ height: '76px', width: '100%' }} />
        )}
      </div>

      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fill, minmax(360px, 1fr))',
          gap: '16px',
          alignItems: 'start',
        }}
      >
        {/* Download activity (full width) */}
        {activity.isLoading ? (
          <Card title="Download Activity" icon={Download} span>
            <Skeleton style={{ height: '120px', width: '100%' }} />
          </Card>
        ) : (
          activity.data && <ActivityCard data={activity.data} />
        )}

        {/* This week's pull list */}
        <Card title="This Week's Pull List" icon={Layers}>
          {pull.isLoading ? (
            <Skeleton style={{ height: '120px', width: '100%' }} />
          ) : !pull.data || pull.data.this_week.length === 0 ? (
            <EmptyRow text="No releases found for this week." />
          ) : (
            <div style={{ maxHeight: 340, overflowY: 'auto' }}>
              {pull.data.this_week.map((r) => (
                <ReleaseRow key={r.issue_id} release={r} />
              ))}
            </div>
          )}
        </Card>

        {/* Upcoming pulls (subscribed only) */}
        <Card title="Upcoming — Subscribed" icon={Clock}>
          {pull.isLoading ? (
            <Skeleton style={{ height: '120px', width: '100%' }} />
          ) : !pull.data || pull.data.upcoming.length === 0 ? (
            <EmptyRow text="Nothing scheduled for your subscriptions." />
          ) : (
            <div style={{ maxHeight: 340, overflowY: 'auto' }}>
              {pull.data.upcoming.map((r) => (
                <div
                  key={r.issue_id}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    gap: '10px',
                    padding: '5px 0',
                  }}
                >
                  <div style={{ minWidth: 0 }}>
                    <div
                      style={{
                        fontSize: '0.8rem',
                        color: 'var(--color-text)',
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                        whiteSpace: 'nowrap',
                      }}
                    >
                      {r.series_title}{' '}
                      <span style={{ color: 'var(--color-muted)' }}>#{r.issue_number}</span>
                    </div>
                  </div>
                  <span
                    style={{
                      fontSize: '0.72rem',
                      color: 'var(--color-muted)',
                      whiteSpace: 'nowrap',
                    }}
                  >
                    {formatReleaseDate(r.release_date)}
                  </span>
                </div>
              ))}
            </div>
          )}
        </Card>

        {/* Library stats */}
        <Card title="Library" icon={Library}>
          {overview.isLoading || !overview.data ? (
            <Skeleton style={{ height: '110px', width: '100%' }} />
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}>
              <StatTile
                icon={Library}
                value={overview.data.library_stats.total_series}
                label="Series tracked"
              />
              <StatTile
                icon={Database}
                value={overview.data.library_stats.downloaded_issues}
                label="Issues downloaded"
              />
              <StatTile
                icon={HardDrive}
                value={formatBytes(overview.data.library_stats.storage_bytes)}
                label="Storage used"
              />
            </div>
          )}
        </Card>

        {/* Sync status */}
        <Card title="Sync Status" icon={RefreshCw}>
          {overview.isLoading || !overview.data ? (
            <Skeleton style={{ height: '110px', width: '100%' }} />
          ) : (
            <div>
              <SyncRow label="ComicVine calendar" info={overview.data.sync_status.calendar} />
              <SyncRow label="Metadata backfill" info={overview.data.sync_status.backfill} />
              <div
                style={{
                  marginTop: '8px',
                  paddingTop: '8px',
                  borderTop: '1px solid var(--color-border)',
                  fontSize: '0.72rem',
                  color: 'var(--color-muted)',
                }}
              >
                {overview.data.sync_status.import_pending > 0 ? (
                  <>
                    {overview.data.sync_status.import_pending} issue(s) pending · next backfill{' '}
                    {formatDateTime(overview.data.sync_status.next_backfill_at)}
                  </>
                ) : (
                  'All imported issues synced.'
                )}
              </div>
            </div>
          )}
        </Card>

        {/* Recently added to library */}
        <Card title="Recently Added" icon={CheckCircle2}>
          {overview.isLoading || !overview.data ? (
            <Skeleton style={{ height: '110px', width: '100%' }} />
          ) : overview.data.recent_library.length === 0 ? (
            <EmptyRow text="No downloaded issues yet." />
          ) : (
            <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
              {overview.data.recent_library.map((i) => (
                <Link
                  key={i.id}
                  to="/series/$seriesId"
                  params={{ seriesId: String(i.series_id) }}
                  title={`${i.series_title} #${i.issue_number}`}
                  style={{ display: 'block' }}
                >
                  <Cover url={i.cover_url} size={52} />
                </Link>
              ))}
            </div>
          )}
        </Card>

        {/* Stuck subscribed series */}
        <Card title="Needs Attention — Stuck Series" icon={AlertTriangle}>
          {overview.isLoading || !overview.data ? (
            <Skeleton style={{ height: '110px', width: '100%' }} />
          ) : overview.data.stuck_series.length === 0 ? (
            <EmptyRow text="No stuck series. Everything's finding sources." />
          ) : (
            <div>
              {overview.data.stuck_series.map((s) => (
                <StuckSeriesRow key={s.series_id} series={s} />
              ))}
            </div>
          )}
        </Card>
      </div>
    </div>
  )
}

function StuckSeriesRow({
  series,
}: {
  series: OverviewResponse['stuck_series'][number]
}) {
  const navigate = useNavigate()
  return (
    <div
      onClick={() =>
        navigate({ to: '/series/$seriesId', params: { seriesId: String(series.series_id) } })
      }
      style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        gap: '10px',
        padding: '6px 0',
        cursor: 'pointer',
      }}
    >
      <div style={{ minWidth: 0 }}>
        <div
          style={{
            fontSize: '0.82rem',
            color: 'var(--color-text)',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}
        >
          {series.series_title}
        </div>
        <div style={{ fontSize: '0.72rem', color: 'var(--color-muted)' }}>
          {series.wanted_count} issue(s) not found
        </div>
      </div>
      <span
        style={{
          fontSize: '0.72rem',
          fontWeight: 600,
          color: 'var(--color-status-failed)',
          whiteSpace: 'nowrap',
        }}
      >
        {series.max_attempts} tries
      </span>
    </div>
  )
}
