import type { ReactNode } from 'react'

export function NoticeDot({ className = 'mt-0.5' }: { className?: string }) {
  return <span aria-hidden="true" className={`h-2 w-2 shrink-0 rounded-full bg-orange-600 dark:bg-orange-400 ${className}`} />
}

export function NoticeLabel({ children, notice }: { children: ReactNode; notice?: string | null }) {
  return notice ? (
    <span className="inline-flex items-start gap-0.5" title={notice}>
      {children}
      <NoticeDot />
    </span>
  ) : children
}
