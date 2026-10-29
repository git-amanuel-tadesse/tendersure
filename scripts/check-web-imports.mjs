// Spec 06 M1: the web app reaches the API only through @tendersure/api-client.
// No fetch, XMLHttpRequest or "/api/..." of its own in apps/web/src; links come from api-client's `links`.
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";

const root = new URL("../apps/web/src", import.meta.url).pathname;
const rules = [
  [/\bfetch\s*\(/, "fetch() outside api-client"],
  [/\bXMLHttpRequest\b/, "XMLHttpRequest outside api-client"],
  [/["'`]\/api\//, 'an "/api/..." path of its own (use api-client\'s api or links)'],
];
const walk = (d) => readdirSync(d).flatMap((n) => (statSync(join(d, n)).isDirectory() ? walk(join(d, n)) : [join(d, n)]));
const found = [];
for (const f of walk(root).filter((f) => /\.(ts|tsx)$/.test(f) && !f.endsWith(".gen.ts"))) {
  readFileSync(f, "utf8").split("\n").forEach((line, i) => {
    // a typed call of the client (api.GET("/api/..."), checked against the contract) is the allowed way
    const rest = line.replace(/\bapi\.(GET|POST|PUT|PATCH|DELETE)\(\s*["'`]\/api\/[^"'`]*["'`]/g, "");
    for (const [re, why] of rules) if (re.test(rest)) found.push(`${f.slice(root.length + 1)}:${i + 1}: ${why}`);
  });
}
if (found.length) {
  console.error(`The web app reaches the API around api-client:\n  ${found.join("\n  ")}`);
  process.exit(1);
}
console.log("web: the API only through @tendersure/api-client");
