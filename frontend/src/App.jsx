import { Routes, Route, Navigate } from 'react-router-dom'
import { AccountProvider } from './lib/AccountContext'
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

export default function App() {
  return (
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
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </Layout>
    </AccountProvider>
  )
}
