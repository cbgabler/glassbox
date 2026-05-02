import { useState } from "react"
import { useNavigate } from "react-router-dom"
import { startAudit } from "@/lib/api"
import { Shield, Loader2, ArrowRight } from "lucide-react"

export function InitiatePage() {
  const [repoUrl, setRepoUrl] = useState("")
  const [isSubmitting, setIsSubmitting] = useState(false)
  const navigate = useNavigate()

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!repoUrl) return
    
    setIsSubmitting(true)
    try {
      const response = await startAudit(repoUrl)
      // Navigate to the dashboard with the newly minted runId
      navigate(`/audit/\${response.run_id}`)
    } catch (err) {
      console.error(err)
      setIsSubmitting(false)
    }
  }

  return (
    <div className="min-h-screen w-full bg-background flex items-center justify-center p-4 relative overflow-hidden">
      {/* Background decoration */}
      <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[800px] h-[800px] bg-indigo-500/10 rounded-full blur-[120px] pointer-events-none" />
      
      <div className="w-full max-w-md bg-card/60 backdrop-blur-xl border border-border rounded-2xl shadow-2xl p-8 relative animate-in fade-in zoom-in duration-500">
        <div className="flex items-center justify-center w-12 h-12 bg-indigo-500/20 text-indigo-400 rounded-full mb-6 mx-auto ring-1 ring-indigo-500/30 shadow-[0_0_15px_rgba(99,102,241,0.2)]">
          <Shield className="w-6 h-6" />
        </div>
        
        <div className="text-center mb-8">
          <h2 className="text-2xl font-bold text-foreground tracking-tight mb-2">Initiate Audit</h2>
          <p className="text-sm text-muted-foreground">
            Enter a GitHub repository URL or absolute local path to begin scanning for side-channels and secrets.
          </p>
        </div>

        <form onSubmit={handleSubmit} className="flex flex-col gap-4">
          <div className="flex flex-col gap-2">
            <input
              type="text"
              value={repoUrl}
              onChange={(e) => setRepoUrl(e.target.value)}
              disabled={isSubmitting}
              placeholder="e.g., https://github.com/org/repo"
              className="w-full bg-background/50 border border-border rounded-lg px-4 py-3 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500/50 focus:border-indigo-500/50 transition-all text-foreground placeholder:text-muted-foreground disabled:opacity-50"
              required
            />
          </div>

          <button
            type="submit"
            disabled={isSubmitting || !repoUrl}
            className="w-full flex items-center justify-center gap-2 bg-foreground text-background hover:bg-foreground/90 disabled:bg-muted disabled:text-muted-foreground px-4 py-3 rounded-lg font-medium transition-all shadow-lg active:scale-95 group"
          >
            {isSubmitting ? (
              <>
                <Loader2 className="w-4 h-4 animate-spin" />
                Initializing Pod...
              </>
            ) : (
              <>
                Start Audit
                <ArrowRight className="w-4 h-4 group-hover:translate-x-1 transition-transform" />
              </>
            )}
          </button>
        </form>
      </div>
    </div>
  )
}
