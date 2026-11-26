import * as esbuild from "esbuild";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const outHtmlPath = path.resolve(here, "../assets/app.html");

const result = await esbuild.build({
  entryPoints: [path.join(here, "main.ts")],
  bundle: true,
  minify: true,
  format: "esm",
  target: ["es2020"],
  write: false,
});

const jsCode = result.outputFiles[0].text;
const template = fs.readFileSync(path.join(here, "index.html"), "utf8");
const bundledHtml = template.replace(
  '<script type="module" src="./bundle.js"></script>',
  () => `<script type="module">\n${jsCode}\n</script>`,
);

fs.mkdirSync(path.dirname(outHtmlPath), { recursive: true });
fs.writeFileSync(outHtmlPath, bundledHtml, "utf8");

const kb = (Buffer.byteLength(bundledHtml, "utf8") / 1024).toFixed(1);
console.log(`Built ${outHtmlPath} (${kb} KiB)`);
