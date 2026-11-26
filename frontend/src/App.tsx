import { useCallback, useEffect, useMemo, useState } from 'react'
import { createBrowserRouter, RouterProvider, Routes, Route, NavLink, Navigate, useLocation } from 'react-router-dom'
import { AuthProvider, useAuth } from './contexts/AuthContext'
import { api } from './api/client'
import type { BrowserProfile } from './api/types'
import { NoticeDot, NoticeLabel } from './components/NoticeLabel'
import { needsBrowserLogin } from './utils/browserLogin'
import Orders from './pages/Orders'
import BuyingGroups from './pages/BuyingGroups'
import BfmrOrderReview from './pages/BfmrOrderReview'
import Rewards from './pages/Rewards'
import PaymentMethods from './pages/PaymentMethods'
import Payments from './pages/Payments'
import PaymentLinking from './pages/PaymentLinking'
import PaymentReview from './pages/PaymentReview'
import Stores from './pages/Stores'
import Login from './pages/Login'
import Admin from './pages/Admin'
import Profile from './pages/Profile'
import ImportPreview from './pages/ImportPreview'
import ImportReview from './pages/ImportReview'
import BestBuyImport from './pages/BestBuyImport'
import ImportReviewBulk from './pages/ImportReviewBulk'
import AutomationOrderImport from './pages/AutomationOrderImport'
import ReceivingReview from './pages/ReceivingReview'
import ReceivingAudit from './pages/ReceivingAudit'
import Portals from './pages/Portals'
import ExtensionAuth from './pages/ExtensionAuth'
import BrowserExtension from './pages/BrowserExtension'
import Settings from './pages/Settings'
import IgnoredZipCodes from './pages/IgnoredZipCodes'
import BrowserImportLog from './pages/BrowserImportLog'
import MonarchSettings from './pages/MonarchSettings'
import MonarchReview from './pages/MonarchReview'

function LegacyRedirect({ to }: { to: string }) {
  const { search, hash } = useLocation()
  return <Navigate to={`${to}${search}${hash}`} replace />
}

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

async function getReceivingNotice() {
  const pending = await api.get<boolean>('/buying-groups/receiving-review/has-pending')
  return pending ? 'Pending matches to approve' : null
}

async function getProfilesNeedingLogin() {
  const profiles = await api.get<BrowserProfile[]>('/browser-profiles')
  return profiles.filter(needsBrowserLogin)
}

function getBrowserLoginNotice(storeNeedsLogin: boolean, groupNeedsLogin: boolean) {
  if (storeNeedsLogin && groupNeedsLogin) return 'Store and buying group browser sessions require login'
  if (storeNeedsLogin) return 'Store browser sessions require login'
  if (groupNeedsLogin) return 'Buying group browser sessions require login'
  return null
}

function useNavStatus<T>(userId: number | undefined, pathname: string, getStatus: () => Promise<T>) {
  const [status, setStatus] = useState<T | null>(null)
  const [statusVersion, setStatusVersion] = useState(0)
  const refreshNotice = useCallback(() => setStatusVersion(version => version + 1), [])

  useEffect(() => {
    if (userId === undefined) {
      setStatus(null)
      return
    }
    let requestVersion = 0
    const refresh = async () => {
      if (document.visibilityState !== 'visible') return
      const version = ++requestVersion
      try {
        const nextStatus = await getStatus()
        if (version === requestVersion) setStatus(nextStatus)
      } catch {
        // Keep the last known state if the status request fails.
      }
    }
    void refresh()
    window.addEventListener('focus', refresh)
    const timer = window.setInterval(refresh, 60_000)
    return () => {
      requestVersion++
      window.removeEventListener('focus', refresh)
      window.clearInterval(timer)
    }
  }, [userId, pathname, getStatus, statusVersion])

  return [status, refreshNotice] as const
}

