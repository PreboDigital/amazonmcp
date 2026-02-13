import { Routes, Route, Navigate, useLocation } from 'react-router-dom'
import { AccountProvider } from './lib/AccountContext'
import { AuthProvider, useAuth } from './lib/AuthContext'
import Layout from './components/Layout'
import Dashboard from './pages/Dashboard'
import CampaignManager from './pages/CampaignManager'
import AIAssistant from './pages/AIAssistant'
import ApprovalQueue from './pages/ApprovalQueue'
import Audit from './pages/Audit'
import Reports from './pages/Reports'
import Harvester from './pages/Harvester'
import Optimizer from './pages/Optimizer'
import Settings from './pages/Settings'
import DataSync from './pages/DataSync'
import Login from './pages/Login'
import Register from './pages/Register'
import ForgotPassword from './pages/ForgotPassword'
import ResetPassword from './pages/ResetPassword'
import UserManagement from './pages/UserManagement'
import { Loader2 } from 'lucide-react'

function ProtectedRoute({ children }) {
  const { user, loading } = useAuth()
  const location = useLocation()
  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-slate-50">
        <Loader2 className="w-10 h-10 animate-spin text-brand-600" />
      </div>
    )
  }
  if (!user) {
    return <Navigate to="/login" replace state={{ from: location }} />
  }
  return children
}

export default function App() {
  return (
    <AuthProvider>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/register" element={<Register />} />
        <Route path="/forgot-password" element={<ForgotPassword />} />
        <Route path="/reset-password" element={<ResetPassword />} />
        <Route
          path="/*"
          element={
            <ProtectedRoute>
              <AccountProvider>
                <Layout>
                  <Routes>
                    <Route path="/" element={<Dashboard />} />
                    <Route path="/campaigns" element={<CampaignManager />} />
                    <Route path="/ai" element={<AIAssistant />} />
                    <Route path="/approvals" element={<ApprovalQueue />} />
                    <Route path="/audit" element={<Audit />} />
                    <Route path="/reports" element={<Reports />} />
                    <Route path="/harvester" element={<Harvester />} />
                    <Route path="/optimizer" element={<Optimizer />} />
                    <Route path="/settings" element={<Settings />} />
                    <Route path="/data-sync" element={<DataSync />} />
                    <Route path="/users" element={<UserManagement />} />
                    <Route path="*" element={<Navigate to="/" replace />} />
                  </Routes>
                </Layout>
              </AccountProvider>
            </ProtectedRoute>
          }
        />
      </Routes>
    </AuthProvider>
  )
}
