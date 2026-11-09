/* <market-chart> Web Component
 *
 * 用法（一行引入即可，依赖会自动按需加载）：
 *   <script src="http://127.0.0.1:8080/static/widget.js"></script>
 *   <market-chart code="000001.SH" period="day" height="420"></market-chart>
 *
 * 属性：
 *   code        指数代码，如 000001.SH
 *   period      minute | day | week | month | year（默认 minute）
 *   height      组件总高度 px（默认 420）
 *   api-base    行情服务地址（默认取本脚本所在源）
 *   controls    是否显示内建周期切换，true | false（默认 true）
 *   poll        分时轮询秒数，0 表示不轮询（默认 30）
 *   colorscheme cn（涨红跌绿，默认）| us（涨绿跌红）
 *
 * 主题：CSS 变量 --mb-bg / --mb-text / --mb-line / --mb-sub / --mb-radius
 * 方法：el.refresh() / el.setCode(code) / el.setPeriod(period)
 * 事件：mb-loaded、mb-error
 */
(function () {
  "use strict";

  if (window.customElements && window.customElements.get("market-chart")) return;

  var SELF_SRC = (document.currentScript && document.currentScript.src) || "";
  var SELF_BASE = SELF_SRC ? new URL(SELF_SRC, location.href).origin : "";

  var TABS = [
    ["minute", "分时"],
    ["day", "日K"],
    ["week", "周K"],
    ["month", "月K"],
    ["year", "年K"]
  ];

  var depCache = {};

  function loadScript(src) {
    return new Promise(function (resolve, reject) {
      var s = document.createElement("script");
      s.src = src;
      s.async = false;
      s.onload = function () { resolve(); };
      s.onerror = function () { reject(new Error("资源加载失败：" + src)); };
      document.head.appendChild(s);
    });
  }

  function ensureDeps(base) {
    if (window.echarts && window.MarketChartCore) return Promise.resolve();
    var key = base || "__self__";
    if (!depCache[key]) {
      var root = base || SELF_BASE;
      depCache[key] = (window.echarts
        ? Promise.resolve()
        : loadScript(root + "/static/vendor/echarts.min.js")
      ).then(function () {
        if (window.MarketChartCore) return null;
        return loadScript(root + "/static/chart-core.js");
      });
    }
    return depCache[key];
  }

  var STYLE =
    ":host{display:block;" +
    "--mb-bg:#ffffff;--mb-text:#171a20;--mb-sub:#6a7280;--mb-line:#e7e9ee;--mb-radius:12px;" +
    "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif;" +
    "color:var(--mb-text);font-size:14px;-webkit-font-smoothing:antialiased}" +
    "*{box-sizing:border-box}" +
    ".wrap{background:var(--mb-bg);border:1px solid var(--mb-line);border-radius:var(--mb-radius);padding:14px 16px 10px}" +
    ".hd{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;margin-bottom:4px;min-height:28px}" +
    ".title{display:flex;align-items:baseline;gap:10px;min-width:0}" +
    ".name{font-size:15px;font-weight:500;white-space:nowrap}" +
    ".meta{font-size:12px;color:var(--mb-sub);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-variant-numeric:tabular-nums}" +
    ".tabs{display:inline-flex;background:#f3f4f7;border-radius:8px;padding:3px;gap:2px}" +
    ".tabs button{appearance:none;border:0;background:transparent;font:inherit;font-size:12px;color:var(--mb-sub);padding:4px 10px;border-radius:6px;cursor:pointer}" +
    ".tabs button:hover{color:var(--mb-text)}" +
    ".tabs button[aria-selected='true']{background:var(--mb-bg);color:var(--mb-text);box-shadow:0 1px 2px rgba(20,24,34,.08)}" +
    ".stage{position:relative;width:100%}" +
    ".cv{width:100%;height:100%}" +
    ".msg{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;" +
    "background:var(--mb-bg);color:var(--mb-sub);font-size:13px;text-align:center;padding:12px}" +
    ":host([theme='dark']){--mb-bg:#161a21;--mb-text:#e7e9ee;--mb-line:#262c36;--mb-sub:#8b93a3}" +
    ":host([theme='dark']) .tabs{background:#1e232c}" +
    ":host([theme='dark']) .tabs button:hover{color:#fff}" +
    ":host([theme='dark']) .tabs button[aria-selected='true']{background:#2a3140;color:#fff;box-shadow:none}";

  var MarketChart = (function () {
    var proto = Object.create(HTMLElement.prototype);

    function MarketChart() {
      var self = Reflect.construct(HTMLElement, [], MarketChart);
      self._shadow = self.attachShadow({ mode: "open" });
      self._shadow.innerHTML =
        "<style>" + STYLE + "</style>" +
        "<div class='wrap'><div class='hd'>" +
        "<div class='title'><span class='name'>--</span><span class='meta'></span></div>" +
        "<div class='tabs'></div></div>" +
        "<div class='stage'><div class='cv'></div><div class='msg'>加载中…</div></div></div>";
      self._nameEl = self._shadow.querySelector(".name");
      self._metaEl = self._shadow.querySelector(".meta");
      self._tabsEl = self._shadow.querySelector(".tabs");
      self._cv = self._shadow.querySelector(".cv");
      self._stage = self._shadow.querySelector(".stage");
      self._msgEl = self._shadow.querySelector(".msg");
      self._chart = null;
      self._timer = null;
      self._ready = false;
      return self;
    }

    MarketChart.prototype = proto;
    proto.constructor = MarketChart;

    Object.defineProperty(MarketChart, "observedAttributes", {
      get: function () {
        return ["code", "period", "height", "api-base", "controls", "poll", "colorscheme", "theme"];
      }
    });

    proto._attr = function (name, fallback) {
      var v = this.getAttribute(name);
      return v === null || v === "" ? fallback : v;
    };

    proto._base = function () {
      return this._attr("api-base", SELF_BASE).replace(/\/+$/, "");
    };

    proto._height = function () {
      var h = parseInt(this._attr("height", "420"), 10);
      return isNaN(h) || h < 240 ? 420 : h;
    };

    proto._code = function () {
      return this._attr("code", "000001.SH");
    };

    proto._period = function () {
      return this._attr("period", "minute");
    };

    proto._applyHeight = function () {
      this._stage.style.height = Math.max(180, this._height() - 46) + "px";
    };

    proto._showMsg = function (text) {
      this._msgEl.textContent = text;
      this._msgEl.style.display = "flex";
    };

    proto._hideMsg = function () {
      this._msgEl.style.display = "none";
    };

    proto._ensureChart = function () {
      if (this._chart) return this._chart;
      this._applyHeight();
      this._chart = echarts.init(this._cv, null, { renderer: "canvas" });
      return this._chart;
    };

    proto._buildTabs = function () {
      var self = this;
      if (this._attr("controls", "true") === "false") {
        this._tabsEl.style.display = "none";
        return;
      }
      this._tabsEl.style.display = "";
      if (!this._tabsEl.children.length) {
        TABS.forEach(function (pair) {
          var b = document.createElement("button");
          b.textContent = pair[1];
          b.dataset.period = pair[0];
          b.addEventListener("click", function () {
            self.setPeriod(pair[0]);
          });
          self._tabsEl.appendChild(b);
        });
      }
      var current = this._period();
      Array.prototype.forEach.call(this._tabsEl.children, function (b) {
        b.setAttribute("aria-selected", b.dataset.period === current ? "true" : "false");
      });
    };

    proto._bindResize = function () {
      var self = this;
      if (typeof ResizeObserver === "undefined") {
        window.addEventListener("resize", function () {
          if (self._chart) self._chart.resize();
        });
        return;
      }
      this._ro = new ResizeObserver(function () {
        if (self._chart) self._chart.resize();
      });
      this._ro.observe(this._stage);
    };

    proto._fail = function (msg, raw) {
      if (this._chart) {
        this._chart.dispose();
        this._chart = null;
      }
      this._showMsg(msg);
      this.dispatchEvent(
        new CustomEvent("mb-error", { detail: { message: msg, error: String(raw || "") }, bubbles: true })
      );
    };

    proto._load = function () {
      var self = this;
      if (!this._ready) return Promise.resolve();
      var code = this._code();
      var period = this._period();
      var url =
        period === "minute"
          ? "/api/intraday?code=" + encodeURIComponent(code)
          : "/api/kline?code=" + encodeURIComponent(code) +
            "&period=" + period + "&limit=1500";

      return fetch(this._base() + url, { cache: "no-store" })
        .then(function (r) { return r.json(); })
        .then(function (body) {
          if (!body.ok) throw new Error(body.error || "接口返回异常");
          var d = body.data;
          var built =
            period === "minute"
              ? window.MarketChartCore.buildIntraday(d, { height: self._height() })
              : window.MarketChartCore.buildKline(d, { height: self._height() });

          if (!built) {
            self._fail("该指数暂无数据");
            return;
          }
          var chart = self._ensureChart();
          self._hideMsg();
          self._nameEl.textContent = built.title;
          self._metaEl.textContent = built.meta;
          chart.setOption(built.option, true);
          chart.resize();
          self.dispatchEvent(
            new CustomEvent("mb-loaded", {
              detail: { code: code, period: period, meta: built.meta },
              bubbles: true
            })
          );
        })
        .catch(function (err) {
          self._fail("加载失败：" + err.message, err);
        });
    };

    proto._schedule = function () {
      var self = this;
      if (this._timer) clearInterval(this._timer);
      this._timer = null;
      var sec = parseInt(this._attr("poll", "30"), 10);
      if (!sec || this._period() !== "minute") return;
      this._timer = setInterval(function () {
        if (!document.hidden && self.isConnected) self._load();
      }, sec * 1000);
    };

    proto.connectedCallback = function () {
      var self = this;
      this._buildTabs();
      this._applyHeight();
      this._showMsg("加载中…");

      ensureDeps(this._base())
        .then(function () {
          if (window.MarketChartCore.setScheme) {
            window.MarketChartCore.setScheme(self._attr("colorscheme", "cn"));
          }
          if (window.MarketChartCore.setTheme) {
            window.MarketChartCore.setTheme(self._attr("theme", "light"));
          }
          self._ready = true;
          self._bindResize();
          self._schedule();
          return self._load();
        })
        .catch(function (err) {
          self._fail(err.message, err);
        });
    };

    proto.disconnectedCallback = function () {
      if (this._timer) clearInterval(this._timer);
      this._timer = null;
      if (this._ro) {
        this._ro.disconnect();
        this._ro = null;
      }
      if (this._chart) {
        this._chart.dispose();
        this._chart = null;
      }
      this._ready = false;
    };

    proto.attributeChangedCallback = function (name) {
      if (!this._ready) return;
      if (name === "height") {
        this._applyHeight();
        if (this._chart) this._chart.resize();
        return;
      }
      if (name === "colorscheme" && window.MarketChartCore) {
        window.MarketChartCore.setScheme(this._attr("colorscheme", "cn"));
        this._load();
        return;
      }
      if (name === "theme" && window.MarketChartCore) {
        window.MarketChartCore.setTheme(this._attr("theme", "light"));
        this._load();
        return;
      }
      if (name === "code" || name === "period" || name === "api-base") {
        this._buildTabs();
        this._schedule();
        this._load();
      }
    };

    proto.refresh = function () {
      return this._load();
    };

    proto.setCode = function (code) {
      this.setAttribute("code", code);
    };

    proto.setPeriod = function (period) {
      this.setAttribute("period", period);
    };

    return MarketChart;
  })();

  window.customElements.define("market-chart", MarketChart);
})();
