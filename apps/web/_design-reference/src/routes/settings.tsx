import { createFileRoute } from "@tanstack/react-router";
import { AppShell } from "@/components/app-shell";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Slider } from "@/components/ui/slider";
import { Button } from "@/components/ui/button";

export const Route = createFileRoute("/settings")({
  head: () => ({
    meta: [
      { title: "Settings · Daastaan AI" },
      { name: "description", content: "Voice defaults, audio quality, language and notifications." },
      { property: "og:title", content: "Settings · Daastaan" },
      { property: "og:description", content: "Tune your studio." },
    ],
  }),
  component: SettingsPage,
});

function Section({ title, desc, children }: { title: string; desc: string; children: React.ReactNode }) {
  return (
    <div className="grid gap-6 md:grid-cols-[280px_minmax(0,1fr)] py-8 border-b border-border last:border-0">
      <div>
        <h3 className="font-display text-lg">{title}</h3>
        <p className="text-xs text-muted-foreground mt-1">{desc}</p>
      </div>
      <div className="space-y-5">{children}</div>
    </div>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-4">
      <Label className="text-sm">{label}</Label>
      <div>{children}</div>
    </div>
  );
}

function SettingsPage() {
  return (
    <AppShell subtitle="Settings" title="Studio preferences">
      <div className="max-w-4xl">
        <Section title="Voice defaults" desc="What every new story starts with, before you cast.">
          <Row label="Default narrator persona">
            <Select defaultValue="romance">
              <SelectTrigger className="w-56 bg-[var(--ink-2)]/60"><SelectValue /></SelectTrigger>
              <SelectContent>
                {["thriller", "romance", "fantasy", "podcast", "grandmother", "sage"].map((p) => <SelectItem key={p} value={p}>{p}</SelectItem>)}
              </SelectContent>
            </Select>
          </Row>
          <Row label="Baseline energy">
            <div className="w-56"><Slider defaultValue={[65]} /></div>
          </Row>
          <Row label="Auto-detect characters"><Switch defaultChecked /></Row>
        </Section>

        <Section title="Audio quality" desc="Balance render time against fidelity.">
          <Row label="Sample rate">
            <Select defaultValue="48">
              <SelectTrigger className="w-56 bg-[var(--ink-2)]/60"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="24">24 kHz</SelectItem>
                <SelectItem value="44">44.1 kHz</SelectItem>
                <SelectItem value="48">48 kHz · studio</SelectItem>
              </SelectContent>
            </Select>
          </Row>
          <Row label="Music ducking"><Switch defaultChecked /></Row>
          <Row label="Loudness target">
            <Select defaultValue="-16">
              <SelectTrigger className="w-56 bg-[var(--ink-2)]/60"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="-14">-14 LUFS · streaming</SelectItem>
                <SelectItem value="-16">-16 LUFS · podcast</SelectItem>
                <SelectItem value="-20">-20 LUFS · broadcast</SelectItem>
              </SelectContent>
            </Select>
          </Row>
        </Section>

        <Section title="Language" desc="Default output language for narration.">
          <Row label="Language">
            <Select defaultValue="English">
              <SelectTrigger className="w-56 bg-[var(--ink-2)]/60"><SelectValue /></SelectTrigger>
              <SelectContent>
                {["English", "Urdu", "Hindi", "Bengali", "Tamil"].map((l) => <SelectItem key={l} value={l}>{l}</SelectItem>)}
              </SelectContent>
            </Select>
          </Row>
        </Section>

        <Section title="Notifications" desc="How the studio taps you on the shoulder.">
          <Row label="Episode ready"><Switch defaultChecked /></Row>
          <Row label="Pipeline failed"><Switch defaultChecked /></Row>
          <Row label="Weekly digest"><Switch /></Row>
        </Section>

        <div className="pt-6 flex justify-end">
          <Button className="bg-[var(--ember)] text-[var(--ink)] hover:bg-[var(--ember)]/90 ember-glow">Save changes</Button>
        </div>
      </div>
    </AppShell>
  );
}
