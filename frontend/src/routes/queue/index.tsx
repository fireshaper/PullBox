import { createFileRoute } from '@tanstack/react-router'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { del, get, post } from '../../api/client'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from '../../components/ui/alert-dialog'
import { Skeleton } from '../../components/ui/skeleton'

// ── Types ─────────────────────────────────────────────────────────────────────

type IssueSummary = {
  id: number
  issue_number: string
  title: string | null
  cover_url: string | null
  status: string
}

type SeriesSummary = {
  id: number
  title: string
  publisher: string | null
}

type DownloadJob = {
  id: number
  issue_id: number
  status: string
  attempts: number
  last_attempt_at: string | null
  next_attempt_at: string | null
  result_title: string | null
  created_at: string
  updated_at: string
  issue: IssueSummary | null
  series: SeriesSummary | null
}

// ── Route definition ──────────────────────────────────────────────────────────

export const Route = createFileRoute('/queue/')({
  component: QueuePage,
})

// ── Helpers ───────────────────────────────────────────────────────────────────

const JOB_STATUS_COLORS: Record<string, string> = {
  queued: 'var(--color-muted)',
  searching: 'var(--color-status-downloading)',
  pending: 'var(--color-status-wanted)',
  downloading: 'var(--color-status-downloading)',
  completed: 'var(--color-status-downloaded)',
  failed: 'var(--color-status-failed)',
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

// ── Sub-components ────────────────────────────────────────────────────────────

function RetryButton({ jobId }: { jobId: number }) {
  const queryClient = useQueryClient()
  const { mutate, isPending } = useMutation({
    mutationFn: () => post(`/queue/retry/${jobId}`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['queue'] }),
  })

  return (
    <button
      onClick={() => mutate()}
      disabled={isPending}
      style={{
        fontSize: '0.72rem',
        padding: '3px 8px',
        borderRadius: '4px',
        background: 'var(--color-accent)',
        color: '#fff',
        border: 'none',
        cursor: isPending ? 'wait' : 'pointer',
        opacity: isPending ? 0.7 : 1,
        whiteSpace: 'nowrap',
      }}
    >
      {isPending ? 'Retrying…' : 'Retry'}
    </button>
  )
}

function RemoveButton({ jobId }: { jobId: number }) {
  const queryClient = useQueryClient()
  const { mutate } = useMutation({
    mutationFn: () => del(`/queue/${jobId}`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['queue'] }),
  })

  return (
    <AlertDialog>
      <AlertDialogTrigger asChild>
        <button
          style={{
            fontSize: '0.72rem',
            padding: '3px 8px',
            borderRadius: '4px',
            background: 'transparent',
            color: 'var(--color-muted)',
            border: '1px solid var(--color-border)',
            cursor: 'pointer',
            whiteSpace: 'nowrap',
          }}
        >
          Remove
        </button>
      </AlertDialogTrigger>
      <AlertDialogContent
        size="sm"
        style={{ background: 'var(--color-surface)', border: '1px solid var(--color-border)' }}
      >
        <AlertDialogHeader>
          <AlertDialogTitle style={{ color: 'var(--color-text)' }}>Remove job?</AlertDialogTitle>
          <AlertDialogDescription style={{ color: 'var(--color-muted)' }}>
            This will permanently delete the download job. This action cannot be undone.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction
            onClick={() => mutate()}
            style={{ background: 'var(--color-status-failed)', color: '#fff', border: 'none' }}
          >
            Remove
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}

// ── Grid column definition ────────────────────────────────────────────────────

const GRID_COLS = '50px 1fr 90px 56px 136px 136px 112px'

// ── Main page ─────────────────────────────────────────────────────────────────

