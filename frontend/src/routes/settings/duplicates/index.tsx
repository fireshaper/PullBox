import { createFileRoute } from '@tanstack/react-router'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, ArrowRight, Check, Merge } from 'lucide-react'
import { useState } from 'react'
import { get, post } from '../../../api/client'
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
import { Skeleton } from '../../../components/ui/skeleton'

// ── Types ─────────────────────────────────────────────────────────────────────

type DuplicateRow = {
  id: number
  metron_id: string | null
  comicvine_id: string | null
  title: string
  publisher: string | null
  start_year: number | null
  subscribed: boolean
  auto_download: boolean
  cover_url: string | null
  issue_count: number
  downloaded_count: number
}

type DuplicateGroup = {
  key: string
  title: string
  conflicting_years: boolean
  mergeable: boolean
  rows: DuplicateRow[]
}

type ScanResponse = {
  groups: DuplicateGroup[]
  total_groups: number
  mergeable_groups: number
  conflicting_groups: number
}

export const Route = createFileRoute('/settings/duplicates/')({
  component: DuplicatesPage,
})

// ── Bits ──────────────────────────────────────────────────────────────────────

const surface = {
  background: 'var(--color-surface)',
  border: '1px solid var(--color-border)',
  borderRadius: '8px',
}

function Badge({ children, tone = 'muted' }: { children: React.ReactNode; tone?: string }) {
  return (
    <span
      style={{
        fontSize: '0.62rem',
        fontWeight: 600,
        textTransform: 'uppercase',
        letterSpacing: '0.04em',
        color: tone,
        border: `1px solid ${tone}`,
        borderRadius: '4px',
        padding: '1px 5px',
        whiteSpace: 'nowrap',
      }}
    >
      {children}
    </span>
  )
}

/** The row that survives a merge: most issues, then oldest. Mirrors
 *  services/dedupe.pick_winner so the preview matches what actually happens. */
function keeperId(group: DuplicateGroup): number {
  return [...group.rows].sort(
    (a, b) => b.issue_count - a.issue_count || a.id - b.id,
  )[0].id
}

function SeriesRowLine({ row, keeper }: { row: DuplicateRow; keeper: boolean }) {
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: '10px',
        padding: '7px 10px',
        borderRadius: '6px',
        background: keeper ? 'color-mix(in srgb, var(--color-accent) 8%, transparent)' : 'transparent',
        borderLeft: keeper ? '3px solid var(--color-accent)' : '3px solid transparent',
      }}
    >
      <span style={{ fontSize: '0.8rem', color: 'var(--color-text)', flex: 1, minWidth: 0 }}>
        {row.title}
        {row.start_year ? (
          <span style={{ color: 'var(--color-muted)' }}> ({row.start_year})</span>
        ) : null}
      </span>

      <span style={{ fontSize: '0.7rem', color: 'var(--color-muted)', whiteSpace: 'nowrap' }}>
        {row.metron_id ? `metron ${row.metron_id}` : null}
        {row.metron_id && row.comicvine_id ? ' · ' : null}
        {row.comicvine_id ? `comicvine ${row.comicvine_id}` : null}
        {!row.metron_id && !row.comicvine_id ? 'no id' : null}
      </span>

      <span style={{ fontSize: '0.7rem', color: 'var(--color-muted)', whiteSpace: 'nowrap' }}>
        {row.issue_count} issue{row.issue_count === 1 ? '' : 's'}
        {row.downloaded_count > 0 ? ` · ${row.downloaded_count} downloaded` : ''}
      </span>

      {row.subscribed && <Badge tone="var(--color-status-downloaded)">Subscribed</Badge>}
      {keeper && <Badge tone="var(--color-accent)">Keeps</Badge>}
    </div>
  )
}

function MergeButton({ group }: { group: DuplicateGroup }) {
  const queryClient = useQueryClient()
  const mutation = useMutation({
    mutationFn: () =>
      post('/duplicates/merge', { series_ids: group.rows.map((r) => r.id) }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['duplicates'] })
      queryClient.invalidateQueries({ queryKey: ['series'] })
    },
  })

  const keeper = group.rows.find((r) => r.id === keeperId(group))!
  const totalIssues = group.rows.reduce((n, r) => n + r.issue_count, 0)

  return (
    <AlertDialog>
      <AlertDialogTrigger asChild>
        <button
          disabled={mutation.isPending}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '6px',
            fontSize: '0.75rem',
            fontWeight: 600,
            padding: '5px 12px',
            borderRadius: '5px',
            background: 'var(--color-accent)',
            color: '#fff',
            border: 'none',
            cursor: mutation.isPending ? 'default' : 'pointer',
            opacity: mutation.isPending ? 0.6 : 1,
          }}
        >
          <Merge size={13} />
          {mutation.isPending ? 'Merging…' : 'Merge'}
        </button>
      </AlertDialogTrigger>
      <AlertDialogContent style={{ background: 'var(--color-surface)' }}>
        <AlertDialogHeader>
          <AlertDialogTitle>Merge {group.rows.length} series into one?</AlertDialogTitle>
          <AlertDialogDescription>
            <strong>{keeper.title}</strong> (#{keeper.id}) is kept and absorbs the others: up
            to {totalIssues} issues, both metadata ids, and any subscription. Issues sharing a
            number are combined, keeping whichever copy has the downloaded file. This cannot be
            undone.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction onClick={() => mutation.mutate()}>Merge</AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}

