import { useEffect, useLayoutEffect, useRef, useState } from "react";

/**
 * Sizes text to its box: picks the largest font size at which the content, laid
 * out on unwrapped lines, fits both the width and the height of the widget. Size
 * everything inside in `em` so the lines keep their proportions as it scales.
 * `min` / `max` bound the base font size in px.
 */
export default function FitText({ className = "", min = 8, max = 400, title, children }) {
  const outer = useRef(null);
  const inner = useRef(null);
  const [size, setSize] = useState(null);
  const fit = useRef(() => {});

  fit.current = () => {
    const o = outer.current;
    const i = inner.current;
    if (!o || !i) return;
    const cs = getComputedStyle(o);
    const availW = o.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight);
    const availH = o.clientHeight - parseFloat(cs.paddingTop) - parseFloat(cs.paddingBottom);
    const { width, height } = i.getBoundingClientRect();
    if (availW <= 0 || availH <= 0 || !width || !height) return;
    const cur = parseFloat(getComputedStyle(i).fontSize);
    // Text scales ~linearly with font size; 2% headroom absorbs rounding.
    const next = Math.min(max, Math.max(min, cur * Math.min(availW / width, availH / height) * 0.98));
    setSize((s) => (s != null && Math.abs(s - next) < 0.5 ? s : next));
  };

  // Content changes (the ticking clock, a new greeting) re-measure after every render.
  useLayoutEffect(() => fit.current());

  useEffect(() => {
    const ro = new ResizeObserver(() => fit.current());
    ro.observe(outer.current);
    document.fonts?.ready.then(() => fit.current());
    return () => ro.disconnect();
  }, []);

  return (
    <div ref={outer} className={`fit ${className}`} title={title}>
      <div ref={inner} className="fit-inner" style={size != null ? { fontSize: `${size}px` } : undefined}>
        {children}
      </div>
    </div>
  );
}
