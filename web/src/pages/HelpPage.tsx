import type { ComponentProps } from 'react'
import Markdown from 'react-markdown'
import { Link } from 'react-router'
import rehypeSlug from 'rehype-slug'
import remarkGfm from 'remark-gfm'
// The same file as on GitHub: docs/ is the only copy, the panel shows it as it is.
import gameTemplates from '../../../docs/create-game-template.md?raw'

// Links between docs and to the repository work on GitHub; in the panel, in-page ones stay here and the
// rest open GitHub in a new tab.
function DocLink({ href = '', children }: ComponentProps<'a'>) {
  const className = 'text-zinc-100 underline underline-offset-2 hover:text-zinc-50'
  if (href.startsWith('#')) {
    return (
      <a href={href} className={className}>
        {children}
      </a>
    )
  }
  return (
    <a href={href} target="_blank" rel="noreferrer" className={className}>
      {children}
    </a>
  )
}

const components: ComponentProps<typeof Markdown>['components'] = {
  h1: (props) => <h1 className="mb-4 text-2xl font-semibold" {...props} />,
  h2: (props) => <h2 className="mt-10 mb-3 scroll-mt-4 border-b border-line-soft pb-2 text-lg font-semibold" {...props} />,
  h3: (props) => <h3 className="mt-7 mb-2 scroll-mt-4 font-semibold" {...props} />,
  p: (props) => <p className="my-3 leading-relaxed text-zinc-300" {...props} />,
  a: DocLink,
  ul: (props) => <ul className="my-3 list-disc space-y-1 pl-5 text-zinc-300" {...props} />,
  ol: (props) => <ol className="my-3 list-decimal space-y-1 pl-5 text-zinc-300" {...props} />,
  strong: (props) => <strong className="font-semibold text-zinc-100" {...props} />,
  pre: (props) => <pre className="my-4 overflow-x-auto rounded-xl bg-panel p-4 text-xs leading-relaxed" {...props} />,
  code: ({ className, ...props }) =>
    // Fenced blocks carry a language class and sit in <pre>; the rest is inline.
    className ? (
      <code className={className} {...props} />
    ) : (
      <code className="rounded bg-raised px-1 py-0.5 text-[0.9em] text-zinc-100" {...props} />
    ),
  table: (props) => (
    <div className="my-4 overflow-x-auto">
      <table className="w-full border-collapse text-left text-xs" {...props} />
    </div>
  ),
  th: (props) => <th className="border-b border-line px-2 py-2 font-semibold text-zinc-200" {...props} />,
  td: (props) => <td className="border-b border-line-soft px-2 py-2 align-top text-zinc-300" {...props} />,
}

function HelpPage() {
  return (
    <div className="mx-auto w-full max-w-4xl px-4 py-8 text-sm">
      <Link to="/" className="mb-6 inline-block text-muted hover:text-zinc-200">
        ← servers
      </Link>
      <Markdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeSlug]} components={components}>
        {gameTemplates}
      </Markdown>
    </div>
  )
}

export default HelpPage
