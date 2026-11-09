import "@testing-library/jest-dom/vitest";

// Recharts' ResponsiveContainer relies on ResizeObserver, absent from jsdom.
class RO {
  observe() {}
  unobserve() {}
  disconnect() {}
}
(globalThis as unknown as { ResizeObserver: typeof RO }).ResizeObserver = RO;
