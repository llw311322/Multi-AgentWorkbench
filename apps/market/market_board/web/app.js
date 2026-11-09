(function () {
  "use strict";

  var CORE = window.MarketChartCore;
  var state = {
    code: "000001.SH",
    period: "minute",
    chart: null,
    minuteTimer: null,
    cardTimer: null,
    busy: false
  };

  function $(id) {
    return document.getElementById(id);
  }

  function api(path) {
    return fetch(path, { cache: "no-store" }).then(function (res) {
      return res.json().then(function (body) {
        if (!body.ok) throw new Error(body.error || "HTTP " + res.status);
        return body.data;
      });
    });
  }

  // ------------------------------------------------------------ 指数卡片
  function sparkline(values, color) {
    if (!values || values.length < 2) return "";
    var min = Math.min.apply(null, values);
    var max = Math.max.apply(null, values);
    var span = max - min || 1;
    var pts = values
      .map(function (v, i) {
        var x = (i / (values.length - 1)) * 100;
        var y = 25 - ((v - min) / span) * 22;
        return x.toFixed(2) + "," + y.toFixed(2);
      })
      .join(" ");
    return (
      "<svg viewBox='0 0 100 26' preserveAspectRatio='none'>" +
      "<polyline points='" + pts + "' fill='none' stroke='" + color +
      "' stroke-width='1.4' vector-effect='non-scaling-stroke'/></svg>"
    );
  }

  function renderCards(data) {
    $("cards").innerHTML = data.indices
      .map(function (it) {
        var color = CORE.colorOf(it.pct);
        return (
          "<button class='card" + (it.code === state.code ? " active" : "") +
          "' data-code='" + it.code + "'>" +
          "<div class='card-top'><span class='card-name'>" + it.name +
          "</span><span class='card-pct' style='color:" + color + "'>" +
          CORE.pctText(it.pct) + "</span></div>" +
          "<div class='card-price' style='color:" + color + "'>" +
          CORE.priceText(it.close) + "</div>" +
          "<div class='spark'>" + sparkline(it.spark, color) + "</div>" +
          "</button>"
        );
      })
      .join("");

    var badge = $("marketState");
    badge.textContent = data.marketState;
    badge.className =
      "state" + (data.marketState === "盘中" ? " live" : data.marketState === "休市" ? "" : " done");
    $("serverTime").textContent =
      data.serverTime + (data.source === "eastmoney" ? " · 实时" : " · 收盘");
  }

  function loadIndices() {
    return api("/api/indices")
      .then(renderCards)
      .catch(function (err) {
        $("serverTime").textContent = "行情获取失败：" + err.message;
      });
  }

  // ------------------------------------------------------------ 图表
  function renderEmpty(msg) {
    state.chart.clear();
    state.chart.setOption({
      title: {
        text: msg,
        left: "center",
        top: "middle",
        textStyle: { color: "#9aa1ad", fontSize: 13, fontWeight: 400 }
      }
    });
  }

  function loadChart() {
    if (state.busy) return Promise.resolve();
    state.busy = true;
    var url =
      state.period === "minute"
        ? "/api/intraday?code=" + encodeURIComponent(state.code)
        : "/api/kline?code=" + encodeURIComponent(state.code) +
          "&period=" + state.period + "&limit=1500";

    return api(url)
      .then(function (d) {
        var built =
          state.period === "minute"
            ? CORE.buildIntraday(d, { height: 480 })
            : CORE.buildKline(d, { height: 480 });
        if (!built) return renderEmpty("该指数暂无数据");
        state.chart.setOption(built.option, true);
        $("chartTitle").textContent = built.title;
        $("chartMeta").textContent = built.meta;
      })
      .catch(function (err) {
        renderEmpty("加载失败：" + err.message);
      })
      .then(function () {
        state.busy = false;
      });
  }

  // ------------------------------------------------------------ 编排
  function schedule() {
    if (state.minuteTimer) clearInterval(state.minuteTimer);
    if (state.cardTimer) clearInterval(state.cardTimer);
    if (state.period === "minute") {
      state.minuteTimer = setInterval(function () {
        if (!document.hidden) loadChart();
      }, 30000);
    }
    state.cardTimer = setInterval(function () {
      if (!document.hidden) loadIndices();
    }, 15000);
  }

  function bind() {
    $("cards").addEventListener("click", function (e) {
      var card = e.target.closest(".card");
      if (!card || card.dataset.code === state.code) return;
      state.code = card.dataset.code;
      Array.prototype.forEach.call(document.querySelectorAll(".card"), function (el) {
        el.classList.toggle("active", el.dataset.code === state.code);
      });
      loadChart();
    });

    $("tabs").addEventListener("click", function (e) {
      var btn = e.target.closest("button[data-period]");
      if (!btn || btn.dataset.period === state.period) return;
      state.period = btn.dataset.period;
      Array.prototype.forEach.call(document.querySelectorAll("#tabs button"), function (el) {
        el.classList.toggle("active", el === btn);
      });
      loadChart();
      schedule();
    });

    window.addEventListener("resize", function () {
      if (state.chart) state.chart.resize();
    });

    document.addEventListener("visibilitychange", function () {
      if (!document.hidden) {
        loadIndices();
        loadChart();
      }
    });
  }

  // URL 参数即组件 props：?code=000688.SH&period=day
  function applyQuery() {
    var qp = new URLSearchParams(location.search);
    var code = (qp.get("code") || "").toUpperCase();
    if (/^\d{6}\.(SH|SZ|BJ)$/.test(code)) state.code = code;
    var period = qp.get("period");
    if (["minute", "day", "week", "month", "year"].indexOf(period) >= 0) {
      state.period = period;
    }
    Array.prototype.forEach.call(document.querySelectorAll("#tabs button"), function (el) {
      el.classList.toggle("active", el.dataset.period === state.period);
    });
  }

  function init() {
    applyQuery();
    state.chart = echarts.init($("chart"), null, { renderer: "canvas" });
    bind();
    loadIndices();
    loadChart();
    schedule();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
