/*
 * "Install as an app" support: remembers the browser's install prompt (it fires once, early, long
 * before Settings is opened) and works out which instructions fit this browser.
 */
import { useSyncExternalStore } from "react";

let deferred = null; // the beforeinstallprompt event, while the browser offers an install
let installed = false;
const listeners = new Set();
const notify = () => listeners.forEach((l) => l());

/** Call once at startup, before the event can fire. */
export function initInstallPrompt(win = window) {
  win.addEventListener("beforeinstallprompt", (e) => {
    e.preventDefault(); // keep it for the Settings button instead of the browser's own mini-bar
    deferred = e;
    notify();
  });
  win.addEventListener("appinstalled", () => {
    deferred = null;
    installed = true;
    notify();
  });
}

/** Ask the browser to install. Resolves to the user's choice ("accepted" | "dismissed"), or null when there is no prompt. */
export async function promptInstall() {
  const ev = deferred;
  if (!ev) return null;
  deferred = null; // a prompt can be used once
  notify();
  await ev.prompt();
  const choice = await ev.userChoice;
  return choice?.outcome ?? null;
}

/**
 * Which hint to show.
 *  "insecure"  - not a secure context: neither install nor offline can work
 *  "installed" - already running as an installed app
 *  "prompt"    - the browser offered an install (a button works)
 *  "ios"       - iPhone/iPad Safari: Share -> Add to Home Screen
 *  "manual"    - anything else: use the browser's own menu
 */
export function installState({ win = window, nav = navigator, canPrompt = !!deferred, wasInstalled = installed } = {}) {
  if (!win.isSecureContext) return "insecure";
  const standalone = win.matchMedia?.("(display-mode: standalone)")?.matches || nav.standalone === true;
  if (standalone || wasInstalled) return "installed";
  if (canPrompt) return "prompt";
  const ua = nav.userAgent || "";
  const iOS = /iPad|iPhone|iPod/.test(ua) || (nav.platform === "MacIntel" && nav.maxTouchPoints > 1);
  return iOS ? "ios" : "manual";
}

const subscribe = (cb) => {
  listeners.add(cb);
  return () => listeners.delete(cb);
};

/** React hook: the current install hint, updating when the browser offers or completes an install. */
export function useInstallState() {
  const prompt = useSyncExternalStore(subscribe, () => !!deferred);
  const done = useSyncExternalStore(subscribe, () => installed);
  return installState({ canPrompt: prompt, wasInstalled: done });
}
