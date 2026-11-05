#!/usr/bin/env bash
# Pulls the Halftone upgrade from GitHub into a Lovable project (no pasting, ~1 credit).
# Usage inside the Lovable agent shell: bash fetch-from-github.sh
set -euo pipefail
SHA="${HALFTONE_SHA:-537540541372af00476817912c67293544e0dee0}"
BASE="https://raw.githubusercontent.com/dhruvchatgptanu-prog/blow-studios/$SHA/lovable/menu-master-ai"
FILES="src/components/BriefCoach.tsx src/components/ChatComposer.tsx src/components/ChatMessageView.tsx src/components/EnginePanel.tsx src/components/MenuSheet.tsx src/components/StoreWizard.tsx src/lib/ai/assets.server.ts src/lib/ai/coach.ts src/lib/ai/color.ts src/lib/ai/cuisine.ts src/lib/ai/display.ts src/lib/ai/ensemble.server.ts src/lib/ai/intent.ts src/lib/ai/knowledge.ts src/lib/ai/memory.server.ts src/lib/ai/providers.server.ts src/lib/ai/schema.ts src/lib/ai/store.server.ts src/lib/ai/store.ts src/lib/ai/templates.ts src/lib/ai/verify.ts src/lib/assets.functions.ts src/lib/chat.ts src/lib/images.ts src/lib/menu.functions.ts src/lib/setup.ts src/lib/store.functions.ts src/routeTree.gen.ts src/routes/index.tsx src/routes/setup.tsx src/routes/studio.tsx"
for f in $FILES; do
  mkdir -p "$(dirname "$f")"
  curl -fsSL "$BASE/$f" -o "$f"
  echo "fetched $f"
done
echo "done: $(echo $FILES | wc -w) files from $SHA"
