import '@fontsource/ibm-plex-mono/400.css'
import '@fontsource/ibm-plex-mono/500.css'
import '@fontsource/ibm-plex-mono/600.css'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { createBrowserRouter } from 'react-router'
import { App } from './App.tsx'
import { ApiError } from './api/client.ts'
import { Layout } from './components/Layout.tsx'
import './index.css'
import { AboutPage } from './pages/AboutPage.tsx'
import { ServerPage } from './pages/ServerPage.tsx'
import { ServersPage } from './pages/ServersPage.tsx'
import { UsersPage } from './pages/UsersPage.tsx'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // Retrying won't fix "log in first" or "not allowed".
      retry: (count, error) => count < 1 && !(error instanceof ApiError && [401, 403, 404].includes(error.status)),
      refetchOnWindowFocus: false,
    },
  },
})

const router = createBrowserRouter([
  {
    element: <Layout />,
    children: [
      { path: '/', element: <ServersPage /> },
      { path: '/servers/:serverId', element: <ServerPage /> },
      { path: '/users', element: <UsersPage /> },
      { path: '/about', element: <AboutPage /> },
    ],
  },
])

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <App router={router} />
    </QueryClientProvider>
  </StrictMode>,
)
