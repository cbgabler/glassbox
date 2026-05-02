import { useState } from "react"
import { useNavigate, Link } from "react-router-dom"
import { startAudit } from "@/lib/api"
import { Shield, Loader2, ArrowRight, ArrowLeft, Target, Database } from "lucide-react"
import { motion } from "framer-motion"

export function InitiatePage() {
  const [repoUrl, setRepoUrl] = useState("")
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const navigate = useNavigate()

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)
    
    if (!repoUrl.trim()) return
    
    // Basic frontend validation
    if (!repoUrl.includes("github.com") && !repoUrl.startsWith("/") && !repoUrl.includes(":\\")) {
      setError("Please enter a valid GitHub URL or local path.")
      return
    }

    setIsSubmitting(true)
    try {
      const response = await startAudit(repoUrl)
      // Navigate to the dashboard with the newly minted runId
      navigate(`/audit/${response.run_id}`)
    } catch (err) {
      console.error(err)
      setError("Failed to initiate. Ensure the repository exists and is accessible.")
      setIsSubmitting(false)
    }
  }

  return (
    <div className="min-h-screen w-full bg-background flex items-center justify-center p-4 relative overflow-hidden bg-[linear-gradient(to_right,#80808012_1px,transparent_1px),linear-gradient(to_bottom,#80808012_1px,transparent_1px)] bg-[size:24px_24px]">
      {/* Background decoration */}
      <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[800px] h-[800px] bg-emerald-500/5 rounded-full blur-[150px] pointer-events-none" />
      
      <Link to="/" className="absolute top-8 left-8 flex items-center gap-2 text-muted-foreground hover:text-emerald-400 transition-colors font-medium z-10">
        <ArrowLeft className="w-4 h-4" />
        Back to Home
      </Link>

      <motion.div 
        initial={{ opacity: 0, scale: 0.95, y: 20 }}
        animate={{ opacity: 1, scale: 1, y: 0 }}
        transition={{ duration: 0.5, ease: "easeOut" }}
        className="w-full max-w-md bg-[#0a0a0a]/80 backdrop-blur-2xl border border-white/10 rounded-2xl shadow-[0_0_80px_rgba(16,185,129,0.1)] p-8 relative ring-1 ring-white/5 z-10"
      >
        <div className="absolute top-0 inset-x-0 h-px bg-gradient-to-r from-transparent via-emerald-500/40 to-transparent" />
        
        <div className="flex items-center justify-center w-14 h-14 bg-emerald-500/10 text-emerald-400 rounded-full mb-6 mx-auto ring-1 ring-emerald-500/20 shadow-[0_0_20px_rgba(16,185,129,0.2)]">
          <Target className="w-7 h-7" />
        </div>
        
        <div className="text-center mb-8">
          <h2 className="text-2xl font-bold text-foreground tracking-tight mb-2">Configure Target</h2>
          <p className="text-sm text-muted-foreground">
            Enter a GitHub URL or local path to attach the MCP auditor and extract vulnerabilities.
          </p>
        </div>

        <form onSubmit={handleSubmit} className="flex flex-col gap-5">
          <div className="flex flex-col gap-2 relative">
            <div className="absolute left-4 top-3.5 text-muted-foreground">
              <Database className="w-5 h-5" />
            </div>
            <input
              type="text"
              value={repoUrl}
              onChange={(e) => {
                setRepoUrl(e.target.value)
                if (error) setError(null)
              }}
              disabled={isSubmitting}
              placeholder="https://github.com/org/repo"
              className={`w-full bg-[#050505] border rounded-xl pl-12 pr-4 py-3.5 text-sm focus:outline-none focus:ring-1 transition-all text-foreground placeholder:text-muted-foreground/50 disabled:opacity-50 shadow-inner ${
                error ? "border-red-500/50 focus:ring-red-500 focus:border-red-500" : "border-white/10 focus:ring-emerald-500/50 focus:border-emerald-500/50 hover:border-white/20"
              }`}
              required
            />
            {error && (
              <motion.p initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: "auto" }} className="text-xs text-red-400 font-medium px-1">{error}</motion.p>
            )}
          </div>

          <button
            type="submit"
            disabled={isSubmitting || !repoUrl}
            className="w-full flex items-center justify-center gap-2 bg-emerald-500 text-black hover:bg-emerald-400 disabled:bg-white/5 disabled:text-muted-foreground px-4 py-3.5 rounded-xl font-bold transition-all shadow-[0_0_20px_rgba(16,185,129,0.2)] hover:shadow-[0_0_30px_rgba(16,185,129,0.4)] disabled:shadow-none active:scale-95 group mt-2"
          >
            {isSubmitting ? (
              <>
                <Loader2 className="w-5 h-5 animate-spin" />
                Initializing Pod...
              </>
            ) : (
              <>
                Attach Auditor
                <ArrowRight className="w-5 h-5 group-hover:translate-x-1 transition-transform" />
              </>
            )}
          </button>
        </form>
      </motion.div>
    </div>
  )
}
