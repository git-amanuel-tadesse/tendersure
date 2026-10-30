import { describe, expect, it } from "vitest";
import { readItem, writeItem } from "./items";

describe("a technical item with its own marks", () => {
  it("reads and writes 'text [n]'", () => {
    expect(readItem("Attach Evidence Letter to extend warranty [3]")).toEqual({ text: "Attach Evidence Letter to extend warranty", marks: "3" });
    expect(readItem("(Dump Truck, Pick up)")).toEqual({ text: "(Dump Truck, Pick up)", marks: "" });
    expect(writeItem({ text: "Attach the warranty letter ", marks: "3" })).toBe("Attach the warranty letter [3]");
    expect(writeItem({ text: "MAF/DAF", marks: "" })).toBe("MAF/DAF");
    expect(writeItem({ text: "MAF/DAF", marks: "x" })).toBe("MAF/DAF");
  });
});
