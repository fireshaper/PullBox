import { describe, expect, it } from 'vitest'
import { parseServerTime } from './time'

describe('parseServerTime', () => {
  const utc = Date.UTC(2026, 8, 22, 12, 0, 0)

  it('reads an offset-less timestamp as UTC, not local time', () => {
    expect(parseServerTime('2026-09-22T12:00:00')).toBe(utc)
    expect(parseServerTime('2026-09-22T12:00:00.000000')).toBe(utc)
  })

  it('respects an explicit offset', () => {
    expect(parseServerTime('2026-09-22T12:00:00Z')).toBe(utc)
    expect(parseServerTime('2026-09-22T08:00:00-04:00')).toBe(utc)
  })
})
