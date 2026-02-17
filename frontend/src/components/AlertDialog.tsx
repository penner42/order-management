type AlertDialogProps = {
  open: boolean
  message: string
  onClose: () => void
}

export function AlertDialog({ open, message, onClose }: AlertDialogProps) {
  if (!open) return null
  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50" onClick={onClose}>
      <div
        className="bg-white dark:bg-gray-800 rounded-xl shadow-xl p-6 max-w-md w-full mx-4 border border-brand-200/80 dark:border-gray-700"
        onClick={(e) => e.stopPropagation()}
      >
        <p className="text-ink mb-6">{message}</p>
        <div className="flex justify-end">
          <button
            type="button"
            onClick={onClose}
            className="px-3 py-1.5 bg-brand-600 text-white rounded-lg text-sm hover:bg-brand-700"
          >
            OK
          </button>
        </div>
      </div>
    </div>
  )
}
