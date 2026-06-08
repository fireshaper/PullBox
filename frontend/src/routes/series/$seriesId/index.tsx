import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, RefreshCw } from 'lucide-react'
import { useEffect, useRef } from 'react'
import { ApiError, get, patch, post } from '../../../api/client'

// ── Types ─────────────────────────────────────────────────────────────────────

type SeriesDetail = {
  id: number
  comicvine_id: string
  title: string
  publisher: string | null
  start_year: number | null
  status: string
  subscribed: boolean
  auto_download: boolean
  cover_url: string | null
  description: string | null
  created_at: string
}

type Issue = {
  id: number
  issue_number: string
  title: string | null
  cover_date: string | null
  store_date: string | null
  cover_url: string | null
  status: string
}

// ── Route ─────────────────────────────────────────────────────────────────────

export const Route = createFileRoute('/series/$seriesId/')({
  component: SeriesDetailPage,
})

// ── Status colours ────────────────────────────────────────────────────────────

const STATUS_COLORS: Record<string, string> = {
  wanted: 'var(--color-status-wanted)',
  downloading: 'var(--color-status-downloading)',
  downloaded: 'var(--color-status-downloaded)',
  skipped: 'var(--color-status-skipped)',
  failed: 'var(--color-status-failed)',
  unknown: 'var(--color-muted)',
}

// ── Issue action buttons ──────────────────────────────────────────────────────

function IssueActions({ issue, seriesId }: { issue: Issue; seriesId: number }) {
  const queryClient = useQueryClient()

  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: ['series', seriesId, 'issues'] })

  const downloadMutation = useMutation({
    mutationFn: async () => {
      if (issue.status !== 'wanted') {
        try {
          await post(`/issues/${issue.id}/want`)
        } catch (e) {
          if (!(e instanceof ApiError && e.status === 409)) throw e
        }
      }
      try {
        await post(`/queue/enqueue/${issue.id}`)
      } catch (e) {
        if (!(e instanceof ApiError && e.status === 409)) throw e
      }
    },
    onSuccess: invalidate,
  })

  const skipMutation = useMutation({
    mutationFn: () => post(`/issues/${issue.id}/skip`),
    onSuccess: invalidate,
  })

  const wantMutation = useMutation({
    mutationFn: () => post(`/issues/${issue.id}/want`),
    onSuccess: invalidate,
  })

  if (issue.status === 'downloaded' || issue.status === 'downloading') return null

  const actionBtn = (
    label: string,
    onClick: () => void,
    pending: boolean,
    variant: 'primary' | 'ghost' = 'ghost',
  ) => (
    <button
      onClick={onClick}
      disabled={pending}
      style={{
        padding: '3px 10px',
        borderRadius: '4px',
        fontSize: '0.75rem',
        fontWeight: 600,
        border: variant === 'primary' ? 'none' : '1px solid var(--color-border)',
        cursor: pending ? 'wait' : 'pointer',
        background: variant === 'primary' ? 'var(--color-accent)' : 'transparent',
        color: variant === 'primary' ? '#fff' : 'var(--color-muted)',
        flexShrink: 0,
      }}
    >
      {pending ? '…' : label}
    </button>
  )

  if (issue.status === 'skipped') {
    return actionBtn('Want', () => wantMutation.mutate(), wantMutation.isPending, 'ghost')
  }

  return (
    <div style={{ display: 'flex', gap: '6px', flexShrink: 0 }}>
      {(issue.status === 'unknown' || issue.status === 'failed') &&
        actionBtn('Download', () => downloadMutation.mutate(), downloadMutation.isPending, 'primary')}
      {issue.status === 'wanted' && (
        <span
          style={{
            fontSize: '0.75rem',
            fontWeight: 600,
            color: 'var(--color-status-wanted)',
            alignSelf: 'center',
          }}
        >
          Queued
        </span>
      )}
      {actionBtn('Skip', () => skipMutation.mutate(), skipMutation.isPending)}
    </div>
  )
}

// ── Page ──────────────────────────────────────────────────────────────────────

