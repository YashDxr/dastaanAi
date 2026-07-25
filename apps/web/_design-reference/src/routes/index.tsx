import { createFileRoute, Link } from "@tanstack/react-router";
import { motion } from "framer-motion";
import { ArrowRight, Sparkles, Wand2, Radio } from "lucide-react";
import { useState } from "react";
import { NARRATOR_PERSONAS } from "@/lib/mock-data";
import { WaveformSpine } from "@/components/waveform-spine";
import { Button } from "@/components/ui/button";

export const Route = createFileRoute("/")({
  head: () => ({
    meta: [
      { title: "Daastaan AI — Turn stories into cinematic audio" },
      { name: "description", content: "Same story. Any narrator. Turn text into podcast-quality episodes with character voices, emotion, and music." },
      { property: "og:title", content: "Daastaan AI — Story Studio" },
      { property: "og:description", content: "Same story. Any narrator." },
    ],
  }),
  component: Landing,
});

const SAMPLE = "The door was already open when she got home. She hadn't left it that way. Somewhere in the attic, an old cassette began, on its own, to play.";

function Landing() {
  const [active, setActive] = useState(NARRATOR_PERSONAS[0].id);
  const current = NARRATOR_PERSONAS.find((p) => p.id === active)!;

  return (
    <div className="min-h-dvh bg-[var(--ink)] text-parchment grain">
      {/* nav */}
      <header className="mx-auto max-w-7xl px-6 md:px-10 py-6 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="grid h-9 w-9 place-items-center rounded-lg bg-[var(--ember)]/15 ring-1 ring-[var(--ember)]/30">
            <Sparkles className="h-4 w-4 text-[var(--ember)]" />
          </div>
          <span className="font-display text-lg">Daastaan</span>
        </div>
        <nav className="hidden md:flex items-center gap-8 text-sm text-muted-foreground">
          <a href="#compare" className="hover:text-parchment">Personas</a>
          <a href="#pipeline" className="hover:text-parchment">How it works</a>
          <a href="#features" className="hover:text-parchment">Studio</a>
          <Link to="/dashboard" className="hover:text-parchment">Dashboard</Link>
        </nav>
        <div className="flex items-center gap-2">
          <Button asChild variant="ghost" className="text-parchment hover:bg-white/5">
            <Link to="/dashboard">Judge Mode</Link>
          </Button>
          <Button asChild className="bg-[var(--ember)] text-[var(--ink)] hover:bg-[var(--ember)]/90 ember-glow">
            <Link to="/upload">Try it live <ArrowRight className="ml-1 h-4 w-4" /></Link>
          </Button>
        </div>
      </header>

      {/* hero */}
      <section className="relative mx-auto max-w-7xl px-6 md:px-10 pt-16 pb-20 md:pt-24 md:pb-28">
        <div
          aria-hidden
          className="absolute inset-0 -z-10"
          style={{
            backgroundImage:
              "radial-gradient(60% 50% at 50% 0%, rgba(232,135,58,0.18), transparent 60%), radial-gradient(40% 40% at 90% 30%, rgba(79,216,196,0.10), transparent 60%)",
          }}
        />
        <motion.p initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} className="text-[10px] uppercase tracking-[0.28em] text-muted-foreground mb-6">
          Night Radio Theatre · for the AI age
        </motion.p>
        <motion.h1
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.05 }}
          className="font-display text-5xl md:text-7xl lg:text-8xl leading-[0.98] max-w-5xl"
        >
          Same story.<br />
          <span className="italic text-[var(--ember)]">Any narrator.</span>
        </motion.h1>
        <p className="mt-8 max-w-xl text-parchment/75 text-lg leading-relaxed">
          Daastaan reads your story like a director — scenes, characters, emotion, silence — then performs it as a cinematic episode. In any voice you choose.
        </p>
        <div className="mt-10 flex flex-wrap items-center gap-3">
          <Button asChild size="lg" className="bg-[var(--ember)] text-[var(--ink)] hover:bg-[var(--ember)]/90 ember-glow h-12 px-6">
            <Link to="/upload">
              <Wand2 className="mr-2 h-4 w-4" /> Try it live
            </Link>
          </Button>
          <Button asChild size="lg" variant="outline" className="h-12 px-6 border-parchment/20 bg-transparent text-parchment hover:bg-white/5">
            <Link to="/dashboard">
              <Radio className="mr-2 h-4 w-4" /> Judge Mode
            </Link>
          </Button>
        </div>

        <div className="mt-16">
          <WaveformSpine bars={160} seed={11} active height={80} color="var(--ember)" />
        </div>
      </section>

      {/* narrator comparison */}
      <section id="compare" className="mx-auto max-w-7xl px-6 md:px-10 py-20 border-t border-border/70">
        <div className="max-w-2xl">
          <p className="text-[10px] uppercase tracking-[0.28em] text-[var(--ember)] mb-4">Narrator Persona Engine</p>
          <h2 className="font-display text-4xl md:text-5xl">One paragraph. Six directors.</h2>
          <p className="mt-4 text-parchment/70">
            Nothing about the text changes. Only the storytelling changes. Click a persona to hear the same words performed by a different voice, mood, and pace.
          </p>
        </div>

        <div className="mt-12 grid gap-8 md:grid-cols-[minmax(0,1fr)_360px]">
          <div className="rounded-3xl border border-border p-8 bg-[var(--ink-2)]/60">
            <p className="text-[10px] uppercase tracking-widest text-muted-foreground mb-4">Now performing · {current.name}</p>
            <p className="font-display text-2xl md:text-3xl leading-snug text-parchment/95">"{SAMPLE}"</p>
            <div className="mt-8">
              <WaveformSpine bars={110} seed={current.id.length + 3} active height={64} color={current.color} />
            </div>
          </div>
          <div className="flex flex-col gap-2">
            {NARRATOR_PERSONAS.map((p) => (
              <button
                key={p.id}
                onClick={() => setActive(p.id)}
                className={`text-left rounded-xl border p-4 transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ember)]/60 ${
                  active === p.id
                    ? "border-[var(--ember)]/60 bg-[var(--ember)]/8"
                    : "border-border bg-[var(--ink-2)]/40 hover:border-parchment/25"
                }`}
              >
                <div className="flex items-center gap-3">
                  <span className="h-2.5 w-2.5 rounded-full" style={{ background: p.color }} />
                  <span className="font-display text-base">{p.name}</span>
                </div>
                <p className="text-xs text-muted-foreground mt-1 pl-6">{p.vibe}</p>
              </button>
            ))}
          </div>
        </div>
      </section>

      {/* pipeline */}
      <section id="pipeline" className="mx-auto max-w-7xl px-6 md:px-10 py-20 border-t border-border/70">
        <div className="max-w-2xl mb-12">
          <p className="text-[10px] uppercase tracking-[0.28em] text-[var(--signal)] mb-4">The Director's Room</p>
          <h2 className="font-display text-4xl md:text-5xl">A pipeline that thinks like a storyteller.</h2>
        </div>
        <div className="grid gap-4 md:grid-cols-3 lg:grid-cols-5">
          {[
            { t: "Understand", d: "Scenes, arcs, silences, pacing." },
            { t: "Cast", d: "Characters detected. Voices assigned." },
            { t: "Direct", d: "Emotion, breath, room tone." },
            { t: "Score", d: "Cinematic music, ducked to dialogue." },
            { t: "Deliver", d: "One episode. Podcast quality." },
          ].map((s, i) => (
            <div key={s.t} className="relative rounded-2xl border border-border p-5 bg-[var(--ink-2)]/60">
              <span className="font-mono text-[10px] text-[var(--ember)]">0{i + 1}</span>
              <h3 className="mt-2 font-display text-xl">{s.t}</h3>
              <p className="mt-2 text-sm text-muted-foreground">{s.d}</p>
            </div>
          ))}
        </div>
      </section>

      {/* features */}
      <section id="features" className="mx-auto max-w-7xl px-6 md:px-10 py-20 border-t border-border/70">
        <div className="grid gap-6 md:grid-cols-3">
          {[
            { t: "Story Understanding", d: "Not TTS. Real narrative comprehension: relationships, arcs, and the pause before a reveal." },
            { t: "Character Voices", d: "Cast the room. Each character gets a voice, pitch, tempo, and temperament that stays consistent." },
            { t: "Cinematic Music", d: "Score generated per scene and ducked automatically under dialogue. Nothing feels stitched." },
          ].map((f) => (
            <div key={f.t} className="rounded-2xl border border-border p-6 bg-[var(--ink-2)]/50">
              <h3 className="font-display text-xl">{f.t}</h3>
              <p className="mt-3 text-sm text-parchment/70 leading-relaxed">{f.d}</p>
            </div>
          ))}
        </div>
      </section>

      {/* cta */}
      <section className="mx-auto max-w-7xl px-6 md:px-10 py-24">
        <div className="relative overflow-hidden rounded-3xl border border-[var(--ember)]/30 bg-gradient-to-br from-[var(--ember)]/15 via-transparent to-[var(--signal)]/10 p-10 md:p-16">
          <h2 className="font-display text-4xl md:text-5xl max-w-2xl">Bring a story. Leave with a cinema.</h2>
          <p className="mt-4 max-w-xl text-parchment/70">Upload a paragraph or a PDF. Choose a narrator. In minutes, hear it performed.</p>
          <div className="mt-8 flex gap-3">
            <Button asChild size="lg" className="bg-[var(--ember)] text-[var(--ink)] hover:bg-[var(--ember)]/90 ember-glow">
              <Link to="/upload">New Story <ArrowRight className="ml-2 h-4 w-4" /></Link>
            </Button>
            <Button asChild size="lg" variant="outline" className="border-parchment/20 bg-transparent text-parchment hover:bg-white/5">
              <Link to="/library">Explore library</Link>
            </Button>
          </div>
        </div>
      </section>

      <footer className="border-t border-border/70">
        <div className="mx-auto max-w-7xl px-6 md:px-10 py-8 flex items-center justify-between text-xs text-muted-foreground">
          <span>© Daastaan AI · Built for Pocket FM Zero-to-One</span>
          <span className="font-mono">v0.1 · night-radio</span>
        </div>
      </footer>
    </div>
  );
}
