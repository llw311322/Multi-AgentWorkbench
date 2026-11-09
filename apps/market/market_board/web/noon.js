/* 午盘复盘页：纯渲染，所有数字来自 /api/review，前端不做任何计算。 */
(function () {
  var state = { session: "noon", date: null, chart: null };

  function $(id) {
    return document.getElementById(id);
  }

  function num(v, nd) {
    if (v === null || v === undefined || isNaN(v)) return "--";
    return Number(v).toFixed(nd === undefined ? 2 : nd);
  }

  function yi(v) {
    return v === null || v === undefined ? "--" : (v / 1e8).toFixed(0);
  }

  function pctClass(v) {
    if (v === null || v === undefined) return "";
    return v > 0 ? "up" : v < 0 ? "down" : "flat";
  }

  function signed(v, nd) {
    if (v === null || v === undefined) return "--";
    return (v >= 0 ? "+" : "") + Number(v).toFixed(nd === undefined ? 2 : nd);
  }

  // ------------------------------------------------------------ 指标卡
  function buildMetrics(d) {
    var sh = null;
    d.indices.forEach(function (x) {
      if (x.code === "000001.SH") sh = x;
    });
    sh = sh || d.indices[0];
    var b = d.breadth || {};
    var stale = !!b.staleAtPoint;
    var amountLabel = d.session === "noon" ? "沪市半日成交" : "沪市成交";

    var cards = [
      {
        k: sh.name,
        v: num(sh.price),
        s: signed(sh.pct) + "%",
        cls: pctClass(sh.pct),
      },
      {
        k: amountLabel,
        v: yi(sh.amount),
        unit: "亿元",
        s: sh.energy ? "量能 " + num(sh.energy) + " 倍" : "",
      },
      {
        k: "量比",
        v: num(sh.volRatio),
        tip: "当日每分钟均量 ÷ 近 5 日每分钟均量",
        s: sh.volRatio ? (sh.volRatio > 1 ? "高于近期" : "低于近期") : "",
      },
      {
        k: "全市场换手率",
        v: b.turnover === undefined || b.turnover === null ? "--" : num(b.turnover),
        unit: "%",
        tip: "全市场成交额 ÷ 流通市值",
        s: stale
          ? "收盘口径，非时点值"
          : b.turnover
          ? b.turnover >= 3
            ? "偏过热"
            : b.turnover >= 2
            ? "活跃"
            : b.turnover >= 1
            ? "温和"
            : "清淡"
          : "",
      },
      {
        k: "涨跌比",
        v: b.adRatio === undefined || b.adRatio === null ? "--" : num(b.adRatio),
        tip: "上涨家数 ÷ 下跌家数",
        s: b.upRatio === null || b.upRatio === undefined ? "" : "上涨占比 " + num(b.upRatio, 1) + "%",
      },
      {
        k: "涨停 / 跌停",
        v: (b.limitUp === undefined ? "--" : b.limitUp) + " / " + (b.limitDown === undefined ? "--" : b.limitDown),
        s: b.up5 === undefined ? "" : "涨超5% " + b.up5 + " 家",
      },
      {
        k: "振幅",
        v: num(sh.amplitude),
        unit: "%",
        s: "最高 " + num(sh.high) + " / 最低 " + num(sh.low),
      },
      {
        k: "VWAP 偏离",
        v: signed(sh.vwapDev),
        unit: "%",
        tip: "现价相对成交量加权均价的位置",
        s: "均价 " + num(sh.vwap),
        cls: pctClass(sh.vwapDev),
      },
    ];

    return cards
      .map(function (c) {
        return (
          '<div class="metric">' +
          '<div class="metric-k" title="' + (c.tip || "") + '">' + c.k + "</div>" +
          '<div class="metric-v ' + (c.cls || "") + '">' + c.v +
          (c.unit ? '<span class="metric-u">' + c.unit + "</span>" : "") +
          "</div>" +
          '<div class="metric-s">' + (c.s || "&nbsp;") + "</div>" +
          "</div>"
        );
      })
      .join("");
  }

  // ------------------------------------------------------------ 分布图
  function renderDist(d) {
    var b = d.breadth;
    var el = $("distChart");
    if (!state.chart) state.chart = echarts.init(el, null, { renderer: "canvas" });

    if (!b || !b.distribution) {
      state.chart.clear();
      state.chart.setOption({
        title: {
          text: "该时点无全市场广度数据",
          left: "center",
          top: "middle",
          textStyle: { color: "#9aa1ad", fontSize: 12, fontWeight: "normal" },
        },
      });
      return;
    }

    var labels = b.distribution.map(function (x) {
      return x.label;
    });
    var data = b.distribution.map(function (x, i) {
      var color = i < 5 ? "#0f9d63" : i === 5 ? "#9aa1ad" : "#d03a3a";
      return { value: x.count, itemStyle: { color: color, borderRadius: [2, 2, 0, 0] } };
    });

    state.chart.setOption(
      {
        grid: { left: 4, right: 8, top: 24, bottom: 6, containLabel: true },
        tooltip: {
          trigger: "axis",
          axisPointer: { type: "shadow" },
          backgroundColor: "rgba(255,255,255,0.98)",
          borderColor: "#e7e9ee",
          textStyle: { color: "#171a20", fontSize: 12 },
          formatter: function (p) {
            return p[0].axisValue + "：<b>" + p[0].value + "</b> 家";
          },
        },
        xAxis: {
          type: "category",
          data: labels,
          axisLine: { lineStyle: { color: "#e7e9ee" } },
          axisTick: { show: false },
          axisLabel: { color: "#9aa1ad", fontSize: 9, interval: 0 },
        },
        yAxis: {
          type: "value",
          splitLine: { lineStyle: { color: "#f0f1f4" } },
          axisLabel: { color: "#9aa1ad", fontSize: 10 },
        },
        series: [
          {
            type: "bar",
            data: data,
            barWidth: "58%",
            label: {
              show: true,
              position: "top",
              fontSize: 9,
              color: "#6a7280",
              formatter: function (p) {
                return p.value > 0 ? p.value : "";
              },
            },
          },
        ],
      },
      true
    );
  }

  // ------------------------------------------------------------ 分市场表
  function renderBoards(d) {
    var tbody = $("boardTable").querySelector("tbody");
    var b = d.breadth;
    if (!b || !b.boards || !b.boards.length) {
      tbody.innerHTML = '<tr><td colspan="6" class="muted center">无数据</td></tr>';
      return;
    }
    tbody.innerHTML = b.boards
      .filter(function (r) {
        return r.count > 0;
      })
      .map(function (r) {
        return (
          "<tr>" +
          "<td>" + r.name + "</td>" +
          '<td class="num">' + r.count + "</td>" +
          '<td class="num">' + r.up + " / " + r.down + "</td>" +
          '<td class="num ' + pctClass(r.pct) + '">' + signed(r.pct) + "%</td>" +
          '<td class="num">' + num(r.turnover) + "%</td>" +
          '<td class="num">' + yi(r.amount) + " 亿</td>" +
          "</tr>"
        );
      })
      .join("");
  }

  // ------------------------------------------------------------ 分节解读
  function renderSections(d) {
    var box = $("sections");
    var html = d.sections
      .map(function (s) {
        return '<article class="sec"><h2>' + s.title + "</h2><p>" + s.text + "</p></article>";
      })
      .join("");

    if (d.notes && d.notes.length) {
      html +=
        '<div class="notes"><h3>口径提示</h3><ul>' +
        d.notes
          .map(function (n) {
            return "<li>" + n + "</li>";
          })
          .join("") +
        "</ul></div>";
    }
    box.innerHTML = html;
  }

  function renderGlossary(d) {
    var g = d.glossary || {};
    $("glossary").innerHTML = Object.keys(g)
      .map(function (k) {
        return "<div class='gloss-item'><dt>" + k + "</dt><dd>" + g[k] + "</dd></div>";
      })
      .join("");
  }

  // ------------------------------------------------------------ 主流程
  function render(d) {
    $("heroTag").textContent = d.tradeDate + " · " + d.label + " " + (d.cutoff || "");
    $("heroTime").textContent = d.generatedAt ? "生成于 " + d.generatedAt : "";

    if (!d.available) {
      $("headline").textContent = d.message || "暂无复盘数据";
      $("metrics").innerHTML = "";
      $("sections").innerHTML = "";
      $("stateTag").textContent = "无数据";
      state.chart && state.chart.clear();
      return;
    }

    $("headline").textContent = d.headline;
    $("metrics").innerHTML = buildMetrics(d);
    renderDist(d);
    renderBoards(d);
    renderSections(d);
    renderGlossary(d);
    $("distMeta").textContent =
      d.breadth && d.breadth.traded ? d.breadth.traded + " 只可交易个股" : "全市场个股家数";
    $("sectionMeta").textContent = d.breadthSource === "archive" ? "读取存档" : "现场生成";
    $("stateTag").textContent = d.label;
    $("stateTag").className = "state" + (d.session === "noon" ? " live" : " done");
    $("footnote").textContent = d.disclaimer || "";
    if (d.tradeDate) $("datePick").value = d.tradeDate;
  }

  function load(refresh) {
    var url = "/api/review?session=" + state.session;
    if (state.date) url += "&date=" + state.date;
    if (refresh) url += "&refresh=1";
    $("headline").textContent = "加载中…";
    fetch(url)
      .then(function (r) {
        return r.json();
      })
      .then(function (res) {
        if (!res.ok) throw new Error(res.error || "接口异常");
        render(res.data);
      })
      .catch(function (e) {
        $("headline").textContent = "加载失败：" + e.message;
        $("stateTag").textContent = "异常";
      });
  }

  function bind() {
    $("sessionTabs").addEventListener("click", function (e) {
      var btn = e.target.closest("button");
      if (!btn) return;
      state.session = btn.dataset.session;
      Array.prototype.forEach.call(this.querySelectorAll("button"), function (b) {
        b.classList.toggle("active", b === btn);
      });
      load(false);
    });

    $("datePick").addEventListener("change", function () {
      state.date = this.value || null;
      load(false);
    });

    $("refreshBtn").addEventListener("click", function () {
      load(true);
    });

    window.addEventListener("resize", function () {
      state.chart && state.chart.resize();
    });
  }

  function init() {
    var q = new URLSearchParams(location.search);
    if (q.get("session") === "close") {
      state.session = "close";
      Array.prototype.forEach.call($("sessionTabs").querySelectorAll("button"), function (b) {
        b.classList.toggle("active", b.dataset.session === "close");
      });
    }
    if (q.get("date")) state.date = q.get("date");
    bind();
    load(false);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
