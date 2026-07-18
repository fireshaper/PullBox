import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Search } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { get, post } from '../../api/client'
import { Skeleton } from '../../components/ui/skeleton'

// ── Types ─────────────────────────────────────────────────────────────────────

type SeriesRow = {
  id: number
  comicvine_id: string
  title: string
  publisher: string | null
  start_year: number | null
  subscribed: boolean
  cover_url: string | null
}

type PaginatedSeries = {
  total: number
  page: number
  per_page: number
  items: SeriesRow[]
}

type CvSearchResult = {
  comicvine_id: string
  title: string
  publisher: string | null
  start_year: number | null
  cover_url: string | null
  issue_count: number
  in_library: boolean
}

type AddSeriesResponse = { id: number }

// ── Route ─────────────────────────────────────────────────────────────────────

export const Route = createFileRoute('/series/')({
  component: SeriesPage,
})

// ── Alphabet helpers ──────────────────────────────────────────────────────────

const ALPHABET = ['#', ...'ABCDEFGHIJKLMNOPQRSTUVWXYZ'.split('')]

// Bucket a title under its first alphanumeric character; anything else (numbers,
// symbols, leading punctuation) lands under '#'. Kept in sync with the backend's
// case-insensitive title ordering.
function letterOf(title: string): string {
  const ch = title.trim().charAt(0).toUpperCase()
  return ch >= 'A' && ch <= 'Z' ? ch : '#'
}

// How many rows to reveal initially and per scroll step (client-side infinite
// scroll — all data is already loaded, this just keeps the DOM light).
const REVEAL_STEP = 40

// ── Sub-components ────────────────────────────────────────────────────────────

function CoverThumb({ url, alt }: { url: string | null; alt: string }) {
  return url ? (
    <img
      src={url}
      alt={alt}
      style={{ width: 40, height: 55, objectFit: 'cover', borderRadius: '4px', flexShrink: 0 }}
    />
  ) : (
    <div
      style={{ width: 40, height: 55, borderRadius: '4px', background: 'var(--color-border)', flexShrink: 0 }}
    />
  )
}

const ROW_STYLE: React.CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  gap: '12px',
  padding: '10px 14px',
  background: 'var(--color-surface)',
  border: '1px solid var(--color-border)',
  borderRadius: '6px',
}

function MetaLine({ publisher, startYear }: { publisher: string | null; startYear: number | null }) {
  const parts = [publisher, startYear].filter(Boolean)
  if (parts.length === 0) return null
  return (
    <div style={{ fontSize: '0.75rem', color: 'var(--color-muted)', marginTop: '2px' }}>
      {parts.join(' · ')}
    </div>
  )
}

function LetterHeader({ letter }: { letter: string }) {
  return (
    <div
      id={`series-letter-${letter}`}
      style={{
        scrollMarginTop: '12px',
        fontSize: '0.7rem',
        fontWeight: 700,
        letterSpacing: '0.05em',
        color: 'var(--color-muted)',
        padding: '10px 4px 2px',
      }}
    >
      {letter}
    </div>
  )
}

