import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { composeSw } from "./src/sw/compose.js";

const read = (rel) => readFileSync(fileURLToPath(new URL(rel, import.meta.url)), "utf8");

/**
 * Emits /sw.js at the root of the build with the build id filled in.
 *
 * The id is a hash of the names of everything the build emits (Vite content-hashes asset names, so any
 * change to the app changes a name) plus the text of index.html. The same source builds to the same
 * sw.js byte for byte, so browsers see no update when nothing changed; any real change gives a new
 * cache name, which is what makes the new worker install, claim and drop the old caches.
 * Done in a plugin rather than by the backend so a plain `npm run build` yields a complete app.
 *
 * The same id is written into index.html as <meta name="startpage-build">, so a page can tell whether the
 * worker that just took over belongs to the build the page itself came from (see sw/register.js). The hash
 * leaves index.html's emitted name out, so it does not depend on whether the HTML was already emitted.
 */
export function swPlugin() {
  return {
    name: "startpage-sw",
    apply: "build",
    generateBundle: {
      order: "post",
      handler(_options, bundle) {
        const hash = createHash("sha256");
        for (const name of Object.keys(bundle).sort()) if (name !== "sw.js" && name !== "index.html") hash.update(name + "\n");
        hash.update(read("./index.html"));
        const buildId = hash.digest("hex").slice(0, 12);
        const html = bundle["index.html"];
        if (html && html.type === "asset" && typeof html.source === "string") {
          html.source = html.source.replace("</head>", `  <meta name="startpage-build" content="${buildId}" />\n  </head>`);
        }
        this.emitFile({
          type: "asset",
          fileName: "sw.js",
          source: composeSw(read("./src/sw/routing.js"), read("./src/sw/sw.js"), buildId),
        });
      },
    },
  };
}
