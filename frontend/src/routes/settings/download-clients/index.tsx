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

export const Route = createFileRoute('/settings/download-clients/')({
  component: DownloadClientsPage,
})

// ── Types ─────────────────────────────────────────────────────────────────────

type DownloadClient = {
  id: number
  name: string
  type: string
  host: string
  port: number
  username: string | null
  password: string | null
  api_key: string | null
  category: string
  enabled: boolean
  last_tested_at: string | null
  last_test_success: boolean | null
}

type AddForm = {
  name: string
  type: string
  host: string
  port: number
  username: string
  password: string
  api_key: string
  category: string
  enabled: boolean
}

const DEFAULT_PORTS: Record<string, number> = {
  nzbget: 6789,
  sabnzbd: 8080,
  qbittorrent: 8080,
}

const EMPTY_FORM: AddForm = {
  name: '',
  type: 'nzbget',
  host: '',
  port: DEFAULT_PORTS['nzbget'],
  username: '',
  password: '',
  api_key: '',
  category: 'pullbox-comics',
  enabled: true,
}

const TYPE_LABELS: Record<string, string> = {
  nzbget: 'NZBGet',
  sabnzbd: 'SABnzbd',
  qbittorrent: 'qBittorrent',
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

function TestButton({ clientId }: { clientId: number }) {
  const queryClient = useQueryClient()
  const { mutate, isPending } = useMutation({
    mutationFn: () => post(`/download-clients/${clientId}/test`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['download-clients'] }),
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

function DeleteButton({ clientId }: { clientId: number }) {
  const queryClient = useQueryClient()
  const { mutate } = useMutation({
    mutationFn: () => del(`/download-clients/${clientId}`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['download-clients'] }),
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
          <AlertDialogTitle style={{ color: 'var(--color-text)' }}>
            Delete download client?
          </AlertDialogTitle>
          <AlertDialogDescription style={{ color: 'var(--color-muted)' }}>
            This will remove the download client configuration. Active downloads will not be
            affected.
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

function AddClientDialog({ onAdded }: { onAdded: () => void }) {
  const [open, setOpen] = useState(false)
  const [form, setForm] = useState<AddForm>(EMPTY_FORM)

  const { mutate, isPending, error, reset } = useMutation({
    mutationFn: () =>
      post('/download-clients/', {
        ...form,
        username: form.username || null,
        password: form.password || null,
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

  function handleTypeChange(type: string) {
    setForm({ ...form, type, port: DEFAULT_PORTS[type] ?? 80 })
  }

  const canSubmit = form.name.trim() !== '' && form.host.trim() !== '' && !isPending

  const labelStyle = {
    fontSize: '0.8rem',
    color: 'var(--color-muted)' as const,
    display: 'block' as const,
    marginBottom: '5px',
  }

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
          Add Client
        </button>
      </DialogTrigger>

      <DialogContent
        style={{ background: 'var(--color-surface)', border: '1px solid var(--color-border)' }}
      >
        <DialogHeader>
          <DialogTitle style={{ color: 'var(--color-text)' }}>Add Download Client</DialogTitle>
        </DialogHeader>

        <div style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}>
          {/* Honeypot inputs: absorb browser credential autofill before it reaches the real fields */}
          <input type="text" autoComplete="username" style={{ position: 'absolute', left: '-9999px', width: '1px', height: '1px', opacity: 0 }} tabIndex={-1} aria-hidden="true" />
          <input type="password" autoComplete="current-password" style={{ position: 'absolute', left: '-9999px', width: '1px', height: '1px', opacity: 0 }} tabIndex={-1} aria-hidden="true" />

          {/* Name */}
          <div>
            <label style={labelStyle}>Name</label>
            <Input
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              placeholder="e.g. My NZBGet"
            />
          </div>

          {/* Type */}
          <div>
            <label style={labelStyle}>Type</label>
            <Select value={form.type} onValueChange={handleTypeChange}>
              <SelectTrigger style={{ width: '100%' }}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="nzbget">NZBGet</SelectItem>
                <SelectItem value="sabnzbd">SABnzbd</SelectItem>
                <SelectItem value="qbittorrent">qBittorrent</SelectItem>
              </SelectContent>
            </Select>
          </div>

          {/* Host + Port row */}
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 90px', gap: '10px' }}>
            <div>
              <label style={labelStyle}>Host</label>
              <Input
                value={form.host}
                onChange={(e) => setForm({ ...form, host: e.target.value })}
                placeholder="localhost"
              />
            </div>
            <div>
              <label style={labelStyle}>Port</label>
              <Input
                type="number"
                value={form.port}
                onChange={(e) => setForm({ ...form, port: parseInt(e.target.value) || 80 })}
                min={1}
                max={65535}
              />
            </div>
          </div>

          {/* Username */}
          <div>
            <label style={labelStyle}>Username</label>
            <Input
              value={form.username}
              onChange={(e) => setForm({ ...form, username: e.target.value })}
              placeholder="optional"
              autoComplete="off"
            />
          </div>

          {/* Password */}
          <div>
            <label style={labelStyle}>Password</label>
            <Input
              type="password"
              value={form.password}
              onChange={(e) => {
                const val = e.target.value
                setForm((prev) => ({ ...prev, password: val }))
              }}
              placeholder="optional"
              autoComplete="new-password"
            />
          </div>

          {/* API Key */}
          <div>
            <label style={labelStyle}>API Key</label>
            <Input
              value={form.api_key}
              onChange={(e) => setForm({ ...form, api_key: e.target.value })}
              placeholder="optional"
              autoComplete="off"
            />
          </div>

          {/* Category */}
          <div>
            <label style={labelStyle}>Category</label>
            <Input
              value={form.category}
              onChange={(e) => setForm({ ...form, category: e.target.value })}
              placeholder="pullbox-comics"
            />
          </div>

          {/* Enabled */}
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
            <Checkbox
              id="client-enabled"
              checked={form.enabled}
              onCheckedChange={(checked) => setForm({ ...form, enabled: checked === true })}
            />
            <label
              htmlFor="client-enabled"
              style={{ fontSize: '0.875rem', color: 'var(--color-text)', cursor: 'pointer' }}
            >
              Enabled
            </label>
          </div>

          {error && (
            <p style={{ fontSize: '0.8rem', color: 'var(--color-status-failed)', margin: 0 }}>
              {error instanceof Error ? error.message : 'Failed to add client'}
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
            {isPending ? 'Adding…' : 'Add Client'}
          </button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function DownloadClientCard({ dc }: { dc: DownloadClient }) {
  const typeLabel = TYPE_LABELS[dc.type] ?? dc.type

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
          <span style={{ fontWeight: 600, fontSize: '0.925rem', color: 'var(--color-text)' }}>
            {dc.name}
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
            {typeLabel}
          </span>

          {!dc.enabled && (
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

        <div style={{ fontSize: '0.8rem', color: 'var(--color-muted)' }}>
          {dc.host}:{dc.port}
        </div>

        <div style={{ fontSize: '0.75rem', color: 'var(--color-muted)', marginTop: '3px' }}>
          Category: {dc.category}
          {dc.username && <span style={{ marginLeft: '10px' }}>User: {dc.username}</span>}
        </div>
      </div>

      {/* Last test result */}
      <div style={{ flexShrink: 0, textAlign: 'right', minWidth: '100px' }}>
        {dc.last_tested_at !== null ? (
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '5px',
              justifyContent: 'flex-end',
            }}
          >
            {dc.last_test_success ? (
              <CheckCircle
                size={13}
                style={{ color: 'var(--color-status-downloaded)', flexShrink: 0 }}
              />
            ) : (
              <XCircle
                size={13}
                style={{ color: 'var(--color-status-failed)', flexShrink: 0 }}
              />
            )}
            <span style={{ fontSize: '0.72rem', color: 'var(--color-muted)' }}>
              {formatDateTime(dc.last_tested_at)}
            </span>
          </div>
        ) : (
          <span style={{ fontSize: '0.72rem', color: 'var(--color-muted)' }}>Not tested</span>
        )}
      </div>

      {/* Actions */}
      <div style={{ display: 'flex', gap: '6px', flexShrink: 0 }}>
        <TestButton clientId={dc.id} />
        <DeleteButton clientId={dc.id} />
      </div>
    </div>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────

function DownloadClientsPage() {
  const queryClient = useQueryClient()
  const {
    data: clients,
    isLoading,
    isError,
    error,
  } = useQuery<DownloadClient[]>({
    queryKey: ['download-clients'],
    queryFn: () => get<DownloadClient[]>('/download-clients/'),
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
          <h1 className="text-2xl font-bold" style={{ color: 'var(--color-text)', margin: 0 }}>
            Download Clients
          </h1>
          <p style={{ fontSize: '0.875rem', color: 'var(--color-muted)', marginTop: '4px' }}>
            Configure NZBGet, SABnzbd, and qBittorrent connections
          </p>
        </div>
        <AddClientDialog
          onAdded={() => queryClient.invalidateQueries({ queryKey: ['download-clients'] })}
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
          Failed to load download clients:{' '}
          {error instanceof Error ? error.message : 'Unknown error'}
        </div>
      )}

      {/* Loading */}
      {!isError && isLoading && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
          {[1, 2].map((i) => (
            <Skeleton key={i} style={{ height: '80px', width: '100%' }} />
          ))}
        </div>
      )}

      {/* Empty state */}
      {!isError && !isLoading && clients && clients.length === 0 && (
        <div
          style={{
            textAlign: 'center',
            padding: '64px 0',
            color: 'var(--color-muted)',
            fontSize: '0.95rem',
          }}
        >
          No download clients configured — click "Add Client" to get started
        </div>
      )}

      {/* Client cards */}
      {!isError && !isLoading && clients && clients.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
          {clients.map((dc) => (
            <DownloadClientCard key={dc.id} dc={dc} />
          ))}
        </div>
      )}
    </div>
  )
}
