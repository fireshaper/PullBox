import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, Download, HardDrive, RefreshCw } from 'lucide-react'
import { type CSSProperties, useState } from 'react'
import { ApiError, get, patch, post } from '../../../api/client'
import { ArcCover, ArcProgress, type ArcListItem } from '../../../components/arcs'

// ── Types ─────────────────────────────────────────────────────────────────────

type ArcIssueRow = {
  id: number
  series_id: number
  series_title: string
  issue_number: string
  title: string | null
  cover_url: string | null
  cover_date: string | null
  store_date: string | null
  status: string
  has_file: boolean
}

type ArcDetail = ArcListItem & {
  description: string | null
  issues: ArcIssueRow[]
}

type SyncResult = {
  members: number
  in_library: number
  added: number
  enqueued: number
  failed: number
  remaining: number
  rate_limited: boolean
  message: string
}

type DownloadResult = { enqueued: number; message: string }

// ── Route ─────────────────────────────────────────────────────────────────────

export const Route = createFileRoute('/arcs/$arcId/')({
  component: ArcDetailPage,
})

// ── Styling ───────────────────────────────────────────────────────────────────

const STATUS_COLORS: Record<string, string> = {
  wanted: 'var(--color-status-wanted)',
  downloading: 'var(--color-status-downloading)',
  downloaded: 'var(--color-status-downloaded)',
  skipped: 'var(--color-status-skipped)',
  failed: 'var(--color-status-failed)',
  unknown: 'var(--color-muted)',
}

const ACTION_BUTTON: CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  gap: 6,
  padding: '5px 12px',
  borderRadius: 6,
  fontSize: '0.8rem',
  fontWeight: 600,
  background: 'var(--color-surface)',
  border: '1px solid var(--color-border)',
  color: 'var(--color-text)',
}

function fmtDate(iso: string | null): string | null {
  if (!iso) return null
  const d = new Date(`${iso}T00:00:00`)
  if (Number.isNaN(d.getTime())) return null
  return d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' })
}

// ── Toggles ───────────────────────────────────────────────────────────────────

function Toggle({
  label,
  hint,
  checked,
  disabled,
  onChange,
}: {
  label: string
  hint: string
  checked: boolean
  disabled?: boolean
  onChange: (next: boolean) => void
}) {
  return (
    <label
      style={{
        display: 'flex',
        alignItems: 'flex-start',
        gap: 8,
        cursor: disabled ? 'default' : 'pointer',
        opacity: disabled ? 0.55 : 1,
      }}
    >
      <input
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={(e) => onChange(e.target.checked)}
        style={{ marginTop: 2, accentColor: 'var(--color-accent)' }}
      />
      <span>
        <span style={{ fontSize: '0.82rem', fontWeight: 600, color: 'var(--color-text)' }}>
          {label}
        </span>
        <span style={{ display: 'block', fontSize: '0.72rem', color: 'var(--color-muted)' }}>
          {hint}
        </span>
      </span>
    </label>
  )
}

// ── Issue row ─────────────────────────────────────────────────────────────────

