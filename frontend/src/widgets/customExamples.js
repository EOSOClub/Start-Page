/* The "Insert example" menu of the custom widget: three short, working starting points. */
export const EXAMPLES = [
  {
    label: "Stat from integration data",
    config: {
      html: '<div class="stat"><div id="value">-</div><div id="label">No data</div></div>',
      css: ".stat{height:100vh;display:flex;flex-direction:column;align-items:center;justify-content:center}\n#value{font-size:36px;font-weight:700;color:var(--sp-accent)}\n#label{color:var(--sp-muted)}",
      js: 'StartPage.onData(function (data) {\n  var stat = data.integration && data.integration.stats && data.integration.stats[0];\n  document.getElementById("value").textContent = stat ? stat.value + (stat.unit ? " " + stat.unit : "") : "-";\n  document.getElementById("label").textContent = stat ? stat.label : "Pick an integration in the widget settings";\n});',
    },
  },
  {
    label: "Service status list",
    config: {
      html: '<ul id="list"></ul>',
      css: 'ul{list-style:none;margin:0;padding:8px}\nli::before{content:"";display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:8px;background:var(--sp-muted)}\nli.online::before{background:#3ecf6e}\nli.offline::before{background:#ef5350}\nli.degraded::before{background:#f5a623}',
      js: 'StartPage.onData(function (data) {\n  var list = document.getElementById("list");\n  list.textContent = "";\n  data.services.forEach(function (s) {\n    var li = document.createElement("li");\n    li.className = s.status;\n    li.textContent = s.name + (s.latency_ms != null ? " - " + s.latency_ms + " ms" : "");\n    list.appendChild(li);\n  });\n});',
    },
  },
  {
    label: "Live clock",
    config: {
      html: '<div class="clock"><div id="time"></div><div id="date"></div></div>',
      css: ".clock{height:100vh;display:flex;flex-direction:column;align-items:center;justify-content:center}\n#time{font-size:34px;font-weight:600}\n#date{color:var(--sp-muted)}",
      js: 'function tick() {\n  var now = new Date();\n  document.getElementById("time").textContent = now.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });\n  document.getElementById("date").textContent = now.toLocaleDateString([], { weekday: "long", month: "long", day: "numeric" });\n}\ntick();\nsetInterval(tick, 1000);',
    },
  },
];
