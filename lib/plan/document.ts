// Turns the three agent outputs into one ordered document model. The React page and the
// downloadable HTML file both render this model, so they always agree on order and content.

export type Block =
  | { type: "text"; text: string }
  | { type: "lead"; text: string }
  | { type: "heading"; text: string }
  | { type: "list"; items: string[]; ordered?: boolean }
  | { type: "pairs"; items: { label: string; value: string }[] }
  | { type: "kpis"; items: { label: string; value: string; note?: string }[] }
  | { type: "table"; columns: string[]; rows: string[][] }
  | { type: "columns"; columns: { title: string; items: string[] }[] }
  | { type: "timeline"; phases: TimelinePhase[] };

export type TimelinePhase = {
  title: string;
  subtitle: string;
  activities: string[];
  milestones: { text: string; value: string; basis: string }[];
};

export type Section = { id: string; title: string; blocks: Block[] };
export type Part = { id: string; title: string; internal?: boolean; sections: Section[] };

export type PlanDocument = {
  customerName: string;
  title: string;
  subtitle: string;
  parts: Part[];
};

export type PlanInternal = {
  camNotes: unknown;
  flags: unknown;
  overrideReason: string | null;
  repairs: { agent: string; path: string; fix: string }[];
};

type Json = Record<string, unknown>;

// --- Tolerant readers: stored output is schema-checked, but never trust shape when rendering.
const obj = (value: unknown): Json =>
  value && typeof value === "object" && !Array.isArray(value) ? (value as Json) : {};
const list = (value: unknown): unknown[] => (Array.isArray(value) ? value : []);
const rows = (value: unknown): Json[] => list(value).map(obj);
const str = (value: unknown): string =>
  value == null ? "" : typeof value === "string" ? value.trim() : String(value);
const strings = (value: unknown): string[] => list(value).map(str).filter(Boolean);
const yesNo = (value: unknown) => (value === true ? "Yes" : value === false ? "No" : "");
const label = (value: unknown) => {
  const text = str(value).replace(/_/g, " ");
  return text ? text[0].toUpperCase() + text.slice(1) : "";
};

const inr = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2 });
const RUPEE_UNIT = /₹|\binr\b|rupee|\brs\.?\b/i;

/** "₹1,00,000 – ₹5,00,000", "20 – 30 %", "45 days". Indian digit grouping throughout. */
export function formatRange(low: unknown, high: unknown, unit: unknown): string {
  const numbers = [low, high].filter((n): n is number => typeof n === "number" && Number.isFinite(n));
  if (!numbers.length) return "";
  const unitText = str(unit);
  const rupees = RUPEE_UNIT.test(unitText);
  const suffix = unitText.replace(/₹/g, "").trim();
  const one = (n: number) => `${rupees ? "₹" : ""}${inr.format(n)}`;
  const range =
    numbers.length === 2 && numbers[0] !== numbers[1]
      ? `${one(numbers[0])} – ${one(numbers[1])}`
      : one(numbers[0]);
  return suffix && !(rupees && /^(inr|rupees?|rs\.?)$/i.test(suffix)) ? `${range} ${suffix}` : range;
}

// --- Block builders that drop themselves when empty, so sections never show blank shells.
const text = (value: unknown): Block[] => (str(value) ? [{ type: "text", text: str(value) }] : []);
const lead = (value: unknown): Block[] => (str(value) ? [{ type: "lead", text: str(value) }] : []);
const heading = (value: string, following: Block[]): Block[] =>
  following.length ? [{ type: "heading", text: value }, ...following] : [];
const bullets = (value: unknown, ordered = false): Block[] => {
  const items = strings(value);
  return items.length ? [{ type: "list", items, ordered }] : [];
};
const pairs = (items: [string, unknown][]): Block[] => {
  const kept = items.map(([l, v]) => ({ label: l, value: str(v) })).filter((p) => p.value);
  return kept.length ? [{ type: "pairs", items: kept }] : [];
};
const table = (columns: string[], data: string[][]): Block[] => {
  const kept = data.filter((row) => row.some(Boolean));
  return kept.length ? [{ type: "table", columns, rows: kept }] : [];
};
const columns = (cols: [string, unknown][]): Block[] => {
  const kept = cols.map(([title, v]) => ({ title, items: strings(v) })).filter((c) => c.items.length);
  return kept.length ? [{ type: "columns", columns: kept }] : [];
};
const joined = (value: unknown) => strings(value).join("; ");

