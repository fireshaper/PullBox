import { createFileRoute } from '@tanstack/react-router'
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { get, post } from '../../../api/client'
import { Input } from '../../../components/ui/input'
import { Checkbox } from '../../../components/ui/checkbox'

// ── Route definition ──────────────────────────────────────────────────────────

export const Route = createFileRoute('/settings/library-import/')({
  component: LibraryImportPage,
})

// ── Types ─────────────────────────────────────────────────────────────────────

type ScannedFile = { file_path: string; issue_number: string | null }
type ScannedSeries = {
  title: string
  year: number | null
  file_count: number
  files: ScannedFile[]
}
type ScanResponse = { root: string; unparsed_count: number; series: ScannedSeries[] }

type ImportResponse = {
  series_queued: number
  files_queued: number
  errors: string[]
}

type ImportStatus = {
  pending_files: number
  series_pending: number
  synced_files: number
  unmatched_files: number
  no_match_files: number
}

const labelStyle = {
  fontSize: '0.8rem',
  color: 'var(--color-muted)' as const,
  display: 'block' as const,
  marginBottom: '5px',
}

// ── Background-sync progress panel ────────────────────────────────────────────

// Persistent view of how far the background ComicVine backfill has progressed.
// Driven by the polling /status endpoint, so it survives page reloads and shows
// progress even when the import happened in an earlier session. Renders nothing
// until something has been imported.
function ImportProgress() {
  const { data } = useQuery<ImportStatus>({
    queryKey: ['library-import-status'],
    queryFn: () => get<ImportStatus>('/library-import/status'),
    // Poll while there's still work; stop once the backfill is drained.
    refetchInterval: (query) =>
      (query.state.data?.pending_files ?? 0) > 0 ? 5_000 : false,
  })
  if (!data) return null

  const processed = data.synced_files + data.unmatched_files + data.no_match_files
  const total = processed + data.pending_files
  if (total === 0) return null // nothing has ever been imported

  const pct = total === 0 ? 0 : Math.round((processed / total) * 100)
  const done = data.pending_files === 0

  return (
    <div
      style={{
        marginTop: '20px',
        padding: '16px',
        borderRadius: '8px',
        background: 'var(--color-surface)',
        border: '1px solid var(--color-border)',
      }}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'baseline',
          justifyContent: 'space-between',
          marginBottom: '8px',
        }}
      >
        <span style={{ fontSize: '0.9rem', color: 'var(--color-text)', fontWeight: 600 }}>
          {done ? 'ComicVine sync complete' : 'Syncing ComicVine metadata'}
        </span>
        <span style={{ fontSize: '0.82rem', color: 'var(--color-muted)' }}>
          {processed} / {total} issues ({pct}%)
        </span>
      </div>

      {/* Progress bar */}
      <div
        style={{
          height: '8px',
          borderRadius: '4px',
          background: 'var(--color-border)',
          overflow: 'hidden',
        }}
      >
        <div
          style={{
            height: '100%',
            width: `${pct}%`,
            background: done ? 'var(--color-status-downloaded, var(--color-accent))' : 'var(--color-accent)',
            transition: 'width 0.4s ease',
          }}
        />
      </div>

      <div style={{ fontSize: '0.8rem', color: 'var(--color-muted)', marginTop: '10px' }}>
        {data.pending_files} pending across {data.series_pending} series · {data.synced_files}{' '}
        synced · {data.unmatched_files} unmatched · {data.no_match_files} no ComicVine match
      </div>
    </div>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────

