import { useState } from 'react'
import { ConfirmDialog } from './ConfirmDialog'

export function ResetBrowserDataButton({ disabled, onReset }: {
  disabled: boolean
  onReset: () => void
}) {
  const [open, setOpen] = useState(false)

  return (
    <>
      <button
        type="button"
        disabled={disabled}
        onClick={() => setOpen(true)}
        className="rounded-lg px-2 py-1.5 text-sm text-red-600 hover:bg-red-50 dark:text-red-400 dark:hover:bg-red-900/20 disabled:opacity-50"
      >
        Reset browser data
      </button>
      <ConfirmDialog
        open={open}
        message="Clear all data for this browser profile, including saved sign-in and site data? Your schedule settings will be kept. You’ll need to log in again."
        confirmLabel="Reset browser data"
        danger
        onConfirm={() => {
          setOpen(false)
          if (!disabled) onReset()
        }}
        onCancel={() => setOpen(false)}
      />
    </>
  )
}
