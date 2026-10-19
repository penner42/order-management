import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom'

const groups = [
  { label: 'Account', sections: [{ path: 'account', label: 'Account' }] },
  { label: 'Order setup', sections: [
    { path: 'buying-groups', label: 'Buying groups' },
    { path: 'stores', label: 'Stores' },
    { path: 'ignored-zip-codes', label: 'Ignored zip codes' },
  ] },
  { label: 'Payments and rewards', sections: [
    { path: 'monarch', label: 'Monarch' },
    { path: 'payment-methods', label: 'Payment methods' },
    { path: 'rewards', label: 'Rewards' },
    { path: 'portals', label: 'Portals' },
  ] },
  { label: 'Browser extension', sections: [{ path: 'extension', label: 'Browser extension' }] },
]

export default function Settings() {
  const location = useLocation()
  const navigate = useNavigate()
  return (
    <div>
      <h1 className="text-2xl font-semibold text-ink dark:text-gray-100 mb-6">Settings</h1>
      <div className="lg:grid lg:grid-cols-[13rem_minmax(0,1fr)] lg:gap-8">
        <nav aria-label="Settings sections" className="hidden lg:block space-y-5 self-start sticky top-20">
          {groups.map(group => (
            <div key={group.label}>
              <p className="px-3 mb-1 text-xs font-semibold uppercase tracking-wide text-ink-muted dark:text-gray-400">{group.label}</p>
              {group.sections.map(section => (
                <NavLink key={section.path} to={`/settings/${section.path}`}
                  className={({ isActive }) => `block px-3 py-2 rounded-lg text-sm ${isActive ? 'bg-brand-100 text-brand-800 font-medium dark:bg-gray-700 dark:text-gray-100' : 'text-ink-muted hover:bg-brand-50 dark:text-gray-300 dark:hover:bg-gray-800'}`}>
                  {section.label}
                </NavLink>
              ))}
            </div>
          ))}
        </nav>
        <div className="lg:hidden mb-6">
          <label htmlFor="settings-section" className="block mb-1 text-sm font-medium text-ink dark:text-gray-200">Settings section</label>
          <select id="settings-section" value={location.pathname} onChange={e => navigate(e.target.value)}
            className="w-full rounded-lg border border-brand-200 dark:border-gray-600 bg-white dark:bg-gray-800 text-ink dark:text-gray-100 px-3 py-2">
            {groups.map(group => <optgroup key={group.label} label={group.label}>
              {group.sections.map(section => <option key={section.path} value={`/settings/${section.path}`}>{section.label}</option>)}
            </optgroup>)}
          </select>
        </div>
        <div className="min-w-0"><Outlet /></div>
      </div>
    </div>
  )
}