function QueuePage() {
  const { data: jobs, isLoading } = useQuery<DownloadJob[]>({
    queryKey: ['queue'],
    queryFn: () => get<DownloadJob[]>('/queue/'),
    refetchInterval: 30_000,
  })

  return (
    <div className="p-6">
      {/* Header */}
      <div style={{ marginBottom: '24px' }}>
        <h1 className="text-2xl font-bold" style={{ color: 'var(--color-text)', margin: 0 }}>
          Download Queue
        </h1>
        {!isLoading && jobs !== undefined && (
          <p style={{ fontSize: '0.875rem', color: 'var(--color-muted)', marginTop: '4px' }}>
            {jobs.length} {jobs.length === 1 ? 'job' : 'jobs'}
          </p>
        )}
      </div>

      {/* Loading skeleton */}
      {isLoading && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
          {[1, 2, 3].map((i) => (
            <Skeleton key={i} style={{ height: '72px', width: '100%' }} />
          ))}
        </div>
      )}

      {/* Empty state */}
      {!isLoading && jobs && jobs.length === 0 && (
        <div
          style={{
            textAlign: 'center',
            padding: '64px 0',
            color: 'var(--color-muted)',
            fontSize: '0.95rem',
          }}
        >
          Queue is empty — nothing to download
        </div>
      )}

      {/* Queue table */}
      {!isLoading && jobs && jobs.length > 0 && (
        <div>
          {/* Column headers */}
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: GRID_COLS,
              gap: '12px',
              padding: '0 14px 8px',
              fontSize: '0.68rem',
              fontWeight: 600,
              textTransform: 'uppercase',
              letterSpacing: '0.06em',
              color: 'var(--color-muted)',
              borderBottom: '1px solid var(--color-border)',
              marginBottom: '6px',
            }}
          >
            <div />
            <div>Series / Issue</div>
            <div>Status</div>
            <div style={{ textAlign: 'center' }}>Tries</div>
            <div>Last Attempt</div>
            <div>Next Attempt</div>
            <div />
          </div>

          {/* Rows */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
            {jobs.map((job) => (
              <div
                key={job.id}
                style={{
                  display: 'grid',
                  gridTemplateColumns: GRID_COLS,
                  gap: '12px',
                  alignItems: 'center',
                  padding: '10px 14px',
                  background: 'var(--color-surface)',
                  border: '1px solid var(--color-border)',
                  borderRadius: '8px',
                }}
              >
                {/* Cover thumbnail */}
                {job.issue?.cover_url ? (
                  <img
                    src={job.issue.cover_url}
                    alt=""
                    style={{ width: 36, height: 50, objectFit: 'cover', borderRadius: '3px' }}
                  />
                ) : (
                  <div
                    style={{
                      width: 36,
                      height: 50,
                      borderRadius: '3px',
                      background: 'var(--color-border)',
                    }}
                  />
                )}

                {/* Series + Issue */}
                <div style={{ minWidth: 0 }}>
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
                    {job.series?.title ?? 'Unknown Series'}
                  </div>
                  <div style={{ fontSize: '0.75rem', color: 'var(--color-muted)', marginTop: '2px' }}>
                    #{job.issue?.issue_number ?? '?'}
                    {job.issue?.title ? ` — ${job.issue.title}` : ''}
                  </div>
                  {job.result_title && (
                    <div
                      style={{
                        fontSize: '0.68rem',
                        color: 'var(--color-muted)',
                        marginTop: '2px',
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                        whiteSpace: 'nowrap',
                        opacity: 0.7,
                      }}
                      title={job.result_title}
                    >
                      {job.result_title}
                    </div>
                  )}
                </div>

                {/* Status badge */}
                <span
                  style={{
                    fontSize: '0.72rem',
                    fontWeight: 600,
                    textTransform: 'capitalize',
                    color: JOB_STATUS_COLORS[job.status] ?? 'var(--color-muted)',
                  }}
                >
                  {job.status}
                </span>

                {/* Attempts */}
                <span
                  style={{
                    fontSize: '0.8rem',
                    color: 'var(--color-text)',
                    textAlign: 'center',
                  }}
                >
                  {job.attempts}
                </span>

                {/* Last attempt */}
                <span style={{ fontSize: '0.75rem', color: 'var(--color-muted)' }}>
                  {formatDateTime(job.last_attempt_at)}
                </span>

                {/* Next attempt */}
                <span
                  style={{
                    fontSize: '0.75rem',
                    color:
                      job.status === 'failed' && job.next_attempt_at === null
                        ? 'var(--color-status-failed)'
                        : 'var(--color-muted)',
                  }}
                >
                  {job.status === 'failed' && job.next_attempt_at === null
                    ? 'Max retries'
                    : formatDateTime(job.next_attempt_at)}
                </span>

                {/* Actions */}
                <div style={{ display: 'flex', gap: '6px', justifyContent: 'flex-end' }}>
                  {['queued', 'pending', 'failed'].includes(job.status) && <RetryButton jobId={job.id} />}
                  <RemoveButton jobId={job.id} />
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
