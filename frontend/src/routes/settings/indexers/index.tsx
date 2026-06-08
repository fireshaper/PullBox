import { createFileRoute } from '@tanstack/react-router'
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CheckCircle, XCircle } from 'lucide-react'
import { del, get, post } from '../../../api/client'
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '../../../components/ui/dialog'
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
} from '../../../components/ui/alert-dialog'
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

export const Route = createFileRoute('/settings/indexers/')({
  component: IndexersPage,
})

// ── Types ─────────────────────────────────────────────────────────────────────

type Indexer = {
  id: number
  name: string
  type: string
  url: string
  api_key: string | null
  enabled: boolean
  priority: number
  last_tested_at: string | null
  last_test_success: boolean | null
}

type AddForm = {
  name: string
  type: string
  url: string
  api_key: string
  priority: number
  enabled: boolean
}

const EMPTY_FORM: AddForm = {
  name: '',
  type: 'newznab',
  url: '',
  api_key: '',
  priority: 100,
  enabled: true,
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function formatDateTime(iso: string | null): string {
  if (!iso) return 'Never'
  return new Date(iso).toLocaleString('en-US', {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  })
}

// ── Sub-components ────────────────────────────────────────────────────────────

function TestButton({ indexerId }: { indexerId: number }) {
  const queryClient = useQueryClient()
  const { mutate, isPending } = useMutation({
    mutationFn: () => post(`/indexers/${indexerId}/test`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['indexers'] }),
  })

  return (
    <button
      onClick={() => mutate()}
      disabled={isPending}
      style={{
        fontSize: '0.72rem',
        padding: '3px 8px',
        borderRadius: '4px',
        background: 'transparent',
        color: 'var(--color-muted)',
        border: '1px solid var(--color-border)',
        cursor: isPending ? 'wait' : 'pointer',
        opacity: isPending ? 0.7 : 1,
        whiteSpace: 'nowrap',
      }}
    >
      {isPending ? 'Testing…' : 'Test'}
    </button>
  )
}

function DeleteButton({ indexerId }: { indexerId: number }) {
  const queryClient = useQueryClient()
  const { mutate } = useMutation({
    mutationFn: () => del(`/indexers/${indexerId}`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['indexers'] }),
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
            color: 'var(--color-status-failed)',
            border: '1px solid color-mix(in srgb, var(--color-status-failed) 40%, transparent)',
            cursor: 'pointer',
            whiteSpace: 'nowrap',
          }}
        >
          Delete
        </button>
      </AlertDialogTrigger>
      <AlertDialogContent
        style={{ background: 'var(--color-surface)', border: '1px solid var(--color-border)' }}
      >
        <AlertDialogHeader>
          <AlertDialogTitle style={{ color: 'var(--color-text)' }}>Delete indexer?</AlertDialogTitle>
          <AlertDialogDescription style={{ color: 'var(--color-muted)' }}>
            This will permanently remove this indexer. Existing download jobs will not be affected.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction
            onClick={() => mutate()}
            style={{ background: 'var(--color-status-failed)', color: '#fff', border: 'none' }}
          >
            Delete
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}

