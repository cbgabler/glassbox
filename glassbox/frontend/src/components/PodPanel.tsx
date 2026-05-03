import { Activity, Power, ShieldAlert } from "lucide-react"

export function PodPanel() {
  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Activity className="w-4 h-4 text-emerald-500" />
          <span className="text-sm font-medium text-foreground tracking-tight">Hardware Pod</span>
        </div>
        <div className="flex items-center gap-1.5 bg-emerald-500/10 px-2 py-0.5 rounded-full border border-emerald-500/20">
          <div className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse" />
          <span className="text-[10px] font-semibold text-emerald-500 uppercase tracking-wider">Connected</span>
        </div>
      </div>
      
      <div className="grid grid-cols-2 gap-2">
        <button className="flex flex-col items-center justify-center gap-1 p-2 rounded-lg bg-red-500/10 hover:bg-red-500/20 border border-red-500/20 text-red-500 transition-colors group">
          <ShieldAlert className="w-4 h-4 group-hover:scale-110 transition-transform" />
          <span className="text-[10px] font-bold tracking-widest uppercase">Quarantine</span>
        </button>
        
        <button className="flex flex-col items-center justify-center gap-1 p-2 rounded-lg bg-emerald-500/10 hover:bg-emerald-500/20 border border-emerald-500/20 text-emerald-500 transition-colors group">
          <Power className="w-4 h-4 group-hover:scale-110 transition-transform" />
          <span className="text-[10px] font-bold tracking-widest uppercase">Release</span>
        </button>
      </div>
      <div className="flex items-center justify-between text-xs text-muted-foreground px-1">
        <span>Port</span>
        <span className="font-mono">COM3</span>
      </div>
    </div>
  )
}
