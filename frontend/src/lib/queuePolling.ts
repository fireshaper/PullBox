// Adaptive refresh for views that show download-queue state. Stand-in for the
// /api/events SSE stream the design calls for: fast while something is moving,
// slow when the queue is idle, so an open tab isn't hammering the API all day.

import { parseServerTime } from './time'

export const ACTIVE_POLL_MS = 5_000
export const IDLE_POLL_MS = 30_000

/** Job states that change within seconds (search → grab) or on the next
 *  download-client poll. */
const ACTIVE_JOB_STATUSES = new Set(['searching', 'pending', 'downloading'])

/** How long a freshly queued job counts as about to start. Enqueue and Retry
 *  hand the job straight to run_job_now, so it flips to 'searching' almost at
 *  once; this window covers that gap. Not keyed on next_attempt_at being due —
 *  scheduled retries sit due until the daily sweep, which would keep a tab
 *  polling fast for hours. */
const JUST_QUEUED_MS = 2 * 60_000

export function isJobActive(job: { status: string; updated_at: string }, now = Date.now()) {
  if (ACTIVE_JOB_STATUSES.has(job.status)) return true
  return job.status === 'queued' && now - parseServerTime(job.updated_at) < JUST_QUEUED_MS
}

export function queuePollInterval(jobs: { status: string; updated_at: string }[] | undefined) {
  const now = Date.now()
  return jobs?.some((j) => isJobActive(j, now)) ? ACTIVE_POLL_MS : IDLE_POLL_MS
}

/** Delay before a follow-up refetch after enqueue/retry, for views that can't
 *  see 'queued' jobs themselves: long enough for run_job_now to have moved the
 *  job to 'searching', which the adaptive interval then picks up. */
export const FOLLOW_UP_REFETCH_MS = 3_000
