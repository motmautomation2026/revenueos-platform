# RevenueOS

RevenueOS is a web app for MOTM Technologies. A signed-in user creates a project for a new customer, uploads that customer's documents, fills in an AI-written discovery checklist, and gets a complete go-to-market (GTM) plan written by three AI agents in sequence.

## How it works

1. **Upload** the customer's documents (BD handover, proposal, signed scope, notes). Text is extracted on the server.
2. **Checklist.** The Checklist Agent reads the documents and writes 40–50 questions specific to this customer, prefilled where the documents already give the answer. The user fills it in on the page or through Excel.
3. **Plan.** Three agents run in order, each building on the previous one:
   - **Diagnosis** — what MOTM understands about the customer. It also decides whether there is enough information to continue.
   - **Strategy** — positioning, market map, priorities, personas, demand triggers.
   - **Execution** — channels, process, qualification, roadmap, accountability.
4. **Review** the plan as a document, and download it as HTML or PDF.

## Tech stack

- Next.js 15 (App Router), TypeScript, Tailwind CSS, shadcn/ui
- Supabase: Auth (email + password), Postgres with row level security, Storage
- OpenAI (`gpt-4.1` by default), called from the server only
- `unpdf`, `mammoth` and `exceljs` for text extraction; `ajv` for validating agent output

## Setup

You need Node.js 20 or newer, a Supabase project, and an OpenAI API key.

### 1. Install

```bash
git clone https://github.com/motmautomation2026/revenueos-platform.git
cd revenueos-platform
npm install
```

### 2. Environment variables

Create a file named `.env.local` in the project root:

```
NEXT_PUBLIC_SUPABASE_URL=
NEXT_PUBLIC_SUPABASE_ANON_KEY=
SUPABASE_SERVICE_ROLE_KEY=
OPENAI_API_KEY=
OPENAI_MODEL=gpt-4.1
```

| Variable | Where to find it |
| --- | --- |
| `NEXT_PUBLIC_SUPABASE_URL` | Supabase → Project Settings → API |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | Supabase → Project Settings → API |
| `SUPABASE_SERVICE_ROLE_KEY` | Supabase → Project Settings → API. **Secret.** |
| `OPENAI_API_KEY` | OpenAI dashboard. **Secret.** |
| `OPENAI_MODEL` | Optional. Defaults to `gpt-4.1`. |

`SUPABASE_SERVICE_ROLE_KEY` and `OPENAI_API_KEY` are server-only. Never prefix them with `NEXT_PUBLIC_` and never commit them. All `.env*` files are git-ignored.

### 3. Database

Open the Supabase SQL editor, paste the contents of [`supabase/migrations/0001_init.sql`](supabase/migrations/0001_init.sql), and run it once. It creates the tables, row level security policies, the sign-up trigger and the private storage bucket.

Everything the migration creates is prefixed `revenueos_` (for example `revenueos_projects`, and the bucket `revenueos-project-files`), so it can share a Supabase project with other apps.

### 4. Supabase Auth

In Supabase → Authentication → URL Configuration, add this to the redirect URLs:

```
http://localhost:3000/auth/callback
```

Add your production URL with the same path when you deploy. Without it, the links in the confirmation and password-reset emails are rejected.

### 5. Run

```bash
npm run dev
```

Open http://localhost:3000, sign up, confirm your email, and create a project.

## Commands

| Command | What it does |
| --- | --- |
| `npm run dev` | Start the development server |
| `npm run build` | Production build |
| `npm run start` | Serve the production build |
| `npm run lint` | Lint |
| `npm test` | Run the unit tests |
| `npm run schemas` | Regenerate the agent output schemas from the prompts |

## Agents, prompts and schemas

Each agent's system prompt is a file in `prompts/`, and the JSON shape it must return is a file in `schemas/`.

| Agent | Prompt | Schema |
| --- | --- | --- |
| Checklist | `prompts/checklist.md` | `schemas/checklist.schema.json` |
| Diagnosis | `prompts/diagnose.md` | `schemas/diagnosis.schema.json` |
| Strategy | `prompts/strategy.md` | `schemas/strategy.schema.json` |
| Execution | `prompts/execution.md` | `schemas/execution.schema.json` |

To change what an agent writes, edit its prompt. No code change is needed.

The diagnosis, strategy and execution schemas are generated from the `OUTPUT FORMAT` block at the end of each prompt. **If you change a prompt's output format, run `npm run schemas`** so the schema matches. The checklist schema is maintained by hand.

Every agent call goes through one function, `lib/agents/run-agent.ts`, which:

- loads the prompt and schema and calls OpenAI
- repairs small slips in the returned JSON (code fences, missing fields, wrong enum case, numbers sent as text)
- validates the result against the schema and, if it fails, asks the model once to correct it
- records the call in `revenueos_agent_runs`, including tokens used, duration and any repairs

Extracting text from uploaded files does not call OpenAI. Tokens are used only when a checklist or a plan is generated.

## Project structure

```
app/
  (auth)/          login, signup, reset-password
  (app)/           projects, projects/new, projects/[id]/{upload,checklist,plan}, account
  api/             plan start and progress, HTML download, checklist Excel export
  auth/callback/   landing route for email confirmation and reset links
components/        ui/ (shadcn), upload/, checklist/, plan/
lib/
  supabase/        server, browser, middleware and service-role clients; table names
  agents/          run-agent, normalize, build-inputs, checklist, pipeline, config
  files/           text extraction, Excel import/export, upload rules
  plan/            plan document model and HTML renderer
prompts/           agent system prompts
schemas/           agent output schemas
supabase/migrations/0001_init.sql
tests/             unit tests
```

## How the plan pipeline runs

`POST /api/projects/[id]/plan` creates a plan row and returns immediately; the three agents then run on the server, so closing the browser does not stop them. The page polls `GET /api/plans/[planId]` for progress.

- Each step's result is saved before the next step starts. A failed run resumes from the failed step.
- If the Diagnosis Agent reports that it cannot proceed, the plan is marked blocked. The user either answers the blockers (saved as `Follow-up` checklist items, then diagnosis re-runs) or overrides with a written reason.
- Only one plan can run per project at a time.
- Re-running creates a new version; earlier versions stay available.

The background run uses the Supabase service-role key, because the user's session cookies are gone once the request has returned. The route checks that the signed-in user owns the project before starting it.

## Security

- Every page, server action and route handler checks the session and that the project belongs to the signed-in user. Row level security is the second layer.
- All AI calls and all file extraction happen on the server.
- Uploads are checked by extension, MIME type and file signature. File names are sanitised.
- Agent output is always escaped when rendered, in the page and in the downloaded HTML.
- The checklist's internal fields (`reason`, flags, CAM notes) are never sent to the customer-facing list or the Excel file. The downloaded plan leaves out the internal section unless it is switched on.

## Deploying

The plan route sets `maxDuration = 800` seconds. On a serverless host, check that your plan allows function runs that long; a full plan takes several minutes. Set the same environment variables on the host, and add the production `/auth/callback` URL in Supabase.
