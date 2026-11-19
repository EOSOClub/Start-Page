/*
 * Builds the text of /sw.js from the two source files. Pure string work, shared by the Vite plugin
 * and by the tests (which run the composed script against a fake worker scope).
 */

/** routing.js with its `export`s dropped, then sw.js without its import line, with the build id filled in. */
export function composeSw(routingSrc, swSrc, buildId) {
  if (!/^[A-Za-z0-9_-]+$/.test(buildId)) throw new Error(`bad build id: ${buildId}`);
  const routing = routingSrc.replace(/^export\s+/gm, "");
  const worker = swSrc.replace(/^import[^;]*;\s*$/gm, "").replace("__BUILD_ID__", buildId);
  if (/^\s*(import|export)\s/m.test(routing + worker)) throw new Error("sw.js must compose into a classic script");
  return `${routing}\n${worker}`;
}
