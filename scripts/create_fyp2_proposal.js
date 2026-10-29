import fs from "node:fs";
import path from "node:path";
import {
  AlignmentType,
  Document,
  Footer,
  Header,
  HeadingLevel,
  ImportedXmlComponent,
  Packer,
  PageNumber,
  Paragraph,
  ShadingType,
  Table,
  TableCell,
  TableRow,
  TextRun,
  WidthType,
  convertInchesToTwip,
} from "docx";

const outputPath = process.argv[2];
if (!outputPath) throw new Error("Usage: node create.js /abs/output.docx");

const T = String.raw;
const palette = { dark: "1F323E", primary: "37474F", light: "78909C", border: "D8E0E3", fill: "EEF3F6", accent: "2E6B5E" };

const font = { ascii: "Times New Roman", hAnsi: "Times New Roman", cs: "Times New Roman", eastAsia: "SimSun" };
const run = (text, options = {}) => new TextRun({ text, font, size: 24, ...options });
const para = (children, options = {}) =>
  new Paragraph({ spacing: { after: 160, line: 300 }, ...options, children: Array.isArray(children) ? children : [children] });
const bodyPara = (text, options = {}) =>
  para(run(text), { indent: { firstLine: convertInchesToTwip(0.33) }, ...options });
const bullet = (text) =>
  para(run(text), { bullet: { level: 0 }, spacing: { after: 80, line: 300 } });
const h1 = (text) =>
  para(run(text, { bold: true, size: 30, color: palette.dark }), { heading: HeadingLevel.HEADING_1, spacing: { before: 280, after: 140 } });
const h2 = (text) =>
  para(run(text, { bold: true, size: 26, color: palette.primary }), { heading: HeadingLevel.HEADING_2, spacing: { before: 220, after: 120 } });

const cell = (text, options = {}) =>
  new TableCell({
    children: [para(run(text, { size: options.fontSize || 20 }), { spacing: { after: 40, line: 260 } })],
    margins: { top: 100, bottom: 100, left: 100, right: 100 },
    ...options,
  });

const xmlEscape = (v) => String(v).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;");
const toc = (entries) => {
  const cached = entries.map(({ title, level, page }) => {
    const indent = Math.max(0, level - 1) * 360;
    return `<w:p><w:pPr><w:pStyle w:val="TOC${level}"/><w:tabs><w:tab w:val="right" w:leader="dot" w:pos="9000"/></w:tabs><w:ind w:left="${indent}"/></w:pPr><w:r><w:t>${xmlEscape(title)}</w:t></w:r><w:r><w:tab/></w:r><w:r><w:t>${page}</w:t></w:r></w:p>`;
  }).join("");
  return ImportedXmlComponent.fromXmlString(`<w:sdt xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
    <w:sdtPr><w:alias w:val="Table of Contents"/></w:sdtPr>
    <w:sdtContent>
      <w:p><w:r><w:fldChar w:fldCharType="begin" w:dirty="true"/><w:instrText xml:space="preserve"> TOC \\o &quot;1-3&quot; \\h \\z \\u </w:instrText><w:fldChar w:fldCharType="separate"/></w:r></w:p>
      ${cached}
      <w:p><w:r><w:fldChar w:fldCharType="end"/></w:r></w:p>
    </w:sdtContent>
  </w:sdt>`).root[0];
};

// ---------- Tables ----------

