import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ArrowLeft,
  ChevronDown,
  ChevronRight,
  ExternalLink,
  HardDrive,
  Layers,
  RefreshCw,
} from 'lucide-react'
import { type CSSProperties, useEffect, useRef, useState } from 'react'
import { ApiError, get, patch, post } from '../../../api/client'

// ── Types ─────────────────────────────────────────────────────────────────────

type SeriesDetail = {
  id: number
  metron_id: string | null
  comicvine_id: string | null
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

type ArcSummary = {
  id: number
  metron_id: string | null
  comicvine_id: string | null
  name: string
}

type Issue = {
  id: number
  issue_number: string
  title: string | null
  cover_date: string | null
  store_date: string | null
  cover_url: string | null
  status: string
  arcs: ArcSummary[]
}

type ArcMember = {
  metron_id: string | null
  comicvine_id: string | null
  name: string | null
  site_detail_url: string | null
  in_library: boolean
  local_issue_id: number | null
  local_series_id: number | null
  local_series_title: string | null
  local_issue_number: string | null
  local_status: string | null
}

type RescanResult = {
  found: number
  relinked: number
  missing: number
  unchanged: number
  files_scanned: number
  folders: string[]
  unmatched_files: string[]
  message: string
}

type ArcDetail = {
  id: number
  metron_id: string | null
  comicvine_id: string | null
  name: string
  publisher: string | null
  cover_url: string | null
  description: string | null
  count_of_issue_appearances: number | null
  issues: ArcMember[]
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

// Shared look for the buttons in the Issues header (cursor/opacity per button).
const ACTION_BUTTON: CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  gap: '6px',
  padding: '4px 12px',
  borderRadius: '6px',
  fontSize: '0.8rem',
  background: 'var(--color-surface)',
  border: '1px solid var(--color-border)',
  color: 'var(--color-text)',
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

// ── Story arc panel ───────────────────────────────────────────────────────────

function ArcMemberRow({
  member,
  onNavigate,
}: {
  member: ArcMember
  onNavigate: (seriesId: number) => void
}) {
  if (member.in_library && member.local_series_id != null) {
    return (
      <button
        onClick={() => onNavigate(member.local_series_id!)}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '8px',
          width: '100%',
          textAlign: 'left',
          padding: '6px 10px',
          borderRadius: '4px',
          background: 'var(--color-bg)',
          border: '1px solid var(--color-border)',
          cursor: 'pointer',
          color: 'var(--color-text)',
          fontSize: '0.8rem',
        }}
      >
        <span style={{ fontWeight: 600, flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {member.local_series_title} #{member.local_issue_number}
        </span>
        <span
          style={{
            fontSize: '0.7rem',
            fontWeight: 600,
            textTransform: 'capitalize',
            color: STATUS_COLORS[member.local_status ?? 'unknown'] ?? 'var(--color-muted)',
            flexShrink: 0,
          }}
        >
          {member.local_status}
        </span>
      </button>
    )
  }

  // Not in the local library — ComicVine only gives us the issue name + link.
  const label = member.name || 'Untracked issue'
  const inner = (
    <>
      <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
        {label}
      </span>
      {member.site_detail_url && <ExternalLink size={12} style={{ flexShrink: 0 }} />}
    </>
  )
  const style: React.CSSProperties = {
    display: 'flex',
    alignItems: 'center',
    gap: '8px',
    padding: '6px 10px',
    borderRadius: '4px',
    background: 'transparent',
    border: '1px dashed var(--color-border)',
    color: 'var(--color-muted)',
    fontSize: '0.8rem',
  }
  return member.site_detail_url ? (
    <a href={member.site_detail_url} target="_blank" rel="noreferrer" style={{ ...style, textDecoration: 'none' }}>
      {inner}
    </a>
  ) : (
    <div style={style}>{inner}</div>
  )
}

function ArcPanel({ issueId, seriesId }: { issueId: number; seriesId: number }) {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { data, isLoading, isError } = useQuery<ArcDetail[]>({
    queryKey: ['issue', issueId, 'arcs'],
    queryFn: () => get<ArcDetail[]>(`/issues/${issueId}/arcs`),
  })

  // The arcs endpoint enriches membership on demand, so once it resolves the
  // issue list may have new badges — refresh it (once) so they appear.
  const refreshedRef = useRef(false)
  useEffect(() => {
    if (data && !refreshedRef.current) {
      refreshedRef.current = true
      queryClient.invalidateQueries({ queryKey: ['series', seriesId, 'issues'] })
    }
  }, [data, queryClient, seriesId])

  const goToSeries = (seriesId: number) => navigate({ to: '/series/$seriesId', params: { seriesId: String(seriesId) } })

  if (isLoading) {
    return <div style={{ padding: '12px 16px', color: 'var(--color-muted)', fontSize: '0.8rem' }}>Loading arcs…</div>
  }
  if (isError) {
    return (
      <div style={{ padding: '12px 16px', color: 'var(--color-status-failed)', fontSize: '0.8rem' }}>
        Failed to load story arcs.
      </div>
    )
  }
  if (!data || data.length === 0) {
    return <div style={{ padding: '12px 16px', color: 'var(--color-muted)', fontSize: '0.8rem' }}>Not part of any story arc.</div>
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '16px', padding: '14px 16px' }}>
      {data.map((arc) => (
        <div key={arc.id}>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: '8px', marginBottom: '8px' }}>
            <Layers size={14} style={{ color: 'var(--color-accent)', flexShrink: 0 }} />
            <span style={{ fontWeight: 700, fontSize: '0.9rem', color: 'var(--color-text)' }}>{arc.name}</span>
            {arc.publisher && (
              <span style={{ fontSize: '0.75rem', color: 'var(--color-muted)' }}>{arc.publisher}</span>
            )}
            {arc.count_of_issue_appearances != null && (
              <span style={{ fontSize: '0.75rem', color: 'var(--color-muted)', marginLeft: 'auto' }}>
                {arc.count_of_issue_appearances} issues
              </span>
            )}
          </div>
          {arc.issues.length > 0 ? (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
              {arc.issues.map((m, idx) => (
                <ArcMemberRow
                  key={m.metron_id ?? m.comicvine_id ?? idx}
                  member={m}
                  onNavigate={goToSeries}
                />
              ))}
            </div>
          ) : (
            <div style={{ fontSize: '0.8rem', color: 'var(--color-muted)' }}>No issue list available for this arc.</div>
          )}
        </div>
      ))}
    </div>
  )
}

