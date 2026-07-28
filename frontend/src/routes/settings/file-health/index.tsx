import { createFileRoute, Link } from '@tanstack/react-router'
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { del, get, post } from '../../../api/client'
import { Input } from '../../../components/ui/input'
import { Checkbox } from '../../../components/ui/checkbox'
import { Skeleton } from '../../../components/ui/skeleton'

// ── Route definition ──────────────────────────────────────────────────────────

export const Route = createFileRoute('/settings/file-health/')({
  component: FileHealthPage,
})

// ── Types ─────────────────────────────────────────────────────────────────────

type FileIssue = {
  id: number
  issue_id: number | null
  file_path: string
  kind: string
  severity: 'error' | 'warning'
  detail: string
  size_bytes: number | null
  detected_at: string
  series_id: number | null
  series_title: string | null
  issue_number: string | null
}

type Summary = {
  total: number
  errors: number
  warnings: number
  by_kind: Record<string, number>
}

type FileHealth = {
  summary: Summary
  issues: FileIssue[]
  last_scan_at: string | null
  last_scan_message: string | null
  scanned_root: string
}

type ScanResult = FileHealth & { files_scanned: number }

type Recheck = { resolved: boolean; issue: FileIssue | null }

// Must stay in sync with the kind constants in services/file_health.py.
const KIND_LABELS: Record<string, string> = {
  missing: 'Missing file',
  wrong_format: 'Wrong format',
  corrupt: 'Corrupt archive',
  empty: 'Empty file',
  unknown_format: 'Unrecognized',
  unreadable: 'Unreadable',
  no_images: 'No pages',
}

const labelStyle = {
  fontSize: '0.8rem',
  color: 'var(--color-muted)' as const,
  display: 'block' as const,
  marginBottom: '5px',
}

function kindLabel(kind: string) {
  return KIND_LABELS[kind] ?? kind
}

function severityColor(severity: string) {
  return severity === 'warning'
    ? 'var(--color-status-wanted)'
    : 'var(--color-status-failed)'
}

function formatBytes(bytes: number | null) {
  if (bytes === null) return null
  if (bytes === 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB']
  const i = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1)
  return `${(bytes / 1024 ** i).toFixed(i === 0 ? 0 : 1)} ${units[i]}`
}

function formatDateTime(iso: string | null) {
  if (!iso) return null
  return new Date(iso).toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  })
}

// ── Summary chips (double as kind filters) ────────────────────────────────────

function SummaryBar({
  summary,
  active,
  onSelect,
}: {
  summary: Summary
  active: string | null
  onSelect: (kind: string | null) => void
}) {
  const kinds = Object.entries(summary.by_kind).sort((a, b) => b[1] - a[1])

  const chip = (key: string | null, label: string, count: number, color: string) => {
    const selected = active === key
    return (
      <button
        key={label}
        onClick={() => onSelect(selected ? null : key)}
        style={{
          display: 'flex',
          alignItems: 'baseline',
          gap: '6px',
          padding: '5px 11px',
          borderRadius: '999px',
          fontSize: '0.78rem',
          cursor: 'pointer',
          color: selected ? 'var(--color-text)' : 'var(--color-muted)',
          background: selected
            ? `color-mix(in srgb, ${color} 22%, transparent)`
            : 'var(--color-surface)',
          border: `1px solid ${
            selected ? `color-mix(in srgb, ${color} 45%, transparent)` : 'var(--color-border)'
          }`,
        }}
      >
        <span>{label}</span>
        <span style={{ color, fontWeight: 600 }}>{count}</span>
      </button>
    )
  }

  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px', marginBottom: '16px' }}>
      {chip(null, 'All problems', summary.total, 'var(--color-accent)')}
      {kinds.map(([kind, count]) =>
        chip(kind, kindLabel(kind), count, severityColor(kind === 'no_images' ? 'warning' : 'error')),
      )}
    </div>
  )
}

// ── Per-finding actions ───────────────────────────────────────────────────────

const actionButtonStyle = {
  fontSize: '0.75rem',
  padding: '4px 10px',
  borderRadius: '5px',
  background: 'transparent',
  border: '1px solid var(--color-border)',
  cursor: 'pointer',
  whiteSpace: 'nowrap' as const,
}

