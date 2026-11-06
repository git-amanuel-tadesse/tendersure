# Spec 19: What Amref's first proposal showed

The first proposal for Amref (2026-11-04: 94 draft rows and the 2025-2026
list, with the model) was reviewed without changes. Five faults in the code
came out of it, and the team asked for all five to be fixed (2026-11-04).
Research noise found in the same review (questions offered as licences,
"License Given") is left for a separate research cleanup.

Every rule is written for any client.

## A. Only the client's own words decide eligibility

- **The rule.** An eligibility document is applied only when the client's
  own text states it. That text is:
  - the draft rows a line comes from;
  - for an addition from the previous list, the previous category's name.
- **Everything else is a suggestion.** A document read from the model's new
  wording, or from the model's own additions, goes to Suggested eligibility.
- **Example.** Amref's "Provision of air ticketing services" was reworded by
  the model to "… (IATA Registered Only)". It got "IATA Registration
  Certificate" as an applied eligibility. Now the certificate is only
  suggested.

## B. Previous categories remember their tab

- **What is kept.** Each previous name keeps the tab it was read from.
- **Who may apply.** A name read from a tab that reads as consultancy (e.g.
  `Prequalified_Consultant`) is open to Individual Consultants when it is
  proposed. Example: Statisticians.
- **The reason names the tab.** "In the client's 2025-2026 list
  (Prequalified_Consultant), not in the draft".
- **Read again on each proposal.** The previous list is read again from its
  file on every proposal, as the draft is, so a list read before this change
  gets its tabs.

## C. Supply is not repair

- **The rule.** A previous category and a draft row do not match when one
  only supplies and the other only maintains or repairs.
  - Supply wording: supply, delivery, purchase.
  - Repair wording: repair, maintenance, servicing.
  - Example: "Maintenance and repair of cameras" is not "Supply of cameras".
- **When they still match.** Rows with both kinds of wording, or neither,
  match as before.

## D. Suggested splits keep their wording

- **The rule.** A sub-category that already starts with a procurement word
  ("Provision of …", "Supply of …") is used as it is in a suggested split.
- **Example.** The split no longer reads "Provision of Provision of
  Project-Based Consultancy Services".

## E. Abbreviations stay lower case

- **The rule.** "i.e.", "e.g." and "etc." stay lower case wherever they
  stand, inside brackets too.
- **Example.** "Materials, i.e. Cement (e.g. Pumps)" is no longer changed to
  "I.e." and "(E.g.".

## Acceptance

- **A.** A model rename that adds "(IATA Registered Only)" to a draft row
  without it gives no applied eligibility, and suggests "IATA Registration
  Certificate". A draft row with it still applies it.
- **B.** A name from a tab called `Prequalified_Consultant` is proposed with
  Individual Consultant, and its reason names the tab.
- **C.** "Maintenance and repair of cameras" is proposed when the draft has
  only "Supply of cameras". "Supply, installation and maintenance of solar
  panels" still matches "Supply and maintenance of solar panels".
- **D.** A suggested split of sub-category "Provision of X" is "Provision of
  X".
- **E.** `title_case("Supply of materials, i.e. cement (e.g. pumps, etc.)")`
  keeps "i.e.", "(e.g." and "etc.".
- **On Amref:** propose again. Air ticketing has no applied eligibility, the
  consultancy additions include Individual Consultant, and camera repair is
  proposed.
