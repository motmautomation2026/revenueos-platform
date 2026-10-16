import type { Metadata } from "next";
import Link from "next/link";
import { Building2, ExternalLink, Mail, SearchIcon, Smartphone, UsersRound, type LucideIcon } from "lucide-react";
import {
  EmptyTool,
  ExecutionEmpty,
  ExecutionHeader,
  InsightTileCard,
  Pill,
  SectionTitle,
  number,
} from "@/components/brain/parts";
import { ContactEditButton } from "@/components/brain/contact-edit-button";
import { buttonVariants } from "@/components/ui/button";
import { loadResearchRecords } from "@/lib/brain/read";
import { buildResearchReport, buildResultTiles, placeOf, toolsInRun, type ResultTile } from "@/lib/brain/tools";
import { maskPhone } from "@/lib/brain/view";
import { cn } from "@/lib/utils";
import { loadExecutionPage, type ExecutionPageProps } from "../load";

export const metadata: Metadata = { title: "Data research" };

/** Only http(s) links are rendered as links; anything else is shown as plain text. */
function safeUrl(value: string | null): string | null {
  if (!value) return null;
  const candidate = /^https?:\/\//i.test(value) ? value : `https://${value}`;
  try {
    const url = new URL(candidate);
    return url.protocol === "https:" || url.protocol === "http:" ? url.toString() : null;
  } catch {
    return null;
  }
}

function initials(name: string | null) {
  const words = (name ?? "").trim().split(/\s+/).filter(Boolean);
  return ((words[0]?.[0] ?? "") + (words.length > 1 ? words[words.length - 1][0] : "")).toUpperCase() || "?";
}

const TILE_ICONS: Record<ResultTile["key"], LucideIcon> = {
  companies: Building2,
  contacts: UsersRound,
  email: Mail,
  mobile: Smartphone,
};

