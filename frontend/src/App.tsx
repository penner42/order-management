import { useEffect, useMemo, useState } from 'react'
import { BrowserRouter, Routes, Route, NavLink, Navigate, useLocation } from 'react-router-dom'
import { AuthProvider, useAuth } from './contexts/AuthContext'
import Orders from './pages/Orders'
import BuyingGroups from './pages/BuyingGroups'
import Rewards from './pages/Rewards'
import PaymentMethods from './pages/PaymentMethods'
import Payments from './pages/Payments'
import Shipments from './pages/Shipments'
import Stores from './pages/Stores'
import Login from './pages/Login'
import Admin from './pages/Admin'
import Profile from './pages/Profile'
import ImportPreview from './pages/ImportPreview'
import ImportReview from './pages/ImportReview'
import ImportReviewBulk from './pages/ImportReviewBulk'
import ImportedOrders from './pages/ImportedOrders'
import Portals from './pages/Portals'
import ExtensionAuth from './pages/ExtensionAuth'

function DarkToggle() {
  const [dark, setDark] = useState(() => document.documentElement.classList.contains('dark'))
  function toggle() {
    const next = !document.documentElement.classList.contains('dark')
    document.documentElement.classList.toggle('dark', next)
    localStorage.setItem('theme', next ? 'dark' : 'light')
    setDark(next)
  }
  return (
    <button
      type="button"
      onClick={toggle}
      className="p-2 rounded-md text-ink-muted hover:bg-brand-100/60 hover:text-ink dark:hover:bg-gray-700 dark:text-gray-300 dark:hover:text-gray-100 transition"
      aria-label={dark ? 'Switch to light mode' : 'Switch to dark mode'}
    >
      {dark ? (
        <svg className="w-5 h-5" fill="currentColor" viewBox="0 0 20 20" aria-hidden>
          <path fillRule="evenodd" d="M10 2a1 1 0 011 1v1a1 1 0 11-2 0V3a1 1 0 011-1zm4 8a4 4 0 11-8 0 4 4 0 018 0zm-.464 4.95l.707.707a1 1 0 001.414-1.414l-.707-.707a1 1 0 00-1.414 1.414zm2.12-10.607a1 1 0 010 1.414l-.706.707a1 1 0 11-1.414-1.414l.707-.707a1 1 0 011.414 0zM17 11a1 1 0 100-2h-1a1 1 0 100 2h1zm-7 4a1 1 0 011 1v1a1 1 0 11-2 0v-1a1 1 0 011-1zM5.05 6.464A1 1 0 106.465 5.05l-.708-.707a1 1 0 00-1.414 1.414l.707.707zm1.414 8.486l-.707.707a1 1 0 01-1.414-1.414l.707-.707a1 1 0 011.414 1.414zM4 11a1 1 0 100-2H3a1 1 0 000 2h1z" clipRule="evenodd" />
        </svg>
      ) : (
        <svg className="w-5 h-5" fill="currentColor" viewBox="0 0 20 20" aria-hidden>
          <path d="M17.293 13.293A8 8 0 016.707 2.707a8.001 8.001 0 1010.586 10.586z" />
        </svg>
      )}
    </button>
  )
}