function diagnosisPart(d: Json): Part {
  const business = obj(d.business_understanding);
  const chain = obj(business.value_chain);
  const gap = obj(d.gap);
  const competition = obj(d.competition);
  const capacity = obj(d.capacity);

  return {
    id: "part-1",
    title: "Part 1 · Diagnosis",
    sections: [
      {
        id: "business-understanding",
        title: "Business understanding",
        blocks: [
          ...lead(business.summary),
          ...pairs([
            ["Products or equipment", joined(chain.products_or_equipment)],
            ["Risk the buyer faces", chain.buyer_risk],
            ["Need this creates", joined(chain.need_created)],
            ["The customer's solution", chain.customer_solution],
          ]),
          ...heading(
            "Offerings",
            table(
              ["Offering", "Type", "Notes"],
              rows(business.offerings).map((o) => [str(o.name), label(o.type), str(o.notes)])
            )
          ),
        ],
      },
      {
        id: "key-numbers",
        title: "Key numbers",
        blocks: (() => {
          const items = rows(d.key_numbers)
            .map((k) => ({ label: str(k.label), value: str(k.value), note: str(k.source_ref) }))
            .filter((k) => k.label || k.value);
          return items.length ? [{ type: "kpis", items } as Block] : [];
        })(),
      },
      {
        id: "where-they-win",
        title: "Where they win",
        blocks: table(
          ["Question", "Answer", "Source"],
          rows(d.where_they_win).map((r) => [str(r.label), str(r.answer), str(r.source_ref)])
        ),
      },
      {
        id: "how-they-sell",
        title: "How they sell today",
        blocks: table(
          ["Question", "Answer", "Source"],
          rows(d.how_they_sell_today).map((r) => [str(r.label), str(r.answer), str(r.source_ref)])
        ),
      },
      {
        id: "gap",
        title: "The gap",
        blocks: [
          ...columns([
            ["Today", gap.today],
            ["Target", gap.target],
          ]),
          ...lead(gap.core_message),
        ],
      },
      {
        id: "market-conditions",
        title: "Buyer behaviour and market conditions",
        blocks: table(
          ["Insight", "Detail", "What it means for the customer", "Basis"],
          rows(d.market_conditions).map((m) => [
            str(m.insight),
            str(m.detail),
            str(m.implication_for_customer),
            label(m.basis),
          ])
        ),
      },
      {
        id: "competition",
        title: "Competition",
        blocks: [
          ...text(competition.summary),
          ...table(
            ["Competitor", "Where they win", "Where the customer wins", "Public profile (to verify)"],
            rows(competition.named_competitors).map((c) => [
              str(c.name),
              str(c.where_they_win),
              str(c.where_customer_wins),
              str(c.public_profile_verify),
            ])
          ),
          ...heading("Competitor categories", bullets(competition.competitor_categories)),
        ],
      },
      {
        id: "funnel-facts",
        title: "Funnel facts",
        blocks: table(
          ["Metric", "Value", "Source", "Confidence", "Reasoning"],
          rows(d.funnel_facts).map((f) => [
            str(f.metric),
            formatRange(f.value_low, f.value_high, f.unit) || str(f.unit),
            label(f.source),
            label(f.confidence),
            str(f.reasoning),
          ])
        ),
      },
      {
        id: "capacity",
        title: "Delivery capacity",
        blocks: [
          ...text(capacity.summary),
          ...pairs([["Limits volume targets", yesNo(capacity.limits_volume_targets)]]),
        ],
      },
    ],
  };
}