const fyp1ResultsTable = () => {
  const widths = [4600, 1600, 1600, 1600];
  const hd = (t) => cell(t, { shading: { type: ShadingType.CLEAR, fill: palette.fill }, width: { size: widths[0], type: WidthType.DXA } });
  const rows = [
    ["Pilot respondents successfully interacted with the bot", "95.3%", "-", "-"],
    [T`"I often skip reading full local news articles because they take too much time" (agree/strongly agree)`, "69.8%", "-", "-"],
    [T`"I sometimes miss urgent community updates because local news is scattered" (agree/strongly agree)`, "73.3%", "-", "-"],
    [T`"AI summaries were easy to read and gave core information quickly"`, "88.4%", "4.22", "0.92"],
    [T`"Settings feature was useful for customising news to my location/interests"`, "83.7%", "4.24", "0.86"],
    [T`"Navigating the bot using menu buttons was simple and straightforward"`, "77.9%", "4.17", "1.05"],
    [T`"I would use this bot in my daily life to stay updated on Sarawak news"`, "82.6%", "4.19", "0.86"],
  ];
  return new Table({
    width: { size: 100, type: WidthType.PERCENTAGE },
    columnWidths: widths,
    rows: [
      new TableRow({ children: [hd("Survey Item (n = 86)"), hd("Agreement (4-5)"), hd("Mean"), hd("SD")] }),
      ...rows.map((r) => new TableRow({ children: r.map((t) => cell(t)) })),
    ],
  });
};

const wpTable = () => {
  const widths = [700, 3000, 3400, 3100];
  const hd = (t) => cell(t, { shading: { type: ShadingType.CLEAR, fill: palette.fill }, width: { size: widths[0], type: WidthType.DXA } });
  const rows = [
    ["WP1", "Upgrade RAG question-answering", "Replace in-memory retrieval with a persistent vector store (pgvector on existing Supabase PostgreSQL), add article chunking and query pre-processing; expand candidate pool safely.", "Q&A accuracy on a fixed 20-question benchmark set; target >= 80% correct grounded answers"],
    ["WP2", "Semantic story deduplication", "Add an embedding-similarity clustering layer on top of the existing Jaccard token dedup so same-event stories from different publishers merge even with different wording.", "Duplicate-story rate per digest measured on 2 weeks of production data; target >= 50% reduction vs. FYP1 baseline"],
    ["WP3", "Official utility alert sources", "Integrate primary-source disruption notices (Sarawak Energy / Sarawak Water official channels via platform APIs or approved feeds) for real-time utility alerts.", "Alert latency from source publication to bot push; target <= 15 minutes for covered providers"],
    ["WP4", "Multilingual summaries", "User-selectable summary language (English / Bahasa Melayu / Mandarin) driven by a new user preference; prompts and fallback text localised.", "Language preference available in /settings; TAM usefulness score parity (+-0.2) across languages in final survey"],
    ["WP5", "Automated testing and CI", "Introduce a pytest suite (scrapers, summarizer parsing, dedup, preference logic) and GitHub Actions CI on every push.", "Critical modules covered by unit tests; CI green on main branch; regression bugs caught before deploy"],
    ["WP6", "Summary quality evaluation harness", "Systematic scoring of AI summaries on relevance, coherence, factual consistency and compression (framework adopted in FYP1 Chapter 2) over a 50-article sample with human spot-checks.", "Documented scores per metric; factual consistency >= 90% on human spot-check sample"],
    ["WP7", "Full-scale user evaluation", "Deploy stabilised system and run the main TAM survey with 150 respondents after a guided usage period.", "150 valid responses; overall usefulness/ease-of-use means >= 4.0 on 5-point Likert scale"],
  ];
  return new Table({
    width: { size: 100, type: WidthType.PERCENTAGE },
    columnWidths: widths,
    rows: [
      new TableRow({ children: [hd("WP"), hd("Work Package"), hd("Description"), hd("Measurable Success Criterion")] }),
      ...rows.map((r) => new TableRow({ children: r.map((t) => cell(t)) })),
    ],
  });
};