function FindingActions({ finding }: { finding: FileIssue }) {
  const queryClient = useQueryClient()
  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['file-health'] })

  const recheck = useMutation({
    mutationFn: () => post<Recheck>(`/file-health/${finding.id}/recheck`),
    onSuccess: invalidate,
  })
  const dismiss = useMutation({
    mutationFn: () => del(`/file-health/${finding.id}`),
    onSuccess: invalidate,
  })

  // A re-check that still finds the problem is silent otherwise — the row just
  // re-renders identically, which reads as "nothing happened".
  const stillBroken = recheck.isSuccess && recheck.data?.resolved === false

  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
      {stillBroken && (
        <span style={{ fontSize: '0.72rem', color: 'var(--color-status-failed)' }}>
          Still failing
        </span>
      )}
      <button
        onClick={() => recheck.mutate()}
        disabled={recheck.isPending}
        title="Re-inspect this file, including a full CRC check"
        style={{
          ...actionButtonStyle,
          color: 'var(--color-text)',
          cursor: recheck.isPending ? 'wait' : 'pointer',
        }}
      >
        {recheck.isPending ? 'Checking…' : 'Re-check'}
      </button>
      <button
        onClick={() => dismiss.mutate()}
        disabled={dismiss.isPending}
        title="Hide this finding until the next scan"
        style={{ ...actionButtonStyle, color: 'var(--color-muted)' }}
      >
        Dismiss
      </button>
    </div>
  )
}

// ── One finding ───────────────────────────────────────────────────────────────

function FindingRow({ finding }: { finding: FileIssue }) {
  const color = severityColor(finding.severity)
  const size = formatBytes(finding.size_bytes)

  return (
    <div
      style={{
        padding: '12px 14px',
        borderRadius: '7px',
        background: 'var(--color-surface)',
        border: '1px solid var(--color-border)',
        borderLeft: `3px solid ${color}`,
      }}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '10px',
          flexWrap: 'wrap',
          marginBottom: '6px',
        }}
      >
        <span
          style={{
            fontSize: '0.7rem',
            fontWeight: 600,
            textTransform: 'uppercase',
            letterSpacing: '0.04em',
            color,
            background: `color-mix(in srgb, ${color} 14%, transparent)`,
            padding: '2px 8px',
            borderRadius: '4px',
          }}
        >
          {kindLabel(finding.kind)}
        </span>

        {finding.series_id !== null ? (
          <Link
            to="/series/$seriesId"
            params={{ seriesId: String(finding.series_id) }}
            style={{
              fontSize: '0.875rem',
              fontWeight: 500,
              color: 'var(--color-text)',
              textDecoration: 'none',
            }}
          >
            {finding.series_title} #{finding.issue_number}
          </Link>
        ) : (
          <span style={{ fontSize: '0.8rem', color: 'var(--color-muted)', fontStyle: 'italic' }}>
            Not tracked in your library
          </span>
        )}

        {size && (
          <span style={{ fontSize: '0.72rem', color: 'var(--color-muted)' }}>{size}</span>
        )}

        <div style={{ marginLeft: 'auto' }}>
          <FindingActions finding={finding} />
        </div>
      </div>

      <code
        style={{
          display: 'block',
          fontSize: '0.76rem',
          color: 'var(--color-muted)',
          wordBreak: 'break-all',
          marginBottom: '5px',
        }}
      >
        {finding.file_path}
      </code>

      <p style={{ fontSize: '0.8rem', color: 'var(--color-text)', margin: 0, lineHeight: 1.5 }}>
        {finding.detail}
      </p>
    </div>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────