// ── Page ──────────────────────────────────────────────────────────────────────

function SeriesDetailPage() {
  const { seriesId } = Route.useParams()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const id = Number(seriesId)

  const [expandedArcs, setExpandedArcs] = useState<Set<number>>(new Set())
  const toggleArc = (issueId: number) =>
    setExpandedArcs((prev) => {
      const next = new Set(prev)
      if (next.has(issueId)) next.delete(issueId)
      else next.add(issueId)
      return next
    })

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

  // Disk-only pass: links files that appeared in the series folder and clears
  // issues whose file is gone. Never talks to the metadata provider.
  const rescanMutation = useMutation<RescanResult>({
    mutationFn: () => post<RescanResult>(`/series/${id}/rescan`),
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
                ...ACTION_BUTTON,
                cursor: markAllWantedMutation.isPending ? 'wait' : 'pointer',
                opacity: (!issues || issues.every(i => !['unknown', 'failed'].includes(i.status))) ? 0.4 : 1,
              }}
            >
              {markAllWantedMutation.isPending ? 'Marking…' : 'Mark all as Wanted'}
            </button>
            <button
              onClick={() => rescanMutation.mutate()}
              disabled={rescanMutation.isPending}
              title="Check this series' folder for files added or deleted outside PullBox"
              style={{
                ...ACTION_BUTTON,
                cursor: rescanMutation.isPending ? 'wait' : 'pointer',
              }}
            >
              <HardDrive size={12} />
              {rescanMutation.isPending ? 'Scanning…' : 'Re-scan Files'}
            </button>
            <button
              onClick={() => syncMutation.mutate()}
              disabled={syncMutation.isPending}
              title="Refresh metadata and the issue list from Metron (ComicVine as fallback)"
              style={{
                ...ACTION_BUTTON,
                cursor: syncMutation.isPending ? 'wait' : 'pointer',
              }}
            >
              <RefreshCw size={12} />
              {syncMutation.isPending ? 'Syncing…' : 'Sync Series'}
            </button>
          </div>
        </div>

        {rescanMutation.data && !rescanMutation.isPending && (
          <div style={{ color: 'var(--color-muted)', fontSize: '0.8rem', marginBottom: '12px' }}>
            {rescanMutation.data.message}
            {rescanMutation.data.unmatched_files.length > 0 && (
              <> — {rescanMutation.data.unmatched_files.length} file(s) matched no issue.</>
            )}
          </div>
        )}

        {rescanMutation.isError && (
          <div
            style={{
              color: 'var(--color-status-failed)',
              fontSize: '0.8rem',
              marginBottom: '12px',
            }}
          >
            Re-scan failed: {(rescanMutation.error as Error).message}
          </div>
        )}

        {(issuesLoading || syncMutation.isPending) && (
          <div style={{ color: 'var(--color-muted)', fontSize: '0.875rem' }}>
            {syncMutation.isPending ? 'Syncing series metadata and issues…' : 'Loading issues…'}
          </div>
        )}

        {!issuesLoading && !syncMutation.isPending && sortedIssues.length === 0 && (
          <div style={{ color: 'var(--color-muted)', fontSize: '0.875rem' }}>
            No issues found.
          </div>
        )}

        {sortedIssues.length > 0 && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
            {sortedIssues.map((issue) => {
              const hasArcs = issue.arcs && issue.arcs.length > 0
              const isExpanded = expandedArcs.has(issue.id)
              return (
                <div
                  key={issue.id}
                  style={{
                    borderRadius: '6px',
                    background: 'var(--color-surface)',
                    border: '1px solid var(--color-border)',
                    overflow: 'hidden',
                  }}
                >
                  <div
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: '12px',
                      padding: '8px 12px',
                    }}
                  >
                    <button
                      onClick={() => toggleArc(issue.id)}
                      title="Show story arcs"
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: '10px',
                        flex: 1,
                        minWidth: 0,
                        background: 'none',
                        border: 'none',
                        padding: 0,
                        cursor: 'pointer',
                        textAlign: 'left',
                      }}
                    >
                      {isExpanded ? (
                        <ChevronDown size={14} style={{ color: 'var(--color-muted)', flexShrink: 0 }} />
                      ) : (
                        <ChevronRight size={14} style={{ color: 'var(--color-muted)', flexShrink: 0 }} />
                      )}
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
                          minWidth: 0,
                          fontSize: '0.875rem',
                          color: 'var(--color-text)',
                          overflow: 'hidden',
                          textOverflow: 'ellipsis',
                          whiteSpace: 'nowrap',
                        }}
                      >
                        {issue.title || `Issue #${issue.issue_number}`}
                      </span>
                    </button>
                    {hasArcs && (
                      <span
                        title={issue.arcs.map((a) => a.name).join(', ')}
                        style={{
                          display: 'flex',
                          alignItems: 'center',
                          gap: '4px',
                          flexShrink: 0,
                          padding: '2px 8px',
                          borderRadius: '10px',
                          fontSize: '0.7rem',
                          fontWeight: 600,
                          background: 'color-mix(in srgb, var(--color-accent) 15%, transparent)',
                          border: '1px solid color-mix(in srgb, var(--color-accent) 40%, transparent)',
                          color: 'var(--color-accent)',
                        }}
                      >
                        <Layers size={11} />
                        {issue.arcs.length === 1 ? issue.arcs[0].name : `${issue.arcs.length} arcs`}
                      </span>
                    )}
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
                  {isExpanded && (
                    <div style={{ borderTop: '1px solid var(--color-border)', background: 'var(--color-bg)' }}>
                      <ArcPanel issueId={issue.id} seriesId={id} />
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}
