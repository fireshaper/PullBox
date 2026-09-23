// One colour per status word, shared by every view. Issue statuses (wanted,
// downloading, …) and DownloadJob statuses (queued, searching, …) never collide,
// so a single map covers both.

const STATUS_COLOR: Record<string, string> = {
  // Issue.status
  wanted: 'var(--color-status-wanted)',
  downloading: 'var(--color-status-downloading)',
  downloaded: 'var(--color-status-downloaded)',
  skipped: 'var(--color-status-skipped)',
  failed: 'var(--color-status-failed)',
  unknown: 'var(--color-muted)',
  // DownloadJob.status (downloading/failed shared with the above)
  queued: 'var(--color-muted)',
  searching: 'var(--color-status-downloading)',
  pending: 'var(--color-status-wanted)',
  completed: 'var(--color-status-downloaded)',
}

export function statusColor(status: string | null | undefined): string {
  return (status && STATUS_COLOR[status]) || 'var(--color-muted)'
}
