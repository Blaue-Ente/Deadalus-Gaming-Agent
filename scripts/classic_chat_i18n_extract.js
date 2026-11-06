#!/usr/bin/env node
/**
 * Extract unique Cyrillic string fragments from ui/static/classic_chat.js (AST).
 * Used to build classic.* keys for i18n — run: node scripts/classic_chat_i18n_extract.js
 */
const fs = require("fs");
const path = require("path");
const acorn = require("acorn");
const walk = require("acorn-walk");

const root = path.resolve(__dirname, "..");
const srcPath = path.join(root, "ui/static/classic_chat.js");
const src = fs.readFileSync(srcPath, "utf8");
const ast = acorn.parse(src, { ecmaVersion: 2022, locations: true, sourceType: "script" });

const cyr = /[\u0400-\u04FF]/;
const seen = new Map(); // value -> first loc

function note(val, loc) {
  if (typeof val !== "string" || !cyr.test(val)) return;
  if (!seen.has(val)) seen.set(val, loc);
}

walk.simple(ast, {
  Literal(node) {
    if (typeof node.value === "string") note(node.value, node.loc.start);
  },
  TemplateLiteral(node) {
    for (const q of node.quasis) {
      const v = q.value.cooked;
      if (v) note(v, q.loc.start);
    }
  },
});

const entries = [...seen.entries()].sort((a, b) => a[0].localeCompare(b[0]));
console.log(JSON.stringify({ count: entries.length, samples: entries.slice(0, 20).map(([v]) => v.slice(0, 120)) }, null, 2));
console.error("total unique cyrillic string fragments:", entries.length);
