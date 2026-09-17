// jsdom has no ResizeObserver, and Canvas.jsx calls a bare `new ResizeObserver(...)`. This no-op
// stub never fires, so Canvas stays on the grid branch deterministically (ADR Iteration 019).
globalThis.ResizeObserver ??= class {
  observe() {}
  unobserve() {}
  disconnect() {}
};
