import { Link } from 'react-router'
import { PERMISSIONS, useServerAccess, useSetAccess, type AccessEntry, type Permission } from '../api/auth'

/** Administrators only: who may do what on this server. */
export function AccessTab({ serverId }: { serverId: string }) {
  const { data: entries, isPending, isError, error } = useServerAccess(serverId)
  const setAccess = useSetAccess(serverId)

  if (isPending) return <p className="text-sm text-muted">loading…</p>
  if (isError) return <p className="text-sm text-red-400">{error.message}</p>

  const toggle = (entry: AccessEntry, permission: Permission) => {
    const has = entry.permissions.includes(permission)
    let next: Permission[]
    if (permission === 'view') {
      // Taking "view" away removes all access: nothing else works without seeing the server.
      next = has ? [] : ['view']
    } else {
      next = has ? entry.permissions.filter((p) => p !== permission) : [...entry.permissions, permission]
    }
    setAccess.mutate({ userId: entry.user_id, permissions: next })
  }

  if (entries.length === 0) {
    return (
      <div className="rounded-xl border border-dashed border-line p-10 text-center text-sm text-muted">
        <p className="text-zinc-200">no other users yet</p>
        <p className="mt-1">
          create them on the{' '}
          <Link to="/users" className="text-zinc-200 underline">
            users
          </Link>{' '}
          page, then choose here what each of them may do. administrators can always do everything.
        </p>
      </div>
    )
  }

  return (
    <div className="pb-10">
      <p className="mb-4 text-sm text-muted">administrators aren't listed: they can do everything on every server.</p>
      {setAccess.error && <p className="mb-4 text-sm text-red-400">{setAccess.error.message}</p>}
      <div className="overflow-x-auto rounded-2xl bg-panel">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-line-soft text-xs text-muted">
              <th className="px-3 py-2 text-left font-normal">user</th>
              {PERMISSIONS.map((p) => (
                <th key={p.value} className="px-2 py-2 font-normal whitespace-nowrap" title={p.help}>
                  {p.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {entries.map((entry) => (
              <tr key={entry.user_id} className={`border-b border-line-soft last:border-0 ${entry.disabled ? 'opacity-50' : ''}`}>
                <td className="px-3 py-2.5 whitespace-nowrap">{entry.username}</td>
                {PERMISSIONS.map((p) => (
                  <td key={p.value} className="px-2 py-2.5 text-center">
                    <input
                      type="checkbox"
                      className="size-4 accent-emerald-500"
                      aria-label={`${entry.username}: ${p.label}`}
                      title={p.help}
                      checked={entry.permissions.includes(p.value)}
                      disabled={setAccess.isPending}
                      onChange={() => toggle(entry, p.value)}
                    />
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
