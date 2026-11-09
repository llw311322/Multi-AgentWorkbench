/* 图表内核：只负责「数据 -> ECharts option」，不含任何 DOM 与请求逻辑。
   页面(app.js) 与 Web Component(widget.js) 共用这一份，保证两处渲染完全一致。 */
(function (global) {
  "use strict";

  var UP = "#d03a3a";
  var DOWN = "#0f9d63";
  var FLAT = "#6a7280";
  var GRID = "#f0f1f4";
  var AXIS = "#9aa1ad";
  var LINE = "#e7e9ee";

  var PIVOT = ["09:30", "10:30", "11:30", "14:00", "15:00"];

  var TIP_BG = "rgba(255,255,255,0.98)";
  var TIP_TEXT = "#171a20";
  var LABEL_COLOR = "#9aa1ad";
  var VALUE_COLOR = "#171a20";
  var ZOOM_BG = "#f7f8fa";
  var ZOOM_FILL = "rgba(47,111,237,0.10)";
  var ZOOM_DATA_LINE = "#dfe3ea";
  var ZOOM_DATA_AREA = "#f2f4f8";

  function colorOf(v) {
    if (typeof v !== "number" || isNaN(v)) return FLAT;
    if (v > 0) return UP;
    if (v < 0) return DOWN;
    return FLAT;
  }

  function pctText(v) {
    if (typeof v !== "number" || isNaN(v)) return "--";
    return (v > 0 ? "+" : "") + v.toFixed(2) + "%";
  }

  function priceText(v) {
    if (typeof v !== "number" || isNaN(v)) return "--";
    return v.toFixed(2);
  }

  function amountText(v) {
    if (!v) return "--";
    if (v >= 1e12) return (v / 1e12).toFixed(2) + " 万亿";
    if (v >= 1e8) return Math.round(v / 1e8) + " 亿";
    return Math.round(v / 1e4) + " 万";
  }

  function volText(v) {
    if (!v) return "--";
    if (v >= 1e8) return (v / 1e8).toFixed(2) + " 亿手";
    if (v >= 1e4) return (v / 1e4).toFixed(1) + " 万手";
    return v + " 手";
  }

  function row(label, value, color) {
    return (
      "<div style='display:flex;justify-content:space-between;gap:18px;line-height:1.75'>" +
      "<span style='color:" + LABEL_COLOR + "'>" + label + "</span>" +
      "<span style='color:" + (color || VALUE_COLOR) + ";font-variant-numeric:tabular-nums'>" +
      value + "</span></div>"
    );
  }

  function ma(values, n) {
    var out = [];
    var sum = 0;
    for (var i = 0; i < values.length; i++) {
      sum += values[i];
      if (i >= n) sum -= values[i - n];
      out.push(i >= n - 1 ? +(sum / n).toFixed(2) : "-");
    }
    return out;
  }

  function tooltipBase() {
    return {
      backgroundColor: TIP_BG,
      borderColor: LINE,
      borderWidth: 1,
      padding: [8, 10],
      textStyle: { color: TIP_TEXT, fontSize: 12 },
      trigger: "axis",
      axisPointer: {
        type: "cross",
        link: [{ xAxisIndex: "all" }],
        label: { backgroundColor: "#40454f", fontSize: 11 }
      }
    };
  }

  // ---------------------------------------------------------------- 分时
  function buildIntraday(d, opts) {
    opts = opts || {};
    var H = opts.height || 480;
    var points = d.points || [];
    var real = points.filter(function (p) {
      return p.p !== null && p.p !== undefined;
    });
    if (!real.length) return null;

    var slots = d.slots || points.map(function (p) { return p.t; });
    var prices = points.map(function (p) { return p.p; });
    var avgs = points.map(function (p) { return p.avg; });

    var preClose = d.preClose;
    var last = real[real.length - 1];
    var lastPct = preClose ? ((last.p - preClose) / preClose) * 100 : 0;
    var mainColor = colorOf(lastPct);

    var hi = -Infinity;
    var lo = Infinity;
    real.forEach(function (p) {
      if (p.p > hi) hi = p.p;
      if (p.p < lo) lo = p.p;
    });
    if (preClose) {
      hi = Math.max(hi, preClose);
      lo = Math.min(lo, preClose);
    }
    var mid = preClose || (hi + lo) / 2;
    var range = Math.max(Math.abs(hi - mid), Math.abs(lo - mid));
    if (!range) range = mid * 0.01;

    var volData = [];
    var prevPrice = preClose || real[0].p;
    points.forEach(function (p) {
      if (p.p === null || p.p === undefined) {
        volData.push(null);
        return;
      }
      volData.push({
        value: p.v,
        itemStyle: { color: p.p >= prevPrice ? "rgba(208,58,58,0.55)" : "rgba(15,157,99,0.55)" }
      });
      prevPrice = p.p;
    });

    var volH = Math.min(110, Math.max(50, Math.round(H * 0.17)));
    var mainTop = 20;
    var mainH = H - mainTop - volH - 26 - 26;
    var volTop = mainTop + mainH + 26;

    var option = {
      animation: false,
      grid: [
        { left: 64, right: 58, top: mainTop, height: mainH },
        { left: 64, right: 58, top: volTop, height: volH }
      ],
      tooltip: Object.assign(tooltipBase(), {
        formatter: function (params) {
          var idx = params[0].dataIndex;
          var p = points[idx];
          if (!p || p.p === null || p.p === undefined) {
            return "<span style='color:#9aa1ad'>" + d.date + " " + slots[idx] + " · 无成交</span>";
          }
          var chg = preClose ? p.p - preClose : 0;
          var pct = preClose ? (chg / preClose) * 100 : 0;
          return (
            "<div style='font-size:11px;color:#9aa1ad;margin-bottom:5px'>" + d.date + " " + p.t + "</div>" +
            row("价格", p.p.toFixed(2), colorOf(pct)) +
            row("涨跌", (chg >= 0 ? "+" : "") + chg.toFixed(2) + "   " + pctText(pct), colorOf(pct)) +
            row("均价", p.avg === null ? "--" : p.avg.toFixed(2)) +
            row("成交", volText(p.v))
          );
        }
      }),
      axisPointer: { link: [{ xAxisIndex: "all" }] },
      xAxis: [
        {
          type: "category",
          gridIndex: 0,
          data: slots,
          boundaryGap: false,
          axisLine: { lineStyle: { color: LINE } },
          axisTick: { show: false },
          splitLine: { show: false },
          axisLabel: {
            color: AXIS,
            fontSize: 11,
            interval: function (idx, val) { return PIVOT.indexOf(val) >= 0; }
          }
        },
        {
          type: "category",
          gridIndex: 1,
          data: slots,
          boundaryGap: false,
          axisLine: { lineStyle: { color: LINE } },
          axisTick: { show: false },
          splitLine: { show: false },
          axisLabel: { show: false }
        }
      ],
      yAxis: [
        {
          gridIndex: 0,
          min: mid - range * 1.15,
          max: mid + range * 1.15,
          axisLine: { show: false },
          axisTick: { show: false },
          splitLine: { lineStyle: { color: GRID } },
          axisLabel: { color: AXIS, fontSize: 11, formatter: function (v) { return v.toFixed(2); } }
        },
        {
          gridIndex: 1,
          splitNumber: 2,
          axisLine: { show: false },
          axisTick: { show: false },
          splitLine: { show: false },
          axisLabel: {
            color: AXIS,
            fontSize: 10,
            formatter: function (v) {
              if (v >= 1e8) return (v / 1e8).toFixed(1) + "亿";
              if (v >= 1e4) return Math.round(v / 1e4) + "万";
              return v;
            }
          }
        }
      ],
      series: [
        {
          name: "价格",
          type: "line",
          data: prices,
          showSymbol: false,
          connectNulls: false,
          lineStyle: { width: 1.6, color: mainColor },
          itemStyle: { color: mainColor },
          areaStyle: {
            color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
              { offset: 0, color: mainColor + "33" },
              { offset: 1, color: mainColor + "05" }
            ])
          },
          markLine: preClose
            ? {
                silent: true,
                symbol: "none",
                animation: false,
                lineStyle: { color: "#b4b2a9", type: "dashed", width: 1 },
                label: {
                  show: true,
                  position: "insideEndTop",
                  formatter: "昨收 " + preClose.toFixed(2),
                  color: AXIS,
                  fontSize: 10
                },
                data: [{ yAxis: preClose }]
              }
            : undefined
        },
        {
          name: "均价",
          type: "line",
          data: avgs,
          showSymbol: false,
          connectNulls: false,
          lineStyle: { width: 1, color: "#c9952f" },
          itemStyle: { color: "#c9952f" }
        },
        {
          name: "成交量",
          type: "bar",
          xAxisIndex: 1,
          yAxisIndex: 1,
          data: volData,
          barWidth: "62%"
        }
      ]
    };

    return {
      option: option,
      title: d.name,
      meta:
        d.date + " · 分时 · " + (d.source === "eastmoney" ? "实时" : "已收盘") +
        (preClose ? " · 昨收 " + preClose.toFixed(2) : "")
    };
  }

  // ---------------------------------------------------------------- K 线
  function buildKline(d, opts) {
    opts = opts || {};
    var H = opts.height || 480;
    var bars = d.bars || [];
    if (!bars.length) return null;

    var cats = bars.map(function (b) { return b.d; });
    var ohlc = bars.map(function (b) { return [b.o, b.c, b.l, b.h]; });
    var closes = bars.map(function (b) { return b.c; });
    var vols = bars.map(function (b) {
      return { value: b.v, itemStyle: { color: b.c >= b.o ? UP : DOWN } };
    });
    var n = bars.length;
    var start = n > 130 ? (1 - 130 / n) * 100 : 0;
    var periodName = { day: "日K", week: "周K", month: "月K", year: "年K" }[d.period] || "日K";

    var volH = Math.min(110, Math.max(48, Math.round(H * 0.16)));
    var mainTop = 26;
    var mainH = H - mainTop - volH - 26 - 40;
    var volTop = mainTop + mainH + 26;

    var option = {
      animation: false,
      legend: {
        top: 0,
        right: 58,
        itemWidth: 14,
        itemHeight: 2,
        itemGap: 14,
        textStyle: { fontSize: 11, color: "#6a7280" },
        data: ["MA5", "MA10", "MA20"]
      },
      grid: [
        { left: 64, right: 58, top: mainTop, height: mainH },
        { left: 64, right: 58, top: volTop, height: volH }
      ],
      tooltip: Object.assign(tooltipBase(), {
        formatter: function (params) {
          var b = bars[params[0].dataIndex];
          if (!b) return "";
          var c = colorOf(b.p);
          return (
            "<div style='font-size:11px;color:#9aa1ad;margin-bottom:5px'>" + b.d + "</div>" +
            row("开盘", b.o.toFixed(2), b.o === b.c ? FLAT : b.o < b.c ? UP : DOWN) +
            row("收盘", b.c.toFixed(2), c) +
            row("最高", b.h.toFixed(2)) +
            row("最低", b.l.toFixed(2)) +
            row("涨跌", pctText(b.p), c) +
            row("成交额", amountText(b.a))
          );
        }
      }),
      axisPointer: { link: [{ xAxisIndex: "all" }] },
      xAxis: [
        {
          type: "category",
          gridIndex: 0,
          data: cats,
          boundaryGap: true,
          axisLine: { lineStyle: { color: LINE } },
          axisTick: { show: false },
          splitLine: { show: false },
          axisLabel: { show: false }
        },
        {
          type: "category",
          gridIndex: 1,
          data: cats,
          boundaryGap: true,
          axisLine: { lineStyle: { color: LINE } },
          axisTick: { show: false },
          splitLine: { show: false },
          axisLabel: { color: AXIS, fontSize: 11, hideOverlap: true }
        }
      ],
      yAxis: [
        {
          gridIndex: 0,
          scale: true,
          position: "left",
          axisLine: { show: false },
          axisTick: { show: false },
          splitLine: { lineStyle: { color: GRID } },
          axisLabel: {
            color: AXIS,
            fontSize: 11,
            formatter: function (v) { return v >= 1000 ? Math.round(v) : v.toFixed(0); }
          }
        },
        {
          gridIndex: 1,
          splitNumber: 2,
          axisLine: { show: false },
          axisTick: { show: false },
          splitLine: { show: false },
          axisLabel: {
            color: AXIS,
            fontSize: 10,
            formatter: function (v) {
              if (v >= 1e8) return (v / 1e8).toFixed(1) + "亿";
              if (v >= 1e4) return Math.round(v / 1e4) + "万";
              return v;
            }
          }
        }
      ],
      dataZoom: [
        { type: "inside", xAxisIndex: [0, 1], start: start, end: 100 },
        {
          type: "slider",
          xAxisIndex: [0, 1],
          start: start,
          end: 100,
          bottom: 8,
          height: 18,
          borderColor: "transparent",
          backgroundColor: ZOOM_BG,
          fillerColor: ZOOM_FILL,
          handleStyle: { color: "#fff", borderColor: "#c9ced8" },
          moveHandleStyle: { color: ZOOM_DATA_LINE },
          dataBackground: {
            lineStyle: { color: ZOOM_DATA_LINE },
            areaStyle: { color: ZOOM_DATA_AREA }
          },
          textStyle: { color: AXIS, fontSize: 10 }
        }
      ],
      series: [
        {
          name: "K线",
          type: "candlestick",
          data: ohlc,
          itemStyle: {
            color: UP,
            color0: DOWN,
            borderColor: UP,
            borderColor0: DOWN,
            borderWidth: 1
          }
        },
        {
          name: "MA5",
          type: "line",
          data: ma(closes, 5),
          showSymbol: false,
          smooth: true,
          lineStyle: { width: 1, color: "#c98b2e" },
          itemStyle: { color: "#c98b2e" }
        },
        {
          name: "MA10",
          type: "line",
          data: ma(closes, 10),
          showSymbol: false,
          smooth: true,
          lineStyle: { width: 1, color: "#2f6fed" },
          itemStyle: { color: "#2f6fed" }
        },
        {
          name: "MA20",
          type: "line",
          data: ma(closes, 20),
          showSymbol: false,
          smooth: true,
          lineStyle: { width: 1, color: "#8b5cf6" },
          itemStyle: { color: "#8b5cf6" }
        },
        {
          name: "成交量",
          type: "bar",
          xAxisIndex: 1,
          yAxisIndex: 1,
          data: vols,
          barWidth: "56%"
        }
      ]
    };

    return {
      option: option,
      title: d.name,
      meta: periodName + " · 共 " + d.total + " 根 · 截至 " + d.asOf
    };
  }

  function setTheme(theme) {
    if (theme === "dark") {
      GRID = "#232833";
      AXIS = "#7b8494";
      LINE = "#2b313d";
      TIP_BG = "rgba(22,25,31,0.96)";
      TIP_TEXT = "#e7e9ee";
      LABEL_COLOR = "#7b8494";
      VALUE_COLOR = "#e7e9ee";
      ZOOM_BG = "#1b202a";
      ZOOM_FILL = "rgba(122,167,245,0.18)";
      ZOOM_DATA_LINE = "#39414f";
      ZOOM_DATA_AREA = "#232935";
    } else {
      GRID = "#f0f1f4";
      AXIS = "#9aa1ad";
      LINE = "#e7e9ee";
      TIP_BG = "rgba(255,255,255,0.98)";
      TIP_TEXT = "#171a20";
      LABEL_COLOR = "#9aa1ad";
      VALUE_COLOR = "#171a20";
      ZOOM_BG = "#f7f8fa";
      ZOOM_FILL = "rgba(47,111,237,0.10)";
      ZOOM_DATA_LINE = "#dfe3ea";
      ZOOM_DATA_AREA = "#f2f4f8";
    }
  }

  function setScheme(scheme) {
    if (scheme === "us") {
      UP = "#0f9d63";
      DOWN = "#d03a3a";
    } else {
      UP = "#d03a3a";
      DOWN = "#0f9d63";
    }
  }

  global.MarketChartCore = {
    buildIntraday: buildIntraday,
    buildKline: buildKline,
    setScheme: setScheme,
    setTheme: setTheme,
    colorOf: colorOf,
    pctText: pctText,
    priceText: priceText,
    amountText: amountText,
    volText: volText,
    row: row,
    ma: ma,
    UP: UP,
    DOWN: DOWN,
    FLAT: FLAT
  };
})(window);
