import { IconInfoCircle, IconKey, IconLogout, IconServer2, IconUser, IconUsers, type Icon } from '@tabler/icons-react'
import { useState, type FormEvent } from 'react'
import { Link, Outlet, useLocation } from 'react-router'
import { useChangePassword, useLogout, useMe } from '../api/auth'
import { ApiError } from '../api/client'
import { ContextMenu } from './files/ContextMenu'
import { FieldError, inputClass } from './FieldInput'
import { Button, Modal } from './ui'

export function Layout() {
  const { data: me } = useMe()
  const [menu, setMenu] = useState<{ x: number; y: number } | null>(null)
  const [changingPassword, setChangingPassword] = useState(false)
  const logout = useLogout()

  return (
    // Phones: content on top, nav bar at the bottom. Wider screens: sidebar on the left.
    <div className="flex h-dvh flex-col-reverse sm:flex-row">
      <nav className="flex shrink-0 justify-around gap-1 p-1.5 sm:w-[5.5rem] sm:flex-col sm:justify-start sm:py-4">
        <div className="mb-4 hidden text-center text-2xl font-semibold tracking-tighter sm:block" aria-hidden>
          &gt;_
        </div>
        {/* A server's page belongs to "servers" too. */}
        <NavItem to="/" matches={(path) => path === '/' || path.startsWith('/servers/')} icon={IconServer2} label="servers" />
        {me?.is_admin && <NavItem to="/users" icon={IconUsers} label="users" />}
        <div className="hidden flex-1 sm:block" />
        <NavItem to="/about" icon={IconInfoCircle} label="about" />
        <button
          onClick={(e) => {
            const rect = e.currentTarget.getBoundingClientRect()
            setMenu({ x: rect.right + 4, y: rect.top })
          }}
          className="flex flex-col items-center gap-1 rounded-xl px-3 py-2.5 text-xs text-zinc-300 transition hover:bg-panel sm:px-1"
          title={me?.username}
        >
          <IconUser size={22} stroke={1.5} />
          <span className="max-w-full truncate">{me?.username}</span>
        </button>
      </nav>

      <main className="min-h-0 flex-1 overflow-y-auto bg-page sm:my-1 sm:mr-1 sm:rounded-2xl sm:border sm:border-line-soft">
        <Outlet />
      </main>

      {menu && (
        <ContextMenu
          x={menu.x}
          y={menu.y}
          onClose={() => setMenu(null)}
          items={[
            { label: 'change password', icon: <IconKey size={16} />, onSelect: () => setChangingPassword(true) },
            { label: 'log out', icon: <IconLogout size={16} />, onSelect: () => logout.mutate() },
          ]}
        />
      )}
      {changingPassword && <ChangePasswordDialog onClose={() => setChangingPassword(false)} />}
    </div>
  )
}

interface NavItemProps {
  to: string
  icon: Icon
  label: string
  matches?: (pathname: string) => boolean
}

function NavItem({ to, icon: Icon, label, matches = (path) => path === to }: NavItemProps) {
  const active = matches(useLocation().pathname)
  return (
    <Link
      to={to}
      aria-current={active ? 'page' : undefined}
      className={`flex flex-col items-center gap-1 rounded-xl px-3 py-2.5 text-xs transition sm:px-1 ${
        active ? 'bg-active text-black' : 'text-zinc-300 hover:bg-panel'
      }`}
    >
      <Icon size={22} stroke={1.5} />
      {label}
    </Link>
  )
}

function ChangePasswordDialog({ onClose }: { onClose: () => void }) {
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [repeat, setRepeat] = useState('')
  const change = useChangePassword()
  const errors = change.error instanceof ApiError ? change.error.fieldErrors : {}

  const submit = (e: FormEvent) => {
    e.preventDefault()
    change.mutate({ current_password: current, new_password: next })
  }

  return (
    <Modal title="change password" onClose={onClose}>
      {change.isSuccess ? (
        <>
          <p className="mb-5 text-sm text-muted">done. other browsers logged in as you were logged out.</p>
          <Button variant="primary" className="w-full" onClick={onClose}>
            close
          </Button>
        </>
      ) : (
        <form onSubmit={submit} className="space-y-3">
          <input type="password" autoComplete="current-password" placeholder="current password" aria-label="current password" className={inputClass} value={current} onChange={(e) => setCurrent(e.target.value)} />
          <FieldError error={errors.current_password} />
          <input type="password" autoComplete="new-password" placeholder="new password (8+ characters)" aria-label="new password" className={inputClass} value={next} onChange={(e) => setNext(e.target.value)} />
          <FieldError error={errors.new_password} />
          <input type="password" autoComplete="new-password" placeholder="repeat new password" aria-label="repeat new password" className={inputClass} value={repeat} onChange={(e) => setRepeat(e.target.value)} />
          {repeat && next !== repeat && <FieldError error="the passwords don't match" />}
          <Button type="submit" variant="primary" className="w-full" disabled={!current || next.length < 8 || next !== repeat || change.isPending}>
            change password
          </Button>
        </form>
      )}
    </Modal>
  )
}