function strategyPart(s: Json): Part {
  const reframe = obj(s.offer_reframe);
  const positioning = obj(s.positioning);
  const map = obj(s.market_map);
  const triggers = obj(s.demand_triggers);

  return {
    id: "part-2",
    title: "Part 2 · Strategy",
    sections: [
      {
        id: "offer-reframe",
        title: "The real market",
        blocks: [
          ...pairs([
            ["From", reframe.from],
            ["To", reframe.to],
          ]),
          ...text(reframe.why_it_matters),
        ],
      },
      {
        id: "positioning",
        title: "Positioning",
        blocks: [
          ...lead(positioning.territory),
          ...heading("Pillars", bullets(positioning.pillars)),
          ...pairs([
            ["What the customer should remember", positioning.customer_should_remember],
            ["Note on claims", positioning.claim_caveat],
          ]),
        ],
      },
      {
        id: "market-map",
        title: "Market map",
        blocks: [
          ...columns([
            ["Industries", map.industries],
            ["Equipment or products", map.equipment_or_products],
            ["Requirements", map.requirements],
            ["Customer types", map.customer_types],
          ]),
          ...heading("Example targets", bullets(map.example_targets)),
        ],
      },
      {
        id: "priorities",
        title: "Priorities",
        blocks: [
          ...heading("Criteria used", bullets(s.prioritisation_criteria)),
          ...table(
            ["Tier", "Segment", "Why", "Evidence"],
            rows(s.priorities).map((p) => [str(p.tier), str(p.segment), str(p.rationale), joined(p.evidence)])
          ),
        ],
      },
      {
        id: "geography",
        title: "Geography",
        blocks: table(
          ["Region", "Priority", "Clusters (public data)", "Relevant demand", "Why", "Applies to"],
          rows(s.geography).map((g) => [
            str(g.region),
            str(g.priority),
            str(g.clusters_public_data),
            str(g.relevant_demand),
            str(g.why),
            label(g.applies_to),
          ])
        ),
      },
      {
        id: "buying-behaviour",
        title: "How each company type buys",
        blocks: table(
          ["Company type", "How they buy", "What they care about", "Typical barrier", "Best way in"],
          rows(s.buying_behaviour).map((b) => [
            str(b.company_type),
            str(b.how_they_buy),
            str(b.what_they_care_about),
            str(b.typical_barrier),
            str(b.best_way_in),
          ])
        ),
      },
      {
        id: "personas",
        title: "Personas",
        blocks: table(
          ["Persona", "Role in buying", "Main concern", "What MOTM says", "Proof to show"],
          rows(s.personas).map((p) => [
            str(p.persona),
            str(p.role_in_buying),
            str(p.main_concern),
            str(p.what_motm_says),
            str(p.proof_to_show),
          ])
        ),
      },
      {
        id: "problem-solution",
        title: "Buyer problems and the customer's answers",
        blocks: table(
          ["What the buyer feels", "What the customer does"],
          rows(s.problem_solution).map((p) => [str(p.buyer_problem), str(p.customer_solution)])
        ),
      },
      {
        id: "demand-triggers",
        title: "Demand triggers",
        blocks: [
          ...columns([
            ["Visible demand", triggers.visible],
            ["Hidden or early demand", triggers.hidden_early],
          ]),
          ...lead(triggers.principle),
        ],
      },
      {
        id: "account-selection",
        title: "Account selection",
        blocks: table(
          ["Tier", "Account profile", "Illustrative examples", "Examples are"],
          rows(s.account_selection).map((a) => [
            str(a.tier),
            str(a.account_profile),
            joined(a.illustrative_examples),
            a.examples_are === "none" ? "" : label(a.examples_are),
          ])
        ),
      },
    ],
  };
}

function executionPart(e: Json): Part {
  const process = obj(e.process);
  const qualification = obj(e.qualification);
  const accountability = obj(e.accountability);
  const prerequisites = obj(e.prerequisites);

  const phases: TimelinePhase[] = rows(e.roadmap).map((phase) => ({
    title: str(phase.phase),
    subtitle: str(phase.months),
    activities: strings(phase.activities),
    milestones: rows(phase.milestones)
      .map((m) => ({
        text: str(m.milestone),
        value: formatRange(m.value_low, m.value_high, m.metric),
        basis: m.basis === "qualitative" ? "" : label(m.basis),
      }))
      .filter((m) => m.text),
  }));

  return {
    id: "part-3",
    title: "Part 3 · Execution",
    sections: [
      {
        id: "channels",
        title: "Channels",
        blocks: table(
          ["Channel", "Scope item", "Its job", "Audience"],
          rows(e.channels).map((c) => [str(c.channel), str(c.scope_item), str(c.job), str(c.audience)])
        ),
      },
      {
        id: "process",
        title: "Process",
        blocks: [
          ...columns([
            ["MOTM: generate and qualify", process.motm_steps],
            ["Customer: convert", process.customer_steps],
          ]),
          ...lead(process.handoff_rule),
        ],
      },
      {
        id: "qualification",
        title: "Qualification",
        blocks: [
          ...heading("Questions asked on every enquiry", bullets(qualification.questions, true)),
          ...pairs([
            ["Grade A", qualification.grade_a],
            ["Grade B", qualification.grade_b],
            ["Grade C", qualification.grade_c],
          ]),
        ],
      },
      {
        id: "conversion-support",
        title: "Conversion support",
        blocks: [
          ...table(
            ["Stage", "MOTM sets up", "Customer delivers"],
            rows(e.conversion_support).map((c) => [str(c.stage), str(c.motm_sets_up), str(c.customer_delivers)])
          ),
          ...heading("Answering price pressure", bullets(e.price_objection_playbook)),
        ],
      },
      {
        id: "roadmap",
        title: "Roadmap and milestones",
        blocks: [
          ...(phases.length ? [{ type: "timeline", phases } as Block] : []),
          ...text(e.roadmap_guardrail),
        ],
      },
      {
        id: "accountability",
        title: "Accountability",
        blocks: columns([
          ["MOTM", accountability.motm],
          ["Customer", accountability.customer],
        ]),
      },
      {
        id: "prerequisites",
        title: "Prerequisites",
        blocks: columns([
          ["Proof assets", prerequisites.proof_assets],
          ["Operational support", prerequisites.operational_support],
          ["Governance inputs", prerequisites.governance_inputs],
        ]),
      },
      { id: "next-steps", title: "Next steps", blocks: bullets(e.next_steps, true) },
    ],
  };
}

