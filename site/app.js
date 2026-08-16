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

  function logoImg(domain, size) {
    size = size || 16;
    if (!domain) return "";
    return '<img class="co-logo" src="https://www.google.com/s2/favicons?domain=' + encodeURIComponent(domain) +
      "&sz=" + (size * 2) + '" width="' + size + '" height="' + size + '" alt="">';
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
        return '<a href="' + base + "kigyou/" + c.slug + '.html">' + logoImg(c.domain) + '<span>' + esc(c.name) +
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
          var name = r[0], href = r[1], v = r[2], domain = r[3];
          if (v === null || v === undefined) {
            html += '<div class="hbar-row"><span class="hbar-name" title="' + esc(name) +
              '"><a href="' + href + '">' + logoImg(domain) + esc(name) + '</a></span>' +
              '<span class="hbar-track"></span><span class="hbar-val na">非公表</span></div>';
          } else {
            var w = scalePct ? v * 100 : (v / max * 100);
            html += '<div class="hbar-row"><span class="hbar-name" title="' + esc(name) +
              '"><a href="' + href + '">' + logoImg(domain) + esc(name) + '</a></span>' +
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
          return picked.map(function (c) { return [c.name, base + "kigyou/" + c.slug + ".html", c[key], c.domain]; });
        };
        var tableRows = picked.map(function (c) {
          return "<tr><th scope=\"row\"><a href=\"" + base + "kigyou/" + c.slug + ".html\">" + logoImg(c.domain) + esc(c.name) + "</a></th>" +
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

  // ---- 就活の軸診断（shindan.html でのみ動く） ----
  // 回答はどこにも送らない。fetch するのは自サイトの静的JSONだけ。
  function initShindan() {
    var root = document.getElementById("shindan");
    if (!root) return;

    var D = null, qi = 0, answers = [];
    var elQuiz = root.querySelector(".sd-quiz");
    var elIntro = root.querySelector(".sd-intro");
    var elRes = root.querySelector(".sd-result");
    var btns = root.querySelectorAll(".sd-choice");
    var back = root.querySelector(".sd-back");

    function show(sec) {
      [elIntro, elQuiz, elRes].forEach(function (s) { s.hidden = s !== sec; });
      root.setAttribute("data-state", sec === elIntro ? "intro" : sec === elQuiz ? "quiz" : "result");
      if (sec !== elIntro) sec.scrollIntoView({ behavior: "smooth", block: "start" });
    }

    function paintQuestion(dir) {
      var q = D.questions[qi];
      root.querySelector(".sd-no").textContent = qi + 1;
      root.querySelector(".sd-bar").style.width = ((qi) / D.questions.length * 100) + "%";
      btns[0].querySelector("span").textContent = q.at;
      btns[1].querySelector("span").textContent = q.bt;
      back.hidden = qi === 0;
      var wrap = root.querySelector(".sd-choices");
      wrap.classList.remove("in-l", "in-r");
      void wrap.offsetWidth;                       // アニメーションを再生させるための強制リフロー
      wrap.classList.add(dir === "back" ? "in-r" : "in-l");
    }

    function answer(side) {
      answers[qi] = side;
      qi++;
      if (qi >= D.questions.length) { result(); return; }
      paintQuestion();
    }

    function weights() {
      var w = {};
      D.axes.forEach(function (a) { w[a.key] = 0; });
      answers.forEach(function (side, i) {
        var q = D.questions[i];
        w[side === "a" ? q.a : q.b] += 1;
      });
      return w;
    }

    var FMT = {
      pay: function (v) { return manYen(v); },
      tenure: function (v) { return v.toFixed(1) + "年"; },
      women: pct1, wagegap: pct1, global: pct1, stability: pct1,
      scale: function (v) { return num0(v, "人"); },
      growth: function (v) { return (v * 100 >= 0 ? "+" : "") + (v * 100).toFixed(1) + "%"; }
    };

    function result() {
      root.querySelector(".sd-bar").style.width = "100%";
      var w = weights();
      var axes = D.axes.slice().sort(function (a, b) { return w[b.key] - w[a.key]; });
      var maxw = Math.max.apply(null, D.axes.map(function (a) { return w[a.key]; })) || 1;

      root.querySelector(".sd-axes").innerHTML = axes.map(function (a, i) {
        var pctw = Math.round(w[a.key] / maxw * 100);
        return '<div class="sd-axis" style="--i:' + i + '">' +
          '<span class="sd-axis-name">' + esc(a.label) + "</span>" +
          '<span class="sd-axis-bar"><i style="width:' + pctw + '%"></i></span>' +
          '<span class="sd-axis-val">' + w[a.key] + "/3</span></div>";
      }).join("");

      var top = axes.filter(function (a) { return w[a.key] > 0; }).slice(0, 3)
        .map(function (a) { return a.label; });
      var zero = axes.filter(function (a) { return w[a.key] === 0; }).map(function (a) { return a.label; });
      root.querySelector(".sd-axes-note").innerHTML =
        "12問のうち、あなたが選んだ回数です。上位は<b>" + esc(top.join("・")) + "</b>。" +
        (zero.length ? "一度も選ばなかったのは" + esc(zero.join("・")) + "でした。" : "") +
        "この重みで下の順位を出しています。";

      var sel = root.querySelector(".sd-group");
      if (sel.options.length <= 1) {
        var groups = {};
        D.companies.forEach(function (c) { groups[c.g] = 1; });
        Object.keys(groups).sort().forEach(function (g) {
          var o = document.createElement("option"); o.value = g; o.textContent = g; sel.appendChild(o);
        });
      }
      renderTable(w);
      show(elRes);
    }

    function renderTable(w) {
      var noHd = root.querySelector(".sd-nohd").checked;
      var group = root.querySelector(".sd-group").value;
      var keys = D.axes.map(function (a) { return a.key; }).filter(function (k) { return w[k] > 0; });
      var labelOf = {}; D.axes.forEach(function (a) { labelOf[a.key] = a.short; });

      var rows = D.companies.filter(function (c) {
        if (noHd && c.h) return false;
        if (group && c.g !== group) return false;
        return keys.some(function (k) { return c.p[k] !== undefined; });
      }).map(function (c) {
        var sum = 0, wsum = 0, used = [];
        keys.forEach(function (k) {
          if (c.p[k] === undefined) return;        // 欠損は平均で埋めず、その軸を外す
          sum += c.p[k] * w[k]; wsum += w[k]; used.push(k);
        });
        var score = wsum ? sum / wsum : 0;
        // 「なぜ上位なのか」＝重み×順位が大きい軸を2つ出す
        var why = used.slice().sort(function (a, b) { return c.p[b] * w[b] - c.p[a] * w[a]; }).slice(0, 2);
        return { c: c, score: score, used: used.length, why: why };
      }).sort(function (a, b) { return b.score - a.score; }).slice(0, 50);

      root.querySelector(".sd-table tbody").innerHTML = rows.map(function (r, i) {
        var why = r.why.map(function (k) {
          var v = r.c.v[k];
          var t = (FMT[k] && typeof v === "number") ? FMT[k](v) : "";
          return '<span class="sd-why">' + esc(labelOf[k]) + (t ? " " + esc(t) : "") + "</span>";
        }).join(" ");
        return '<tr style="--i:' + i + '"><td class="rank-no">' + (i + 1) + "</td>" +
          '<th scope="row"><a href="' + base + "kigyou/" + esc(r.c.s) + '.html">' + esc(r.c.n) + "</a></th>" +
          '<td><span class="sd-score"><i style="width:' + Math.round(r.score * 100) + '%"></i></span>' +
          Math.round(r.score * 100) + "</td>" +
          "<td>" + why + '<span class="sd-used">' + r.used + "軸で評価</span></td>" +
          '<td class="small">' + esc(r.c.g) + "</td></tr>";
      }).join("");
    }

    root.querySelector(".sd-start").addEventListener("click", function () {
      fetch(base + "data/shindan.json").then(function (r) { return r.json(); }).then(function (d) {
        D = d; qi = 0; answers = []; show(elQuiz); paintQuestion();
      });
    });
    btns.forEach(function (b) {
      b.addEventListener("click", function () { answer(b.getAttribute("data-side")); });
    });
    back.addEventListener("click", function () { if (qi > 0) { qi--; paintQuestion("back"); } });
    root.querySelector(".sd-retry").addEventListener("click", function () {
      qi = 0; answers = []; show(elQuiz); paintQuestion();
    });
    root.querySelector(".sd-nohd").addEventListener("change", function () { renderTable(weights()); });
    root.querySelector(".sd-group").addEventListener("change", function () { renderTable(weights()); });
  }

  // ---- スクロールで現れる（見えたときに一度だけ） ----
  // 隠すのは html.js-anim が付いている間だけ。IntersectionObserver が
  // 何らかの理由で発火しない環境（描画されないタブなど）に備えて、
  // 一定時間後に必ず全部を表示する安全網を置く。本文が読めないほうが害が大きい。
  //
  // threshold は必ず 0 にする。**割合で指定すると背の高い要素が永久に隠れる。**
  // トップページの掲載企業欄は高さ7万pxあり、threshold 0.02 は「1,400px以上見えたら」
  // という意味になって、画面の高さを超えるため一度も満たされなかった。
  // 同じ理由で、画面より背の高い要素はそもそも隠さない（現れる演出の意味もない）。
  function initReveal() {
    if (!("IntersectionObserver" in window)) return;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    var all = document.querySelectorAll("main section, main .scroll, main h1, main .lead");
    if (!all.length) return;
    var vh = window.innerHeight || 800;
    var targets = [];
    for (var i = 0; i < all.length; i++) {
      if (all[i].getBoundingClientRect().height <= vh * 1.5) targets.push(all[i]);
    }
    if (!targets.length) return;
    var root = document.documentElement;
    root.classList.add("js-anim");
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        if (en.isIntersecting) { en.target.classList.add("is-in"); io.unobserve(en.target); }
      });
    }, { rootMargin: "0px 0px -8% 0px", threshold: 0 });
    targets.forEach(function (t) { t.classList.add("reveal"); io.observe(t); });
    // 安全網。2.5秒後、まだ隠れたままで画面内に入っている要素は Observer が
    // 働いていないということなので表示に倒す（1つでも発火していれば、で判定しない。
    // 上部だけ発火して下が永久に隠れる、という今回の壊れ方を検出できないため）
    setTimeout(function () {
      var stuck = targets.filter(function (t) {
        var r = t.getBoundingClientRect();
        return !t.classList.contains("is-in") && r.top < vh && r.bottom > 0;
      });
      if (!stuck.length) return;
      root.classList.remove("js-anim");
      targets.forEach(function (t) { t.classList.add("is-in"); });
      io.disconnect();
    }, 2500);
  }

  // ---- ヘッダーを縮める・読了バー ----
  function initChrome() {
    var head = document.querySelector("header.site");
    var bar = document.createElement("div");
    bar.className = "read-bar";
    document.body.appendChild(bar);
    var ticking = false;
    function onScroll() {
      if (ticking) return;
      ticking = true;
      requestAnimationFrame(function () {
        var y = window.pageYOffset || document.documentElement.scrollTop;
        if (head) head.classList.toggle("is-stuck", y > 12);
        var h = document.documentElement.scrollHeight - window.innerHeight;
        bar.style.transform = "scaleX(" + (h > 0 ? Math.min(1, y / h) : 0) + ")";
        ticking = false;
      });
    }
    window.addEventListener("scroll", onScroll, { passive: true });
    onScroll();
  }

  document.addEventListener("DOMContentLoaded", function () {
    initSearch();
    initCompare();
    initShindan();
    initReveal();
    initChrome();
  });
})();
