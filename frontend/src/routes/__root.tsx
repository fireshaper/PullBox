import { useQuery } from '@tanstack/react-query'
import { createRootRoute, Link, Outlet } from '@tanstack/react-router'
import {
  BookOpen,
  CalendarDays,
  CalendarRange,
  Download,
  Layers,
  LayoutDashboard,
  Settings,
} from 'lucide-react'

import { get } from '../api/client'

export const Route = createRootRoute({
  component: AppLayout,
})

const NAV_ITEMS = [
  { to: '/dashboard', label: 'Dashboard', icon: LayoutDashboard },
  { to: '/pull-list', label: 'Pull List', icon: CalendarDays },
  { to: '/calendar', label: 'Calendar', icon: CalendarRange },
  { to: '/series', label: 'Series', icon: BookOpen },
  { to: '/arcs', label: 'Story Arcs', icon: Layers },
  { to: '/queue', label: 'Queue', icon: Download },
  { to: '/settings', label: 'Settings', icon: Settings },
] as const

// The version is set in one place — backend/pyproject.toml — and served by
// /api/health, so the footer shows what the running server actually is.
function useServerVersion() {
  const { data } = useQuery({
    queryKey: ['health'],
    queryFn: () => get<{ version: string }>('/health'),
    staleTime: Infinity,
  })
  return data?.version
}

function AppLayout() {
  const version = useServerVersion()
  return (
    <div className="flex h-screen overflow-hidden">
      <aside
        className="w-56 flex-shrink-0 flex flex-col"
        style={{
          backgroundColor: 'var(--color-surface)',
          borderRight: '1px solid var(--color-border)',
        }}
      >
        <div
          className="px-4 py-5"
          style={{ borderBottom: '1px solid var(--color-border)' }}
        >
          <span
            className="text-xl font-bold tracking-tight"
            style={{ color: 'var(--color-accent)' }}
          >
            PullBox
          </span>
        </div>

        <nav className="flex-1 p-2 space-y-1">
          {NAV_ITEMS.map(({ to, label, icon: Icon }) => (
            <Link
              key={to}
              to={to}
              activeOptions={{ exact: to === '/settings' ? false : false, includeSearch: false }}
              className="flex items-center gap-3 px-3 py-2 rounded-md text-sm transition-colors"
              style={{ color: 'var(--color-muted)' }}
              activeProps={{
                style: {
                  backgroundColor: 'color-mix(in srgb, var(--color-accent) 15%, transparent)',
                  color: 'var(--color-text)',
                },
              }}
              inactiveProps={{
                style: { color: 'var(--color-muted)' },
              }}
            >
              <Icon size={16} />
              <span>{label}</span>
            </Link>
          ))}
        </nav>

        <div
          className="px-4 py-3"
          style={{
            borderTop: '1px solid var(--color-border)',
            color: 'var(--color-muted)',
          }}
        >
          {version && <span className="text-xs">v{version}</span>}
        </div>
      </aside>

      <main className="flex-1 overflow-auto" style={{ backgroundColor: 'var(--color-bg)' }}>
        <Outlet />
      </main>
    </div>
  )
}
