import { createFileRoute } from '@tanstack/react-router'
import { useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { get, patch } from '../../../api/client'
import { Input } from '../../../components/ui/input'
import { Skeleton } from '../../../components/ui/skeleton'

// ── Route definition ──────────────────────────────────────────────────────────

export const Route = createFileRoute('/settings/general/')({
  component: GeneralPage,
})

// ── Types ─────────────────────────────────────────────────────────────────────

type GeneralSettings = {
  id: number
  library_path: string | null
  effective_path: string
  config_library_path: string
  exists: boolean
  writable: boolean
}

// ── Path status pill ──────────────────────────────────────────────────────────

function PathStatus({ data }: { data: GeneralSettings }) {
  const [color, text] = data.exists
    ? data.writable
      ? (['var(--color-status-downloaded)', 'Folder exists and is writable'] as const)
      : (['var(--color-status-failed)', 'Folder exists but is not writable'] as const)
    : (['var(--color-status-failed)', 'Folder does not exist'] as const)

  return (
    <div
      style={{
        background: 'var(--color-bg)',
        border: '1px solid var(--color-border)',
        borderRadius: '6px',
        padding: '12px 14px',
      }}
    >
      <div
        style={{
          fontSize: '0.7rem',
          textTransform: 'uppercase',
          letterSpacing: '0.05em',
          color: 'var(--color-muted)',
          marginBottom: '6px',
        }}
      >
        In use
      </div>
      <code
        style={{ fontSize: '0.82rem', color: 'var(--color-text)', wordBreak: 'break-all' }}
      >
        {data.effective_path}
      </code>
      <div style={{ fontSize: '0.75rem', color, marginTop: '6px' }}>{text}</div>

      {/* Where the value came from. The DB override and config.yaml disagree by
          design once a path is saved here — spell that out rather than leaving
          the stale config.yaml value to look authoritative. */}
      <div
        style={{
          fontSize: '0.72rem',
          color: 'var(--color-muted)',
          marginTop: '8px',
          paddingTop: '8px',
          borderTop: '1px solid var(--color-border)',
          lineHeight: 1.5,
        }}
      >
        {data.library_path ? (
          <>
            Saved in PullBox's database. This is <strong>not</strong> written back to
            config.yaml — the startup value is still{' '}
            <code style={{ wordBreak: 'break-all' }}>{data.config_library_path}</code>, and it
            is ignored while an override is set. Clear the field to fall back to it.
          </>
        ) : (
          <>
            The startup value, from config.yaml or PULLBOX_LIBRARY_PATH. Saving a path above
            stores an override in PullBox's database and leaves config.yaml unchanged.
          </>
        )}
      </div>
    </div>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────

function GeneralPage() {
  const queryClient = useQueryClient()
  const { data, isLoading, isError, error } = useQuery<GeneralSettings>({
    queryKey: ['general-settings'],
    queryFn: () => get<GeneralSettings>('/settings/general'),
  })

  const [libraryPath, setLibraryPath] = useState<string | null>(null)
  // Seed the form once the settings load.
  const seeded = useRef(false)
  useEffect(() => {
    if (data && !seeded.current) {
      seeded.current = true
      setLibraryPath(data.library_path ?? '')
    }
  }, [data])

  const { mutate, isPending, isSuccess, error: saveError, reset } = useMutation({
    mutationFn: (value: string) =>
      patch('/settings/general', { library_path: value.trim() || null }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['general-settings'] }),
  })

  const dirty = useMemo(() => {
    if (!data || libraryPath === null) return false
    return libraryPath !== (data.library_path ?? '')
  }, [data, libraryPath])

  const labelStyle = {
    fontSize: '0.8rem',
    color: 'var(--color-muted)' as const,
    display: 'block' as const,
    marginBottom: '5px',
  }

  return (
    <div className="p-6" style={{ maxWidth: '640px' }}>
      {/* Header */}
      <div style={{ marginBottom: '24px' }}>
        <h1 className="text-2xl font-bold" style={{ color: 'var(--color-text)', margin: 0 }}>
          General
        </h1>
        <p style={{ fontSize: '0.875rem', color: 'var(--color-muted)', marginTop: '4px' }}>
          Where PullBox stores your comics.
        </p>
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
          Failed to load settings: {error instanceof Error ? error.message : 'Unknown error'}
        </div>
      )}

      {!isError && (isLoading || libraryPath === null || !data) && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
          {[1, 2].map((i) => (
            <Skeleton key={i} style={{ height: '52px', width: '100%' }} />
          ))}
        </div>
      )}

      {!isError && data && libraryPath !== null && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '18px' }}>
          {/* Library path */}
          <div>
            <label style={labelStyle}>Library Path</label>
            <Input
              value={libraryPath}
              onChange={(e) => {
                setLibraryPath(e.target.value)
                reset()
              }}
              placeholder={data.config_library_path}
            />
            <p style={{ fontSize: '0.72rem', color: 'var(--color-muted)', marginTop: '5px' }}>
              Absolute path as PullBox sees it — inside Docker that is the container path
              (e.g. <code>/comics</code>), not the host path. Takes effect immediately, no
              restart needed. Leave blank to use the value from config.yaml.
            </p>
          </div>

          {/* Effective path + existence check */}
          <PathStatus data={data} />

          {/* Post-download note */}
          <p style={{ fontSize: '0.72rem', color: 'var(--color-muted)', margin: 0 }}>
            Note: a Destination Root set under Post-Download overrides this path for
            completed downloads.
          </p>

          {/* Save */}
          <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
            <button
              onClick={() => mutate(libraryPath)}
              disabled={!dirty || isPending}
              style={{
                fontSize: '0.875rem',
                padding: '6px 16px',
                borderRadius: '6px',
                background: dirty && !isPending ? 'var(--color-accent)' : 'var(--color-border)',
                color: '#fff',
                border: 'none',
                cursor: dirty && !isPending ? 'pointer' : 'not-allowed',
                fontWeight: 500,
              }}
            >
              {isPending ? 'Saving…' : 'Save'}
            </button>
            {isSuccess && !dirty && (
              <span style={{ fontSize: '0.8rem', color: 'var(--color-status-downloaded)' }}>
                Saved
              </span>
            )}
            {saveError && (
              <span style={{ fontSize: '0.8rem', color: 'var(--color-status-failed)' }}>
                {saveError instanceof Error ? saveError.message : 'Save failed'}
              </span>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