export default async function ResearchPage(props: ExecutionPageProps) {
  const { project, data, base, query } = await loadExecutionPage(props);
  // Profiles can be edited only in Staging: it is how a trial reaches your own phone and inbox.
  const canEdit = project.run_mode === "staging";
  if (data.state !== "ok") return <ExecutionEmpty data={data} />;

  const { view, runs, rows, customer } = data;
  const report = buildResearchReport(rows);
  const used = toolsInRun(rows.tasks).research;
  const records = await loadResearchRecords(customer, { search: query.q, page: Number(query.page) || 1 });

  const list = query.list === "companies" || query.q || query.page ? "companies" : "contacts";
  const usedCompanies = new Set(report.usedCompanyIds);
  const usedPeople = new Set(report.usedPeopleIds);

  const href = (changes: Record<string, string | null>) => {
    const params = new URLSearchParams();
    const all = { run: query.run ?? null, list, q: query.q ?? null, page: query.page ?? null, ...changes };
    for (const [key, value] of Object.entries(all)) if (value) params.set(key, value);
    const text = params.toString();
    return `${base}/research${text ? `?${text}` : ""}#records`;
  };

  return (
    <div className="grid gap-8">
      <ExecutionHeader title="Data research" view={view} runs={runs} basePath={`${base}/research`} />

      {!used && <EmptyTool>This month plan has no data research task.</EmptyTool>}

      {used && (
        <section className="grid gap-3">
          <SectionTitle>This month plan</SectionTitle>
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            {buildResultTiles(report).map((tile) => (
              <InsightTileCard key={tile.key} tile={tile} icon={TILE_ICONS[tile.key]} />
            ))}
          </div>
        </section>
      )}

      {!records ? (
        <EmptyTool>The company and contact records could not be loaded.</EmptyTool>
      ) : (
        <section id="records" className="grid scroll-mt-20 gap-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div role="tablist" aria-label="Records" className="flex rounded-lg border p-0.5">
              {(
                [
                  { key: "contacts", label: "Contacts", count: records.peopleTotal, icon: UsersRound },
                  { key: "companies", label: "Companies", count: records.companyTotal, icon: Building2 },
                ] as const
              ).map((tab) => (
                <Link
                  key={tab.key}
                  role="tab"
                  aria-selected={list === tab.key}
                  href={href({ list: tab.key, q: null, page: null })}
                  className={cn(
                    "flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium outline-none focus-visible:ring-3 focus-visible:ring-ring/50",
                    list === tab.key ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground"
                  )}
                >
                  <tab.icon className="size-4" aria-hidden />
                  {tab.label}
                  <span className={cn("text-xs tabular-nums", list === tab.key ? "opacity-80" : "")}>
                    {number.format(tab.count)}
                  </span>
                </Link>
              ))}
            </div>

            {list === "companies" && (
              <form action={`${base}/research`} className="flex min-w-0 flex-1 flex-wrap justify-end gap-2">
                {query.run && <input type="hidden" name="run" value={query.run} />}
                <input type="hidden" name="list" value="companies" />
                <label className="relative min-w-0 flex-1 sm:max-w-xs">
                  <span className="sr-only">Search companies by name</span>
                  <SearchIcon className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" aria-hidden />
                  <input
                    type="search"
                    name="q"
                    defaultValue={query.q ?? ""}
                    placeholder="Search companies by name"
                    className="h-9 w-full rounded-lg border bg-background pr-3 pl-8 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
                  />
                </label>
                <button type="submit" className={buttonVariants({ variant: "outline", size: "lg" })}>
                  Search
                </button>
                {query.q && (
                  <Link href={href({ q: null, page: null })} className={buttonVariants({ variant: "ghost", size: "lg" })}>
                    Clear
                  </Link>
                )}
              </form>
            )}
          </div>

          {canEdit && list === "contacts" && records.people.length > 0 && (
            <p className="rounded-lg border border-amber-500/50 bg-amber-500/10 px-3 py-2 text-sm text-amber-900">
              Staging: calls and emails go to the phone and email shown on each profile. Use Edit to put in your
              own before approving a campaign.
            </p>
          )}

          {list === "contacts" ? (
            records.people.length === 0 ? (
              <EmptyTool>No contacts have been saved yet.</EmptyTool>
            ) : (
              <div className="overflow-x-auto rounded-xl border">
                <table className="w-full min-w-[52rem] text-left text-sm">
                  <thead className="bg-muted/60 text-xs text-muted-foreground">
                    <tr>
                      <th className="px-4 py-2.5 font-semibold">Contact</th>
                      <th className="px-4 py-2.5 font-semibold">Company</th>
                      <th className="px-4 py-2.5 font-semibold">Email</th>
                      <th className="px-4 py-2.5 font-semibold">Phone</th>
                      <th className="px-4 py-2.5 font-semibold">This plan</th>
                      {canEdit && (
                        <th className="px-4 py-2.5">
                          <span className="sr-only">Edit</span>
                        </th>
                      )}
                    </tr>
                  </thead>
                  <tbody>
                    {records.people.map((person) => {
                      const profile = safeUrl(person.user_social);
                      const phoneType = report.phoneTypes[String(person.id)];
                      return (
                        <tr key={person.id} className="border-t align-middle">
                          <td className="px-4 py-2.5">
                            <div className="flex items-center gap-3">
                              <span
                                aria-hidden
                                className="flex size-8 shrink-0 items-center justify-center rounded-full bg-primary/10 text-xs font-semibold text-primary"
                              >
                                {initials(person.full_name)}
                              </span>
                              <div className="min-w-0">
                                {profile ? (
                                  <a
                                    href={profile}
                                    target="_blank"
                                    rel="noopener noreferrer"
                                    className="inline-flex items-center gap-1 font-medium hover:underline"
                                  >
                                    {person.full_name ?? "—"}
                                    <ExternalLink className="size-3 text-muted-foreground" aria-hidden />
                                  </a>
                                ) : (
                                  <p className="font-medium">{person.full_name ?? "—"}</p>
                                )}
                                <p className="text-xs text-muted-foreground">{person.job_position ?? "—"}</p>
                              </div>
                            </div>
                          </td>
                          <td className="px-4 py-2.5">
                            <p>{person.company_name ?? "—"}</p>
                            {person.location && <p className="text-xs text-muted-foreground">{person.location}</p>}
                          </td>
                          <td className="px-4 py-2.5 break-all">{person.email ?? "—"}</td>
                          <td className="px-4 py-2.5 whitespace-nowrap">
                            <span className="tabular-nums">{person.phone_number ? maskPhone(person.phone_number) : "—"}</span>
                            {phoneType && (
                              <Pill
                                className={cn(
                                  "ml-2 capitalize",
                                  phoneType === "mobile" ? "bg-emerald-500/15 text-emerald-800" : "bg-amber-500/20 text-amber-900"
                                )}
                              >
                                {phoneType}
                              </Pill>
                            )}
                          </td>
                          <td className="px-4 py-2.5">
                            {usedPeople.has(person.id) ? (
                              <Pill className="bg-primary/10 text-primary">Used</Pill>
                            ) : (
                              <span className="text-xs text-muted-foreground">Not used</span>
                            )}
                          </td>
                          {canEdit && (
                            <td className="px-2 py-2.5 text-right">
                              <ContactEditButton
                                projectId={project.id}
                                personId={person.id}
                                name={person.full_name ?? ""}
                                email={person.email ?? ""}
                                phone={person.phone_number ?? ""}
                              />
                            </td>
                          )}
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )
          ) : records.companies.length === 0 ? (
            <EmptyTool>{query.q ? "No company matches that search." : "No companies have been saved yet."}</EmptyTool>
          ) : (
            <>
              <div className="overflow-x-auto rounded-xl border">
                <table className="w-full min-w-[52rem] text-left text-sm">
                  <thead className="bg-muted/60 text-xs text-muted-foreground">
                    <tr>
                      <th className="px-4 py-2.5 font-semibold">Company</th>
                      <th className="px-4 py-2.5 font-semibold">Industry</th>
                      <th className="px-4 py-2.5 font-semibold">Location</th>
                      <th className="px-4 py-2.5 text-right font-semibold">Employees</th>
                      <th className="px-4 py-2.5 font-semibold">This plan</th>
                    </tr>
                  </thead>
                  <tbody>
                    {records.companies.map((company) => {
                      const site = safeUrl(company.website);
                      const linkedin = safeUrl(company.linkedin);
                      const place = placeOf(company.location);
                      return (
                        <tr key={company.id} className="border-t align-middle">
                          <td className="px-4 py-2.5">
                            <p className="font-medium">{company.company_name ?? "—"}</p>
                            <p className="flex flex-wrap gap-x-3 text-xs text-muted-foreground">
                              {site && (
                                <a href={site} target="_blank" rel="noopener noreferrer" className="hover:underline">
                                  {company.website}
                                </a>
                              )}
                              {linkedin && (
                                <a href={linkedin} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-0.5 hover:underline">
                                  LinkedIn
                                  <ExternalLink className="size-3" aria-hidden />
                                </a>
                              )}
                            </p>
                          </td>
                          <td className="px-4 py-2.5">{company.industry ?? "—"}</td>
                          <td className="px-4 py-2.5" title={company.location ?? undefined}>
                            {[place.city, place.region].filter(Boolean).join(", ") || "—"}
                          </td>
                          <td className="px-4 py-2.5 text-right tabular-nums">
                            {company.headcount != null ? number.format(company.headcount) : "—"}
                          </td>
                          <td className="px-4 py-2.5">
                            {usedCompanies.has(company.id) ? (
                              <Pill className="bg-primary/10 text-primary">Used</Pill>
                            ) : (
                              <span className="text-xs text-muted-foreground">Not used</span>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>

              <nav aria-label="Company pages" className="flex items-center justify-between gap-3 text-sm">
                {records.page > 1 ? (
                  <Link href={href({ page: records.page > 2 ? String(records.page - 1) : null })} className={buttonVariants({ variant: "outline" })}>
                    Previous
                  </Link>
                ) : (
                  <span />
                )}
                <span className="text-muted-foreground">
                  {query.q && <>{number.format(records.companyTotal)} matching · </>}
                  Page {records.page} of {records.pageCount}
                </span>
                {records.page < records.pageCount ? (
                  <Link href={href({ page: String(records.page + 1) })} className={buttonVariants({ variant: "outline" })}>
                    Next
                  </Link>
                ) : (
                  <span />
                )}
              </nav>
            </>
          )}
        </section>
      )}
    </div>
  );
}
