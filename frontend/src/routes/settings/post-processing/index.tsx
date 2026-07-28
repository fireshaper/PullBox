import { createFileRoute } from '@tanstack/react-router'
import { useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { get, patch, post } from '../../../api/client'
import { Input } from '../../../components/ui/input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '../../../components/ui/select'
import { Checkbox } from '../../../components/ui/checkbox'
import { Skeleton } from '../../../components/ui/skeleton'

// ── Route definition ──────────────────────────────────────────────────────────

export const Route = createFileRoute('/settings/post-processing/')({
  component: PostProcessingPage,
})

// ── Types ─────────────────────────────────────────────────────────────────────

type PostProcessingSettings = {
  id: number
  enabled: boolean
  operation: string
  destination_root: string | null
  folder_pattern: string
  file_pattern: string
  delete_empty_folder: boolean
}

type Form = {
  enabled: boolean
  operation: string
  destination_root: string
  folder_pattern: string
  file_pattern: string
  delete_empty_folder: boolean
}

const TOKENS = ['{series}', '{issue}', '{issue:3}', '{publisher}', '{year}', '{title}', '{ext}']

// ── Live preview (debounced) ──────────────────────────────────────────────────

function useDebounced<T>(value: T, delayMs: number): T {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), delayMs)
    return () => clearTimeout(t)
  }, [value, delayMs])
  return debounced
}

