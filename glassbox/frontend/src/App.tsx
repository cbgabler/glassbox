import { BrowserRouter, Routes, Route } from "react-router-dom"
import { LandingPage } from "@/pages/LandingPage"
import { InitiatePage } from "@/pages/InitiatePage"
import { AuditDashboard } from "@/pages/AuditDashboard"

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<LandingPage />} />
        <Route path="/initiate" element={<InitiatePage />} />
        <Route path="/audit/:runId" element={<AuditDashboard />} />
      </Routes>
    </BrowserRouter>
  )
}
