import { describe, expect, it } from 'vitest'
import { ACTIVE_POLL_MS, IDLE_POLL_MS, isJobActive, queuePollInterval } from './queuePolling'

const NOW = Date.parse('2026-09-22T12:00:00Z')
// The API sends naive UTC (no offset), so the fixtures do too.
const naive = (ms: number) => new Date(ms).toISOString().replace(/Z$/, '')
const ago = (ms: number) => naive(NOW - ms)

describe('isJobActive', () => {
  it.each(['searching', 'pending', 'downloading'])('treats %s as active', (status) => {
    expect(isJobActive({ status, updated_at: ago(60 * 60_000) }, NOW)).toBe(true)
  })

  it('treats a just-queued job as active', () => {
    expect(isJobActive({ status: 'queued', updated_at: ago(10_000) }, NOW)).toBe(true)
  })

  it('treats a long-waiting queued job as idle', () => {
    expect(isJobActive({ status: 'queued', updated_at: ago(10 * 60_000) }, NOW)).toBe(false)
  })

  it.each(['failed', 'completed'])('treats %s as idle', (status) => {
    expect(isJobActive({ status, updated_at: ago(0) }, NOW)).toBe(false)
  })
})

describe('queuePollInterval', () => {
  it('is slow with no data or an idle queue', () => {
    expect(queuePollInterval(undefined)).toBe(IDLE_POLL_MS)
    expect(queuePollInterval([{ status: 'failed', updated_at: naive(Date.now()) }])).toBe(
      IDLE_POLL_MS,
    )
  })

  it('is fast when any job is moving', () => {
    expect(
      queuePollInterval([
        { status: 'failed', updated_at: naive(Date.now()) },
        { status: 'downloading', updated_at: naive(Date.now()) },
      ]),
    ).toBe(ACTIVE_POLL_MS)
  })
})
