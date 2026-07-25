import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { UploadCloud, FileText, ArrowRight, Wand2 } from "lucide-react";
import { AppShell } from "@/components/app-shell";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Slider } from "@/components/ui/slider";
import { Label } from "@/components/ui/label";
import { WaveformSpine } from "@/components/waveform-spine";
import { NARRATOR_PERSONAS } from "@/lib/mock-data";
import { api } from "@/lib/api";

export const Route = createFileRoute("/upload")({
  head: () => ({
    meta: [
      { title: "New Story · Daastaan AI" },
      { name: "description", content: "Paste a story, upload a manuscript, and cast the perfect narrator." },
      { property: "og:title", content: "New Story · Daastaan" },
      { property: "og:description", content: "Upload · Cast · Generate." },
    ],
  }),
  component: UploadPage,
});

function UploadPage() {
  const navigate = useNavigate();
  const [text, setText] = useState(
    "The door was already open when she got home. She hadn't left it that way. In the attic, an old cassette began, on its own, to play."
  );
  const [persona, setPersona] = useState("romance");
  const [genre, setGenre] = useState("Literary Fiction");
  const [language, setLanguage] = useState("English");
  const [music, setMusic] = useState([40]);
  const [dragging, setDragging] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  const words = text.trim().split(/\s+/).filter(Boolean).length;
  const est = Math.max(1, Math.round(words / 140));

  const submit = async () => {
    setSubmitting(true);
    const { id } = await api.createEpisode({ text, persona, genre, language, music: music[0] });
    navigate({ to: "/processing/$id", params: { id } });
  };

  return (
    <AppShell subtitle="Upload" title="Begin a new story">
      <div className="grid gap-8 lg:grid-cols-[minmax(0,1fr)_380px]">
        <div className="space-y-6">
          <div
            onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
            onDragLeave={() => setDragging(false)}
            onDrop={(e) => { e.preventDefault(); setDragging(false); }}
            className={`rounded-2xl border-2 border-dashed p-8 text-center transition-colors ${
              dragging ? "border-[var(--ember)] bg-[var(--ember)]/5" : "border-border bg-[var(--ink-2)]/40"
            }`}
          >
            <div className="mx-auto grid h-12 w-12 place-items-center rounded-full bg-[var(--ember)]/15 ring-1 ring-[var(--ember)]/30 mb-4">
              <UploadCloud className="h-5 w-5 text-[var(--ember)]" />
            </div>
            <p className="font-display text-lg">Drag a manuscript</p>
            <p className="text-sm text-muted-foreground mt-1">.txt or .pdf · up to 40,000 words</p>
            <div className="mt-4 flex justify-center gap-2 text-xs">
              <span className="px-2 py-1 rounded-full bg-white/5 text-muted-foreground flex items-center gap-1"><FileText className="h-3 w-3" /> TXT</span>
              <span className="px-2 py-1 rounded-full bg-white/5 text-muted-foreground flex items-center gap-1"><FileText className="h-3 w-3" /> PDF</span>
            </div>
          </div>

          <div>
            <Label className="text-[10px] uppercase tracking-[0.22em] text-muted-foreground">Or paste your text</Label>
            <Textarea
              value={text}
              onChange={(e) => setText(e.target.value)}
              rows={12}
              className="mt-3 bg-[var(--ink-2)]/60 border-border font-display text-lg leading-relaxed resize-none"
              placeholder="Once upon a time…"
            />
            <div className="mt-2 flex justify-between text-xs text-muted-foreground">
              <span>{words.toLocaleString()} words</span>
              <span>~{est} min episode</span>
            </div>
          </div>

          <div className="grid sm:grid-cols-3 gap-4">
            <div>
              <Label className="text-[10px] uppercase tracking-[0.22em] text-muted-foreground">Genre</Label>
              <Select value={genre} onValueChange={setGenre}>
                <SelectTrigger className="mt-2 bg-[var(--ink-2)]/60"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {["Literary Fiction", "Thriller", "Romance", "Fantasy", "Fable", "Mythology"].map((g) => (
                    <SelectItem key={g} value={g}>{g}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div>
              <Label className="text-[10px] uppercase tracking-[0.22em] text-muted-foreground">Language</Label>
              <Select value={language} onValueChange={setLanguage}>
                <SelectTrigger className="mt-2 bg-[var(--ink-2)]/60"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {["English", "Urdu", "Hindi", "Bengali", "Tamil"].map((g) => (
                    <SelectItem key={g} value={g}>{g}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div>
              <Label className="text-[10px] uppercase tracking-[0.22em] text-muted-foreground">Music intensity</Label>
              <div className="mt-4">
                <Slider value={music} onValueChange={setMusic} max={100} step={1} />
                <p className="mt-1 text-xs text-muted-foreground">{music[0]}% · scored to scene</p>
              </div>
            </div>
          </div>

          <div>
            <Label className="text-[10px] uppercase tracking-[0.22em] text-muted-foreground">Narrator persona</Label>
            <div className="mt-3 grid sm:grid-cols-2 lg:grid-cols-3 gap-2">
              {NARRATOR_PERSONAS.map((p) => (
                <button
                  key={p.id}
                  onClick={() => setPersona(p.id)}
                  className={`text-left rounded-xl border p-3 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ember)]/60 ${
                    persona === p.id ? "border-[var(--ember)]/60 bg-[var(--ember)]/8" : "border-border bg-[var(--ink-2)]/40 hover:border-parchment/25"
                  }`}
                >
                  <div className="flex items-center gap-2">
                    <span className="h-2 w-2 rounded-full" style={{ background: p.color }} />
                    <span className="text-sm">{p.name}</span>
                  </div>
                  <p className="text-[11px] text-muted-foreground mt-1 pl-4">{p.vibe}</p>
                </button>
              ))}
            </div>
          </div>
        </div>

        {/* Live story profile */}
        <aside className="lg:sticky lg:top-24 h-fit space-y-5">
          <div className="rounded-2xl border border-border bg-[var(--ink-2)]/60 p-6">
            <p className="text-[10px] uppercase tracking-widest text-muted-foreground">Live story profile</p>
            <h3 className="font-display text-2xl mt-2">Untitled Story</h3>
            <p className="text-xs text-muted-foreground mt-1">{genre} · {language}</p>

            <div className="mt-5 space-y-3 text-sm">
              <Row label="Words" value={words.toLocaleString()} />
              <Row label="Est. runtime" value={`~${est} min`} />
              <Row label="Narrator" value={NARRATOR_PERSONAS.find((p) => p.id === persona)!.name} />
              <Row label="Music" value={`${music[0]}%`} />
            </div>

            <div className="mt-6">
              <WaveformSpine bars={48} seed={17} height={36} color="var(--ember)" />
            </div>
          </div>

          <Button
            onClick={submit}
            disabled={submitting || words < 5}
            size="lg"
            className="w-full h-12 bg-[var(--ember)] text-[var(--ink)] hover:bg-[var(--ember)]/90 ember-glow disabled:opacity-50"
          >
            {submitting ? (
              <>Preparing studio…</>
            ) : (
              <><Wand2 className="h-4 w-4 mr-2" /> Generate Episode <ArrowRight className="ml-2 h-4 w-4" /></>
            )}
          </Button>
        </aside>
      </div>
    </AppShell>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between gap-4 border-b border-border/60 pb-2 last:border-0">
      <span className="text-muted-foreground text-xs uppercase tracking-widest">{label}</span>
      <span className="text-parchment">{value}</span>
    </div>
  );
}
