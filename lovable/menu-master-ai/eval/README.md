# Eval harness

Runs the deterministic parts of Halftone's engine (cuisine detection, brief
extraction, photo-prompt enrichment, verifier) over fixed briefs. No API keys
or credits needed.

```sh
# from an empty folder
npm init -y && npm i typescript@5.8 zod@3.25.76 @anthropic-ai/sdk @supabase/supabase-js @types/node@22
mkdir -p src/lib src/integrations/supabase
cp -r <repo>/lovable/menu-master-ai/src/lib/ai src/lib/ai
printf 'import { createClient } from "@supabase/supabase-js";\nexport const supabaseAdmin = createClient("https://x.supabase.co", "sb_secret_x");\n' > src/integrations/supabase/client.server.ts
npx tsc --strict --noUncheckedIndexedAccess --exactOptionalPropertyTypes --skipLibCheck --target ES2022 --module ESNext --moduleResolution Bundler --baseUrl . --paths '{"@/*":["src/*"]}' --outDir out --rootDir . src/lib/ai/*.ts
sed -i -E 's#from "\./(schema|verify|knowledge|providers\.server|ensemble\.server|cuisine|memory\.server)"#from "./\1.js"#g' out/src/lib/ai/*.js
cp <repo>/lovable/menu-master-ai/eval/*.mjs . && node eval.mjs && node verifier.test.mjs
```

Expected: `ALL PASS`, and the sample Yiros House brief scores 100/100.
