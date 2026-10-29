# Spec 05: Research - our data first, then the web

The team spends time searching the web for the right technical names: what a
category is properly called, what a supplier of it should be asked, which
licence or standard applies in the country, what a document is officially
called. The tool should do that research and show its sources.

Agreed 2026-10-29: all four kinds below; our own data first, the web only
when it has no answer; Gemini with Google Search; nothing tuned to one
client - every rule and example must hold for any client.

## Principles

- **Bank first, web second.** Every question is first answered from what we
  already hold: past categories of every client, past questions, the
  glossary, past eligibility and technical documents. Only what the bank
  cannot answer goes to a web search.
- **Suggestions with sources, never applied on their own.** Each finding
  carries where it came from: a past sheet ("asked in 4 past questionnaires
  on similar categories") or web pages (title and link). The team decides.
- **Researched once.** Findings are kept as a learned table (`research`,
  behind the same storage driver as the glossary), keyed by topic and
  country, not by client, so one client's research serves all. The team can
  confirm or correct a finding; a confirmed one is never researched again.
- **Client-neutral.** Prompts and rules use no client's names, choices or
  examples. What one team decided for one client is evidence in the bank,
  not a rule.
- **Private.** Only a category's wording, a question's wording and the
  country are sent, anonymised and logged like every call.

## The four kinds

**K1. Category names** (refine). For each draft category:
- the standard technical / trade name used in procurement, and a cleaner
  description;
- what it normally includes, and how such a category is usually split
  between kinds of supplier (e.g. building works by trade), as candidate
  sub-categories or separations.
Bank first: the closest past categories of any client, with their names.
Shown in the Revised tab as *Technical name (suggested)*, *Usually split
as* and *Sources*; the team accepts with the existing Decision column.

**K2. Technical questions** (generate). For a category, the technical
specification questions a supplier of it should answer. Bank first: the
existing suggestions from past sheets on similar categories. When the bank
has none (today's N- line: "no past sheet on this topic"), the web research
proposes questions, drafted as NEW with 0 marks and their sources noted on
the row, for the QA report's review.

**K3. Licences and standards.** For a category and country: the regulator,
licence, registration or certification a supplier must or usually holds
(e.g. a medicines regulator's wholesale licence, an energy regulator's
installer licence, ISO standards). Bank first: documents past sheets ask
for on similar categories. Feeds refine's *Suggested eligibility (not
applied)* and generation's QA suggestions, each with sources; never made
mandatory without the team.

**K4. Document names.** For a document the glossary has no name for, or an
abbreviation the team does not recognise (e.g. "CR12"): its official name,
what it is, and a Short Name proposal (the Acronym still follows the letter
rule). Bank first: the glossary and past sheets.

## How it runs

- **Country.** Research needs the client's country: `--country` (ISO code,
  e.g. ET, KE), defaulting to the country in the sheet code prefix
  (ERCS-ET-2026- -> ET) where there is one.
- **Command.** `tendersure research <draft list or categories
  table> --client X --country ET [--kinds names,questions,licences]` fills
  the research table and writes a review workbook of the new findings.
  `refine` and `generate` read the table; with a model configured they
  research what is missing, bank first, so the separate command is
  optional.
- **Search.** Gemini with Google Search grounding returns the pages it used;
  their titles and links become the finding's sources. Grounded requests
  ask for JSON in the prompt (the provider does not combine search with a
  response schema); replies are schema-checked in code as always. A finding
  with no source is dropped.
- **Cost.** A few categories per grounded call, each finding tied to its own
  sources; nothing already in the table is asked again.

## Acceptance

- A draft category close to a past category of any client gets that name
  and source with no web call (calls counted).
- On the Red Cross draft: "Supply of Aqua Water Purification Chemicals" gets
  a technical name with sources; a general works category shows how it is
  usually split by trade, with sources; a pharmaceutical category in
  Ethiopia gets its licensing body with sources.
- Every finding shown has at least one source; none is applied without the
  team.
- No prompt or rule names a client; the same code runs for an Ethiopian and
  a Kenyan client, differing only by country.
- A second run on the same list makes no web call.

## Amended 2026-10-30: the model's own knowledge before the web

The team searches the web because *they* do not know a name; the model often
does. Research now runs in three steps:

1. **Our own data**, as before.
2. **The model's own knowledge**, with no search. Up to 25 topics go in one
   call. For each thing asked, the model marks whether it is sure: a
   regulator it is not certain of, an abbreviation it does not recognise, or
   a niche document is "not sure".
3. **A web search**, one topic at a time, only for the things the model was
   not sure of.

A finding the model is sure of carries no web source. It is shown labelled
"model knowledge (no source)" and is still a suggestion, never applied. This
replaces the rule that a finding with no source is dropped, which now holds
for web findings only.

The log says how many findings came from the model's knowledge and how many
topics go to the web, and it numbers each search ("searching the web
(3/7)"). On PSI's 77 categories this means 4 knowledge calls and only the
uncertain topics searched, where the first run searched all 77 one by one.
