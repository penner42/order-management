import { useState, useRef, useEffect, useLayoutEffect } from 'react'
import { createPortal } from 'react-dom'

export interface SearchableOption {
  id: number
  name: string
}

interface SearchableComboboxProps<T extends SearchableOption> {
  options: T[]
  value: T | null
  onChange: (item: T | null) => void
  onCreate?: (name: string) => Promise<T>
  placeholder?: string
  label?: string
  required?: boolean
  allowEmpty?: boolean
  disabled?: boolean
  inputClassName?: string
}

export function SearchableCombobox<T extends SearchableOption>({
  options,
  value,
  onChange,
  onCreate,
  placeholder = 'Type to search…',
  label,
  required = false,
  allowEmpty = false,
  disabled = false,
  inputClassName,
}: SearchableComboboxProps<T>) {
  const [query, setQuery] = useState('')
  const [isOpen, setIsOpen] = useState(false)
  const [creating, setCreating] = useState(false)
  const [dropdownPosition, setDropdownPosition] = useState<{ top: number; left: number; width: number } | null>(null)
  const containerRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const dropdownRef = useRef<HTMLUListElement>(null)

  const displayValue = value?.name ?? ''
  const q = query.trim().toLowerCase()
  const filtered = q
    ? options.filter((o) => o.name.toLowerCase().includes(q))
    : options
  const exactMatch = options.find(
    (o) => o.name.toLowerCase() === q
  )
  const showAddOption = onCreate && q && !exactMatch

  useEffect(() => {
    if (!isOpen) setQuery(displayValue)
  }, [isOpen, displayValue])

  useLayoutEffect(() => {
    if (!isOpen || !inputRef.current) {
      setDropdownPosition(null)
      return
    }
    const rect = inputRef.current.getBoundingClientRect()
    const gap = 4
    const maxHeight = 192 // max-h-48
    const spaceBelow = window.innerHeight - rect.bottom - gap
    const openUpward = spaceBelow < maxHeight && rect.top > spaceBelow
    setDropdownPosition({
      left: rect.left,
      width: rect.width,
      top: openUpward ? rect.top - maxHeight - gap : rect.bottom + gap,
    })
  }, [isOpen])

  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      const target = e.target as Node
      const inContainer = containerRef.current?.contains(target)
      const inDropdown = dropdownRef.current?.contains(target)
      if (!inContainer && !inDropdown) {
        setIsOpen(false)
      }
    }
    const handleFocusIn = (e: FocusEvent) => {
      const target = e.target as Node
      const inContainer = containerRef.current?.contains(target)
      if (!inContainer) {
        setIsOpen(false)
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    document.addEventListener('focusin', handleFocusIn)
    return () => {
      document.removeEventListener('mousedown', handleClickOutside)
      document.removeEventListener('focusin', handleFocusIn)
    }
  }, [])

  const handleSelect = (item: T | null) => {
    onChange(item)
    setQuery(item?.name ?? '')
    setIsOpen(false)
  }

  const handleAddNew = async () => {
    const nameToCreate = query.trim()
    if (!onCreate || !nameToCreate || creating) return
    setCreating(true)
    try {
      const created = await onCreate(nameToCreate)
      onChange(created)
      setQuery(created.name)
      setIsOpen(false)
    } catch (e) {
      console.error(e)
    } finally {
      setCreating(false)
    }
  }

  const handleInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setQuery(e.target.value)
    if (!isOpen) setIsOpen(true)
  }

  const handleInputFocus = () => setIsOpen(true)

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape') {
      setIsOpen(false)
      setQuery(displayValue)
    }
  }

  return (
    <div ref={containerRef} className="relative">
      {label && (
        <label className="block text-sm font-medium text-ink mb-1">
          {label}
          {required && <span className="text-red-500 ml-0.5">*</span>}
        </label>
      )}
      <input
        ref={inputRef}
        type="text"
        className={inputClassName ?? 'rounded-lg border border-brand-200 px-3 py-2 text-ink w-full min-w-[160px] focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-transparent disabled:opacity-60 disabled:cursor-not-allowed'}
        placeholder={placeholder}
        value={query}
        onChange={handleInputChange}
        onFocus={handleInputFocus}
        onKeyDown={handleKeyDown}
        disabled={disabled}
        autoComplete="off"
      />
      {isOpen &&
        dropdownPosition &&
        createPortal(
          <ul
            ref={dropdownRef}
            className="fixed z-[100] max-h-48 overflow-auto rounded-lg border border-brand-200 dark:border-gray-600 bg-white dark:bg-gray-800 shadow-lg py-0.5"
            role="listbox"
            style={{
              top: dropdownPosition.top,
              left: dropdownPosition.left,
              width: dropdownPosition.width,
            }}
          >
            {allowEmpty && (
              <li
                role="option"
                className="px-3 py-0.5 cursor-pointer hover:bg-brand-100 dark:hover:bg-gray-700 text-ink-muted text-sm"
                onMouseDown={(e) => {
                  e.preventDefault()
                  handleSelect(null)
                }}
              >
                —
              </li>
            )}
            {filtered.map((opt) => (
              <li
                key={opt.id}
                role="option"
                className={`px-3 py-0.5 cursor-pointer hover:bg-brand-100 dark:hover:bg-gray-700 text-sm ${
                  value?.id === opt.id ? 'bg-brand-50 dark:bg-gray-700' : ''
                }`}
                onMouseDown={(e) => {
                  e.preventDefault()
                  handleSelect(opt)
                }}
              >
                {opt.name}
              </li>
            ))}
            {showAddOption && (
              <li
                role="option"
                className="px-3 py-0.5 cursor-pointer hover:bg-brand-100 dark:hover:bg-gray-700 text-brand-600 border-t border-brand-100 dark:border-gray-600 text-sm"
                onMouseDown={(e) => {
                  e.preventDefault()
                  handleAddNew()
                }}
              >
                {creating ? 'Adding…' : `Add "${query.trim()}"`}
              </li>
            )}
            {filtered.length === 0 && !showAddOption && q && (
              <li className="px-3 py-0.5 text-ink-muted text-sm">No matches</li>
            )}
          </ul>,
          document.body
        )}
    </div>
  )
}
