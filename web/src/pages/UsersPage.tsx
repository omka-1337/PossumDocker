import { IconKey, IconLock, IconLockOpen, IconShield, IconShieldOff, IconTrash, IconUserPlus } from '@tabler/icons-react'
import { useState, type FormEvent } from 'react'
import { useMe, useUserActions, useUsers, type UserInfo } from '../api/auth'
import { ApiError } from '../api/client'
import { FieldError, inputClass } from '../components/FieldInput'
import { Button, IconButton, Modal, Switch } from '../components/ui'
import { formatDate } from '../lib/format'

export function UsersPage() {
  const { data: me } = useMe()
  const { data: users, isPending, isError, error } = useUsers()
  const actions = useUserActions()
  const [creating, setCreating] = useState(false)
  const [resetting, setResetting] = useState<UserInfo | null>(null)
  const [deleting, setDeleting] = useState<UserInfo | null>(null)

  if (!me?.is_admin) return <p className="p-8 text-muted">only administrators can manage users.</p>

  const actionError = actions.update.error ?? actions.remove.error

  return (
    <div className="mx-auto w-full max-w-3xl px-4 py-5">
      <div className="mb-6 flex flex-wrap items-center gap-3">
        <div className="flex-1">
          <h1 className="text-xl font-semibold">users</h1>
          <p className="text-sm text-muted">give people access to a server on its "access" tab.</p>
        </div>
        <Button variant="primary" onClick={() => setCreating(true)}>
          <IconUserPlus size={16} /> new user
        </Button>
      </div>
      {actionError && <p className="mb-4 text-sm text-red-400">{actionError.message}</p>}

      {isPending ? (
        <p className="text-muted">loading…</p>
      ) : isError ? (
        <p className="text-red-400">{error.message}</p>
      ) : (
        <ul className="space-y-2">
          {users.map((user) => {
            const self = user.id === me.id
            return (
              <li key={user.id} className={`flex items-center gap-3 rounded-2xl bg-panel p-3 ${user.disabled ? 'opacity-60' : ''}`}>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2 truncate text-sm font-medium">
                    {user.username}
                    {self && <span className="text-xs font-normal text-muted">(you)</span>}
                    {user.is_admin && <span className="rounded-full bg-raised px-2 py-0.5 text-xs font-normal text-amber-300">admin</span>}
                    {user.disabled && <span className="rounded-full bg-raised px-2 py-0.5 text-xs font-normal text-red-300">disabled</span>}
                  </div>
                  <div className="text-xs text-muted">
                    since {formatDate(Date.parse(user.created_at) / 1000)}
                    {!user.is_admin && ` · ${user.servers} ${user.servers === 1 ? 'server' : 'servers'}`}
                  </div>
                </div>
                <IconButton onClick={() => setResetting(user)} aria-label="set password" title="set password">
                  <IconKey size={18} />
                </IconButton>
                {!self && (
                  <>
                    <IconButton
                      onClick={() => actions.update.mutate({ id: user.id, is_admin: !user.is_admin })}
                      aria-label={user.is_admin ? 'remove admin' : 'make admin'}
                      title={user.is_admin ? 'remove administrator rights' : 'make administrator'}
                    >
                      {user.is_admin ? <IconShieldOff size={18} /> : <IconShield size={18} />}
                    </IconButton>
                    <IconButton
                      onClick={() => actions.update.mutate({ id: user.id, disabled: !user.disabled })}
                      aria-label={user.disabled ? 'enable' : 'disable'}
                      title={user.disabled ? 'enable' : 'disable: logs them out and blocks logging in'}
                    >
                      {user.disabled ? <IconLockOpen size={18} /> : <IconLock size={18} />}
                    </IconButton>
                    <IconButton onClick={() => setDeleting(user)} aria-label="delete" title="delete">
                      <IconTrash size={18} />
                    </IconButton>
                  </>
                )}
              </li>
            )
          })}
        </ul>
      )}

      {creating && <CreateUserDialog onClose={() => setCreating(false)} />}
      {resetting && <SetPasswordDialog user={resetting} onClose={() => setResetting(null)} />}
      {deleting && (
        <Modal title={`delete "${deleting.username}"?`} onClose={() => setDeleting(null)}>
          <p className="mb-5 text-sm text-muted">they lose access to every server. servers themselves are not touched.</p>
          <div className="flex gap-2">
            <Button className="flex-1" onClick={() => setDeleting(null)}>
              cancel
            </Button>
            <Button
              variant="danger"
              className="flex-1"
              onClick={() => actions.remove.mutate(deleting.id, { onSuccess: () => setDeleting(null) })}
            >
              delete
            </Button>
          </div>
        </Modal>
      )}
    </div>
  )
}

function CreateUserDialog({ onClose }: { onClose: () => void }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [isAdmin, setIsAdmin] = useState(false)
  const { create } = useUserActions()
  const errors = create.error instanceof ApiError ? create.error.fieldErrors : {}

  const submit = (e: FormEvent) => {
    e.preventDefault()
    create.mutate({ username: username.trim(), password, is_admin: isAdmin }, { onSuccess: onClose })
  }

  return (
    <Modal title="new user" onClose={onClose}>
      <form onSubmit={submit} className="space-y-4">
        <div>
          <input autoFocus autoComplete="off" placeholder="name" aria-label="name" className={inputClass} value={username} onChange={(e) => setUsername(e.target.value)} />
          <FieldError error={errors.username} />
        </div>
        <div>
          <input type="password" autoComplete="new-password" placeholder="password (8+ characters)" aria-label="password" className={inputClass} value={password} onChange={(e) => setPassword(e.target.value)} />
          <FieldError error={errors.password} />
        </div>
        <div className="flex items-center justify-between gap-4 rounded-xl bg-raised px-3 py-2.5 text-sm">
          <span>
            administrator
            <span className="block text-xs text-muted">sees and manages everything, including users</span>
          </span>
          <Switch checked={isAdmin} onChange={setIsAdmin} />
        </div>
        {create.error && !Object.keys(errors).length && <p className="text-sm text-red-400">{create.error.message}</p>}
        <Button type="submit" variant="primary" className="w-full" disabled={!username.trim() || password.length < 8 || create.isPending}>
          create
        </Button>
      </form>
    </Modal>
  )
}

function SetPasswordDialog({ user, onClose }: { user: UserInfo; onClose: () => void }) {
  const [password, setPassword] = useState('')
  const { update } = useUserActions()
  const errors = update.error instanceof ApiError ? update.error.fieldErrors : {}

  return (
    <Modal title={`new password for ${user.username}`} onClose={onClose}>
      <form
        onSubmit={(e) => {
          e.preventDefault()
          update.mutate({ id: user.id, password }, { onSuccess: onClose })
        }}
        className="space-y-4"
      >
        <input autoFocus type="password" autoComplete="new-password" placeholder="password (8+ characters)" aria-label="new password" className={inputClass} value={password} onChange={(e) => setPassword(e.target.value)} />
        <FieldError error={errors.password} />
        <p className="text-xs text-muted">they are logged out everywhere and log in with the new password.</p>
        <Button type="submit" variant="primary" className="w-full" disabled={password.length < 8 || update.isPending}>
          set password
        </Button>
      </form>
    </Modal>
  )
}