function LibraryImportPage() {
  const queryClient = useQueryClient()
  const [path, setPath] = useState('')
  // Which scanned series (by index) are selected for import. Default: all.
  const [included, setIncluded] = useState<boolean[]>([])

  const scan = useMutation({
    mutationFn: (p: string) => post<ScanResponse>('/library-import/scan', { path: p }),
    onSuccess: (res) => {
      setIncluded(res.series.map(() => true))
    },
  })

  const doImport = useMutation({
    mutationFn: (req: { series: unknown[] }) => post<ImportResponse>('/library-import/import', req),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['series'] })
      queryClient.invalidateQueries({ queryKey: ['library-import-status'] })
    },
  })

  const scanned = scan.data?.series ?? []
  const selectedCount = scanned.filter((_, i) => included[i]).length
  const selectedFiles = scanned.reduce((n, s, i) => (included[i] ? n + s.file_count : n), 0)

  function runImport() {
    const req = {
      series: scanned
        .filter((_, i) => included[i])
        .map((s) => ({ title: s.title, year: s.year, files: s.files })),
    }
    doImport.mutate(req)
  }

  return (
    <div className="p-6" style={{ maxWidth: '760px' }}>
      {/* Header */}
      <div style={{ marginBottom: '24px' }}>
        <h1 className="text-2xl font-bold" style={{ color: 'var(--color-text)', margin: 0 }}>
          Import Library
        </h1>
        <p style={{ fontSize: '0.875rem', color: 'var(--color-muted)', marginTop: '4px' }}>
          Scan an existing comic folder and import it. Series and issues are added to your library
          immediately from the folder names; PullBox then backfills ComicVine metadata (covers,
          dates, descriptions) in the background — about 10 issues every 5 minutes to respect API
          rate limits.
        </p>
      </div>

      {/* Persistent background-sync progress (survives reloads) */}
      <ImportProgress />

      {/* Path input */}
      <div>
        <label style={labelStyle}>Library Folder (path on the server)</label>
        <div style={{ display: 'flex', gap: '10px' }}>
          <Input
            value={path}
            onChange={(e) => setPath(e.target.value)}
            placeholder="/comics or D:\Comics"
            onKeyDown={(e) => {
              if (e.key === 'Enter' && path.trim()) scan.mutate(path.trim())
            }}
          />
          <button
            onClick={() => scan.mutate(path.trim())}
            disabled={!path.trim() || scan.isPending}
            style={{
              fontSize: '0.875rem',
              padding: '6px 16px',
              borderRadius: '6px',
              background: path.trim() && !scan.isPending ? 'var(--color-accent)' : 'var(--color-border)',
              color: '#fff',
              border: 'none',
              cursor: path.trim() && !scan.isPending ? 'pointer' : 'not-allowed',
              fontWeight: 500,
              whiteSpace: 'nowrap',
            }}
          >
            {scan.isPending ? 'Scanning…' : 'Scan'}
          </button>
        </div>
        {scan.error && (
          <p style={{ fontSize: '0.8rem', color: 'var(--color-status-failed)', marginTop: '6px' }}>
            {scan.error instanceof Error ? scan.error.message : 'Scan failed'}
          </p>
        )}
      </div>

      {/* Scan results */}
      {scan.data && (
        <div style={{ marginTop: '24px' }}>
          <div
            style={{
              display: 'flex',
              alignItems: 'baseline',
              justifyContent: 'space-between',
              marginBottom: '10px',
            }}
          >
            <p style={{ fontSize: '0.8rem', color: 'var(--color-muted)', margin: 0 }}>
              Found {scanned.length} series in <code>{scan.data.root}</code>
              {scan.data.unparsed_count > 0 && ` · ${scan.data.unparsed_count} file(s) couldn't be parsed`}
            </p>
            {scanned.length > 0 && (
              <div style={{ display: 'flex', gap: '12px' }}>
                <button
                  onClick={() => setIncluded(scanned.map(() => true))}
                  style={{ background: 'none', border: 'none', color: 'var(--color-accent)', cursor: 'pointer', fontSize: '0.8rem' }}
                >
                  Select all
                </button>
                <button
                  onClick={() => setIncluded(scanned.map(() => false))}
                  style={{ background: 'none', border: 'none', color: 'var(--color-muted)', cursor: 'pointer', fontSize: '0.8rem' }}
                >
                  Clear
                </button>
              </div>
            )}
          </div>

          {scanned.length === 0 ? (
            <p style={{ fontSize: '0.875rem', color: 'var(--color-muted)' }}>
              No comic files found in that folder.
            </p>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
              {scanned.map((s, i) => (
                <label
                  key={`${s.title}-${s.year}-${i}`}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: '12px',
                    padding: '9px 12px',
                    borderRadius: '6px',
                    background: 'var(--color-surface)',
                    border: '1px solid var(--color-border)',
                    cursor: 'pointer',
                    opacity: included[i] ? 1 : 0.55,
                  }}
                >
                  <Checkbox
                    checked={included[i] ?? false}
                    onCheckedChange={(checked) =>
                      setIncluded((prev) => {
                        const copy = [...prev]
                        copy[i] = checked === true
                        return copy
                      })
                    }
                  />
                  <span style={{ fontSize: '0.875rem', color: 'var(--color-text)', fontWeight: 500, flex: 1 }}>
                    {s.title}
                    {s.year ? ` (${s.year})` : ''}
                  </span>
                  <span style={{ fontSize: '0.72rem', color: 'var(--color-muted)' }}>
                    {s.file_count} file{s.file_count === 1 ? '' : 's'}
                  </span>
                </label>
              ))}
            </div>
          )}

          {/* Import action */}
          {scanned.length > 0 && (
            <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginTop: '20px' }}>
              <button
                onClick={runImport}
                disabled={selectedCount === 0 || doImport.isPending}
                style={{
                  fontSize: '0.875rem',
                  padding: '6px 16px',
                  borderRadius: '6px',
                  background:
                    selectedCount > 0 && !doImport.isPending ? 'var(--color-accent)' : 'var(--color-border)',
                  color: '#fff',
                  border: 'none',
                  cursor: selectedCount > 0 && !doImport.isPending ? 'pointer' : 'not-allowed',
                  fontWeight: 500,
                }}
              >
                {doImport.isPending
                  ? 'Importing…'
                  : `Import ${selectedCount} series (${selectedFiles} files)`}
              </button>
              {doImport.error && (
                <span style={{ fontSize: '0.8rem', color: 'var(--color-status-failed)' }}>
                  {doImport.error instanceof Error ? doImport.error.message : 'Import failed'}
                </span>
              )}
            </div>
          )}
        </div>
      )}

      {/* Import summary */}
      {doImport.data && (
        <div
          style={{
            marginTop: '20px',
            padding: '16px',
            borderRadius: '8px',
            background: 'var(--color-surface)',
            border: '1px solid var(--color-border)',
          }}
        >
          <div style={{ fontSize: '0.9rem', color: 'var(--color-text)', fontWeight: 600, marginBottom: '8px' }}>
            Added to library
          </div>
          <ul style={{ fontSize: '0.82rem', color: 'var(--color-muted)', margin: 0, paddingLeft: '18px' }}>
            <li>{doImport.data.series_queued} series added</li>
            <li>{doImport.data.files_queued} issues added</li>
          </ul>
          <p style={{ fontSize: '0.8rem', color: 'var(--color-muted)', marginTop: '8px', marginBottom: 0 }}>
            These are in your library now. PullBox is backfilling ComicVine metadata in the
            background — covers and details fill in over the next several minutes. A large library
            may take a while.
          </p>
          {doImport.data.errors.length > 0 && (
            <div style={{ marginTop: '10px', fontSize: '0.78rem', color: 'var(--color-status-failed)' }}>
              {doImport.data.errors.map((e) => (
                <div key={e} style={{ wordBreak: 'break-all' }}>
                  {e}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
