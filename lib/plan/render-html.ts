// Renders the plan document to one self-contained HTML file (inline CSS, no scripts).
// Every piece of agent text passes through esc(); nothing is interpolated raw.

import type { Block, PlanDocument } from "./document";

export function esc(value: unknown): string {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

const CSS = `
*{box-sizing:border-box}
body{margin:0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;font-size:14px;line-height:1.55;color:#18181b;background:#fff}
.wrap{max-width:1000px;margin:0 auto;padding:40px 24px 80px}
header.doc{border-bottom:2px solid #18181b;padding-bottom:16px;margin-bottom:24px}
header.doc h1{font-size:28px;line-height:1.2;margin:0 0 6px}
header.doc p{margin:0;color:#52525b}
nav.toc{border:1px solid #e4e4e7;border-radius:10px;padding:16px 20px;margin-bottom:32px}
nav.toc ol{margin:4px 0 12px;padding-left:20px}
nav.toc .part{font-weight:600;margin:0}
nav.toc a{color:#18181b;text-decoration:none}
nav.toc a:hover{text-decoration:underline}
h2.part{font-size:22px;margin:48px 0 8px;padding-bottom:8px;border-bottom:1px solid #d4d4d8}
section{margin:28px 0}
h3{font-size:17px;margin:0 0 10px}
h4{font-size:13px;text-transform:uppercase;letter-spacing:.04em;color:#52525b;margin:18px 0 6px}
p{margin:0 0 10px}
p.lead{font-size:16px;font-weight:500;border-left:3px solid #18181b;padding:4px 0 4px 12px}
ul,ol{margin:0 0 10px;padding-left:22px}
li{margin:3px 0}
table{width:100%;border-collapse:collapse;margin:0 0 12px;font-size:13px}
th,td{border:1px solid #e4e4e7;padding:7px 9px;text-align:left;vertical-align:top}
th{background:#f4f4f5;font-weight:600}
dl{display:grid;grid-template-columns:minmax(140px,220px) 1fr;gap:6px 16px;margin:0 0 12px}
dt{font-weight:600;color:#3f3f46}
dd{margin:0}
.kpis{display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));gap:12px;margin:0 0 12px}
.kpi{border:1px solid #e4e4e7;border-radius:10px;padding:12px 14px}
.kpi .l{font-size:12px;color:#52525b}
.kpi .v{font-size:18px;font-weight:600;margin:2px 0}
.kpi .n{font-size:11px;color:#71717a}
.cols{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;margin:0 0 12px}
.col{border:1px solid #e4e4e7;border-radius:10px;padding:12px 14px}
.col h4{margin-top:0}
.col ul{margin-bottom:0}
.phase{border-left:2px solid #18181b;padding:0 0 14px 16px;margin-left:6px;position:relative}
.phase:before{content:"";position:absolute;left:-7px;top:4px;width:12px;height:12px;border-radius:50%;background:#18181b}
.phase .t{font-weight:600;font-size:15px}
.phase .s{color:#52525b;font-size:13px;margin-bottom:6px}
.ms{list-style:none;padding:0}
.ms li{border:1px solid #e4e4e7;border-radius:8px;padding:6px 10px;margin:6px 0}
.ms .val{font-weight:600}
.ms .basis{color:#71717a;font-size:12px}
.internal{background:#fffbeb;border:1px solid #fcd34d;border-radius:10px;padding:4px 20px 12px;margin-top:48px}
footer{margin-top:48px;padding-top:12px;border-top:1px solid #e4e4e7;color:#71717a;font-size:12px}
@media print{.wrap{padding:0;max-width:none}nav.toc{display:none}section,tr,.kpi,.col,.phase{break-inside:avoid}h2.part{break-before:page}h2.part:first-of-type{break-before:auto}}
`;

const items = (values: string[]) => values.map((v) => `<li>${esc(v)}</li>`).join("");

function block(b: Block): string {
  switch (b.type) {
    case "text":
      return `<p>${esc(b.text)}</p>`;
    case "lead":
      return `<p class="lead">${esc(b.text)}</p>`;
    case "heading":
      return `<h4>${esc(b.text)}</h4>`;
    case "list":
      return b.ordered ? `<ol>${items(b.items)}</ol>` : `<ul>${items(b.items)}</ul>`;
    case "pairs":
      return `<dl>${b.items.map((p) => `<dt>${esc(p.label)}</dt><dd>${esc(p.value)}</dd>`).join("")}</dl>`;
    case "kpis":
      return `<div class="kpis">${b.items
        .map(
          (k) =>
            `<div class="kpi"><div class="l">${esc(k.label)}</div><div class="v">${esc(k.value)}</div>${
              k.note ? `<div class="n">${esc(k.note)}</div>` : ""
            }</div>`
        )
        .join("")}</div>`;
    case "table":
      return `<table><thead><tr>${b.columns.map((c) => `<th>${esc(c)}</th>`).join("")}</tr></thead><tbody>${b.rows
        .map((row) => `<tr>${row.map((cell) => `<td>${esc(cell)}</td>`).join("")}</tr>`)
        .join("")}</tbody></table>`;
    case "columns":
      return `<div class="cols">${b.columns
        .map((c) => `<div class="col"><h4>${esc(c.title)}</h4><ul>${items(c.items)}</ul></div>`)
        .join("")}</div>`;
    case "timeline":
      return b.phases
        .map(
          (phase) =>
            `<div class="phase"><div class="t">${esc(phase.title)}</div><div class="s">${esc(phase.subtitle)}</div>${
              phase.activities.length ? `<ul>${items(phase.activities)}</ul>` : ""
            }${
              phase.milestones.length
                ? `<ul class="ms">${phase.milestones
                    .map(
                      (m) =>
                        `<li>${esc(m.text)}${m.value ? ` — <span class="val">${esc(m.value)}</span>` : ""}${
                          m.basis ? ` <span class="basis">(${esc(m.basis)})</span>` : ""
                        }</li>`
                    )
                    .join("")}</ul>`
                : ""
            }</div>`
        )
        .join("");
  }
}

export function renderPlanHtml(doc: PlanDocument, footer: string): string {
  const toc = doc.parts
    .map(
      (part) =>
        `<p class="part"><a href="#${esc(part.id)}">${esc(part.title)}</a></p><ol>${part.sections
          .map((s) => `<li><a href="#${esc(part.id)}-${esc(s.id)}">${esc(s.title)}</a></li>`)
          .join("")}</ol>`
    )
    .join("");

  const body = doc.parts
    .map((part) => {
      const sections = part.sections
        .map(
          (s) =>
            `<section id="${esc(part.id)}-${esc(s.id)}"><h3>${esc(s.title)}</h3>${s.blocks.map(block).join("")}</section>`
        )
        .join("");
      const content = `<h2 class="part" id="${esc(part.id)}">${esc(part.title)}</h2>${sections}`;
      return part.internal ? `<div class="internal">${content}</div>` : content;
    })
    .join("");

  return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${esc(doc.title)}</title>
<style>${CSS}</style>
</head>
<body>
<div class="wrap">
<header class="doc"><h1>${esc(doc.title)}</h1><p>${esc([doc.customerName, doc.subtitle].filter(Boolean).join(" · "))}</p></header>
<nav class="toc">${toc}</nav>
${body}
<footer>${esc(footer)}</footer>
</div>
</body>
</html>
`;
}
