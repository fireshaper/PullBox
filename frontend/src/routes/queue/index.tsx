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
import { Cover } from '../../components/cover'
import { ACTIVE_POLL_MS, queuePollInterval } from '../../lib/queuePolling'
import { parseServerTime } from '../../lib/time'
import { StatusText } from '../../components/status-text'
import { Button } from '../../components/ui/button'
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

function formatDateTime(iso: string | null): string {
  if (!iso) return '—'
  return new Date(parseServerTime(iso)).toLocaleString('en-US', {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  })
}

/** The dashboard's activity card shows the same jobs, so keep it in step. */
function invalidateQueueViews(queryClient: ReturnType<typeof useQueryClient>) {
  queryClient.invalidateQueries({ queryKey: ['queue'] })
  queryClient.invalidateQueries({ queryKey: ['dashboard', 'activity'] })
}

// ── Sub-components ────────────────────────────────────────────────────────────

function RetryButton({ jobId }: { jobId: number }) {
  const queryClient = useQueryClient()
  const { mutate, isPending } = useMutation({
    mutationFn: () => post(`/queue/retry/${jobId}`),
    onSuccess: () => invalidateQueueViews(queryClient),
  })

  return (
    <Button size="xs" onClick={() => mutate()} disabled={isPending}>
      {isPending ? 'Retrying…' : 'Retry'}
    </Button>
  )
}

function RemoveButton({ jobId }: { jobId: number }) {
  const queryClient = useQueryClient()
  const { mutate } = useMutation({
    mutationFn: () => del(`/queue/${jobId}`),
    onSuccess: () => invalidateQueueViews(queryClient),
  })

  return (
    <AlertDialog>
      <AlertDialogTrigger asChild>
        <Button size="xs" variant="subtle">
          Remove
        </Button>
      </AlertDialogTrigger>
      <AlertDialogContent size="sm">
        <AlertDialogHeader>
          <AlertDialogTitle>Remove job?</AlertDialogTitle>
          <AlertDialogDescription>
            This will permanently delete the download job. This action cannot be undone.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction variant="destructive" onClick={() => mutate()}>
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
  // dataUpdatedAt is read only so every refetch re-renders: identical data would
  // otherwise skip the render, leaving the Live badge up after the queue goes idle.
  const { data: jobs, isLoading, dataUpdatedAt } = useQuery<DownloadJob[]>({
    queryKey: ['queue'],
    queryFn: () => get<DownloadJob[]>('/queue/'),
    // 5s while a job is searching/grabbing/downloading (or was just queued), 30s
    // otherwise — see lib/queuePolling.ts. Pauses automatically in background tabs.
    refetchInterval: (query) => queuePollInterval(query.state.data),
  })
  const live = dataUpdatedAt > 0 && queuePollInterval(jobs) === ACTIVE_POLL_MS

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
            {live && (
              <span
                title="Refreshing every few seconds while downloads are in progress"
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: '5px',
                  marginLeft: '10px',
                  color: 'var(--color-status-downloading)',
                }}
              >
                <span
                  className="animate-pulse"
                  style={{
                    width: 7,
                    height: 7,
                    borderRadius: '50%',
                    background: 'var(--color-status-downloading)',
                  }}
                />
                Live
              </span>
            )}
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
                <Cover url={job.issue?.cover_url} width={36} height={50} radius={3} />

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
                <StatusText status={job.status} className="text-[0.72rem]" />

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