function IssueRow({ issue, arcId }: { issue: ArcIssueRow; arcId: number }) {
  const navigate = useNavigate()
  const queryClient = useQueryClient()

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
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['arcs', arcId] }),
  })

  const date = fmtDate(issue.store_date) ?? fmtDate(issue.cover_date)
  const canDownload = !['downloaded', 'downloading', 'wanted'].includes(issue.status)

  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 12,
        padding: '8px 12px',
        background: 'var(--color-surface)',
        border: '1px solid var(--color-border)',
        borderRadius: 6,
      }}
    >
      {issue.cover_url ? (
        <img
          src={issue.cover_url}
          alt=""
          style={{ width: 34, height: 48, objectFit: 'cover', borderRadius: 3, flexShrink: 0 }}
        />
      ) : (
        <div
          style={{ width: 34, height: 48, borderRadius: 3, background: 'var(--color-border)', flexShrink: 0 }}
        />
      )}

      <div style={{ flex: 1, minWidth: 0 }}>
        <button
          onClick={() => navigate({ to: '/series/$seriesId', params: { seriesId: String(issue.series_id) } })}
          style={{
            background: 'none',
            border: 'none',
            padding: 0,
            textAlign: 'left',
            cursor: 'pointer',
            fontWeight: 600,
            fontSize: '0.85rem',
            color: 'var(--color-text)',
            maxWidth: '100%',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
            display: 'block',
          }}
        >
          {issue.series_title} #{issue.issue_number}
        </button>
        <div
          style={{
            fontSize: '0.74rem',
            color: 'var(--color-muted)',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}
        >
          {[issue.title, date].filter(Boolean).join(' · ') || '—'}
        </div>
      </div>

      {issue.has_file && (
        <HardDrive size={13} style={{ color: 'var(--color-status-downloaded)', flexShrink: 0 }} />
      )}
      <span
        style={{
          fontSize: '0.72rem',
          fontWeight: 600,
          textTransform: 'capitalize',
          color: STATUS_COLORS[issue.status] ?? 'var(--color-muted)',
          flexShrink: 0,
          width: 78,
          textAlign: 'right',
        }}
      >
        {issue.status}
      </span>

      {canDownload && (
        <button
          onClick={() => downloadMutation.mutate()}
          disabled={downloadMutation.isPending}
          style={{
            padding: '3px 10px',
            borderRadius: 4,
            fontSize: '0.75rem',
            fontWeight: 600,
            border: 'none',
            background: 'var(--color-accent)',
            color: '#fff',
            cursor: downloadMutation.isPending ? 'wait' : 'pointer',
            flexShrink: 0,
          }}
        >
          {downloadMutation.isPending ? '…' : 'Download'}
        </button>
      )}
    </div>
  )
}

// ── Page ──────────────────────────────────────────────────────────────────────