function AppShell() {
  const { user, loading, logout } = useAuth()
  const location = useLocation()
  const [mobileNavOpen, setMobileNavOpen] = useState(false)
  const isPublic =
    location.pathname === '/login' || location.pathname === '/extension-auth'

  useEffect(() => {
    setMobileNavOpen(false)
  }, [location.pathname])

  useEffect(() => {
    if (!mobileNavOpen) return
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape') setMobileNavOpen(false)
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [mobileNavOpen])

  const navItems = useMemo(() => {
    if (!user) return []
    if (user.role === 'admin') {
      return [{ to: '/admin', label: 'Admin' }]
    }
    return [
      { to: '/', label: 'Orders' },
      { to: '/buying-groups', label: 'Buying Groups' },
      { to: '/rewards', label: 'Rewards' },
      { to: '/payment-methods', label: 'Payment Methods' },
      { to: '/payments', label: 'Payments' },
      { to: '/shipments', label: 'Shipments' },
      { to: '/stores', label: 'Stores' },
      { to: '/portals', label: 'Portals' },
      { to: '/imported-orders', label: 'Imported Orders' },
    ]
  }, [user])

  if (isPublic) {
    return (
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/extension-auth" element={<ExtensionAuth />} />
        <Route path="*" element={<Navigate to="/login" replace />} />
      </Routes>
    )
  }

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-brand-50 dark:bg-gray-900">
        <p className="text-ink-muted dark:text-gray-400">Loading…</p>
      </div>
    )
  }

  if (!user) {
    return <Navigate to="/login" replace />
  }

  return (
    <div className="min-h-screen flex flex-col">
      <header className="bg-white dark:bg-gray-900 border-b border-brand-200/80 dark:border-gray-700 sticky top-0 z-10">
        <div className="max-w-[1920px] mx-auto px-4 h-14 flex items-center gap-3 sm:gap-6 w-full">
          <NavLink
            to={user.role === 'admin' ? '/admin' : '/'}
            className="font-semibold text-brand-800 dark:text-gray-100 text-lg shrink-0 whitespace-nowrap"
          >
            Order Management
          </NavLink>
          <nav className="hidden md:flex flex-1 min-w-0 gap-1 overflow-x-auto">
            {navItems.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                className={({ isActive }) =>
                  `px-3 py-2 rounded-md text-sm font-medium transition ${isActive ? 'bg-brand-100 text-brand-800 dark:bg-gray-700 dark:text-gray-100' : 'text-ink-muted hover:bg-brand-100/60 hover:text-ink dark:hover:bg-gray-700 dark:text-gray-300 dark:hover:text-gray-100'}`
                }
              >
                {item.label}
              </NavLink>
            ))}
          </nav>
          <div className="flex items-center gap-1.5 sm:gap-2 ml-auto">
            <DarkToggle />
            {user.role !== 'admin' && (
              <NavLink
                to="/profile"
                className={({ isActive }) => `hidden md:inline text-sm ${isActive ? 'text-brand-700 dark:text-brand-400 font-medium' : 'text-ink-muted hover:text-ink dark:text-gray-400 dark:hover:text-gray-100'}`}
              >
                {user.username}
              </NavLink>
            )}
            <button
              type="button"
              onClick={logout}
              className="hidden md:inline-flex px-3 py-1.5 text-sm text-ink-muted hover:text-ink dark:hover:text-gray-100 rounded"
            >
              Sign out
            </button>
            <button
              type="button"
              className="md:hidden p-2 rounded-md text-ink-muted hover:bg-brand-100/60 hover:text-ink dark:hover:bg-gray-700 dark:text-gray-300 dark:hover:text-gray-100 transition"
              aria-label={mobileNavOpen ? 'Close navigation menu' : 'Open navigation menu'}
              aria-expanded={mobileNavOpen}
              onClick={() => setMobileNavOpen((v) => !v)}
            >
              {mobileNavOpen ? (
                <svg className="w-5 h-5" viewBox="0 0 20 20" fill="currentColor" aria-hidden>
                  <path fillRule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" clipRule="evenodd" />
                </svg>
              ) : (
                <svg className="w-5 h-5" viewBox="0 0 20 20" fill="currentColor" aria-hidden>
                  <path fillRule="evenodd" d="M3 5h14a1 1 0 010 2H3a1 1 0 010-2zm0 6h14a1 1 0 010 2H3a1 1 0 010-2zm0 6h14a1 1 0 010 2H3a1 1 0 010-2z" clipRule="evenodd" />
                </svg>
              )}
            </button>
          </div>
        </div>
      </header>
      {mobileNavOpen && (
        <div className="fixed inset-0 z-20 md:hidden">
          <button
            type="button"
            aria-label="Close navigation menu"
            className="absolute inset-0 bg-black/30"
            onClick={() => setMobileNavOpen(false)}
          />
          <div className="absolute right-0 top-0 h-full w-[min(20rem,85vw)] bg-white dark:bg-gray-900 border-l border-brand-200/80 dark:border-gray-700 shadow-xl p-3 flex flex-col">
            <div className="px-1 pb-2 border-b border-brand-100/70 dark:border-gray-700">
              <div className="font-semibold text-brand-800 dark:text-gray-100">Menu</div>
            </div>
            <div className="py-2 flex-1 overflow-auto">
              <div className="flex flex-col gap-1">
                {navItems.map((item) => (
                  <NavLink
                    key={item.to}
                    to={item.to}
                    className={({ isActive }) =>
                      `px-3 py-2 rounded-md text-sm font-medium transition ${isActive ? 'bg-brand-100 text-brand-800 dark:bg-gray-700 dark:text-gray-100' : 'text-ink-muted hover:bg-brand-100/60 hover:text-ink dark:hover:bg-gray-700 dark:text-gray-300 dark:hover:text-gray-100'}`
                    }
                  >
                    {item.label}
                  </NavLink>
                ))}
              </div>
              {user.role !== 'admin' && (
                <div className="mt-3 pt-3 border-t border-brand-100/70 dark:border-gray-700">
                  <NavLink
                    to="/profile"
                    className={({ isActive }) =>
                      `block px-3 py-2 rounded-md text-sm transition ${isActive ? 'bg-brand-100 text-brand-800 dark:bg-gray-700 dark:text-gray-100 font-medium' : 'text-ink-muted hover:bg-brand-100/60 hover:text-ink dark:hover:bg-gray-700 dark:text-gray-300 dark:hover:text-gray-100'}`
                    }
                  >
                    Profile ({user.username})
                  </NavLink>
                </div>
              )}
            </div>
            <div className="pt-2 border-t border-brand-100/70 dark:border-gray-700">
              <button
                type="button"
                onClick={logout}
                className="w-full px-3 py-2 rounded-md text-sm text-ink-muted hover:bg-brand-100/60 hover:text-ink dark:hover:bg-gray-700 dark:text-gray-300 dark:hover:text-gray-100 transition text-left"
              >
                Sign out
              </button>
            </div>
          </div>
        </div>
      )}
      <main className="flex-1 w-full mx-auto px-3 sm:px-4 md:px-12 lg:px-12 py-4 md:py-8">
        <Routes>
          <Route path="/" element={user.role === 'admin' ? <Navigate to="/admin" replace /> : <Orders />} />
          <Route path="/import-preview" element={<ImportPreview />} />
          <Route path="/import-review" element={<ImportReview />} />
          <Route path="/import-review/bulk" element={<ImportReviewBulk />} />
          <Route path="/imported-orders" element={<ImportedOrders />} />
          <Route path="/buying-groups" element={<BuyingGroups />} />
          <Route path="/rewards" element={<Rewards />} />
          <Route path="/payment-methods" element={<PaymentMethods />} />
          <Route path="/payments" element={<Payments />} />
          <Route path="/shipments" element={<Shipments />} />
          <Route path="/stores" element={<Stores />} />
          <Route path="/portals" element={<Portals />} />
          <Route path="/profile" element={<Profile />} />
          {user.role === 'admin' && <Route path="/admin" element={<Admin />} />}
          <Route path="*" element={<Navigate to={user.role === 'admin' ? '/admin' : '/'} replace />} />
        </Routes>
      </main>
    </div>
  )
}

function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <AppShell />
      </AuthProvider>
    </BrowserRouter>
  )
}

export default App
