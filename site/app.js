(function () {
  "use strict";
  var base = document.body.getAttribute("data-base") || "";
  var dataPromise = null;

  function loadData() {
    if (!dataPromise) {
      dataPromise = fetch(base + "data/companies.json").then(function (r) { return r.json(); });
    }
    return dataPromise;
  }

  function esc(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function manYen(v) {
    return typeof v === "number" ? "約" + Math.round(v / 10000).toLocaleString() + "万円" : "非公表";
  }
  function pct1(v) {
    return typeof v === "number" ? (v * 100).toFixed(1) + "%" : "非公表";
  }
  function num0(v, unit) {
    return typeof v === "number" ? v.toLocaleString() + (unit || "") : "非公表";
  }

  // ---- 検索窓（ヘッダー・フッター共通） ----
  function initSearch() {
    var widgets = document.querySelectorAll(".site-search");
    if (!widgets.length) return;
    loadData().then(function (data) {
      widgets.forEach(function (w) { wireWidget(w, data.companies); });
    });
  }

  function wireWidget(widget, companies) {
    var input = widget.querySelector(".search-input");
    var results = widget.querySelector(".search-results");
    var activeIndex = -1;

    function render(matches) {
      if (!matches.length) {
        results.innerHTML = '<div class="sr-empty">一致する企業がありません</div>';
        results.hidden = false;
        return;
      }
      results.innerHTML = matches.map(function (c) {
        return '<a href="' + base + "kigyou/" + c.slug + '.html"><span>' + esc(c.name) +
          '</span><span class="sr-group">' + esc(c.group) + "</span></a>";
      }).join("");
      results.hidden = false;
      activeIndex = -1;
    }

    function search(q) {
      q = q.trim().toLowerCase();
      if (!q) { results.hidden = true; results.innerHTML = ""; return; }
      var matches = companies.filter(function (c) {
        return c.name.toLowerCase().indexOf(q) !== -1 ||
          c.slug.toLowerCase().indexOf(q) !== -1 ||
          c.group.toLowerCase().indexOf(q) !== -1;
      }).slice(0, 8);
      render(matches);
    }

    input.addEventListener("input", function () { search(input.value); });
    input.addEventListener("keydown", function (ev) {
      var items = results.querySelectorAll("a");
      if (ev.key === "ArrowDown" && items.length) {
        ev.preventDefault();
        activeIndex = Math.min(activeIndex + 1, items.length - 1);
        items.forEach(function (a, i) { a.classList.toggle("active", i === activeIndex); });
        items[activeIndex].scrollIntoView({ block: "nearest" });
      } else if (ev.key === "ArrowUp" && items.length) {
        ev.preventDefault();
        activeIndex = Math.max(activeIndex - 1, 0);
        items.forEach(function (a, i) { a.classList.toggle("active", i === activeIndex); });
      } else if (ev.key === "Enter") {
        if (activeIndex >= 0 && items[activeIndex]) {
          window.location.href = items[activeIndex].href;
        } else if (items.length) {
          window.location.href = items[0].href;
        }
      } else if (ev.key === "Escape") {
        results.hidden = true;
        input.blur();
      }
    });
    input.addEventListener("blur", function () {
      setTimeout(function () { results.hidden = true; }, 150);
    });
    input.addEventListener("focus", function () {
      if (input.value.trim()) search(input.value);
    });
  }

  // ---- 企業を選んで比較するページ ----
  function initCompare() {
    var root = document.getElementById("compare-app");
    if (!root) return;
    var resultEl = document.getElementById("compare-result");
    var countEl = document.getElementById("compare-count");
    var clearBtn = document.getElementById("compare-clear");
    var checkboxes = root.querySelectorAll('input[type="checkbox"][data-slug]');

    loadData().then(function (data) {
      var bySlug = {};
      data.companies.forEach(function (c) { bySlug[c.slug] = c; });

      function selectedSlugs() {
        return Array.prototype.slice.call(checkboxes)
          .filter(function (cb) { return cb.checked; })
          .map(function (cb) { return cb.dataset.slug; });
      }

      function hbar(rows, fmt, scalePct) {
        var vals = rows.map(function (r) { return r[2]; }).filter(function (v) { return v !== null && v !== undefined; });
        if (!vals.length) return '<p><span class="na">非公表</span></p>';
        var max = Math.max.apply(null, vals) || 1;
        var sorted = rows.slice().sort(function (a, b) {
          var av = a[2], bv = b[2];
          if (av === null || av === undefined) return 1;
          if (bv === null || bv === undefined) return -1;
          return bv - av;
        });
        var html = '<div class="hbars">';
        sorted.forEach(function (r) {
          var name = r[0], href = r[1], v = r[2];
          if (v === null || v === undefined) {
            html += '<div class="hbar-row"><span class="hbar-name" title="' + esc(name) +
              '"><a href="' + href + '">' + esc(name) + '</a></span>' +
              '<span class="hbar-track"></span><span class="hbar-val na">非公表</span></div>';
          } else {
            var w = scalePct ? v * 100 : (v / max * 100);
            html += '<div class="hbar-row"><span class="hbar-name" title="' + esc(name) +
              '"><a href="' + href + '">' + esc(name) + '</a></span>' +
              '<span class="hbar-track"><span class="hbar-fill" style="width:' + Math.max(w, 1.5).toFixed(1) + '%"></span></span>' +
              '<span class="hbar-val">' + fmt(v) + "</span></div>";
          }
        });
        html += "</div>";
        return html;
      }

      function render() {
        var slugs = selectedSlugs();
        if (countEl) countEl.textContent = slugs.length + "社選択中";

        var url = new URL(window.location.href);
        if (slugs.length) { url.searchParams.set("c", slugs.join(",")); } else { url.searchParams.delete("c"); }
        window.history.replaceState(null, "", url.pathname + url.search);

        if (!slugs.length) {
          resultEl.innerHTML = '<p class="lead">上のリストから企業を選ぶと、ここに横比較が表示されます。</p>';
          return;
        }
        var picked = slugs.map(function (s) { return bySlug[s]; }).filter(Boolean);
        var rowsFor = function (key) {
          return picked.map(function (c) { return [c.name, base + "kigyou/" + c.slug + ".html", c[key]]; });
        };
        var tableRows = picked.map(function (c) {
          return "<tr><th scope=\"row\"><a href=\"" + base + "kigyou/" + c.slug + ".html\">" + esc(c.name) + "</a></th>" +
            "<td>" + esc((c.period || "").slice(0, 7)) + "期</td>" +
            "<td>" + manYen(c.salary) + "</td>" +
            "<td>" + (typeof c.age === "number" ? c.age.toFixed(1) + "歳" : "非公表") + "</td>" +
            "<td>" + (typeof c.tenure === "number" ? c.tenure.toFixed(1) + "年" : "非公表") + "</td>" +
            "<td>" + num0(c.employees_single, "人") + "</td>" +
            "<td>" + num0(c.employees_consolidated, "人") + "</td>" +
            "<td>" + pct1(c.female_manager_ratio) + "</td>" +
            "<td>" + pct1(c.wage_ratio_all) + "</td></tr>";
        }).join("");

        resultEl.innerHTML =
          "<section><h2>平均年間給与</h2>" + hbar(rowsFor("salary"), manYen, false) + "</section>" +
          "<section><h2>女性管理職比率</h2>" + hbar(rowsFor("female_manager_ratio"), pct1, true) + "</section>" +
          "<section><h2>男女の賃金の差異（全労働者）</h2>" + hbar(rowsFor("wage_ratio_all"), pct1, true) + "</section>" +
          '<section><h2>詳細データ</h2><div class="scroll"><table class="grid rank"><thead><tr>' +
          "<th>会社</th><th>決算期</th><th>平均年間給与</th><th>平均年齢</th><th>平均勤続年数</th>" +
          "<th>従業員数(単体)</th><th>従業員数(連結)</th><th>女性管理職比率</th><th>男女の賃金の差異</th>" +
          "</tr></thead><tbody>" + tableRows + "</tbody></table></div></section>";
      }

      checkboxes.forEach(function (cb) { cb.addEventListener("change", render); });
      if (clearBtn) {
        clearBtn.addEventListener("click", function () {
          checkboxes.forEach(function (cb) { cb.checked = false; });
          render();
        });
      }

      var params = new URLSearchParams(window.location.search);
      var pre = params.get("c");
      if (pre) {
        var want = {};
        pre.split(",").forEach(function (s) { want[s] = true; });
        checkboxes.forEach(function (cb) {
          if (want[cb.dataset.slug]) {
            cb.checked = true;
            var det = cb.closest("details");
            if (det) det.open = true;
          }
        });
      }
      render();
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    initSearch();
    initCompare();
  });
})();
