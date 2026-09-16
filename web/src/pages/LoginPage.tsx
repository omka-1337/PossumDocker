import { useState, type FormEvent } from 'react'
import { useLogin } from '../api/auth'
import { inputClass } from '../components/FieldInput'
import { Button } from '../components/ui'

export function LoginPage() {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const login = useLogin()

  const submit = (e: FormEvent) => {
    e.preventDefault()
    login.mutate({ username, password }, { onError: () => setPassword('') })
  }

  return (
    <div className="flex min-h-dvh items-center justify-center bg-page px-4">
      <form onSubmit={submit} className="w-full max-w-sm">
        <div className="mb-8 text-center">
          <img src="/logo.png" alt="" className="mx-auto mb-3 size-24 select-none" draggable={false} />
          <h1 className="text-lg font-semibold">possumdocker</h1>
        </div>

        <div className="space-y-3 rounded-3xl border border-line-soft bg-panel p-5">
          <input
            autoFocus
            autoComplete="username"
            aria-label="name"
            placeholder="name"
            className={inputClass}
            value={username}
            onChange={(e) => setUsername(e.target.value)}
          />
          <input
            type="password"
            autoComplete="current-password"
            aria-label="password"
            placeholder="password"
            className={inputClass}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
          {login.error && <p className="text-sm text-red-400">{login.error.message}</p>}
          <Button type="submit" variant="primary" className="w-full" disabled={!username || !password || login.isPending}>
            {login.isPending ? 'logging in…' : 'log in'}
          </Button>
        </div>
        <p className="mt-4 text-center text-xs text-muted">forgot the password? run `./possum admin` on the server.</p>
      </form>
    </div>
  )
}
