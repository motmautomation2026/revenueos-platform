/** Every database object this app owns is prefixed `revenueos_`: the Supabase project is shared. */
export const TABLES = {
  profiles: "revenueos_profiles",
  projects: "revenueos_projects",
  projectFiles: "revenueos_project_files",
  checklists: "revenueos_checklists",
  checklistItems: "revenueos_checklist_items",
  plans: "revenueos_plans",
  agentRuns: "revenueos_agent_runs",
} as const;

export const STORAGE_BUCKET = "revenueos-project-files";
