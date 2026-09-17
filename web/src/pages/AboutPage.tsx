export function AboutPage() {
  return (
    <div className="mx-auto max-w-xl px-4 py-12">
      <img src="/logo.png" alt="" className="mb-6 size-20 select-none" draggable={false} />
      <h1 className="mb-3 text-lg font-semibold">possum</h1>
      <p className="mb-4 text-sm leading-relaxed text-muted">
        a self-hosted panel for running game servers in docker — from minecraft to counter-strike 1.6.
        every game is described by a template, so adding a new one doesn't need new code.
      </p>
      <p className="text-sm text-muted">
        free software, licensed under{' '}
        <a
          href="https://www.gnu.org/licenses/agpl-3.0.html"
          target="_blank"
          rel="noreferrer"
          className="text-zinc-200 underline underline-offset-2"
        >
          agpl-3.0
        </a>
        .
      </p>
    </div>
  )
}
