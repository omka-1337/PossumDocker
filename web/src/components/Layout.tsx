import { IconInfoCircle, IconServer2, type Icon } from '@tabler/icons-react'
import { NavLink, Outlet } from 'react-router'

export function Layout() {
  return (
    // Phones: content on top, nav bar at the bottom. Wider screens: sidebar on the left.
    <div className="flex h-dvh flex-col-reverse sm:flex-row">
      <nav className="flex shrink-0 justify-around gap-1 p-1.5 sm:w-[5.5rem] sm:flex-col sm:justify-start sm:py-4">
        <div className="mb-4 hidden text-center text-2xl font-semibold tracking-tighter sm:block" aria-hidden>
          &gt;_
        </div>
        <NavItem to="/" icon={IconServer2} label="servers" />
        <div className="hidden flex-1 sm:block" />
        <NavItem to="/about" icon={IconInfoCircle} label="about" />
      </nav>

      <main className="min-h-0 flex-1 overflow-y-auto bg-page sm:my-1 sm:mr-1 sm:rounded-2xl sm:border sm:border-line-soft">
        <Outlet />
      </main>
    </div>
  )
}

function NavItem({ to, icon: Icon, label }: { to: string; icon: Icon; label: string }) {
  return (
    <NavLink
      to={to}
      end
      className={({ isActive }) =>
        `flex flex-col items-center gap-1 rounded-xl px-3 py-2.5 text-xs transition sm:px-1 ${
          isActive ? 'bg-active text-black' : 'text-zinc-300 hover:bg-panel'
        }`
      }
    >
      <Icon size={22} stroke={1.5} />
      {label}
    </NavLink>
  )
}