const ganttTable = () => {
  const activities = [
    ["Requirements finalisation & supervisor approval", [1, 2]],
    ["WP1 RAG upgrade (vector store + chunking)", [2, 3, 4, 5]],
    ["WP2 Semantic deduplication", [4, 5, 6]],
    ["WP3 Utility alert source integration", [5, 6, 7, 8]],
    ["WP4 Multilingual summaries", [6, 7, 8, 9]],
    ["WP5 Test suite + CI setup", [3, 4, 9, 10]],
    ["WP6 Summary quality evaluation", [9, 10, 11]],
    ["WP7 Prototype testing (10 testers)", [10, 11]],
    ["WP7 Main survey (150 respondents)", [11, 12, 13]],
    ["Data analysis & thesis writing", [8, 9, 10, 11, 12, 13, 14]],
    ["Final presentation preparation", [13, 14]],
  ];
  const weekNums = Array.from({ length: 14 }, (_, i) => i + 1);
  const widths = [3400, ...weekNums.map(() => 400)];
  const hd = (t) => cell(t, { shading: { type: ShadingType.CLEAR, fill: palette.fill }, fontSize: 16, width: { size: widths[0], type: WidthType.DXA } });
  const headerRow = new TableRow({ children: [hd("Activity"), ...weekNums.map((w) => hd(`W${w}`))] });
  const dataRows = activities.map(([name, weeks]) =>
    new TableRow({
      children: [
        cell(name, { fontSize: 16, width: { size: widths[0], type: WidthType.DXA } }),
        ...weekNums.map((w) =>
          cell(weeks.includes(w) ? "●" : "", {
            fontSize: 16,
            shading: weeks.includes(w) ? { type: ShadingType.CLEAR, fill: "CFE0DA" } : undefined,
          }),
        ),
      ],
    }),
  );
  return new Table({
    width: { size: 100, type: WidthType.PERCENTAGE },
    columnWidths: widths,
    rows: [headerRow, ...dataRows],
  });
};

const riskTable = () => {
  const widths = [3200, 1300, 1300, 5000];
  const hd = (t) => cell(t, { shading: { type: ShadingType.CLEAR, fill: palette.fill }, width: { size: widths[0], type: WidthType.DXA } });
  const rows = [
    ["Official utility APIs restricted or approval delayed", "Medium", "High", "Fall back to approved Telegram channel ingestion (already in production) and public RSS; document API access attempts as part of the research narrative"],
    ["Vector store migration degrades answer quality", "Low", "Medium", "Keep the existing in-memory retriever as a runtime fallback; A/B both retrievers on the 20-question benchmark before cut-over"],
    ["Survey cannot reach 150 respondents", "Medium", "High", "Start recruitment early (week 8), reuse the 86 pilot respondents' opt-in contacts, distribute via university channels and community groups"],
    ["Cloud SLM quota limits during evaluation", "Medium", "Medium", "Multi-endpoint fallback chain (local Ollama, Ollama Cloud, secondary provider) already implemented and verified in production"],
    ["Multilingual summary quality below English baseline", "Medium", "Low", "Evaluate per-language samples in WP6 before enabling in production; ship languages incrementally (BM first)"],
    ["Semester timeline slippage", "Medium", "Medium", "Waterfall phase gates with weekly supervisor check-ins; WP ordering ensures core evaluation (WP6/WP7) is protected by early integration deadlines"],
  ];
  return new Table({
    width: { size: 100, type: WidthType.PERCENTAGE },
    columnWidths: widths,
    rows: [
      new TableRow({ children: [hd("Risk"), hd("Likelihood"), hd("Impact"), hd("Mitigation")] }),
      ...rows.map((r) => new TableRow({ children: r.map((t) => cell(t)) })),
    ],
  });
};

// ---------- Document content ----------

