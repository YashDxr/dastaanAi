import { motion } from "framer-motion";
import { PIPELINE_NODES, PIPELINE_EDGES, type NodeStatus } from "@/lib/mock-data";
import { cn } from "@/lib/utils";
import { CheckCircle2, Loader2, Circle, AlertCircle } from "lucide-react";

interface Props {
  status: Record<string, NodeStatus>;
}

const groupsCount = Math.max(...PIPELINE_NODES.map((n) => n.group)) + 1;

export function PipelineGraph({ status }: Props) {
  // Layout nodes into columns
  const cols: (typeof PIPELINE_NODES)[number][][] = Array.from({ length: groupsCount }, () => []);
  PIPELINE_NODES.forEach((n) => cols[n.group].push(n));

  return (
    <div className="relative rounded-2xl border border-border bg-[var(--ink-2)]/60 p-6 overflow-hidden">
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 opacity-40"
        style={{
          backgroundImage:
            "radial-gradient(circle at 20% 20%, rgba(232,135,58,0.15), transparent 40%), radial-gradient(circle at 80% 80%, rgba(79,216,196,0.12), transparent 45%)",
        }}
      />
      <div className="relative grid gap-6" style={{ gridTemplateColumns: `repeat(${groupsCount}, minmax(0,1fr))` }}>
        {cols.map((col, ci) => (
          <div key={ci} className="flex flex-col gap-4 justify-center">
            {col.map((node) => {
              const s = status[node.id] ?? "pending";
              const Icon =
                s === "completed" ? CheckCircle2 : s === "running" ? Loader2 : s === "failed" ? AlertCircle : Circle;
              return (
                <motion.div
                  layout
                  key={node.id}
                  className={cn(
                    "relative rounded-xl border p-4 bg-[var(--ink-3)]/80 backdrop-blur transition-colors",
                    s === "running" && "border-[var(--ember)]/60 ember-glow",
                    s === "completed" && "border-[var(--signal)]/40",
                    s === "failed" && "border-destructive/60",
                    s === "pending" && "border-border"
                  )}
                >
                  <div className="flex items-center gap-2">
                    <Icon
                      className={cn(
                        "h-4 w-4 shrink-0",
                        s === "completed" && "text-[var(--signal)]",
                        s === "running" && "text-[var(--ember)] animate-spin",
                        s === "failed" && "text-destructive",
                        s === "pending" && "text-muted-foreground"
                      )}
                    />
                    <span className="text-sm truncate">{node.label}</span>
                  </div>
                  <p className="mt-1 text-[10px] uppercase tracking-widest text-muted-foreground">{s}</p>
                  {s === "running" && (
                    <div className="mt-3 h-0.5 w-full overflow-hidden rounded-full bg-white/5">
                      <motion.div
                        className="h-full bg-[var(--ember)]"
                        animate={{ x: ["-100%", "100%"] }}
                        transition={{ duration: 1.4, repeat: Infinity, ease: "linear" }}
                        style={{ width: "40%" }}
                      />
                    </div>
                  )}
                </motion.div>
              );
            })}
          </div>
        ))}
      </div>

      {/* connector legend */}
      <p className="relative mt-6 text-[10px] uppercase tracking-widest text-muted-foreground">
        {PIPELINE_EDGES.length} connections · {PIPELINE_NODES.length} stages · streaming
      </p>
    </div>
  );
}