function ArcDetailPage() {
  const { arcId } = Route.useParams()
  const id = Number(arcId)
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [notice, setNotice] = useState<{ text: string; error: boolean } | null>(null)

  const { data: arc, isLoading } = useQuery<ArcDetail>({
    queryKey: ['arcs', id],
    queryFn: () => get<ArcDetail>(`/arcs/${id}`),
  })

  // Prefix invalidation: covers this arc's detail and both list filters, whose
  // counts move whenever a sync or download changes anything here.
  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['arcs'] })

  const flagMutation = useMutation({
    mutationFn: (body: { subscribed?: boolean; auto_download?: boolean }) =>
      patch(`/arcs/${id}`, body),
    onSuccess: invalidate,
  })

  // Sync reaches out to the metadata provider — one lookup per member the library
  // doesn't hold — so it is always an explicit press, never automatic on load.
  const syncMutation = useMutation({
    mutationFn: (download: boolean) =>
      post<SyncResult>(`/arcs/${id}/sync${download ? '?download=true' : ''}`),
    onSuccess: (result) => {
      setNotice({ text: result.message, error: result.rate_limited && result.added === 0 })
      invalidate()
    },
    onError: (e) =>
      setNotice({ text: e instanceof Error ? e.message : 'Sync failed.', error: true }),
  })

  const downloadMutation = useMutation({
    mutationFn: () => post<DownloadResult>(`/arcs/${id}/download-missing`),
    onSuccess: (result) => {
      setNotice({ text: result.message, error: false })
      invalidate()
    },
    onError: (e) =>
      setNotice({ text: e instanceof Error ? e.message : 'Could not queue downloads.', error: true }),
  })

  if (isLoading) {
    return <div className="p-6" style={{ color: 'var(--color-muted)' }}>Loading arc…</div>
  }
  if (!arc) {
    return <div className="p-6" style={{ color: 'var(--color-status-failed)' }}>Story arc not found.</div>
  }

  const missingCount = arc.issues.filter(
    (i) => !['downloaded', 'downloading', 'skipped'].includes(i.status),
  ).length

  return (
    <div className="p-6">
      <button
        onClick={() => navigate({ to: '/arcs', search: { filter: 'all' } })}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 6,
          background: 'none',
          border: 'none',
          padding: 0,
          marginBottom: 16,
          cursor: 'pointer',
          color: 'var(--color-muted)',
          fontSize: '0.8rem',
        }}
      >
        <ArrowLeft size={14} /> Story Arcs
      </button>

      {/* Header */}
      <div style={{ display: 'flex', gap: 18, marginBottom: 20 }}>
        <ArcCover url={arc.cover_url} name={arc.name} size={110} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <h1 style={{ fontSize: '1.5rem', fontWeight: 700, color: 'var(--color-text)' }}>
            {arc.name}
          </h1>
          <div style={{ fontSize: '0.8rem', color: 'var(--color-muted)', margin: '4px 0 12px' }}>
            {[
              arc.publisher,
              arc.total != null ? `${arc.total} issues in this arc` : 'Size unknown until synced',
              `${arc.series_count} series in your library`,
            ]
              .filter(Boolean)
              .join(' · ')}
          </div>

          <div style={{ maxWidth: 420, marginBottom: 14 }}>
            <ArcProgress arc={arc} />
          </div>

          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 24, marginBottom: 14 }}>
            <Toggle
              label="Subscribe to this arc"
              hint="PullBox fills in the issues you're missing, across every series."
              checked={arc.subscribed}
              disabled={flagMutation.isPending}
              onChange={(next) => flagMutation.mutate({ subscribed: next })}
            />
            <Toggle
              label="Download automatically"
              hint="Queue each newly-found issue instead of just marking it wanted."
              checked={arc.auto_download}
              disabled={flagMutation.isPending || !arc.subscribed}
              onChange={(next) => flagMutation.mutate({ auto_download: next })}
            />
          </div>

          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
            <button
              onClick={() => syncMutation.mutate(false)}
              disabled={syncMutation.isPending}
              style={{ ...ACTION_BUTTON, cursor: syncMutation.isPending ? 'wait' : 'pointer' }}
            >
              <RefreshCw size={13} />
              {syncMutation.isPending ? 'Finding issues…' : 'Find Missing Issues'}
            </button>
            <button
              onClick={() => downloadMutation.mutate()}
              disabled={downloadMutation.isPending || missingCount === 0}
              style={{
                ...ACTION_BUTTON,
                background: missingCount === 0 ? 'var(--color-surface)' : 'var(--color-accent)',
                border: missingCount === 0 ? '1px solid var(--color-border)' : 'none',
                color: missingCount === 0 ? 'var(--color-muted)' : '#fff',
                cursor:
                  downloadMutation.isPending
                    ? 'wait'
                    : missingCount === 0
                      ? 'default'
                      : 'pointer',
              }}
            >
              <Download size={13} />
              {downloadMutation.isPending
                ? 'Queuing…'
                : missingCount === 0
                  ? 'Nothing to download'
                  : `Download ${missingCount} Missing`}
            </button>
          </div>
        </div>
      </div>

      {notice && (
        <div
          style={{
            padding: '9px 12px',
            borderRadius: 6,
            marginBottom: 16,
            fontSize: '0.8rem',
            background: 'var(--color-surface)',
            border: `1px solid ${notice.error ? 'var(--color-status-failed)' : 'var(--color-border)'}`,
            color: notice.error ? 'var(--color-status-failed)' : 'var(--color-text)',
          }}
        >
          {notice.text}
        </div>
      )}

      {arc.description && (
        <div
          style={{
            fontSize: '0.82rem',
            color: 'var(--color-muted)',
            lineHeight: 1.6,
            marginBottom: 20,
            maxWidth: 800,
          }}
          // Provider descriptions are HTML fragments, same as the series page.
          dangerouslySetInnerHTML={{ __html: arc.description }}
        />
      )}

      {/* Issues */}
      <h2
        style={{
          fontSize: '0.95rem',
          fontWeight: 700,
          color: 'var(--color-text)',
          marginBottom: 10,
        }}
      >
        Issues in this arc{' '}
        <span style={{ fontWeight: 400, color: 'var(--color-muted)', fontSize: '0.8rem' }}>
          ({arc.issues.length} tracked
          {arc.total != null && arc.total > arc.issues.length
            ? ` of ${arc.total} — run Find Missing Issues to pull in the rest`
            : ''}
          )
        </span>
      </h2>

      {arc.issues.length === 0 ? (
        <div
          style={{
            textAlign: 'center',
            padding: '40px 16px',
            color: 'var(--color-muted)',
            fontSize: '0.85rem',
            lineHeight: 1.6,
          }}
        >
          No issues from this arc are in your library yet.
          <br />
          Run <strong>Find Missing Issues</strong> to look the arc up and add what it contains.
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
          {arc.issues.map((issue) => (
            <IssueRow key={issue.id} issue={issue} arcId={id} />
          ))}
        </div>
      )}
    </div>
  )
}