function AddIndexerDialog({ onAdded }: { onAdded: () => void }) {
  const [open, setOpen] = useState(false)
  const [form, setForm] = useState<AddForm>(EMPTY_FORM)

  const { mutate, isPending, error, reset } = useMutation({
    mutationFn: () =>
      post('/indexers/', {
        ...form,
        api_key: form.api_key || null,
      }),
    onSuccess: () => {
      setOpen(false)
      setForm(EMPTY_FORM)
      onAdded()
    },
  })

  function handleOpenChange(next: boolean) {
    setOpen(next)
    if (!next) {
      setForm(EMPTY_FORM)
      reset()
    }
  }

  const canSubmit = form.name.trim() !== '' && form.url.trim() !== '' && !isPending

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogTrigger asChild>
        <button
          style={{
            fontSize: '0.875rem',
            padding: '6px 16px',
            borderRadius: '6px',
            background: 'var(--color-accent)',
            color: '#fff',
            border: 'none',
            cursor: 'pointer',
            fontWeight: 500,
          }}
        >
          Add Indexer
        </button>
      </DialogTrigger>

      <DialogContent
        style={{ background: 'var(--color-surface)', border: '1px solid var(--color-border)' }}
      >
        <DialogHeader>
          <DialogTitle style={{ color: 'var(--color-text)' }}>Add Indexer</DialogTitle>
        </DialogHeader>

        <div style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}>
          {/* Name */}
          <div>
            <label
              style={{
                fontSize: '0.8rem',
                color: 'var(--color-muted)',
                display: 'block',
                marginBottom: '5px',
              }}
            >
              Name
            </label>
            <Input
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              placeholder="e.g. NZBGeek"
            />
          </div>

          {/* Type */}
          <div>
            <label
              style={{
                fontSize: '0.8rem',
                color: 'var(--color-muted)',
                display: 'block',
                marginBottom: '5px',
              }}
            >
              Type
            </label>
            <Select
              value={form.type}
              onValueChange={(v) => setForm({ ...form, type: v })}
            >
              <SelectTrigger style={{ width: '100%' }}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="newznab">Newznab</SelectItem>
                <SelectItem value="prowlarr">Prowlarr</SelectItem>
                <SelectItem value="jackett">Jackett</SelectItem>
                <SelectItem value="nzbhydra2">NZBHydra2</SelectItem>
              </SelectContent>
            </Select>
          </div>

          {/* URL */}
          <div>
            <label
              style={{
                fontSize: '0.8rem',
                color: 'var(--color-muted)',
                display: 'block',
                marginBottom: '5px',
              }}
            >
              URL
            </label>
            <Input
              value={form.url}
              onChange={(e) => setForm({ ...form, url: e.target.value })}
              placeholder="https://indexer.example.com"
            />
          </div>

          {/* API Key */}
          <div>
            <label
              style={{
                fontSize: '0.8rem',
                color: 'var(--color-muted)',
                display: 'block',
                marginBottom: '5px',
              }}
            >
              API Key
            </label>
            <Input
              type="password"
              value={form.api_key}
              onChange={(e) => setForm({ ...form, api_key: e.target.value })}
              placeholder="your-api-key"
              autoComplete="off"
            />
          </div>

          {/* Priority */}
          <div>
            <label
              style={{
                fontSize: '0.8rem',
                color: 'var(--color-muted)',
                display: 'block',
                marginBottom: '5px',
              }}
            >
              Priority
            </label>
            <Input
              type="number"
              value={form.priority}
              onChange={(e) =>
                setForm({ ...form, priority: parseInt(e.target.value) || 100 })
              }
              min={1}
              max={999}
            />
          </div>

          {/* Enabled */}
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
            <Checkbox
              id="indexer-enabled"
              checked={form.enabled}
              onCheckedChange={(checked) =>
                setForm({ ...form, enabled: checked === true })
              }
            />
            <label
              htmlFor="indexer-enabled"
              style={{ fontSize: '0.875rem', color: 'var(--color-text)', cursor: 'pointer' }}
            >
              Enabled
            </label>
          </div>

          {error && (
            <p style={{ fontSize: '0.8rem', color: 'var(--color-status-failed)', margin: 0 }}>
              {error instanceof Error ? error.message : 'Failed to add indexer'}
            </p>
          )}
        </div>

        <DialogFooter>
          <button
            onClick={() => mutate()}
            disabled={!canSubmit}
            style={{
              fontSize: '0.875rem',
              padding: '6px 16px',
              borderRadius: '6px',
              background: canSubmit ? 'var(--color-accent)' : 'var(--color-border)',
              color: '#fff',
              border: 'none',
              cursor: canSubmit ? 'pointer' : 'not-allowed',
              fontWeight: 500,
            }}
          >
            {isPending ? 'Adding…' : 'Add Indexer'}
          </button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function IndexerCard({ indexer }: { indexer: Indexer }) {
  return (
    <div
      style={{
        background: 'var(--color-surface)',
        border: '1px solid var(--color-border)',
        borderRadius: '8px',
        padding: '16px',
        display: 'flex',
        alignItems: 'center',
        gap: '16px',
      }}
    >
      {/* Identity */}
      <div style={{ flex: 1, minWidth: 0 }}>
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '8px',
            marginBottom: '5px',
            flexWrap: 'wrap',
          }}
        >
          <span
            style={{ fontWeight: 600, fontSize: '0.925rem', color: 'var(--color-text)' }}
          >
            {indexer.name}
          </span>

          <span
            style={{
              fontSize: '0.65rem',
              fontWeight: 700,
              textTransform: 'uppercase',
              letterSpacing: '0.04em',
              padding: '1px 6px',
              borderRadius: '4px',
              background: 'color-mix(in srgb, var(--color-accent) 18%, transparent)',
              color: 'var(--color-accent)',
            }}
          >
            {indexer.type}
          </span>

          {!indexer.enabled && (
            <span
              style={{
                fontSize: '0.65rem',
                fontWeight: 700,
                textTransform: 'uppercase',
                letterSpacing: '0.04em',
                padding: '1px 6px',
                borderRadius: '4px',
                background: 'color-mix(in srgb, var(--color-muted) 18%, transparent)',
                color: 'var(--color-muted)',
              }}
            >
              disabled
            </span>
          )}
        </div>

        <div
          style={{
            fontSize: '0.8rem',
            color: 'var(--color-muted)',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}
          title={indexer.url}
        >
          {indexer.url}
        </div>

        <div style={{ fontSize: '0.75rem', color: 'var(--color-muted)', marginTop: '3px' }}>
          Priority: {indexer.priority}
        </div>
      </div>

      {/* Last test result */}
      <div style={{ flexShrink: 0, textAlign: 'right', minWidth: '100px' }}>
        {indexer.last_tested_at !== null ? (
          <div style={{ display: 'flex', alignItems: 'center', gap: '5px', justifyContent: 'flex-end' }}>
            {indexer.last_test_success ? (
              <CheckCircle size={13} style={{ color: 'var(--color-status-downloaded)', flexShrink: 0 }} />
            ) : (
              <XCircle size={13} style={{ color: 'var(--color-status-failed)', flexShrink: 0 }} />
            )}
            <span style={{ fontSize: '0.72rem', color: 'var(--color-muted)' }}>
              {formatDateTime(indexer.last_tested_at)}
            </span>
          </div>
        ) : (
          <span style={{ fontSize: '0.72rem', color: 'var(--color-muted)' }}>Not tested</span>
        )}
      </div>

      {/* Actions */}
      <div style={{ display: 'flex', gap: '6px', flexShrink: 0 }}>
        {['newznab', 'nzbhydra2'].includes(indexer.type) && <TestButton indexerId={indexer.id} />}
        <DeleteButton indexerId={indexer.id} />
      </div>
    </div>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────

function IndexersPage() {
  const queryClient = useQueryClient()
  const { data: indexers, isLoading, isError, error } = useQuery<Indexer[]>({
    queryKey: ['indexers'],
    queryFn: () => get<Indexer[]>('/indexers/'),
  })

  return (
    <div className="p-6">
      {/* Header */}
      <div
        style={{
          display: 'flex',
          alignItems: 'flex-start',
          justifyContent: 'space-between',
          marginBottom: '24px',
        }}
      >
        <div>
          <h1
            className="text-2xl font-bold"
            style={{ color: 'var(--color-text)', margin: 0 }}
          >
            Indexers
          </h1>
          <p style={{ fontSize: '0.875rem', color: 'var(--color-muted)', marginTop: '4px' }}>
            Configure NZB and torrent search sources
          </p>
        </div>
        <AddIndexerDialog
          onAdded={() => queryClient.invalidateQueries({ queryKey: ['indexers'] })}
        />
      </div>

      {/* Error state */}
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
          Failed to load indexers:{' '}
          {error instanceof Error ? error.message : 'Unknown error'}
        </div>
      )}

      {/* Loading */}
      {!isError && isLoading && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
          {[1, 2, 3].map((i) => (
            <Skeleton key={i} style={{ height: '80px', width: '100%' }} />
          ))}
        </div>
      )}

      {/* Empty state */}
      {!isError && !isLoading && indexers && indexers.length === 0 && (
        <div
          style={{
            textAlign: 'center',
            padding: '64px 0',
            color: 'var(--color-muted)',
            fontSize: '0.95rem',
          }}
        >
          No indexers configured — click "Add Indexer" to get started
        </div>
      )}

      {/* Indexer cards */}
      {!isError && !isLoading && indexers && indexers.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
          {indexers.map((indexer) => (
            <IndexerCard key={indexer.id} indexer={indexer} />
          ))}
        </div>
      )}
    </div>
  )
}
