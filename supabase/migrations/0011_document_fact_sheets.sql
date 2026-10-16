-- A short fact sheet for each large customer document (catalogue, brochure, company profile,
-- presentation, case study), written once by the AI and reused.
--
-- The planning agents read every document four times in a row (checklist, diagnosis, strategy,
-- execution). A 100-page catalogue read in full each time costs more, is slower, and draws the
-- AI's attention away from the BD documents and the checklist answers. So a large customer
-- document reaches the agents as its fact sheet instead (lib/agents/fact-sheet.ts).
--
-- ai_summary_chars is the length of the text the sheet was written from: when the file's text
-- changes, the lengths no longer match and the sheet is written again.

alter table public.revenueos_project_files
  add column if not exists ai_summary text,
  add column if not exists ai_summary_chars integer,
  add column if not exists ai_summary_at timestamptz;
