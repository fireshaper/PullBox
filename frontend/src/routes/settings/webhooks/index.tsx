import { createFileRoute } from '@tanstack/react-router'
import type { ReactNode } from 'react'
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CheckCircle, XCircle } from 'lucide-react'
import { del, get, patch, post } from '../../../api/client'
import { Button } from '../../../components/ui/button'
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

export const Route = createFileRoute('/settings/webhooks/')({
  component: WebhooksPage,
})

// ── Types ─────────────────────────────────────────────────────────────────────

type Webhook = {
  id: number
  name: string
  url: string
  format: string
  events: string[]
  secret: string | null
  enabled: boolean
  last_delivery_at: string | null
  last_delivery_success: boolean | null
  last_delivery_error: string | null
}

type WebhookEvent = { name: string; description: string }

type TestResult = { success: boolean; message: string; status_code: number | null }

type Form = {
  name: string
  url: string
  format: string
  events: string[]
  secret: string
  enabled: boolean
}

const FORMAT_LABELS: Record<string, string> = {
  generic: 'Generic JSON',
  discord: 'Discord',
}

// The events most people want on by default: what actually landed in the
// library, and when PullBox stopped trying.
const DEFAULT_EVENTS = ['download.completed', 'download.exhausted']

const EMPTY_FORM: Form = {
  name: '',
  url: '',
  format: 'generic',
  events: DEFAULT_EVENTS,
  secret: '',
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

function formFromWebhook(hook: Webhook): Form {
  return {
    name: hook.name,
    url: hook.url,
    format: hook.format,
    events: hook.events,
    secret: hook.secret ?? '',
    enabled: hook.enabled,
  }
}


const pill = {
  fontSize: '0.65rem',
  fontWeight: 700,
  textTransform: 'uppercase',
  letterSpacing: '0.04em',
  padding: '1px 6px',
  borderRadius: '4px',
} as const

// ── Sub-components ────────────────────────────────────────────────────────────

function TestButton({ hookId }: { hookId: number }) {
  const queryClient = useQueryClient()
  const { mutate, isPending } = useMutation({
    mutationFn: () => post<TestResult>(`/webhooks/${hookId}/test`),
    onSettled: () => queryClient.invalidateQueries({ queryKey: ['webhooks'] }),
  })

  return (
    <Button
      variant="subtle"
      size="xs"
      onClick={() => mutate()}
      disabled={isPending}
    >
      {isPending ? 'Sending…' : 'Test'}
    </Button>
  )
}

function DeleteButton({ hookId }: { hookId: number }) {
  const queryClient = useQueryClient()
  const { mutate } = useMutation({
    mutationFn: () => del(`/webhooks/${hookId}`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['webhooks'] }),
  })

  return (
    <AlertDialog>
      <AlertDialogTrigger asChild>
        <Button variant="danger" size="xs">
          Delete
        </Button>
      </AlertDialogTrigger>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Delete webhook?</AlertDialogTitle>
          <AlertDialogDescription>
            PullBox will stop sending notifications to this URL. Nothing is sent to the
            receiver to tell it so.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction variant="destructive" onClick={() => mutate()}>
            Delete
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}

function WebhookFormDialog({
  hook,
  events,
  trigger,
  onSaved,
}: {
  hook?: Webhook
  events: WebhookEvent[]
  trigger: ReactNode
  onSaved: () => void
}) {
  const isEdit = hook !== undefined
  const [open, setOpen] = useState(false)
  const [form, setForm] = useState<Form>(isEdit ? formFromWebhook(hook) : EMPTY_FORM)

  const payload = () => ({
    ...form,
    name: form.name.trim(),
    url: form.url.trim(),
    secret: form.secret.trim() || null,
  })

  const { mutate, isPending, error, reset } = useMutation({
    mutationFn: () =>
      isEdit ? patch(`/webhooks/${hook.id}`, payload()) : post('/webhooks/', payload()),
    onSuccess: () => {
      setOpen(false)
      if (!isEdit) setForm(EMPTY_FORM)
      onSaved()
    },
  })

  const test = useMutation({
    mutationFn: () =>
      post<TestResult>('/webhooks/test', {
        name: form.name.trim() || 'Unsaved webhook',
        url: form.url.trim(),
        format: form.format,
        secret: form.secret.trim() || null,
      }),
  })

  function handleOpenChange(next: boolean) {
    setOpen(next)
    if (next) setForm(isEdit ? formFromWebhook(hook) : EMPTY_FORM)
    reset()
    test.reset()
  }

  function toggleEvent(name: string, on: boolean) {
    setForm((prev) => ({
      ...prev,
      events: on ? [...prev.events, name] : prev.events.filter((e) => e !== name),
    }))
  }

  const urlLooksValid = /^https?:\/\//i.test(form.url.trim())
  const canSubmit = form.name.trim() !== '' && urlLooksValid && !isPending
  const canTest = urlLooksValid && !test.isPending

  const labelStyle = {
    fontSize: '0.8rem',
    color: 'var(--color-muted)' as const,
    display: 'block' as const,
    marginBottom: '5px',
  }
  const hintStyle = { fontSize: '0.72rem', color: 'var(--color-muted)', marginTop: '5px' }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogTrigger asChild>{trigger}</DialogTrigger>

      <DialogContent className="max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>
            {isEdit ? 'Edit Webhook' : 'Add Webhook'}
          </DialogTitle>
        </DialogHeader>

        <div style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}>
          {/* Name */}
          <div>
            <label style={labelStyle}>Name</label>
            <Input
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              placeholder="e.g. Thwip, Discord #comics"
            />
          </div>

          {/* URL */}
          <div>
            <label style={labelStyle}>URL</label>
            <Input
              value={form.url}
              onChange={(e) => setForm({ ...form, url: e.target.value })}
              placeholder="https://…"
              autoComplete="off"
            />
          </div>

          {/* Format */}
          <div>
            <label style={labelStyle}>Format</label>
            <Select value={form.format} onValueChange={(format) => setForm({ ...form, format })}>
              <SelectTrigger style={{ width: '100%' }}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="generic">Generic JSON</SelectItem>
                <SelectItem value="discord">Discord</SelectItem>
              </SelectContent>
            </Select>
            <div style={hintStyle}>
              {form.format === 'discord'
                ? 'Paste a Discord channel webhook URL (Channel settings → Integrations → Webhooks). Each event is posted as an embed.'
                : 'Posts a JSON body {"event", "timestamp", "source", …} with an X-PullBox-Event header. This is the format Thwip and other companion apps consume.'}
            </div>
          </div>

          {/* Secret (generic only — Discord ignores extra headers) */}
          {form.format === 'generic' && (
            <div>
              <label style={labelStyle}>Signing secret (optional)</label>
              <Input
                value={form.secret}
                onChange={(e) => setForm({ ...form, secret: e.target.value })}
                placeholder="shared secret"
                autoComplete="off"
              />
              <div style={hintStyle}>
                When set, each request carries{' '}
                <code style={{ fontSize: '0.7rem' }}>X-PullBox-Signature: sha256=…</code>, an
                HMAC-SHA256 of the body. Give the receiver the same secret so it can reject
                anything PullBox didn't send.
              </div>
            </div>
          )}

          {/* Events */}
          <div>
            <label style={labelStyle}>Events</label>
            <div
              style={{
                display: 'flex',
                flexDirection: 'column',
                gap: '8px',
                background: 'var(--color-bg)',
                border: '1px solid var(--color-border)',
                borderRadius: '6px',
                padding: '10px 12px',
              }}
            >
              {events
                .filter((e) => e.name !== 'test')
                .map((e) => {
                  const id = `evt-${e.name}`
                  return (
                    <div key={e.name} style={{ display: 'flex', alignItems: 'flex-start', gap: '10px' }}>
                      <Checkbox
                        id={id}
                        checked={form.events.includes(e.name)}
                        onCheckedChange={(checked) => toggleEvent(e.name, checked === true)}
                        style={{ marginTop: '2px' }}
                      />
                      <label htmlFor={id} style={{ cursor: 'pointer', lineHeight: 1.35 }}>
                        <code style={{ fontSize: '0.78rem', color: 'var(--color-text)' }}>{e.name}</code>
                        <div style={{ fontSize: '0.72rem', color: 'var(--color-muted)' }}>
                          {e.description}
                        </div>
                      </label>
                    </div>
                  )
                })}
            </div>
            {form.events.length === 0 && (
              <div style={{ ...hintStyle, color: 'var(--color-status-failed)' }}>
                No events selected — this webhook will never fire (Test still works).
              </div>
            )}
          </div>

          {/* Enabled */}
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
            <Checkbox
              id="webhook-enabled"
              checked={form.enabled}
              onCheckedChange={(checked) => setForm({ ...form, enabled: checked === true })}
            />
            <label
              htmlFor="webhook-enabled"
              style={{ fontSize: '0.875rem', color: 'var(--color-text)', cursor: 'pointer' }}
            >
              Enabled
            </label>
          </div>

          {error && (
            <p style={{ fontSize: '0.8rem', color: 'var(--color-status-failed)', margin: 0 }}>
              {error instanceof Error ? error.message : 'Failed to save webhook'}
            </p>
          )}
        </div>

        {/* Test result */}
        {(test.isPending || test.data) && (
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
              fontSize: '0.8rem',
              marginTop: '4px',
              color: test.isPending
                ? 'var(--color-muted)'
                : test.data?.success
                  ? 'var(--color-status-downloaded)'
                  : 'var(--color-status-failed)',
            }}
          >
            {!test.isPending && test.data?.success && <CheckCircle size={14} />}
            {!test.isPending && test.data && !test.data.success && <XCircle size={14} />}
            <span>{test.isPending ? 'Sending test event…' : test.data?.message}</span>
          </div>
        )}
        {test.error && (
          <p style={{ fontSize: '0.8rem', color: 'var(--color-status-failed)', margin: 0 }}>
            {test.error instanceof Error ? test.error.message : 'Test failed'}
          </p>
        )}

        <DialogFooter style={{ justifyContent: 'space-between' }}>
          <Button
            variant="outline"
            size="lg"
            onClick={() => test.mutate()}
            disabled={!canTest}
          >
            {test.isPending ? 'Sending…' : 'Send Test'}
          </Button>
          <Button
            size="lg"
            onClick={() => mutate()}
            disabled={!canSubmit}
          >
            {isPending ? (isEdit ? 'Saving…' : 'Adding…') : isEdit ? 'Save Changes' : 'Add Webhook'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function WebhookCard({
  hook,
  events,
  onSaved,
}: {
  hook: Webhook
  events: WebhookEvent[]
  onSaved: () => void
}) {
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
          style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '5px', flexWrap: 'wrap' }}
        >
          <span style={{ fontWeight: 600, fontSize: '0.925rem', color: 'var(--color-text)' }}>
            {hook.name}
          </span>
          <span
            style={{
              ...pill,
              background: 'color-mix(in srgb, var(--color-accent) 18%, transparent)',
              color: 'var(--color-accent)',
            }}
          >
            {FORMAT_LABELS[hook.format] ?? hook.format}
          </span>
          {hook.secret && (
            <span
              style={{
                ...pill,
                background: 'color-mix(in srgb, var(--color-muted) 18%, transparent)',
                color: 'var(--color-muted)',
              }}
            >
              signed
            </span>
          )}
          {!hook.enabled && (
            <span
              style={{
                ...pill,
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
          title={hook.url}
        >
          {hook.url}
        </div>

        <div style={{ display: 'flex', gap: '4px', flexWrap: 'wrap', marginTop: '6px' }}>
          {hook.events.length === 0 ? (
            <span style={{ fontSize: '0.72rem', color: 'var(--color-status-failed)' }}>
              No events selected
            </span>
          ) : (
            hook.events.map((name) => (
              <code
                key={name}
                title={events.find((e) => e.name === name)?.description}
                style={{
                  fontSize: '0.7rem',
                  padding: '1px 6px',
                  borderRadius: '4px',
                  background: 'var(--color-bg)',
                  border: '1px solid var(--color-border)',
                  color: 'var(--color-text)',
                }}
              >
                {name}
              </code>
            ))
          )}
        </div>
      </div>

      {/* Last delivery */}
      <div style={{ flexShrink: 0, textAlign: 'right', minWidth: '120px', maxWidth: '220px' }}>
        {hook.last_delivery_at !== null ? (
          <>
            <div style={{ display: 'flex', alignItems: 'center', gap: '5px', justifyContent: 'flex-end' }}>
              {hook.last_delivery_success ? (
                <CheckCircle size={13} style={{ color: 'var(--color-status-downloaded)', flexShrink: 0 }} />
              ) : (
                <XCircle size={13} style={{ color: 'var(--color-status-failed)', flexShrink: 0 }} />
              )}
              <span style={{ fontSize: '0.72rem', color: 'var(--color-muted)' }}>
                {formatDateTime(hook.last_delivery_at)}
              </span>
            </div>
            {!hook.last_delivery_success && hook.last_delivery_error && (
              <div
                title={hook.last_delivery_error}
                style={{
                  fontSize: '0.7rem',
                  color: 'var(--color-status-failed)',
                  marginTop: '3px',
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                }}
              >
                {hook.last_delivery_error}
              </div>
            )}
          </>
        ) : (
          <span style={{ fontSize: '0.72rem', color: 'var(--color-muted)' }}>Never delivered</span>
        )}
      </div>

      {/* Actions */}
      <div style={{ display: 'flex', gap: '6px', flexShrink: 0 }}>
        <TestButton hookId={hook.id} />
        <WebhookFormDialog
          hook={hook}
          events={events}
          onSaved={onSaved}
          trigger={<Button variant="subtle" size="xs">Edit</Button>}
        />
        <DeleteButton hookId={hook.id} />
      </div>
    </div>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────

function WebhooksPage() {
  const queryClient = useQueryClient()
  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['webhooks'] })

  const hooks = useQuery<Webhook[]>({
    queryKey: ['webhooks'],
    queryFn: () => get<Webhook[]>('/webhooks/'),
  })
  const events = useQuery<WebhookEvent[]>({
    queryKey: ['webhooks', 'events'],
    queryFn: () => get<WebhookEvent[]>('/webhooks/events'),
    staleTime: Infinity,
  })
  const eventList = events.data ?? []

  return (
    <div className="p-6">
      {/* Header */}
      <div
        style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: '24px' }}
      >
        <div>
          <h1 className="text-2xl font-bold" style={{ color: 'var(--color-text)', margin: 0 }}>
            Webhooks
          </h1>
          <p style={{ fontSize: '0.875rem', color: 'var(--color-muted)', marginTop: '4px' }}>
            Notify Thwip, Discord, or anything else with an HTTP endpoint when things happen
          </p>
        </div>
        <WebhookFormDialog
          events={eventList}
          onSaved={invalidate}
          trigger={
            <Button size="lg">
              Add Webhook
            </Button>
          }
        />
      </div>

      {/* Error state */}
      {hooks.isError && (
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
          Failed to load webhooks:{' '}
          {hooks.error instanceof Error ? hooks.error.message : 'Unknown error'}
        </div>
      )}

      {/* Loading */}
      {!hooks.isError && hooks.isLoading && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
          {[1, 2].map((i) => (
            <Skeleton key={i} style={{ height: '88px', width: '100%' }} />
          ))}
        </div>
      )}

      {/* Empty state */}
      {!hooks.isError && !hooks.isLoading && hooks.data && hooks.data.length === 0 && (
        <div style={{ textAlign: 'center', padding: '64px 0', color: 'var(--color-muted)', fontSize: '0.95rem' }}>
          No webhooks configured — click "Add Webhook" to get started
        </div>
      )}

      {/* Cards */}
      {!hooks.isError && !hooks.isLoading && hooks.data && hooks.data.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
          {hooks.data.map((hook) => (
            <WebhookCard key={hook.id} hook={hook} events={eventList} onSaved={invalidate} />
          ))}
        </div>
      )}

      {/* Delivery notes */}
      <div
        style={{
          marginTop: '24px',
          fontSize: '0.78rem',
          color: 'var(--color-muted)',
          lineHeight: 1.55,
          borderTop: '1px solid var(--color-border)',
          paddingTop: '14px',
        }}
      >
        Deliveries are fire-and-forget: each event is POSTed once the change is committed, with
        a 10-second timeout and up to three attempts on connection errors, 5xx responses and
        rate limits. A failing endpoint never blocks downloads — the last result is shown on
        the card above and in the application log. Generic payloads include the issue's{' '}
        <code>path_rel</code> (its path below the library root), the same join key the
        companion feed uses.
      </div>
    </div>
  )
}
