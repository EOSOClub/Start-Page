import FitText from "./FitText.jsx";
import { Header, Problem, useIntegration } from "./integrations.jsx";
import { SeriesSpark } from "./integrationViews.jsx";
import { formatValue, parseKeys } from "./integrationViz.js";
import { ARC, arcPath, gaugeModel, gaugeText } from "./gauge.js";

/* One stat of any integration that provides `stats`, drawn as an arc, a bar or a big number. */

const TRACK = arcPath(ARC);

export const GaugeWidget = {
  label: "Gauge",
  group: "Integrations",
  size: { w: 4, h: 4 },
  fields: [
    { key: "integration_id", label: "Integration (any that provides stats)", type: "integration", capability: "stats" },
    { key: "stat_key", label: "Stat", type: "statkeys", multiple: false, placeholder: "cpu", help: "One stat key. Pick one of the chips once an integration is selected and has data." },
    { key: "title", label: "Title", type: "text", placeholder: "(stat name)" },
    {
      key: "style",
      label: "Style",
      type: "select",
      default: "arc",
      options: [
        { value: "arc", label: "Arc" },
        { value: "bar", label: "Bar" },
        { value: "number", label: "Number" },
      ],
    },
    { key: "min", label: "Minimum", type: "number", default: 0 },
    { key: "max", label: "Maximum", type: "number", default: 100 },
    { key: "warn", label: "Warn from (optional)", type: "number", help: "The gauge turns amber at this value. Set “Bad” below “Warn” for stats where low is bad, such as free space." },
    { key: "bad", label: "Bad from (optional)", type: "number" },
    { key: "show_sparkline", label: "Show sparkline (when the stat has a series)", type: "checkbox", default: true },
  ],
  Render: ({ widget, integrations, integrationData }) => {
    const { integ, data } = useIntegration(widget, integrations, integrationData);
    const c = widget.config;
    if (!integ || !data || (!data.ok && !data.data)) return <Problem integ={integ} data={data} type="stats" />;
    const stats = data.data?.stats;
    if (!Array.isArray(stats)) return <div className="muted pad">{integ.name} does not provide stats.</div>;
    const key = parseKeys(c.stat_key)[0];
    if (!key) return <div className="muted pad">Pick a stat in widget settings.</div>;
    const stat = stats.find((s) => String(s.key).toLowerCase() === key);
    if (!stat) return <div className="muted pad">{integ.name} has no stat “{key}”.</div>;

    const m = gaugeModel({ value: stat.value, min: c.min, max: c.max, warn: c.warn, bad: c.bad });
    const text = gaugeText(stat.value, stat.unit, formatValue);
    const name = c.title || stat.label;
    const tone = m.tone ?? "none";
    const style = c.style || "arc";
    const meter = {
      role: "meter",
      "aria-label": name,
      "aria-valuemin": m.min,
      "aria-valuemax": m.max,
      "aria-valuetext": text,
      ...(m.value != null ? { "aria-valuenow": m.value } : {}),
    };
    const spark = c.show_sparkline !== false && stat.series?.length > 1 ? <SeriesSpark label={stat.label} series={stat.series} /> : null;

    let body;
    if (style === "bar") {
      body = (
        <div className="gauge-bar-wrap" {...meter}>
          <b className={`gauge-num tone-${tone}`}>{text}</b>
          <div className="gauge-bar"><i className={`gauge-fill-${tone}`} style={{ width: `${m.fraction * 100}%` }} /></div>
          <div className="gauge-ends muted"><span>{formatValue(m.min)}</span><span>{formatValue(m.max)}</span></div>
          {spark}
        </div>
      );
    } else if (style === "number") {
      body = (
        <div className="gauge-body" {...meter}>
          <FitText className="gauge-fit" min={10}>
            <div className={`gauge-big tone-${tone}`}>{text}</div>
          </FitText>
          {spark}
        </div>
      );
    } else {
      body = (
        <div className="gauge-body" {...meter}>
          <svg className="gauge-svg" viewBox="0 0 100 84" preserveAspectRatio="xMidYMid meet" aria-hidden="true" focusable="false">
            <path className="gauge-track" d={TRACK} pathLength="100" />
            {m.fraction > 0 && <path className={`gauge-fill gauge-fill-${tone}`} d={TRACK} pathLength="100" strokeDasharray={`${(m.fraction * 100).toFixed(2)} 100`} />}
            <text className="gauge-value" x="50" y="52" textAnchor="middle">{text}</text>
            <text className="gauge-end" x="17" y="81" textAnchor="middle">{formatValue(m.min)}</text>
            <text className="gauge-end" x="83" y="81" textAnchor="middle">{formatValue(m.max)}</text>
          </svg>
          {spark}
        </div>
      );
    }
    return (
      <div className="group">
        <Header title={name} right={!data.ok ? <span className="muted">stale</span> : null} />
        {body}
      </div>
    );
  },
};
