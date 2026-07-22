import { describe, it, expect, vi, beforeEach } from 'vitest'
import { get, post, patch, del, ApiError } from './client'

const mockFetch = vi.fn<typeof fetch>()

beforeEach(() => {
  vi.stubGlobal('fetch', mockFetch)
  mockFetch.mockReset()
})

function makeResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('get()', () => {
  it('throws ApiError with correct status on 404', async () => {
    mockFetch.mockResolvedValue(makeResponse(404, { detail: 'Not found' }))
    await expect(get('/nonexistent')).rejects.toThrow(ApiError)
    await expect(get('/nonexistent')).rejects.toMatchObject({ status: 404 })
  })

  it('throws ApiError on 500 and extracts message', async () => {
    mockFetch.mockResolvedValue(makeResponse(500, { detail: 'Internal server error' }))
    const err = await get('/fail').catch((e: unknown) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect((err as ApiError).status).toBe(500)
    expect((err as ApiError).message).toBe('Internal server error')
  })

  it('returns parsed JSON body on 200', async () => {
    const data = { status: 'ok', debug: false }
    mockFetch.mockResolvedValue(makeResponse(200, data))
    const result = await get<typeof data>('/health')
    expect(result).toEqual(data)
  })

  it('prefixes the path with /api', async () => {
    mockFetch.mockResolvedValue(makeResponse(200, {}))
    await get('/health')
    expect(mockFetch).toHaveBeenCalledWith('/api/health', expect.any(Object))
  })
})

describe('post()', () => {
  it('sets Content-Type and sends JSON body', async () => {
    mockFetch.mockResolvedValue(makeResponse(201, { id: 1 }))
    await post('/series', { metron_id: '12345' })
    const [, options] = mockFetch.mock.calls[0]
    expect((options as RequestInit).method).toBe('POST')
    expect((options as RequestInit & { headers: Record<string, string> }).headers['Content-Type']).toBe(
      'application/json',
    )
    expect((options as RequestInit).body).toBe(JSON.stringify({ metron_id: '12345' }))
  })

  it('returns parsed response body', async () => {
    mockFetch.mockResolvedValue(makeResponse(200, { id: 42 }))
    const result = await post<{ id: number }>('/enqueue/1')
    expect(result.id).toBe(42)
  })
})

describe('patch()', () => {
  it('sends PATCH with JSON body', async () => {
    mockFetch.mockResolvedValue(makeResponse(200, { subscribed: true }))
    await patch('/series/1', { subscribed: true })
    const [, options] = mockFetch.mock.calls[0]
    expect((options as RequestInit).method).toBe('PATCH')
  })
})

describe('del()', () => {
  it('returns undefined on 204', async () => {
    mockFetch.mockResolvedValue(new Response(null, { status: 204 }))
    const result = await del('/queue/1')
    expect(result).toBeUndefined()
  })
})
