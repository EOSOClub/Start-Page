import { describe, expect, it } from "vitest";
import { installState } from "./pwa.js";

const win = (over = {}) => ({ isSecureContext: true, matchMedia: () => ({ matches: false }), ...over });
const nav = (over = {}) => ({ userAgent: "Mozilla/5.0 (X11; Linux x86_64) Chrome/130", platform: "Linux", maxTouchPoints: 0, ...over });

describe("installState", () => {
  it("says insecure first: neither install nor offline can work", () => {
    expect(installState({ win: win({ isSecureContext: false }), nav: nav(), canPrompt: true })).toBe("insecure");
  });
  it("recognises an installed app", () => {
    expect(installState({ win: win({ matchMedia: () => ({ matches: true }) }), nav: nav(), canPrompt: true })).toBe("installed");
    expect(installState({ win: win(), nav: nav({ standalone: true }) })).toBe("installed");
    expect(installState({ win: win(), nav: nav(), wasInstalled: true })).toBe("installed");
  });
  it("offers a button when the browser offered an install", () => {
    expect(installState({ win: win(), nav: nav(), canPrompt: true })).toBe("prompt");
  });
  it("explains Share -> Add to Home Screen on iOS, the browser menu elsewhere", () => {
    const iphone = nav({ userAgent: "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) Safari" });
    expect(installState({ win: win(), nav: iphone, canPrompt: false })).toBe("ios");
    const ipadOs = nav({ userAgent: "Mozilla/5.0 (Macintosh) Safari", platform: "MacIntel", maxTouchPoints: 5 });
    expect(installState({ win: win(), nav: ipadOs, canPrompt: false })).toBe("ios");
    expect(installState({ win: win(), nav: nav(), canPrompt: false })).toBe("manual");
  });
});
