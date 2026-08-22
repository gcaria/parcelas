import fs from "node:fs";

const html = fs.readFileSync(new URL("index.html", import.meta.url), "utf8");
const inlineScripts = [...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g)]
  .map((match) => match[1])
  .filter((source) => source.trim());

if (inlineScripts.length === 0) {
  throw new Error("No inline JavaScript found in frontend/index.html");
}

for (const source of inlineScripts) {
  new Function(source);
}

console.log(`Validated ${inlineScripts.length} inline script(s)`);
