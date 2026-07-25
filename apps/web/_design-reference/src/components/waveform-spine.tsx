import { motion, useReducedMotion } from "framer-motion";
import { cn } from "@/lib/utils";
import { waveform } from "@/lib/mock-data";

interface WaveformSpineProps {
  bars?: number;
  seed?: number;
  active?: boolean;
  progress?: number; // 0..1
  height?: number;
  color?: string;
  className?: string;
  onSeek?: (pct: number) => void;
}

/**
 * Waveform Spine — the signature visual of Daastaan.
 * Used for loading, playback, pipeline, and any "voice happening" surface.
 */
export function WaveformSpine({
  bars = 96,
  seed = 7,
  active = false,
  progress,
  height = 56,
  color = "var(--ember)",
  className,
  onSeek,
}: WaveformSpineProps) {
  const data = waveform(bars, seed);
  const reduce = useReducedMotion();

  return (
    <div
      className={cn("relative flex items-center gap-[2px] w-full select-none", onSeek && "cursor-pointer")}
      style={{ height }}
      role={onSeek ? "slider" : "img"}
      aria-label="Audio waveform"
      aria-valuenow={progress != null ? Math.round(progress * 100) : undefined}
      onClick={(e) => {
        if (!onSeek) return;
        const rect = e.currentTarget.getBoundingClientRect();
        onSeek(Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width)));
      }}
    >
      {data.map((v, i) => {
        const past = progress != null && i / bars <= progress;
        return (
          <motion.span
            key={i}
            className="flex-1 rounded-full origin-center"
            style={{
              background: past ? color : `color-mix(in oklab, ${color} 35%, transparent)`,
              height: `${v * 100}%`,
              minHeight: 2,
            }}
            animate={active && !reduce ? { scaleY: [v * 0.6, v, v * 0.55, v * 0.9, v * 0.7] } : undefined}
            transition={active && !reduce ? { duration: 1.6 + (i % 5) * 0.2, repeat: Infinity, ease: "easeInOut" } : undefined}
          />
        );
      })}
    </div>
  );
}