function FileHealthPage() {
  const queryClient = useQueryClient()
  const [path, setPath] = useState('')
  const [deep, setDeep] = useState(false)
  const [filter, setFilter] = useState<string | null>(null)

  const { data, isLoading, isError, error } = useQuery<FileHealth>({
    queryKey: ['file-health'],
    queryFn: () => get<FileHealth>('/file-health'),
  })

  const scan = useMutation({
    mutationFn: () =>
      post<ScanResult>('/file-health/scan', { path: path.trim() || null, deep }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['file-health'] }),
  })

  const summary = data?.summary
  const findings = (data?.issues ?? []).filter((f) => filter === null || f.kind === filter)
  const scanned = data?.last_scan_at !== null && data?.last_scan_at !== undefined

  return (
    <div className="p-6" style={{ maxWidth: '860px' }}>
      {/* Header */}
      <div style={{ marginBottom: '24px' }}>
        <h1 className="text-2xl font-bold" style={{ color: 'var(--color-text)', margin: 0 }}>
          File Health
        </h1>
        <p style={{ fontSize: '0.875rem', color: 'var(--color-muted)', marginTop: '4px' }}>
          Checks the comic files in your library for problems a reader will trip over:
          archives whose contents don't match their extension (the cause of{' '}
          <code>File is not a zip file</code>), truncated or 0-byte downloads, and files
          PullBox tracks that are no longer on disk.
        </p>
      </div>

      {/* Scan controls */}
      <div
        style={{
          padding: '16px',
          borderRadius: '8px',
          background: 'var(--color-surface)',
          border: '1px solid var(--color-border)',
          marginBottom: '20px',
        }}
      >
        <label style={labelStyle}>Folder to scan (path on the server)</label>
        <div style={{ display: 'flex', gap: '10px' }}>
          <Input
            value={path}
            onChange={(e) => setPath(e.target.value)}
            placeholder={data?.scanned_root || '/comics'}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !scan.isPending) scan.mutate()
            }}
          />
          <button
            onClick={() => scan.mutate()}
            disabled={scan.isPending}
            style={{
              fontSize: '0.875rem',
              padding: '6px 16px',
              borderRadius: '6px',
              background: scan.isPending ? 'var(--color-border)' : 'var(--color-accent)',
              color: '#fff',
              border: 'none',
              cursor: scan.isPending ? 'wait' : 'pointer',
              fontWeight: 500,
              whiteSpace: 'nowrap',
            }}
          >
            {scan.isPending ? 'Scanning…' : 'Scan now'}
          </button>
        </div>
        <p style={{ fontSize: '0.72rem', color: 'var(--color-muted)', marginTop: '5px' }}>
          Leave blank to scan your library path. Files PullBox tracks are always checked,
          even if they live outside this folder.
        </p>

        <label
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '9px',
            marginTop: '12px',
            cursor: 'pointer',
          }}
        >
          <Checkbox checked={deep} onCheckedChange={(c) => setDeep(c === true)} />
          <span style={{ fontSize: '0.82rem', color: 'var(--color-text)' }}>
            Deep scan (verify every page)
          </span>
        </label>
        <p
          style={{
            fontSize: '0.72rem',
            color: 'var(--color-muted)',
            margin: '4px 0 0 27px',
          }}
        >
          Catches silent corruption inside an archive that still opens. Reads every byte of
          every file — slow on a large library.
        </p>

        {scan.error && (
          <p style={{ fontSize: '0.8rem', color: 'var(--color-status-failed)', marginTop: '10px' }}>
            {scan.error instanceof Error ? scan.error.message : 'Scan failed'}
          </p>
        )}

        {data?.last_scan_at && (
          <p style={{ fontSize: '0.75rem', color: 'var(--color-muted)', marginTop: '10px' }}>
            Last scan {formatDateTime(data.last_scan_at)}
            {data.last_scan_message ? ` — ${data.last_scan_message}` : ''}
          </p>
        )}
      </div>

      {isError && (
        <div
          style={{
            padding: '16px',
            borderRadius: '8px',
            background: 'color-mix(in srgb, var(--color-status-failed) 12%, transparent)',
            border: '1px solid color-mix(in srgb, var(--color-status-failed) 30%, transparent)',
            color: 'var(--color-status-failed)',
            fontSize: '0.875rem',
          }}
        >
          Failed to load results: {error instanceof Error ? error.message : 'Unknown error'}
        </div>
      )}

      {isLoading && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
          {[1, 2, 3].map((i) => (
            <Skeleton key={i} style={{ height: '82px', width: '100%' }} />
          ))}
        </div>
      )}

      {!isLoading && !isError && summary && (
        <>
          {summary.total > 0 && (
            <SummaryBar summary={summary} active={filter} onSelect={setFilter} />
          )}

          {summary.total === 0 ? (
            <div
              style={{
                padding: '28px',
                textAlign: 'center',
                borderRadius: '8px',
                background: 'var(--color-surface)',
                border: '1px solid var(--color-border)',
              }}
            >
              <p
                style={{
                  fontSize: '0.9rem',
                  color: scanned ? 'var(--color-status-downloaded)' : 'var(--color-muted)',
                  margin: 0,
                }}
              >
                {scanned ? 'No problems found.' : 'No scan has been run yet.'}
              </p>
              {!scanned && (
                <p
                  style={{
                    fontSize: '0.8rem',
                    color: 'var(--color-muted)',
                    marginTop: '6px',
                    marginBottom: 0,
                  }}
                >
                  Run a scan to check your library.
                </p>
              )}
            </div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
              {findings.map((finding) => (
                <FindingRow key={finding.id} finding={finding} />
              ))}
            </div>
          )}
        </>
      )}
    </div>
  )
}
