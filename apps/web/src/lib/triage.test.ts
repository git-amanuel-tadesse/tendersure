import { describe, expect, it } from "vitest";
import { aiNotes, benchRows, inTab, plainStatus, quiet, sheetsOf, type Decision } from "./triage";

const q = (status = "", extra: Partial<Decision> = {}): Decision =>
  ({ id: "Q-1", type: "question", codes: ["PSI-ET-2026-022", "PSI-ET-2026-081"], decide: "x", proposed: "remove: 22, 81", status, ...extra });

describe("triage", () => {
  it("puts unanswered and unapplied decisions under Needs you", () => {
    expect(inTab("needs", q(), "", false)).toBe(true);
    expect(inTab("needs", q(), "keep in all", false)).toBe(false);
    expect(inTab("needs", q("NOT APPLIED on 4: section 'technical' is not in this sheet"), "x", false)).toBe(true);
    expect(inTab("needs", q("accepted as proposed"), "", false)).toBe(false);
  });
  it("finds where the AI review went against the proposal", () => {
    const s = "AI: applied: removed from 1 | 22: removal not applied, reviewer: licences are delivered online";
    expect(aiNotes(s)).toBe("22: removal not applied, reviewer: licences are delivered online");
    expect(inTab("disagreed", q(s), "remove: 81", false)).toBe(true);
    expect(inTab("disagreed", q("AI: applied: removed from 2"), "remove: 22, 81", false)).toBe(false);
    expect(plainStatus(s)).toBe("AI disagreed");
    expect(plainStatus("AI: applied: removed from 2")).toBe("AI agreed");
  });
  it("keeps marks against the client on their own tab, never quiet", () => {
    const b: Decision = { id: "M-BENCH-PSI-ET-2026-001", type: "marks", codes: ["PSI-ET-2026-001"], decide: "x",
                          proposed: "technical 15, hr 2", keys: ["technical 10, hr 7, company 0"] };
    expect(inTab("bench", b, "", false) && !inTab("needs", b, "", false) && !quiet(b, "")).toBe(true);
    expect(benchRows(b)).toEqual([{ section: "technical", ours: 15, client: 10 }, { section: "hr", ours: 2, client: 7 }]);
  });
  it("collapses only what needs nothing", () => {
    expect(quiet(q("AI: applied: removed from 2"), "remove: 22, 81")).toBe(true);
    expect(quiet(q(), "")).toBe(false);
  });
  it("lists sheets by category number", () => {
    expect(sheetsOf([{ codes: ["P-010", "P-002"] }, { codes: ["P-002", "P-001"] }])).toEqual(["P-001", "P-002", "P-010"]);
  });
});
