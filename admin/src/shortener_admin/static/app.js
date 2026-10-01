// Charts read their data from a data-chart JSON attribute (no inline scripts); re-render after HTMX swaps.
(function () {
  function renderCharts(root) {
    root.querySelectorAll("canvas[data-chart]").forEach(function (canvas) {
      if (canvas._chart) { canvas._chart.destroy(); }
      var data = JSON.parse(canvas.dataset.chart);
      canvas._chart = new Chart(canvas, {
        type: "bar",
        data: { labels: data.labels, datasets: [{ label: "Clicks", data: data.counts }] },
        options: { animation: false, plugins: { legend: { display: false } },
                   scales: { y: { beginAtZero: true, ticks: { precision: 0 } } } },
      });
    });
  }
  function wireCopyButtons(root) {
    root.querySelectorAll("button[data-copy]").forEach(function (button) {
      button.addEventListener("click", function () {
        navigator.clipboard.writeText(button.dataset.copy);
        button.textContent = "Copied";
      });
    });
  }
  document.addEventListener("DOMContentLoaded", function () { renderCharts(document); wireCopyButtons(document); });
  document.addEventListener("htmx:afterSwap", function (event) { renderCharts(event.target); wireCopyButtons(event.target); });
})();