const tocEntries = [
  { title: "1  Introduction and FYP1 Recap", level: 1, page: 3 },
  { title: "1.1  Project Background", level: 2, page: 3 },
  { title: "1.2  FYP1 Achievements", level: 2, page: 3 },
  { title: "2  Problem Analysis: Limitations and Production Findings", level: 1, page: 4 },
  { title: "2.1  Limitations Identified in the FYP1 Report", level: 2, page: 4 },
  { title: "2.2  Additional Findings from Production Operation", level: 2, page: 5 },
  { title: "3  FYP2 Objectives", level: 1, page: 6 },
  { title: "4  Scope of Work", level: 1, page: 7 },
  { title: "4.1  Work Packages", level: 2, page: 7 },
  { title: "4.2  Out of Scope", level: 2, page: 8 },
  { title: "5  Technical Approach", level: 1, page: 9 },
  { title: "6  Evaluation Plan", level: 1, page: 11 },
  { title: "7  Project Timeline", level: 1, page: 12 },
  { title: "8  Risk Analysis and Mitigation", level: 1, page: 13 },
  { title: "9  Security, Privacy and Ethics", level: 1, page: 14 },
  { title: "10  Expected Outcomes and Deliverables", level: 1, page: 14 },
];

const children = [
  // Cover
  para(run("FYP2 PROJECT PROPOSAL", { bold: true, size: 28, color: palette.light }), { alignment: AlignmentType.CENTER, spacing: { before: 2400, after: 200 } }),
  para(run("AI Local News Summarization and Alert Bot on Telegram", { bold: true, size: 40, color: palette.dark }), { heading: HeadingLevel.TITLE, alignment: AlignmentType.CENTER, spacing: { after: 120 } }),
  para(run("Enhancement, Hardening and Full-Scale Evaluation", { size: 26, color: palette.primary }), { alignment: AlignmentType.CENTER, spacing: { after: 1200 } }),
  para(run("Vincent Lee Cheng Hong  (BCS24020018)", { size: 24 }), { alignment: AlignmentType.CENTER, spacing: { after: 120 } }),
  para(run("Supervisor: Ms. Khairunnisa Ibrahim", { size: 24 }), { alignment: AlignmentType.CENTER, spacing: { after: 120 } }),
  para(run("Bachelor of Computer Science (Hons)", { size: 24 }), { alignment: AlignmentType.CENTER, spacing: { after: 120 } }),
  para(run("School of Computing and Creative Media, University of Technology Sarawak", { size: 24 }), { alignment: AlignmentType.CENTER, spacing: { after: 120 } }),
  para(run("Academic Session 2025/2026", { size: 24 }), { alignment: AlignmentType.CENTER, spacing: { after: 1600 } }),
  para(run("DRAFT for supervisor discussion - work packages and timeline open to adjustment", { italics: true, size: 20, color: palette.light }), { alignment: AlignmentType.CENTER, pageBreakBefore: false }),

  // TOC
  para(run("Table of Contents", { bold: true, size: 30, color: palette.dark }), { heading: HeadingLevel.HEADING_1, pageBreakBefore: true, spacing: { before: 240, after: 120 } }),
  para(run("Right-click the list and choose Update Field to refresh page numbers.", { italics: true, color: palette.light, size: 20 })),
  toc(tocEntries),

  // 1
  h1("1  Introduction and FYP1 Recap"),
  h2("1.1  Project Background"),
  bodyPara(T`This project develops "Apa News Hari Tok?", an intelligent Telegram bot that aggregates fragmented Sarawak local news from RSS feeds and approved Telegram channels, generates concise AI summaries using small language models (SLMs), and delivers personalised scheduled digests and urgent alerts to residents. The system supports SDG 11 (Sustainable Cities and Communities) by improving community awareness of safety, infrastructure and utility disruptions.`),
  bodyPara(T`FYP1 established the complete system architecture (data extraction, AI summarisation, delivery interface), deployed the bot to a 24/7 cloud environment (Fly.io with a Supabase PostgreSQL database), and validated the problem statement and early prototype through a pilot study.`),
  h2("1.2  FYP1 Achievements"),
  bodyPara(T`All three FYP1 objectives were met: (1) the automated aggregation backend was designed and implemented; (2) SLM-based 30-word summarisation was demonstrated in production; (3) the Telegram delivery experience was evaluated with 86 pilot respondents. Key pilot results are reproduced below.`),
  fyp1ResultsTable(),
  bodyPara(T`The pilot confirmed strong problem validation and high user acceptance, providing a solid baseline for full-scale development and evaluation in FYP2.`),

  // 2
  h1("2  Problem Analysis: Limitations and Production Findings"),
  h2("2.1  Limitations Identified in the FYP1 Report"),
  bodyPara(T`The FYP1 report documented six limitations that directly motivate the FYP2 scope:`),
  bullet(T`L1 - Sample size: evaluation limited to a convenience sample of 86 respondents, restricting statistical generalisability.`),
  bullet(T`L2 - Infrastructure uptime: the original fully-local architecture (laptop-hosted LLM and database) could not provide 24/7 availability; a cloud migration (Fly.io, Supabase, cloud-hosted SLMs) was adopted as the production architecture.`),
  bullet(T`L3 - RAG constraints: the in-memory retrieval (nomic-embed-text embeddings, SHA1 cache, cosine similarity over a small candidate pool) frequently fails to capture enough context to answer complex questions accurately.`),
  bullet(T`L4 - Deduplication inconsistencies: token/Jaccard thresholds miss same-event stories when publishers use different vocabulary, so users occasionally receive repeated news.`),
  bullet(T`L5 - Data source constraints: real-time utility disruption notices (Sarawak Energy, Sarawak Water) live on closed social platforms; no official API integration existed within the FYP1 timeline.`),
  bullet(T`L6 - Prompt engineering: the SLM did not consistently respect the 30-word summary limit, causing inconsistent brief lengths.`),
  h2("2.2  Additional Findings from Production Operation"),
  bodyPara(T`Continued operation of the deployed bot between FYP1 and FYP2 surfaced further engineering findings that strengthen the case for the FYP2 work packages:`),
  bullet(T`F1 - Reasoning-model token budget failure: after switching the cloud fallback to a reasoning SLM (gpt-oss), roughly 70% of articles silently received no AI summary because hidden reasoning tokens exhausted the generation budget. Root-caused and fixed (larger token budget plus explicit thinking control); summary generation now succeeds consistently. This directly explains much of L6 and motivates WP5 (regression testing) and WP6 (quality evaluation).`),
  bullet(T`F2 - No automated test coverage: the FYP1 codebase has no unit test suite; all verification was manual, allowing failures such as F1 to persist undetected for days.`),
  bullet(T`F3 - Secrets management incident: environment secrets were accidentally committed to the public repository. Fully remediated through history purging, migration to an encrypted secret store, and complete credential rotation with zero downtime; documented as a security hardening case study for the FYP2 report.`),
  bullet(T`F4 - Unaddressed user feedback: pilot respondents explicitly requested Bahasa Melayu and Mandarin summaries and simpler settings; this feedback was not yet reflected in the feature set.`),
  bullet(T`F5 - RAG answers are currently unvalidated: there is no benchmark or metric tracking answer accuracy, making L3 impossible to quantify or verify when fixed.`),

  // 3
  h1("3  FYP2 Objectives"),
  bodyPara(T`The main aim of FYP2 is to evolve the validated prototype into a hardened, measurable, full-scale system and to complete the comprehensive evaluation required by the project methodology. The specific objectives are:`),
  bullet(T`O1 - Upgrade the question-answering architecture with a persistent vector database and improved retrieval so the bot can accurately answer user questions about stored local news (addresses L3, F5).`),
  bullet(T`O2 - Reduce duplicate story delivery through semantic, embedding-based deduplication layered on the existing token-based approach (addresses L4).`),
  bullet(T`O3 - Integrate primary-source utility disruption channels to shorten the latency of water and electricity alerts (addresses L5).`),
  bullet(T`O4 - Deliver multilingual summaries (English, Bahasa Melayu, Mandarin) in response to pilot user feedback (addresses F4).`),
  bullet(T`O5 - Establish engineering quality gates: an automated test suite with continuous integration, and a repeatable summary-quality evaluation harness measuring relevance, coherence, factual consistency and compression (addresses L6, F1, F2).`),
  bullet(T`O6 - Complete the full-scale TAM evaluation with 150 respondents and statistically analyse system acceptance (addresses L1).`),

  // 4
  h1("4  Scope of Work"),
  h2("4.1  Work Packages"),
  bodyPara(T`FYP2 is organised into seven work packages (WPs). Each WP maps to a specific system module and carries a measurable success criterion so that completion can be demonstrated objectively in the final report.`),
  wpTable(),
  h2("4.2  Out of Scope"),
  bullet(T`No standalone mobile application or web dashboard; the user interface remains Telegram-only, consistent with the FYP1 scope.`),
  bullet(T`No formal collaboration with government agencies or news organisations; all data sources are public or officially published feeds.`),
  bullet(T`Nationwide or international news coverage; the system remains focused on Sarawak local news.`),
  bullet(T`Paid large-scale cloud LLM APIs; the privacy- and cost-conscious SLM architecture is retained.`),

  // 5
  h1("5  Technical Approach"),
  h2("5.1  WP1 - RAG Upgrade (Vector Store and Chunking)"),
  bodyPara(T`The current retriever keeps embeddings in process memory (lost on restart, candidate pool limited by RAM and latency). FYP2 migrates embeddings to pgvector inside the existing Supabase PostgreSQL instance, avoiding new infrastructure. Articles will be chunked (title/summary/body segments), embedded once at ingestion time, and indexed persistently. Retrieval combines metadata filtering (recency, category, location) with vector similarity, enlarging the effective candidate pool while grounding answers in stored news only. The existing in-memory retriever remains as a runtime fallback, and both retrievers are compared on a fixed 20-question benchmark before cut-over.`),
  h2("5.2  WP2 - Semantic Deduplication"),
  bodyPara(T`The existing two-layer dedup (per-user delivery history plus Jaccard token clustering) fails when publishers phrase the same event differently. FYP2 adds a third, semantic layer: story embeddings (reusing the WP1 vector infrastructure) are compared by cosine similarity so paraphrased duplicates cluster together. Thresholds will be tuned on production data and the duplicate rate per digest measured before and after.`),
  h2("5.3  WP3 - Official Utility Alert Sources"),
  bodyPara(T`The bot currently relies on news portals and the Sarawak Water Board Telegram channel for disruption notices. FYP2 investigates official publication APIs and approved feeds from Sarawak Energy and Sarawak Water (and retains the proven Telegram ingestion as fallback). The goal is to reduce end-to-end latency from publication to push for covered providers, measured as the time difference between source timestamp and bot delivery.`),
  h2("5.4  WP4 - Multilingual Summaries"),
  bodyPara(T`A language preference will be added to user settings (English, Bahasa Melayu, Mandarin). Summarisation prompts instruct the SLM to output in the chosen language, with localised fallback text when generation fails. Bahasa Melayu ships first (largest respondent demand), Mandarin second, each gated by a WP6 quality spot-check before release.`),
  h2("5.5  WP5 - Automated Testing and CI"),
  bodyPara(T`A pytest suite will cover the failure-prone logic identified in production: summarizer output parsing (JSON mode, truncation, reasoning models), dedup clustering, preference/location filtering, and delivery eligibility. GitHub Actions runs the suite on every push so regressions are caught before deployment, directly addressing the class of silent failure found in F1.`),
  h2("5.6  WP6 - Summary Quality Evaluation Harness"),
  bodyPara(T`Following the evaluation framework adopted in FYP1 (relevance, coherence, factual consistency, summary length/compression), a repeatable harness samples 50 production articles, scores the generated summaries, and combines automated checks with a human spot-check rubric. The harness produces the quantitative summary-quality results chapter of the final thesis and is re-run after each model or prompt change.`),

  // 6
  h1("6  Evaluation Plan"),
  bodyPara(T`The evaluation retains the FYP1 quantitative design under the Technology Acceptance Model (TAM), executed in two phases after the system stabilises:`),
  bullet(T`Phase 1 - Prototype testing (10 testers): guided functional verification of the upgraded features (WP1-WP4), response times, and summary formatting, with issue triage before the main survey.`),
  bullet(T`Phase 2 - Main survey (150 respondents): the stabilised bot is deployed to the target population (young adults, students, busy professionals in Sarawak) for a defined usage period, followed by the structured Google Forms questionnaire (5-point Likert TAM items plus open-ended feedback).`),
  bodyPara(T`Analysis uses descriptive statistics (mean, median, mode, standard deviation) consistent with the FYP1 pilot, enabling direct comparison between the 86-respondent baseline and the 150-respondent final evaluation. Summary quality is additionally reported through the WP6 harness metrics, giving the thesis both user-acceptance and output-quality evidence.`),
  bodyPara(T`Success thresholds: overall usefulness and ease-of-use means of at least 4.0/5 at n = 150; factual consistency of at least 90% on the human spot-check sample; Q&A benchmark accuracy of at least 80% for the upgraded retriever.`),

  // 7
  h1("7  Project Timeline"),
  bodyPara(T`The plan follows the Waterfall model adopted in FYP1 across a 14-week semester. Work packages overlap where dependencies allow, but each has a defined completion gate. The timeline will be refined with the supervisor and converted into the formal FYP2 Gantt chart.`),
  ganttTable(),

  // 8
  h1("8  Risk Analysis and Mitigation"),
  riskTable(),

  // 9
  h1("9  Security, Privacy and Ethics"),
  bodyPara(T`The system stores only Telegram user IDs and news preferences; no personal content is shared with third parties, and AI processing uses self-managed SLM endpoints rather than commercial data-harvesting APIs. During the inter-semester period, a secrets-exposure incident was detected and fully remediated: credentials were rotated, secrets moved into an encrypted secret store, and repository history purged. FYP2 formalises these practices (environment-based configuration, secret scanning awareness, least-privilege API keys) as standard operating procedure, and the incident will be documented as a security case study in the final report. Survey participation remains voluntary and anonymous, consistent with the FYP1 pilot.`),

  // 10
  h1("10  Expected Outcomes and Deliverables"),
  bullet(T`A hardened production bot with persistent vector-based Q&A, semantic deduplication, faster utility alerts, and multilingual summaries.`),
  bullet(T`An automated test suite with CI and a repeatable summary-quality evaluation harness.`),
  bullet(T`Quantitative evaluation results: WP6 summary-quality metrics and the 150-respondent TAM survey with statistical analysis.`),
  bullet(T`The complete FYP2 thesis documenting design, implementation, security hardening, evaluation results, and comparison against the FYP1 baseline.`),
  bodyPara(T`Together these deliverables complete the project objectives and demonstrate a measurable improvement over the FYP1 prototype in reliability, answer quality, alert latency, and validated user acceptance.`),
];

const doc = new Document({
  features: { updateFields: true },
  sections: [
    {
      properties: { page: { margin: { top: 1440, bottom: 1440, left: 1440, right: 1440 } } },
      headers: {
        default: new Header({
          children: [para(run("FYP2 Proposal - AI Local News Summarization and Alert Bot on Telegram", { size: 18, color: palette.primary }), { alignment: AlignmentType.CENTER })],
        }),
      },
      footers: {
        default: new Footer({
          children: [para(new TextRun({ children: [PageNumber.CURRENT] }), { alignment: AlignmentType.CENTER })],
        }),
      },
      children,
    },
  ],
});

fs.writeFileSync(outputPath, await Packer.toBuffer(doc));