function internalPart(d: Json, e: Json, internal: PlanInternal): Part {
  const readiness = obj(d.readiness);
  return {
    id: "internal",
    title: "Internal (MOTM only)",
    internal: true,
    sections: [
      { id: "cam-notes", title: "CAM notes", blocks: bullets(internal.camNotes) },
      {
        id: "flags",
        title: "Flags from the documents",
        blocks: table(
          ["Type", "Field", "Detail", "Blocks targets"],
          rows(internal.flags).map((f) => [label(f.flag_type), str(f.field), str(f.detail), yesNo(f.blocks_targets)])
        ),
      },
      {
        id: "override",
        title: "Diagnosis gate",
        blocks: [
          ...pairs([["Override reason", internal.overrideReason]]),
          ...table(
            ["Blocker", "Question to resolve"],
            rows(readiness.blockers).map((b) => [str(b.blocker), str(b.question_to_resolve)])
          ),
        ],
      },
      {
        id: "conflicts",
        title: "Conflicts between documents",
        blocks: table(
          ["Field", "BD or proposal says", "Customer says", "Type", "Blocking", "Question"],
          rows(d.conflicts).map((c) => [
            str(c.field),
            str(c.bd_or_proposal_value),
            str(c.customer_value),
            label(c.conflict_type),
            yesNo(c.blocking),
            str(c.suggested_question),
          ])
        ),
      },
      {
        id: "data-gaps",
        title: "Data gaps",
        blocks: table(
          ["Item", "Why it matters", "Source"],
          rows(d.data_gaps).map((g) => [str(g.item), str(g.why_it_matters), str(g.source_ref)])
        ),
      },
      {
        id: "cam-notes-applied",
        title: "How CAM notes changed the plan",
        blocks: table(
          ["Note", "Effect"],
          rows(e.cam_notes_applied).map((n) => [str(n.note), str(n.effect)])
        ),
      },
      {
        id: "out-of-scope",
        title: "Out-of-scope recommendations",
        blocks: bullets(e.out_of_scope_recommendations),
      },
      { id: "open-research", title: "Public facts to verify", blocks: bullets(e.open_research) },
      {
        id: "agent-repairs",
        title: "Agent repairs",
        blocks: table(
          ["Agent", "Where", "Fix made"],
          internal.repairs.map((r) => [label(r.agent), r.path, r.fix])
        ),
      },
    ],
  };
}

export function buildPlanDocument(input: {
  customerName: string;
  diagnosis: unknown;
  strategy: unknown;
  execution: unknown;
  /** Omit to build the customer-facing document with no internal part at all. */
  internal?: PlanInternal;
}): PlanDocument {
  const d = obj(input.diagnosis);
  const s = obj(input.strategy);
  const e = obj(input.execution);

  const parts = [diagnosisPart(d), strategyPart(s), executionPart(e)];
  if (input.internal) parts.push(internalPart(d, e, input.internal));

  return {
    customerName: input.customerName,
    title: str(s.title) || `Go-to-market plan for ${input.customerName}`,
    subtitle: str(s.engagement_period),
    parts: parts
      .map((part) => ({ ...part, sections: part.sections.filter((section) => section.blocks.length) }))
      .filter((part) => part.sections.length),
  };
}
