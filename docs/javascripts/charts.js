// Draws every <div class="sbx-chart" data-src="..."> on the page with plotly.js,
// loaded only when such a div exists, and follows the light and dark palette.
const PLOTLY = "https://cdn.plot.ly/plotly-4.1.1.min.js";
const THEMES = {
  default: { text: "#1f1f1f", muted: "#5f5f5f", grid: "#e4e3df", accent: "#2a78d6", other: "#8f8e89" },
  slate: { text: "#f5f5f5", muted: "#c3c2b7", grid: "#383835", accent: "#3987e5", other: "#8f8e89" },
};
let loading = null;

function plotly() {
  loading ??= new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = PLOTLY;
    script.onload = () => resolve(window.Plotly);
    script.onerror = reject;
    document.head.append(script);
  });
  return loading;
}

function scheme() {
  return document.body.getAttribute("data-md-color-scheme") === "slate" ? "slate" : "default";
}

function themed(figure, name) {
  const t = THEMES[name];
  const family = getComputedStyle(document.body).fontFamily;
  const data = figure.data.map((trace) => ({
    ...trace,
    marker: { ...trace.marker, color: t[trace.meta] },
    line: { ...trace.line, color: t[trace.meta] },
    textfont: { family, size: 12, color: t.muted },
  }));
  const axes = Object.fromEntries(
    Object.keys(figure.layout)
      .filter((k) => /^[xy]axis/.test(k))
      .map((k) => [
        k,
        {
          ...figure.layout[k],
          gridcolor: t.grid,
          linecolor: t.grid,
          tickfont: { family, size: 12, color: t.muted },
          title: figure.layout[k].title && {
            ...figure.layout[k].title,
            font: { family, size: 12, color: t.muted },
          },
        },
      ]),
  );
  const annotations = (figure.layout.annotations ?? []).map((a) =>
    a.name === "note"
      ? { ...a, font: { family, size: 11, color: t.muted } }
      : { ...a, font: { family, size: 13, color: t.text } },
  );
  return {
    data,
    layout: { ...figure.layout, ...axes, annotations, font: { family, color: t.text } },
  };
}

async function draw() {
  const divs = document.querySelectorAll(".sbx-chart");
  if (divs.length === 0) return;
  const Plotly = await plotly();
  for (const div of divs) {
    div.figure ??= await (await fetch(div.dataset.src)).json();
    const { data, layout } = themed(div.figure, scheme());
    Plotly.react(div, data, layout, { displayModeBar: false, responsive: true });
  }
}

new MutationObserver(draw).observe(document.body, {
  attributes: true,
  attributeFilter: ["data-md-color-scheme"],
});
document$.subscribe(draw);
