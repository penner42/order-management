import { useEffect, useState } from 'react'
import { api } from '../api/client'

export function useGroupSubmissionsEnabled(): boolean {
  const [enabled, setEnabled] = useState(false)
  useEffect(() => {
    let active = true
    api.get<{ group_submissions_enabled: boolean }>('/capabilities')
      .then((capabilities) => { if (active) setEnabled(capabilities.group_submissions_enabled) })
      .catch(() => { if (active) setEnabled(false) })
    return () => { active = false }
  }, [])
  return enabled
}
