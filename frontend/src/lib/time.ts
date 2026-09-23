/** Parse a backend timestamp to epoch ms.
 *
 *  The backend stores naive UTC datetimes (SQLite drops tzinfo), so most API
 *  timestamps arrive without an offset — and `Date.parse` reads an offset-less
 *  ISO datetime as *local* time, skewing it by the viewer's UTC offset. Treat a
 *  missing offset as UTC; strings that carry one are parsed as-is. */
export function parseServerTime(iso: string): number {
  return Date.parse(/(Z|[+-]\d{2}:?\d{2})$/i.test(iso) ? iso : `${iso}Z`)
}