function PreviewRow({ form }: { form: Form }) {
  const debounced = useDebounced(
    { folder: form.folder_pattern, file: form.file_pattern, dest: form.destination_root },
    350,
  )

  const { data, isFetching } = useQuery({
    queryKey: ['post-processing-preview', debounced.folder, debounced.file, debounced.dest],
    queryFn: () =>
      post<{ path: string }>('/settings/post-processing/preview', {
        folder_pattern: debounced.folder,
        file_pattern: debounced.file,
        destination_root: debounced.dest || null,
      }),
  })

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
        Preview
      </div>
      <code
        style={{
          fontSize: '0.82rem',
          color: 'var(--color-text)',
          wordBreak: 'break-all',
          opacity: isFetching ? 0.5 : 1,
        }}
      >
        {data?.path ?? '…'}
      </code>
    </div>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────

function PostProcessingPage() {
  const queryClient = useQueryClient()
  const { data, isLoading, isError, error } = useQuery<PostProcessingSettings>({
    queryKey: ['post-processing'],
    queryFn: () => get<PostProcessingSettings>('/settings/post-processing'),
  })

  const [form, setForm] = useState<Form | null>(null)
  // Seed the form once the settings load.
  const seeded = useRef(false)
  useEffect(() => {
    if (data && !seeded.current) {
      seeded.current = true
      setForm({
        enabled: data.enabled,
        operation: data.operation,
        destination_root: data.destination_root ?? '',
        folder_pattern: data.folder_pattern,
        file_pattern: data.file_pattern,
        delete_empty_folder: data.delete_empty_folder,
      })
    }
  }, [data])

  const { mutate, isPending, isSuccess, error: saveError, reset } = useMutation({
    mutationFn: (body: Form) =>
      patch('/settings/post-processing', {
        ...body,
        destination_root: body.destination_root.trim() || null,
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['post-processing'] }),
  })

  const dirty = useMemo(() => {
    if (!data || !form) return false
    return (
      form.enabled !== data.enabled ||
      form.operation !== data.operation ||
      form.destination_root !== (data.destination_root ?? '') ||
      form.folder_pattern !== data.folder_pattern ||
      form.file_pattern !== data.file_pattern ||
      form.delete_empty_folder !== data.delete_empty_folder
    )
  }, [data, form])

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
          Post-Download Actions
        </h1>
        <p style={{ fontSize: '0.875rem', color: 'var(--color-muted)', marginTop: '4px' }}>
          Move and rename completed downloads into an organized library folder.
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

      {!isError && (isLoading || !form) && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
          {[1, 2, 3].map((i) => (
            <Skeleton key={i} style={{ height: '52px', width: '100%' }} />
          ))}
        </div>
      )}

      {!isError && form && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '18px' }}>
          {/* Enabled */}
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
            <Checkbox
              id="pp-enabled"
              checked={form.enabled}
              onCheckedChange={(checked) => {
                setForm({ ...form, enabled: checked === true })
                reset()
              }}
            />
            <label
              htmlFor="pp-enabled"
              style={{ fontSize: '0.875rem', color: 'var(--color-text)', cursor: 'pointer' }}
            >
              Enable post-download move / rename
            </label>
          </div>

          {/* Operation */}
          <div>
            <label style={labelStyle}>Operation</label>
            <Select
              value={form.operation}
              onValueChange={(operation) => {
                setForm({ ...form, operation })
                reset()
              }}
            >
              <SelectTrigger style={{ width: '220px' }}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="move">Move</SelectItem>
                <SelectItem value="copy">Copy</SelectItem>
                <SelectItem value="hardlink">Hardlink</SelectItem>
              </SelectContent>
            </Select>
            <p style={{ fontSize: '0.72rem', color: 'var(--color-muted)', marginTop: '5px' }}>
              {form.operation === 'move'
                ? 'Relocates the file (removes the original).'
                : form.operation === 'copy'
                  ? 'Copies the file, leaving the original in place.'
                  : 'Creates a hardlink (keeps torrents seeding); falls back to copy across filesystems.'}
            </p>

            {/* Move-only: copy/hardlink leave the original, so the folder is never empty. */}
            {form.operation === 'move' && (
              <div
                style={{
                  display: 'flex',
                  alignItems: 'flex-start',
                  gap: '10px',
                  marginTop: '12px',
                  paddingLeft: '2px',
                }}
              >
                <Checkbox
                  id="pp-delete-empty-folder"
                  checked={form.delete_empty_folder}
                  onCheckedChange={(checked) => {
                    setForm({ ...form, delete_empty_folder: checked === true })
                    reset()
                  }}
                  style={{ marginTop: '2px' }}
                />
                <div>
                  <label
                    htmlFor="pp-delete-empty-folder"
                    style={{
                      fontSize: '0.875rem',
                      color: 'var(--color-text)',
                      cursor: 'pointer',
                    }}
                  >
                    Delete the download folder if it's empty afterwards
                  </label>
                  <p
                    style={{
                      fontSize: '0.72rem',
                      color: 'var(--color-muted)',
                      marginTop: '3px',
                    }}
                  >
                    Removes the client's leftover job folder once the comic has been moved
                    out. Folders still holding other files (nfo, par2, samples) are left
                    alone.
                  </p>
                </div>
              </div>
            )}
          </div>

          {/* Destination root */}
          <div>
            <label style={labelStyle}>Destination Root</label>
            <Input
              value={form.destination_root}
              onChange={(e) => {
                setForm({ ...form, destination_root: e.target.value })
                reset()
              }}
              placeholder="Leave blank to use the library path"
            />
          </div>

          {/* Folder pattern */}
          <div>
            <label style={labelStyle}>Folder Pattern</label>
            <Input
              value={form.folder_pattern}
              onChange={(e) => {
                setForm({ ...form, folder_pattern: e.target.value })
                reset()
              }}
              placeholder="{publisher}/{series} ({year})"
            />
          </div>

          {/* File pattern */}
          <div>
            <label style={labelStyle}>File Pattern</label>
            <Input
              value={form.file_pattern}
              onChange={(e) => {
                setForm({ ...form, file_pattern: e.target.value })
                reset()
              }}
              placeholder="{series} #{issue} - {title}"
            />
          </div>

          {/* Token legend */}
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px' }}>
            {TOKENS.map((t) => (
              <code
                key={t}
                style={{
                  fontSize: '0.72rem',
                  padding: '2px 6px',
                  borderRadius: '4px',
                  background: 'var(--color-surface)',
                  border: '1px solid var(--color-border)',
                  color: 'var(--color-muted)',
                }}
              >
                {t}
              </code>
            ))}
          </div>

          {/* Live preview */}
          <PreviewRow form={form} />

          {/* Filesystem note */}
          <p style={{ fontSize: '0.72rem', color: 'var(--color-muted)', margin: 0 }}>
            Note: PullBox must be able to read the download client's completed folder at the
            same path the client reports (mount the same volume in Docker).
          </p>

          {/* Save */}
          <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
            <button
              onClick={() => mutate(form)}
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