function AlphabetRail({
  present,
  onJump,
}: {
  present: Set<string>
  onJump: (letter: string) => void
}) {
  return (
    <div
      style={{
        position: 'sticky',
        top: 0,
        alignSelf: 'flex-start',
        display: 'flex',
        flexDirection: 'column',
        gap: '1px',
        paddingLeft: '8px',
        userSelect: 'none',
      }}
    >
      {ALPHABET.map((letter) => {
        const enabled = present.has(letter)
        return (
          <button
            key={letter}
            onClick={() => enabled && onJump(letter)}
            disabled={!enabled}
            aria-label={`Jump to ${letter}`}
            style={{
              background: 'none',
              border: 'none',
              padding: '0 4px',
              lineHeight: 1.15,
              fontSize: '0.68rem',
              fontWeight: 600,
              cursor: enabled ? 'pointer' : 'default',
              color: enabled ? 'var(--color-accent)' : 'var(--color-border)',
            }}
          >
            {letter}
          </button>
        )
      })}
    </div>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────

function SeriesPage() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [query, setQuery] = useState('')
  const [debouncedQuery, setDebouncedQuery] = useState('')

  useEffect(() => {
    const t = setTimeout(() => setDebouncedQuery(query), 400)
    return () => clearTimeout(t)
  }, [query])

  const isSearching = debouncedQuery.trim().length >= 2

  const { data: library, isLoading: libLoading } = useQuery<PaginatedSeries>({
    queryKey: ['series', 'library'],
    queryFn: () => get<PaginatedSeries>('/series/?subscribed=true&all=true'),
    enabled: !isSearching,
  })

  const { data: searchResults, isLoading: searchLoading } = useQuery<CvSearchResult[]>({
    queryKey: ['series', 'search', debouncedQuery.trim()],
    queryFn: () =>
      get<CvSearchResult[]>(`/series/search?q=${encodeURIComponent(debouncedQuery.trim())}`),
    enabled: isSearching,
  })

  const addMutation = useMutation({
    mutationFn: (comicvine_id: string) =>
      post<AddSeriesResponse>('/series/', { comicvine_id, subscribed: false, auto_download: false }),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['series', 'library'] })
      navigate({ to: '/series/$seriesId', params: { seriesId: String(data.id) } })
    },
  })

  // ── Client-side infinite scroll + alphabet index ────────────────────────────

  const seriesList = library?.items
  const present = useMemo(() => {
    const set = new Set<string>()
    for (const s of seriesList ?? []) set.add(letterOf(s.title))
    return set
  }, [seriesList])

  const [visibleCount, setVisibleCount] = useState(REVEAL_STEP)
  const [pendingJump, setPendingJump] = useState<string | null>(null)
  const sentinelRef = useRef<HTMLDivElement | null>(null)

  // Reset the reveal window whenever the underlying list changes.
  useEffect(() => {
    setVisibleCount(REVEAL_STEP)
  }, [seriesList])

  const total = seriesList?.length ?? 0

  // Reveal more rows as the sentinel scrolls into view.
  useEffect(() => {
    const node = sentinelRef.current
    if (!node || visibleCount >= total) return
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries[0].isIntersecting) {
          setVisibleCount((c) => Math.min(c + REVEAL_STEP, total))
        }
      },
      { rootMargin: '400px' },
    )
    observer.observe(node)
    return () => observer.disconnect()
  }, [visibleCount, total])

  // Jump to a letter: reveal its section if needed, then flag it for scrolling.
  function jumpToLetter(letter: string) {
    if (!seriesList) return
    const index = seriesList.findIndex((s) => letterOf(s.title) === letter)
    if (index < 0) return
    if (index >= visibleCount) {
      setVisibleCount(Math.min(index + REVEAL_STEP, total))
    }
    setPendingJump(letter)
  }

  // Scroll to a pending jump only once its section header is in the DOM — runs
  // after the reveal above has committed, so the target always exists.
  useEffect(() => {
    if (!pendingJump) return
    const el = document.getElementById(`series-letter-${pendingJump}`)
    if (el) {
      el.scrollIntoView({ behavior: 'smooth', block: 'start' })
      setPendingJump(null)
    }
  }, [pendingJump, visibleCount])

  const showLoading = isSearching ? searchLoading : libLoading

  return (
    <div className="p-6">
      {/* Header */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          marginBottom: '20px',
        }}
      >
        <h1 style={{ fontSize: '1.5rem', fontWeight: 700, color: 'var(--color-text)' }}>Series</h1>
        {!isSearching && library && (
          <span style={{ fontSize: '0.8rem', color: 'var(--color-muted)' }}>
            {library.total} subscribed
          </span>
        )}
      </div>

      {/* Search bar */}
      <div style={{ position: 'relative', marginBottom: '20px' }}>
        <Search
          size={15}
          style={{
            position: 'absolute',
            left: '12px',
            top: '50%',
            transform: 'translateY(-50%)',
            color: 'var(--color-muted)',
            pointerEvents: 'none',
          }}
        />
        <input
          type="text"
          placeholder="Search ComicVine to add a series…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          style={{
            width: '100%',
            paddingLeft: '36px',
            paddingRight: '12px',
            paddingTop: '8px',
            paddingBottom: '8px',
            background: 'var(--color-surface)',
            border: '1px solid var(--color-border)',
            borderRadius: '6px',
            color: 'var(--color-text)',
            fontSize: '0.875rem',
            outline: 'none',
            boxSizing: 'border-box',
          }}
        />
      </div>

      {/* Loading skeletons */}
      {showLoading && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
          {[1, 2, 3, 4].map((i) => (
            <Skeleton key={i} style={{ height: '68px', width: '100%', borderRadius: '6px' }} />
          ))}
        </div>
      )}

      {/* ComicVine search results */}
      {isSearching && !searchLoading && searchResults && (
        <>
          {searchResults.length === 0 ? (
            <div
              style={{
                textAlign: 'center',
                padding: '48px 0',
                color: 'var(--color-muted)',
                fontSize: '0.875rem',
              }}
            >
              No results for "{debouncedQuery.trim()}"
            </div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
              {searchResults.map((r) => (
                <div key={r.comicvine_id} style={ROW_STYLE}>
                  <CoverThumb url={r.cover_url} alt={r.title} />
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div
                      style={{
                        fontWeight: 600,
                        fontSize: '0.875rem',
                        color: 'var(--color-text)',
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                        whiteSpace: 'nowrap',
                      }}
                    >
                      {r.title}
                    </div>
                    <MetaLine publisher={r.publisher} startYear={r.start_year} />
                    <div style={{ fontSize: '0.75rem', color: 'var(--color-muted)', marginTop: '2px' }}>
                      {r.issue_count} issues
                    </div>
                  </div>

                  {r.in_library ? (
                    <span
                      style={{
                        fontSize: '0.75rem',
                        fontWeight: 600,
                        color: 'var(--color-status-downloaded)',
                        flexShrink: 0,
                      }}
                    >
                      In Library
                    </span>
                  ) : (
                    <button
                      onClick={() => addMutation.mutate(r.comicvine_id)}
                      disabled={addMutation.isPending && addMutation.variables === r.comicvine_id}
                      style={{
                        padding: '6px 14px',
                        borderRadius: '5px',
                        fontSize: '0.8rem',
                        fontWeight: 600,
                        background: 'var(--color-accent)',
                        color: '#fff',
                        border: 'none',
                        cursor:
                          addMutation.isPending && addMutation.variables === r.comicvine_id
                            ? 'wait'
                            : 'pointer',
                        flexShrink: 0,
                        opacity:
                          addMutation.isPending && addMutation.variables !== r.comicvine_id
                            ? 0.5
                            : 1,
                      }}
                    >
                      {addMutation.isPending && addMutation.variables === r.comicvine_id
                        ? 'Adding…'
                        : 'Add'}
                    </button>
                  )}
                </div>
              ))}
            </div>
          )}
        </>
      )}

      {/* Library list */}
      {!isSearching && !libLoading && library && seriesList && (
        <>
          {seriesList.length === 0 ? (
            <div
              style={{
                textAlign: 'center',
                padding: '48px 0',
                color: 'var(--color-muted)',
                fontSize: '0.875rem',
              }}
            >
              No subscribed series yet. Search above to add one.
            </div>
          ) : (
            <div style={{ display: 'flex', gap: '4px', alignItems: 'flex-start' }}>
              {/* Rows */}
              <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: '6px' }}>
                {seriesList.slice(0, visibleCount).map((s, i) => {
                  const letter = letterOf(s.title)
                  const showHeader = i === 0 || letterOf(seriesList[i - 1].title) !== letter
                  return (
                    <div key={s.id}>
                      {showHeader && <LetterHeader letter={letter} />}
                      <div
                        onClick={() =>
                          navigate({ to: '/series/$seriesId', params: { seriesId: String(s.id) } })
                        }
                        onKeyDown={(e) => {
                          if (e.key === 'Enter')
                            navigate({ to: '/series/$seriesId', params: { seriesId: String(s.id) } })
                        }}
                        role="button"
                        tabIndex={0}
                        style={{ ...ROW_STYLE, cursor: 'pointer' }}
                      >
                        <CoverThumb url={s.cover_url} alt={s.title} />
                        <div style={{ flex: 1, minWidth: 0 }}>
                          <div
                            style={{
                              fontWeight: 600,
                              fontSize: '0.875rem',
                              color: 'var(--color-text)',
                              overflow: 'hidden',
                              textOverflow: 'ellipsis',
                              whiteSpace: 'nowrap',
                            }}
                          >
                            {s.title}
                          </div>
                          <MetaLine publisher={s.publisher} startYear={s.start_year} />
                        </div>
                      </div>
                    </div>
                  )
                })}
                {/* Reveal sentinel */}
                {visibleCount < total && <div ref={sentinelRef} style={{ height: 1 }} />}
              </div>

              {/* Alphabet jump rail */}
              <AlphabetRail present={present} onJump={jumpToLetter} />
            </div>
          )}
        </>
      )}
    </div>
  )
}
