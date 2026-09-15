import { useQueryClient } from '@tanstack/react-query'
import { useEffect } from 'react'
import { RouterProvider, type createBrowserRouter } from 'react-router'
import { meKey, useMe } from './api/auth'
import { UNAUTHORIZED_EVENT } from './api/client'
import { LoginPage } from './pages/LoginPage'

/** The login page until there's a session; the app after. */
export function App({ router }: { router: ReturnType<typeof createBrowserRouter> }) {
  const { data: me, isPending, isError, error } = useMe()
  const client = useQueryClient()

  useEffect(() => {
    // Any request answered with 401 (session expired, logged out elsewhere) brings the login back.
    const onUnauthorized = () => client.setQueryData(meKey, null)
    window.addEventListener(UNAUTHORIZED_EVENT, onUnauthorized)
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, onUnauthorized)
  }, [client])

  if (isPending) return null
  if (isError) return <p className="p-8 text-red-400">could not reach the panel: {error.message}</p>
  if (!me) return <LoginPage />
  return <RouterProvider router={router} />
}