function MergeAllButton({ count }: { count: number }) {
  const queryClient = useQueryClient()
  const [result, setResult] = useState<string | null>(null)
  const mutation = useMutation<{
    merged_groups: number
    skipped_groups: number
    issues_moved: number
    issues_merged: number
  }>({
    mutationFn: () => post('/duplicates/merge-all'),
    onSuccess: (data) => {
      setResult(
        `Merged ${data.merged_groups} group${data.merged_groups === 1 ? '' : 's'} — ` +
          `${data.issues_moved} issues moved, ${data.issues_merged} combined` +
          (data.skipped_groups > 0 ? `, ${data.skipped_groups} skipped` : ''),
      )
      queryClient.invalidateQueries({ queryKey: ['duplicates'] })
      queryClient.invalidateQueries({ queryKey: ['series'] })
    },
  })

  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
      <AlertDialog>
        <AlertDialogTrigger asChild>
          <button
            disabled={mutation.isPending || count === 0}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
              fontSize: '0.8rem',
              fontWeight: 600,
              padding: '7px 14px',
              borderRadius: '6px',
              background: count === 0 ? 'var(--color-surface)' : 'var(--color-accent)',
              color: count === 0 ? 'var(--color-muted)' : '#fff',
              border: count === 0 ? '1px solid var(--color-border)' : 'none',
              cursor: mutation.isPending || count === 0 ? 'default' : 'pointer',
              opacity: mutation.isPending ? 0.6 : 1,
            }}
          >
            <Merge size={14} />
            {mutation.isPending ? 'Merging…' : `Merge all ${count} groups`}
          </button>
        </AlertDialogTrigger>
        <AlertDialogContent style={{ background: 'var(--color-surface)' }}>
          <AlertDialogHeader>
            <AlertDialogTitle>Merge all {count} unambiguous groups?</AlertDialogTitle>
            <AlertDialogDescription>
              Each group collapses into its largest series, keeping every issue, file path,
              download job, subscription, and story-arc link. Groups whose rows disagree on a
              start year are left alone. This cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={() => mutation.mutate()}>Merge all</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {result && (
        <span
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '6px',
            fontSize: '0.78rem',
            color: 'var(--color-status-downloaded)',
          }}
        >
          <Check size={14} />
          {result}
        </span>
      )}
    </div>
  )
}

// ── Page ──────────────────────────────────────────────────────────────────────

function DuplicatesPage() {
  const { data, isLoading } = useQuery<ScanResponse>({
    queryKey: ['duplicates'],
    queryFn: () => get<ScanResponse>('/duplicates'),
  })

  return (
    <div style={{ padding: '24px', maxWidth: '900px' }}>
      <h1
        className="text-xl font-bold"
        style={{ color: 'var(--color-text)', marginBottom: '6px' }}
      >
        Duplicate Series
      </h1>
      <p style={{ fontSize: '0.82rem', color: 'var(--color-muted)', marginBottom: '20px' }}>
        Metron and ComicVine use separate id namespaces, and Metron's weekly list does not
        include a ComicVine id — so before this was handled, a calendar refresh served by one
        source could not recognise rows created by the other and added a second copy. Merging
        folds them back into one series, keeping both ids so it cannot happen again.
      </p>

      {isLoading && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
          {[1, 2, 3].map((i) => (
            <Skeleton key={i} style={{ height: '92px', width: '100%' }} />
          ))}
        </div>
      )}

      {!isLoading && data && data.total_groups === 0 && (
        <div
          style={{
            ...surface,
            padding: '32px',
            textAlign: 'center',
            color: 'var(--color-muted)',
            fontSize: '0.9rem',
          }}
        >
          No duplicate series found.
        </div>
      )}

      {!isLoading && data && data.total_groups > 0 && (
        <>
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              gap: '16px',
              flexWrap: 'wrap',
              marginBottom: '18px',
            }}
          >
            <span style={{ fontSize: '0.82rem', color: 'var(--color-muted)' }}>
              {data.total_groups} group{data.total_groups === 1 ? '' : 's'} ·{' '}
              {data.mergeable_groups} mergeable
              {data.conflicting_groups > 0
                ? ` · ${data.conflicting_groups} need a look`
                : ''}
            </span>
            <MergeAllButton count={data.mergeable_groups} />
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
            {data.groups.map((group) => {
              const keeper = keeperId(group)
              return (
                <div key={group.key} style={{ ...surface, padding: '12px 14px' }}>
                  <div
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: '10px',
                      marginBottom: '8px',
                    }}
                  >
                    <span
                      style={{
                        fontWeight: 600,
                        fontSize: '0.9rem',
                        color: 'var(--color-text)',
                        flex: 1,
                        minWidth: 0,
                      }}
                    >
                      {group.title}
                    </span>
                    {group.conflicting_years ? (
                      <span
                        style={{
                          display: 'flex',
                          alignItems: 'center',
                          gap: '5px',
                          fontSize: '0.72rem',
                          color: 'var(--color-status-wanted)',
                        }}
                      >
                        <AlertTriangle size={13} />
                        Different start years — merge manually if these are one series
                      </span>
                    ) : (
                      <MergeButton group={group} />
                    )}
                  </div>

                  <div style={{ display: 'flex', flexDirection: 'column', gap: '2px' }}>
                    {group.rows.map((row) => (
                      <SeriesRowLine key={row.id} row={row} keeper={row.id === keeper} />
                    ))}
                  </div>

                  {group.mergeable && (
                    <div
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: '6px',
                        marginTop: '8px',
                        paddingTop: '8px',
                        borderTop: '1px solid var(--color-border)',
                        fontSize: '0.72rem',
                        color: 'var(--color-muted)',
                      }}
                    >
                      <ArrowRight size={12} />
                      Becomes one series with{' '}
                      {group.rows.reduce((n, r) => n + r.issue_count, 0)} issues
                      {group.rows.some((r) => r.subscribed) ? ', subscribed' : ''}
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        </>
      )}
    </div>
  )
}
