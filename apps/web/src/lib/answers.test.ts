import { describe, expect, it } from "vitest";
import {
  aAnswer, marksAnswer, numberOf, parseAAnswer, parseMarks, parseQAnswer, qAnswer, rulingOptions, teamOptions, total,
} from "./answers";

const CODES = ["ERCS-ET-2026-003", "ERCS-ET-2026-007", "ERCS-ET-2026-019"];

describe("numberOf", () => {
  it("is the category number without leading zeros", () => {
    expect(numberOf("ERCS-ET-2026-019")).toBe("19");
    expect(numberOf("AAA-ET-2026-004")).toBe("4");
    expect(numberOf("NOCODE")).toBe("NOCODE");
  });
});

describe("Q answers", () => {
  it("name the categories, by number, as the parser reads them", () => {
    expect(qAnswer(CODES, { remove: [], reword: {} })).toBe("keep in all");
    expect(qAnswer(CODES, { remove: [...CODES], reword: {} })).toBe("remove all");
    expect(qAnswer(CODES, { remove: [CODES[0], CODES[1]], reword: {} })).toBe("remove: 3, 7");
    expect(qAnswer(CODES, { remove: [CODES[0]], reword: { [CODES[2]]: true } })).toBe("remove: 3; reword: 19");
    expect(qAnswer(CODES, { remove: [], reword: { [CODES[1]]: "  Attach your   ISO certificate " } }))
      .toBe("reword 7: Attach your ISO certificate");
  });

  it("never reword a category the question is removed from", () => {
    expect(qAnswer(CODES, { remove: [CODES[2]], reword: { [CODES[2]]: true } })).toBe("remove: 19");
  });

  it("read back what they wrote", () => {
    for (const choice of [
      { remove: [CODES[0], CODES[1]], reword: {} },
      { remove: [CODES[0]], reword: { [CODES[2]]: true as const } },
      { remove: [], reword: { [CODES[1]]: "Attach your ISO certificate" } },
    ]) {
      const back = parseQAnswer(qAnswer(CODES, choice), CODES, {});
      expect(back?.remove.sort()).toEqual([...choice.remove].sort());
      expect(back?.reword).toEqual(choice.reword);
    }
    expect(parseQAnswer("keep: 3", CODES, {})?.remove).toEqual([CODES[1], CODES[2]]);
    expect(parseQAnswer("reword all", CODES, { [CODES[0]]: "x" })?.reword).toEqual({ [CODES[0]]: true });
  });

  it("leave anything else as text", () => {
    expect(parseQAnswer("remove: 5", CODES, {})).toBeNull();          // 5 is not on this line
    expect(parseQAnswer("drop the last two", CODES, {})).toBeNull();
  });
});

describe("A answers", () => {
  it("pick suggestions by their number on the line", () => {
    expect(aAnswer(3, [])).toBe("skip");
    expect(aAnswer(3, [1, 2, 3])).toBe("add all");
    expect(aAnswer(3, [3, 1, 1, 9])).toBe("add: 1, 3");
    expect(parseAAnswer("", 3, "add all")).toEqual([1, 2, 3]);           // blank: the proposal
    expect(parseAAnswer("add: 2, 3", 3, "skip")).toEqual([2, 3]);
    expect(parseAAnswer("skip", 3, "add all")).toEqual([]);
    expect(parseAAnswer("whatever", 3, "skip")).toBeNull();
  });
});

describe("M answers", () => {
  it("read and write the whole split", () => {
    const m = parseMarks("company 10, technical 20, experience 20, trade_reference 50");
    expect(m).toEqual([["company", 10], ["technical", 20], ["experience", 20], ["trade_reference", 50]]);
    expect(total(m)).toBe(100);
    expect(marksAnswer([["company", 10], ["experience", 40], ["trade_reference", 50]]))
      .toBe("company 10, experience 40, trade_reference 50");
    expect(parseMarks("Technical: 12.5; SLA=7")).toEqual([["technical", 12.5], ["sla", 7]]);
  });
});

describe("C and T answers", () => {
  it("offer the values the sheets used, most common first", () => {
    expect(rulingOptions("ERCS sheets disagree on marks for 'Years in business' (20: 5, 10: 2)")).toEqual(["20", "10"]);
    expect(rulingOptions("AAA sheets disagree on mandatory for 'X' (True: 3, False: 1)")).toEqual(["True", "False"]);
    expect(rulingOptions("no values here")).toEqual([]);
  });

  it("know the team-wide lines", () => {
    expect(teamOptions("T-ADOPT-MAJORITY")).toEqual(["no", "yes"]);
    expect(teamOptions("T-UPLOAD-FLAG")).toEqual(["keep", "set flags"]);
  });
});
