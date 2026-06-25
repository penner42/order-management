import { useEffect, useState } from 'react'
import { api } from '../api/client'
import { ConfirmDialog } from '../components/ConfirmDialog'
import type { BuyingGroup } from '../api/types'

function AliasEditor({
  group,
  onChange,
}: {
  group: BuyingGroup
  onChange: (updated: BuyingGroup) => void
}) {
  const [newAlias, setNewAlias] = useState('')
  const [saving, setSaving] = useState(false)

  const updateAliases = async (aliases: string[]) => {
    setSaving(true)
    try {
      const updated = await api.patch<BuyingGroup>(`/buying-groups/${group.id}`, { aliases })
      onChange(updated)
    } catch (e) {
      console.error(e)
    } finally {
      setSaving(false)
    }
  }

  const addAlias = async (e: React.FormEvent) => {
    e.preventDefault()
    const alias = newAlias.trim()
    if (!alias) return
    const existing = new Set((group.aliases ?? []).map((a) => a.toLowerCase()))
    if (existing.has(alias.toLowerCase())) {
      setNewAlias('')
      return
    }
    await updateAliases([...(group.aliases ?? []), alias])
    setNewAlias('')
  }

  const removeAlias = async (alias: string) => {
    await updateAliases((group.aliases ?? []).filter((a) => a !== alias))
  }

  return (
    <div className="mt-2">
      <div className="flex flex-wrap items-center gap-1.5">
        {(group.aliases ?? []).length === 0 ? (
          <span className="text-xs text-ink-muted">No aliases</span>
        ) : (
          (group.aliases ?? []).map((alias) => (
            <span
              key={alias}
              className="inline-flex items-center gap-1 rounded-full bg-brand-50 dark:bg-brand-900/30 px-2 py-0.5 text-xs text-ink"
            >
              {alias}
              <button
                type="button"
                onClick={() => removeAlias(alias)}
                disabled={saving}
                className="text-ink-muted hover:text-red-600 disabled:opacity-50"
                aria-label={`Remove alias ${alias}`}
              >
                ×
              </button>
            </span>
          ))
        )}
      </div>
      <form onSubmit={addAlias} className="mt-2 flex gap-2 max-w-md">
        <input
          type="text"
          className="rounded border border-brand-200 px-2 py-1 text-sm flex-1"
          placeholder="Add alias for import matching"
          value={newAlias}
          onChange={(e) => setNewAlias(e.target.value)}
          disabled={saving}
        />
        <button
          type="submit"
          disabled={saving || !newAlias.trim()}
          className="text-sm text-brand-600 hover:underline disabled:opacity-50"
        >
          Add
        </button>
      </form>
    </div>
  )
}

export default function BuyingGroups() {
  const [groups, setGroups] = useState<BuyingGroup[]>([])
  const [loading, setLoading] = useState(true)
  const [editing, setEditing] = useState<number | null>(null)
  const [newName, setNewName] = useState('')
  const [createName, setCreateName] = useState('')
  const [confirmDeleteId, setConfirmDeleteId] = useState<number | null>(null)

  useEffect(() => {
    api.get<BuyingGroup[]>('/buying-groups').then(setGroups).catch(console.error).finally(() => setLoading(false))
  }, [])

  const startEdit = (g: BuyingGroup) => {
    setEditing(g.id)
    setNewName(g.name)
  }
  const saveEdit = async () => {
    if (editing == null) return
    try {
      const updated = await api.patch<BuyingGroup>(`/buying-groups/${editing}`, { name: newName })
      setGroups((prev) => prev.map((p) => (p.id === updated.id ? updated : p)))
      setEditing(null)
    } catch (e) {
      console.error(e)
    }
  }
  const cancelEdit = () => setEditing(null)

  const create = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!createName.trim()) return
    try {
      const created = await api.post<BuyingGroup>('/buying-groups', { name: createName.trim() })
      setGroups((prev) => [created, ...prev])
      setCreateName('')
    } catch (e) {
      console.error(e)
    }
  }

  const remove = async (id: number) => {
    try {
      await api.delete(`/buying-groups/${id}`)
      setGroups((prev) => prev.filter((p) => p.id !== id))
    } catch (e) {
      console.error(e)
    } finally {
      setConfirmDeleteId(null)
    }
  }

  if (loading) return <div className="text-ink-muted">Loading…</div>

  return (
    <div>
      <h1 className="text-2xl font-semibold text-ink mb-2">Buying groups</h1>
      <p className="text-sm text-ink-muted mb-8">
        Add aliases to match shipping names on Walmart and Costco imports.
      </p>
      <form onSubmit={create} className="flex gap-2 mb-6">
        <input
          type="text"
          className="rounded-lg border border-brand-200 px-3 py-2 flex-1 max-w-xs"
          placeholder="New group name"
          value={createName}
          onChange={(e) => setCreateName(e.target.value)}
        />
        <button type="submit" className="px-4 py-2 bg-brand-600 text-white rounded-lg font-medium hover:bg-brand-700">
          Add
        </button>
      </form>
      <div className="w-full md:w-fit md:min-w-[600px] md:max-w-full bg-white dark:bg-gray-800 rounded-xl border border-brand-200/80 dark:border-gray-700 shadow-sm overflow-hidden">
        <ul className="divide-y divide-brand-100 dark:divide-gray-700">
          {groups.length === 0 ? (
            <li className="py-8 text-center text-ink-muted">No buying groups yet.</li>
          ) : (
            groups.map((g) => (
              <li key={g.id} className="px-4 py-3">
                <div className="flex items-center justify-between gap-3">
                  {editing === g.id ? (
                    <>
                      <input
                        type="text"
                        className="rounded border border-brand-200 px-2 py-1 flex-1 max-w-sm"
                        value={newName}
                        onChange={(e) => setNewName(e.target.value)}
                        autoFocus
                      />
                      <div className="flex gap-2 ml-2 shrink-0">
                        <button
                          type="button"
                          onClick={saveEdit}
                          className="text-sm text-brand-600 hover:underline"
                        >
                          Save
                        </button>
                        <button type="button" onClick={cancelEdit} className="text-sm text-ink-muted hover:underline">
                          Cancel
                        </button>
                      </div>
                    </>
                  ) : (
                    <>
                      <span className="font-medium text-ink">{g.name}</span>
                      <div className="flex gap-2 shrink-0">
                        <button
                          type="button"
                          onClick={() => startEdit(g)}
                          className="p-1.5 rounded text-ink-muted hover:text-brand-600 hover:bg-brand-50 dark:hover:bg-brand-900/20 transition"
                          title="Edit"
                          aria-label="Edit"
                        >
                          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden>
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z" />
                          </svg>
                        </button>
                        <button
                          type="button"
                          onClick={() => setConfirmDeleteId(g.id)}
                          className="p-1.5 rounded text-ink-muted hover:text-red-600 hover:bg-red-50 dark:hover:bg-red-900/20 transition"
                          title="Delete"
                          aria-label="Delete"
                        >
                          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden>
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                          </svg>
                        </button>
                      </div>
                    </>
                  )}
                </div>
                <AliasEditor
                  group={g}
                  onChange={(updated) => setGroups((prev) => prev.map((p) => (p.id === updated.id ? updated : p)))}
                />
              </li>
            ))
          )}
        </ul>
      </div>
      <ConfirmDialog
        open={confirmDeleteId !== null}
        message="Delete this buying group?"
        confirmLabel="Delete"
        danger
        onConfirm={() => confirmDeleteId !== null && remove(confirmDeleteId)}
        onCancel={() => setConfirmDeleteId(null)}
      />
    </div>
  )
}
