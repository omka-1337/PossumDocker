import { lazy, Suspense } from 'react'

/** Where the "how to add a game" help lives. Not /docs: that is the panel API's own documentation. */
export const HELP_GAME_TEMPLATES = '/help/game-templates'

// Markdown rendering is only needed on the help page: keep it out of the main bundle.
const HelpPage = lazy(() => import('./HelpPage'))

export function HelpRoute() {
  return (
    <Suspense fallback={<p className="p-8 text-sm text-muted">loading…</p>}>
      <HelpPage />
    </Suspense>
  )
}
