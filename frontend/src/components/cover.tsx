import { type CSSProperties, type ReactNode, useState } from 'react'

/** Issue / series / arc cover thumbnail.
 *
 *  Covers are hotlinked from the metadata provider's CDN, and a busy pull-list
 *  week renders a couple of hundred of them, so they load lazily and decode off
 *  the main thread. The img carries the placeholder colour as its own background
 *  so the slot is visibly reserved while the bytes arrive, and a dead provider
 *  URL falls back to the placeholder rather than the browser's broken-image icon.
 *
 *  `eager` is for the one cover that is the page's main content above the fold
 *  (the series header) — lazy-loading that would only delay it. */
export function Cover({
  url,
  alt = '',
  width,
  height = Math.round(width * 1.4),
  radius = 4,
  eager = false,
  fallback,
}: {
  url: string | null | undefined
  alt?: string
  width: number
  height?: number
  radius?: number
  eager?: boolean
  /** Rendered centred inside the placeholder when there is no usable image. */
  fallback?: ReactNode
}) {
  // Keyed on the URL itself so a row whose cover changes gets a fresh attempt
  // without needing an effect to reset the flag.
  const [failedUrl, setFailedUrl] = useState<string | null>(null)

  const box: CSSProperties = {
    width,
    height,
    borderRadius: radius,
    background: 'var(--color-border)',
    flexShrink: 0,
  }

  if (!url || failedUrl === url) {
    return (
      <div
        style={
          fallback
            ? { ...box, display: 'flex', alignItems: 'center', justifyContent: 'center' }
            : box
        }
      >
        {fallback}
      </div>
    )
  }

  return (
    <img
      src={url}
      alt={alt}
      width={width}
      height={height}
      loading={eager ? 'eager' : 'lazy'}
      fetchPriority={eager ? 'high' : undefined}
      decoding="async"
      onError={() => setFailedUrl(url)}
      style={{ ...box, objectFit: 'cover' }}
    />
  )
}
