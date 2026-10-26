import { createFileRoute, Link } from "@tanstack/react-router";

import { StoreWizard } from "@/components/StoreWizard";

export const Route = createFileRoute("/setup")({
  head: () => ({
    meta: [
      { title: "Halftone — Store setup" },
      { name: "description", content: "Analyse your store, pick a template and fit the menu to your real display." },
    ],
  }),
  component: SetupPage,
});

function SetupPage() {
  return (
    <div className="min-h-screen bg-paper font-body text-ink antialiased">
      <header className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-3 border-b-2 border-ink/15 px-5 py-4 lg:px-10">
        <div className="flex min-w-0 items-center gap-3"><span className="grid size-9 shrink-0 place-items-center bg-poster font-impact text-lg text-poster-foreground">H</span><div className="min-w-0"><span className="font-display text-xl font-black">Halftone</span><span className="ml-2 font-mono text-[10px] font-bold uppercase text-muted">store setup</span></div></div>
        <Link to="/" className="font-mono text-[10px] font-bold uppercase text-press">Back to studio</Link>
      </header>
      <StoreWizard />
    </div>
  );
}
