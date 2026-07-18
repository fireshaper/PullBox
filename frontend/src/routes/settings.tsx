import { createFileRoute, Link, Outlet } from '@tanstack/react-router'

export const Route = createFileRoute('/settings')({
  component: SettingsLayout,
})

const SETTINGS_NAV = [
  { to: '/settings/indexers', label: 'Indexers' },
  { to: '/settings/download-clients', label: 'Download Clients' },
  { to: '/settings/post-processing', label: 'Post-Download' },
  { to: '/settings/library-import', label: 'Import Library' },
] as const

function SettingsLayout() {
  return (
    <div style={{ display: 'flex', height: '100%' }}>
      <aside
        style={{
          width: '168px',
          flexShrink: 0,
          borderRight: '1px solid var(--color-border)',
          padding: '20px 8px',
        }}
      >
        <p
          style={{
            fontSize: '0.68rem',
            fontWeight: 600,
            textTransform: 'uppercase',
            letterSpacing: '0.06em',
            color: 'var(--color-muted)',
            padding: '0 8px',
            marginBottom: '8px',
          }}
        >
          Settings
        </p>
        {SETTINGS_NAV.map(({ to, label }) => (
          <Link
            key={to}
            to={to}
            style={{
              display: 'block',
              padding: '6px 10px',
              borderRadius: '5px',
              fontSize: '0.875rem',
              textDecoration: 'none',
              marginBottom: '2px',
            }}
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
            {label}
          </Link>
        ))}
      </aside>

      <main style={{ flex: 1, overflow: 'auto' }}>
        <Outlet />
      </main>
    </div>
  )
}