function AppShell() {
  const { user, loading, logout } = useAuth()
  const location = useLocation()
  const [mobileNavOpen, setMobileNavOpen] = useState(false)
  const isPublic =
    location.pathname === '/login' || location.pathname === '/extension-auth'
  const navUserId = !isPublic && user?.role !== 'admin' ? user?.id : undefined
  const [receivingNotice, refreshReceivingStatus] = useNavStatus(navUserId, location.pathname, getReceivingNotice)
  const [loginProfiles, refreshBrowserLoginStatus] = useNavStatus(navUserId, location.pathname, getProfilesNeedingLogin)

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
      return [
        { to: '/admin', label: 'Admin' },
        { to: '/extension', label: 'Extension' },
      ]
    }
    return [
      { to: '/', label: 'Orders' },
      { to: '/duplicate-audit', label: 'Duplicate audit' },
      { to: '/receiving-review', label: 'Receiving' },
      { to: '/payments', label: 'Payments' },
      { to: '/browser-import-log', label: 'Log' },
      { to: '/settings', label: 'Settings' },
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
    if (/^\/import-review\/job\/[\w-]+$/.test(location.pathname)) {
      return <Navigate to={`/login?redirect=${encodeURIComponent(location.pathname)}`} replace />
    }
    return <Navigate to="/login" replace />
  }

  const storeNeedsLogin = loginProfiles?.some(profile => profile.store_id != null) ?? false
  const groupsNeedingLogin = new Set(loginProfiles?.map(profile => profile.buying_group_id).filter(id => id != null))
  const groupNeedsLogin = groupsNeedingLogin.size > 0
  const browserLoginNotice = getBrowserLoginNotice(storeNeedsLogin, groupNeedsLogin)
  const notices: Record<string, { id: string; text: string } | undefined> = {}
  if (receivingNotice) notices['/receiving-review'] = { id: 'receiving-pending-description', text: receivingNotice }
  if (browserLoginNotice) notices['/settings'] = { id: 'browser-login-description', text: browserLoginNotice }
  const activeNotices = Object.values(notices).filter(notice => notice !== undefined)
  const navLinks = navItems.map(item => {
    const notice = notices[item.to]
    return (
      <NavLink
        key={item.to}
        reloadDocument
        end={item.to === '/'}
        to={item.to}
        aria-describedby={notice?.id}
        className={({ isActive }) =>
          `px-3 py-2 rounded-md text-sm font-medium transition ${isActive ? 'bg-brand-100 text-brand-800 dark:bg-gray-700 dark:text-gray-100' : 'text-ink-muted hover:bg-brand-100/60 hover:text-ink dark:hover:bg-gray-700 dark:text-gray-300 dark:hover:text-gray-100'}`
        }
      >
        <NoticeLabel notice={notice?.text}>{item.label}</NoticeLabel>
      </NavLink>
    )
  })

  return (
    <div className="min-h-screen flex flex-col">
      {activeNotices.map(({ id, text }) => <span key={id} id={id} hidden>{text}</span>)}
      <header className="bg-white dark:bg-gray-900 border-b border-brand-200/80 dark:border-gray-700 sticky top-0 z-10">
        <div className="max-w-[1920px] mx-auto px-4 h-14 flex items-center gap-3 sm:gap-6 w-full">
          <NavLink
            to={user.role === 'admin' ? '/admin' : '/'}
            className="font-semibold text-brand-800 dark:text-gray-100 text-lg shrink-0 whitespace-nowrap"
          >
            Order Management
          </NavLink>
          <nav aria-label="Main navigation" className="hidden md:flex flex-1 min-w-0 gap-1 overflow-x-auto">
            {navLinks}
          </nav>
          <div className="flex items-center gap-1.5 sm:gap-2 ml-auto">
            <DarkToggle />
            {user.role !== 'admin' && (
              <NavLink
                to="/settings/account"
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
              className="relative md:hidden p-2 rounded-md text-ink-muted hover:bg-brand-100/60 hover:text-ink dark:hover:bg-gray-700 dark:text-gray-300 dark:hover:text-gray-100 transition"
              aria-label={mobileNavOpen ? 'Close navigation menu' : 'Open navigation menu'}
              aria-describedby={activeNotices.map(notice => notice.id).join(' ') || undefined}
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
              {activeNotices.length > 0 && <NoticeDot className="absolute right-1.5 top-1.5 ring-2 ring-white dark:ring-gray-900" />}
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
              <nav aria-label="Mobile navigation" className="flex flex-col gap-1">
                {navLinks}
              </nav>
              {user.role !== 'admin' && (
                <div className="mt-3 pt-3 border-t border-brand-100/70 dark:border-gray-700">
                  <NavLink
                    to="/settings/account"
                    className={({ isActive }) =>
                      `block px-3 py-2 rounded-md text-sm transition ${isActive ? 'bg-brand-100 text-brand-800 dark:bg-gray-700 dark:text-gray-100 font-medium' : 'text-ink-muted hover:bg-brand-100/60 hover:text-ink dark:hover:bg-gray-700 dark:text-gray-300 dark:hover:text-gray-100'}`
                    }
                  >
                    Account ({user.username})
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
          <Route path="/" element={user.role === 'admin' ? <Navigate to="/admin" replace /> : <Orders key="orders" />} />
          <Route path="/duplicate-audit" element={<Orders key="duplicate-audit" duplicateAudit />} />
          <Route path="/import-preview" element={<ImportPreview />} />
          <Route path="/import/best-buy/:orderId?" element={<BestBuyImport />} />
          <Route path="/import-review" element={<ImportReview />} />
          <Route path="/import-review/bulk" element={<ImportReviewBulk />} />
          <Route path="/import-review/job/:jobId" element={<AutomationOrderImport />} />
          <Route path="/receiving-review" element={<ReceivingReview onPendingMatchesChange={refreshReceivingStatus} />} />
          <Route path="/bfmr-order-review" element={<BfmrOrderReview />} />
          <Route path="/receiving-audit" element={<ReceivingAudit />} />
          <Route path="/buying-groups" element={<LegacyRedirect to="/settings/buying-groups" />} />
          <Route path="/rewards" element={<LegacyRedirect to="/settings/rewards" />} />
          <Route path="/payment-methods" element={<LegacyRedirect to="/settings/payment-methods" />} />
          <Route path="/payments" element={<Payments />} />
          <Route path="/payments/:paymentId/link" element={<PaymentLinking />} />
          <Route path="/payments/review" element={<PaymentReview />} />
          <Route path="/payments/monarch" element={<MonarchReview />} />
          <Route path="/stores" element={<LegacyRedirect to="/settings/stores" />} />
          <Route path="/portals" element={<LegacyRedirect to="/settings/portals" />} />
          <Route path="/extension" element={user.role === 'admin' ? <BrowserExtension /> : <LegacyRedirect to="/settings/extension" />} />
          <Route path="/browser-automation" element={<LegacyRedirect to="/settings/stores" />} />
          <Route path="/browser-import-log" element={<BrowserImportLog />} />
          <Route path="/profile" element={<LegacyRedirect to="/settings/account" />} />
          <Route path="/settings" element={<Settings storeNeedsLogin={storeNeedsLogin} groupNeedsLogin={groupNeedsLogin} />}>
            <Route index element={<LegacyRedirect to="/settings/account" />} />
            <Route path="account" element={<Profile />} />
            <Route path="buying-groups" element={<BuyingGroups groupsNeedingLogin={groupsNeedingLogin} onBrowserStatusChange={refreshBrowserLoginStatus} />} />
            <Route path="stores" element={<Stores onBrowserStatusChange={refreshBrowserLoginStatus} />} />
            <Route path="ignored-zip-codes" element={<IgnoredZipCodes />} />
            <Route path="payment-methods" element={<PaymentMethods />} />
            <Route path="monarch" element={<MonarchSettings />} />
            <Route path="rewards" element={<Rewards />} />
            <Route path="portals" element={<Portals />} />
            <Route path="extension" element={<BrowserExtension />} />
            <Route path="*" element={<LegacyRedirect to="/settings/account" />} />
          </Route>
          {user.role === 'admin' && <Route path="/admin" element={<Admin />} />}
          <Route path="*" element={<Navigate to={user.role === 'admin' ? '/admin' : '/'} replace />} />
        </Routes>
      </main>
    </div>
  )
}

// A data router provides navigation blocking for unsaved allocation drafts.
const router = createBrowserRouter([{path: '*', element: <AuthProvider><AppShell /></AuthProvider>}])

function App() {
  return <RouterProvider router={router} />
}

export default App