function SeriesDetailPage() {
  const { seriesId } = Route.useParams()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const id = Number(seriesId)

  const { data: series, isLoading: seriesLoading } = useQuery<SeriesDetail>({
    queryKey: ['series', id],
    queryFn: () => get<SeriesDetail>(`/series/${id}`),
  })

  const { data: issues, isLoading: issuesLoading } = useQuery<Issue[]>({
    queryKey: ['series', id, 'issues'],
    queryFn: () => get<Issue[]>(`/series/${id}/issues`),
  })

  const enrichMutation = useMutation({
    mutationFn: () => post<SeriesDetail>(`/series/${id}/enrich`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['series', id] }),
  })

  const syncMutation = useMutation({
    mutationFn: () => post(`/series/${id}/sync-issues`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['series', id, 'issues'] }),
  })

  const markAllWantedMutation = useMutation({
    mutationFn: () => post(`/series/${id}/mark-all-wanted`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['series', id, 'issues'] }),
  })

  const subscribeMutation = useMutation({
    mutationFn: (subscribed: boolean) =>
      patch<SeriesDetail>(`/series/${id}`, { subscribed }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['series', id] }),
  })

  const autoDownloadMutation = useMutation({
    mutationFn: (auto_download: boolean) =>
      patch<SeriesDetail>(`/series/${id}`, { auto_download }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['series', id] }),
  })

  // Auto-enrich if the series was created by calendar refresh (missing rich metadata)
  const enrichedRef = useRef(false)
  useEffect(() => {
    if (series && !series.description && !enrichedRef.current) {
      enrichedRef.current = true
      enrichMutation.mutate()
    }
  }, [series])

  // Auto-sync issues on first load if there are none
  const syncedRef = useRef(false)
  useEffect(() => {
    if (issues && issues.length === 0 && !syncedRef.current) {
      syncedRef.current = true
      syncMutation.mutate()
    }
  }, [issues])

  if (seriesLoading) {
    return (
      <div className="p-6" style={{ color: 'var(--color-muted)', fontSize: '0.875rem' }}>
        Loading…
      </div>
    )
  }

  if (!series) {
    return (
      <div className="p-6" style={{ color: 'var(--color-status-failed)', fontSize: '0.875rem' }}>
        Series not found.
      </div>
    )
  }

  const sortedIssues = issues
    ? [...issues].sort((a, b) => (parseFloat(a.issue_number) || 0) - (parseFloat(b.issue_number) || 0))
    : []

  const meta = [series.publisher, series.start_year].filter(Boolean).join(' · ')

  return (
    <div className="p-6">
      {/* Back */}
      <button
        onClick={() => navigate({ to: '/series' })}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '6px',
          marginBottom: '24px',
          background: 'none',
          border: 'none',
          color: 'var(--color-muted)',
          cursor: 'pointer',
          fontSize: '0.875rem',
          padding: 0,
        }}
      >
        <ArrowLeft size={16} />
        Back to Series
      </button>

      {/* Header */}
      <div style={{ display: 'flex', gap: '24px', marginBottom: '40px', alignItems: 'flex-start' }}>
        {/* Cover */}
        {series.cover_url ? (
          <img
            src={series.cover_url}
            alt={series.title}
            style={{ width: 160, height: 240, objectFit: 'cover', borderRadius: '8px', flexShrink: 0 }}
          />
        ) : (
          <div
            style={{ width: 160, height: 240, borderRadius: '8px', background: 'var(--color-border)', flexShrink: 0 }}
          />
        )}

        {/* Metadata */}
        <div style={{ flex: 1, minWidth: 0 }}>
          <h1 style={{ fontSize: '1.75rem', fontWeight: 700, color: 'var(--color-text)', marginBottom: '6px' }}>
            {series.title}
          </h1>

          {meta && (
            <div style={{ fontSize: '0.875rem', color: 'var(--color-muted)', marginBottom: '14px' }}>
              {meta}
            </div>
          )}

          {series.description ? (
            <div
              style={{
                fontSize: '0.875rem',
                color: 'var(--color-text)',
                lineHeight: 1.6,
                marginBottom: '24px',
                maxWidth: '600px',
                display: '-webkit-box',
                WebkitLineClamp: 6,
                WebkitBoxOrient: 'vertical',
                overflow: 'hidden',
              }}
              dangerouslySetInnerHTML={{ __html: series.description }}
            />
          ) : enrichMutation.isPending ? (
            <div style={{ fontSize: '0.875rem', color: 'var(--color-muted)', marginBottom: '24px' }}>
              Loading metadata…
            </div>
          ) : null}

          {/* Subscribe / auto-download */}
          <div style={{ display: 'flex', alignItems: 'center', gap: '16px', flexWrap: 'wrap' }}>
            <button
              onClick={() => subscribeMutation.mutate(!series.subscribed)}
              disabled={subscribeMutation.isPending}
              style={{
                padding: '8px 20px',
                borderRadius: '6px',
                fontWeight: 600,
                fontSize: '0.875rem',
                cursor: subscribeMutation.isPending ? 'wait' : 'pointer',
                background: series.subscribed ? 'var(--color-surface)' : 'var(--color-accent)',
                color: series.subscribed ? 'var(--color-text)' : '#fff',
                border: series.subscribed ? '1px solid var(--color-border)' : 'none',
              }}
            >
              {series.subscribed ? 'Unsubscribe' : 'Add to Pullbox'}
            </button>

            {series.subscribed && (
              <label
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '8px',
                  fontSize: '0.875rem',
                  color: 'var(--color-text)',
                  cursor: 'pointer',
                }}
              >
                <input
                  type="checkbox"
                  checked={series.auto_download}
                  onChange={(e) => autoDownloadMutation.mutate(e.target.checked)}
                  disabled={autoDownloadMutation.isPending}
                />
                Auto-download new issues
              </label>
            )}
          </div>
        </div>
      </div>

      {/* Issues */}
      <div>
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            marginBottom: '12px',
          }}
        >
          <h2 style={{ fontSize: '1rem', fontWeight: 600, color: 'var(--color-text)' }}>
            Issues{issues ? ` (${issues.length})` : ''}
          </h2>
          <div style={{ display: 'flex', gap: '8px' }}>
            <button
              onClick={() => markAllWantedMutation.mutate()}
              disabled={markAllWantedMutation.isPending || !issues || issues.every(i => !['unknown', 'failed'].includes(i.status))}
              style={{
                padding: '4px 12px',
                borderRadius: '6px',
                fontSize: '0.8rem',
                background: 'var(--color-surface)',
                border: '1px solid var(--color-border)',
                color: 'var(--color-text)',
                cursor: markAllWantedMutation.isPending ? 'wait' : 'pointer',
                opacity: (!issues || issues.every(i => !['unknown', 'failed'].includes(i.status))) ? 0.4 : 1,
              }}
            >
              {markAllWantedMutation.isPending ? 'Marking…' : 'Mark all as Wanted'}
            </button>
            <button
              onClick={() => syncMutation.mutate()}
              disabled={syncMutation.isPending}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '6px',
                padding: '4px 12px',
                borderRadius: '6px',
                fontSize: '0.8rem',
                background: 'var(--color-surface)',
                border: '1px solid var(--color-border)',
                color: 'var(--color-text)',
                cursor: syncMutation.isPending ? 'wait' : 'pointer',
              }}
            >
              <RefreshCw size={12} />
              {syncMutation.isPending ? 'Syncing…' : 'Sync from ComicVine'}
            </button>
          </div>
        </div>

        {(issuesLoading || syncMutation.isPending) && (
          <div style={{ color: 'var(--color-muted)', fontSize: '0.875rem' }}>
            {syncMutation.isPending ? 'Syncing issues from ComicVine…' : 'Loading issues…'}
          </div>
        )}

        {!issuesLoading && !syncMutation.isPending && sortedIssues.length === 0 && (
          <div style={{ color: 'var(--color-muted)', fontSize: '0.875rem' }}>
            No issues found.
          </div>
        )}

        {sortedIssues.length > 0 && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
            {sortedIssues.map((issue) => (
              <div
                key={issue.id}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '12px',
                  padding: '8px 12px',
                  borderRadius: '6px',
                  background: 'var(--color-surface)',
                  border: '1px solid var(--color-border)',
                }}
              >
                <span
                  style={{
                    fontSize: '0.8rem',
                    fontWeight: 600,
                    color: 'var(--color-muted)',
                    minWidth: '44px',
                    flexShrink: 0,
                  }}
                >
                  #{issue.issue_number}
                </span>
                <span
                  style={{
                    flex: 1,
                    fontSize: '0.875rem',
                    color: 'var(--color-text)',
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    whiteSpace: 'nowrap',
                  }}
                >
                  {issue.title || `Issue #${issue.issue_number}`}
                </span>
                {(issue.store_date ?? issue.cover_date) && (
                  <span style={{ fontSize: '0.75rem', color: 'var(--color-muted)', flexShrink: 0 }}>
                    {new Date(`${issue.store_date ?? issue.cover_date}T00:00:00Z`).toLocaleDateString(
                      'en-US',
                      { month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC' },
                    )}
                  </span>
                )}
                <span
                  style={{
                    fontSize: '0.7rem',
                    fontWeight: 600,
                    textTransform: 'capitalize',
                    color: STATUS_COLORS[issue.status] ?? 'var(--color-muted)',
                    flexShrink: 0,
                    minWidth: '60px',
                    textAlign: 'right',
                  }}
                >
                  {issue.status}
                </span>
                <IssueActions issue={issue} seriesId={id} />
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
