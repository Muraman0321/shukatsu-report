(function () {
  "use strict";
  var base = document.body.getAttribute("data-base") || "";
  var dataPromise = null;
  var koumuPromise = null;

  function loadData() {
    if (!dataPromise) {
      dataPromise = fetch(base + "data/companies.json").then(function (r) { return r.json(); });
    }
    return dataPromise;
  }

  // 官公庁・政府系（data/koumu/、koumu.py が書き出す）。検索窓だけが使うので
  // loadData() とは別キャッシュにし、企業データ側（比較ページ等）に影響を与えない。
  function loadKoumu() {
    if (!koumuPromise) {
      // koumu.json が無くても（官公庁データ未生成等）検索全体を止めない
      koumuPromise = fetch(base + "data/koumu.json")
        .then(function (r) { return r.ok ? r.json() : []; })
        .catch(function () { return []; });
    }
    return koumuPromise;
  }

  function esc(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  // Google等のfavicon代行はドメインによって16x16固定で返ってくることを確認済みで、
  // 拡大表示すると粗くなるため使わない。実物のアイコンが無い会社は何も出さない。
  function logoImg(icon, size) {
    size = size || 16;
    if (!icon) return "";
    return '<img class="co-logo" src="' + icon + '" width="' + size + '" height="' + size + '" alt="">';
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
    Promise.all([loadData(), loadKoumu()]).then(function (results) {
      widgets.forEach(function (w) { wireWidget(w, results[0].companies, results[1]); });
    });
  }

  function wireWidget(widget, companies, koumu) {
    var input = widget.querySelector(".search-input");
    var results = widget.querySelector(".search-results");
    var activeIndex = -1;

    function render(matches) {
      if (!matches.length) {
        results.innerHTML = '<div class="sr-empty">一致する企業・機関がありません</div>';
        results.hidden = false;
        return;
      }
      results.innerHTML = matches.map(function (m) {
        return '<a href="' + m.href + '">' + logoImg(m.icon) + '<span>' + esc(m.name) +
          '</span><span class="sr-group">' + esc(m.group) + "</span></a>";
      }).join("");
      results.hidden = false;
      activeIndex = -1;
    }

    function search(q) {
      q = q.trim().toLowerCase();
      if (!q) { results.hidden = true; results.innerHTML = ""; return; }
      var companyMatches = companies.filter(function (c) {
        return c.name.toLowerCase().indexOf(q) !== -1 ||
          c.slug.toLowerCase().indexOf(q) !== -1 ||
          c.group.toLowerCase().indexOf(q) !== -1;
      }).map(function (c) {
        return { name: c.name, icon: c.icon, group: c.group, href: base + "kigyou/" + c.slug + ".html" };
      });
      var koumuMatches = koumu.filter(function (e) {
        return e.name.toLowerCase().indexOf(q) !== -1 ||
          e.slug.toLowerCase().indexOf(q) !== -1 ||
          e.group.toLowerCase().indexOf(q) !== -1 ||
          (e.alias && e.alias.toLowerCase().indexOf(q) !== -1);
      }).map(function (e) {
        return { name: e.name, icon: e.icon, group: e.group, href: base + "koumu/" + e.slug + ".html" };
      });
      render(companyMatches.concat(koumuMatches).slice(0, 8));
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
    var pickerInput = document.getElementById("compare-picker-input");
    var pickerResults = document.getElementById("compare-picker-results");
    var chipsEl = document.getElementById("compare-chips");

    loadData().then(function (data) {
      var bySlug = {};
      data.companies.forEach(function (c) { bySlug[c.slug] = c; });

      function selectedSlugs() {
        return Array.prototype.slice.call(checkboxes)
          .filter(function (cb) { return cb.checked; })
          .map(function (cb) { return cb.dataset.slug; });
      }

      function setChecked(slug, checked) {
        var cb = root.querySelector('input[type="checkbox"][data-slug="' + slug + '"]');
        if (!cb) return;
        cb.checked = checked;
        if (checked) {
          var det = cb.closest("details");
          if (det) det.open = true;
        }
      }

      function renderChips() {
        if (!chipsEl) return;
        var slugs = selectedSlugs();
        if (!slugs.length) { chipsEl.hidden = true; chipsEl.innerHTML = ""; return; }
        chipsEl.hidden = false;
        chipsEl.innerHTML = slugs.map(function (s) {
          var c = bySlug[s];
          if (!c) return "";
          return '<span class="compare-chip">' + logoImg(c.icon) + "<span>" + esc(c.short || c.name) +
            '</span><button type="button" data-remove="' + esc(s) + '" aria-label="' + esc(c.name) + 'を比較から外す">×</button></span>';
        }).join("");
      }

      if (chipsEl) {
        chipsEl.addEventListener("click", function (ev) {
          var btn = ev.target.closest("button[data-remove]");
          if (!btn) return;
          setChecked(btn.dataset.remove, false);
          render();
        });
      }

      if (pickerInput && pickerResults) {
        var pickerActiveIndex = -1;

        function pickerRender(matches) {
          if (!matches.length) {
            pickerResults.innerHTML = '<div class="sr-empty">一致する企業がありません</div>';
            pickerResults.hidden = false;
            return;
          }
          pickerResults.innerHTML = matches.map(function (c) {
            return '<button type="button" class="cpr-item" data-slug="' + esc(c.slug) + '">' +
              logoImg(c.icon) + "<span>" + esc(c.name) + '</span><span class="sr-group">' + esc(c.group) + "</span></button>";
          }).join("");
          pickerResults.hidden = false;
          pickerActiveIndex = -1;
        }

        function pickerSearch(q) {
          q = q.trim().toLowerCase();
          if (!q) { pickerResults.hidden = true; pickerResults.innerHTML = ""; return; }
          var selected = {};
          selectedSlugs().forEach(function (s) { selected[s] = true; });
          var matches = data.companies.filter(function (c) {
            return !selected[c.slug] && (
              c.name.toLowerCase().indexOf(q) !== -1 ||
              c.slug.toLowerCase().indexOf(q) !== -1 ||
              c.group.toLowerCase().indexOf(q) !== -1
            );
          }).slice(0, 8);
          pickerRender(matches);
        }

        function addFromPicker(slug) {
          setChecked(slug, true);
          pickerInput.value = "";
          pickerResults.hidden = true;
          pickerResults.innerHTML = "";
          render();
          pickerInput.focus();
        }

        pickerInput.addEventListener("input", function () { pickerSearch(pickerInput.value); });
        pickerInput.addEventListener("focus", function () { if (pickerInput.value.trim()) pickerSearch(pickerInput.value); });
        pickerInput.addEventListener("blur", function () { setTimeout(function () { pickerResults.hidden = true; }, 150); });
        pickerInput.addEventListener("keydown", function (ev) {
          var items = pickerResults.querySelectorAll(".cpr-item");
          if (ev.key === "ArrowDown" && items.length) {
            ev.preventDefault();
            pickerActiveIndex = Math.min(pickerActiveIndex + 1, items.length - 1);
            items.forEach(function (a, i) { a.classList.toggle("active", i === pickerActiveIndex); });
            items[pickerActiveIndex].scrollIntoView({ block: "nearest" });
          } else if (ev.key === "ArrowUp" && items.length) {
            ev.preventDefault();
            pickerActiveIndex = Math.max(pickerActiveIndex - 1, 0);
            items.forEach(function (a, i) { a.classList.toggle("active", i === pickerActiveIndex); });
          } else if (ev.key === "Enter") {
            ev.preventDefault();
            if (pickerActiveIndex >= 0 && items[pickerActiveIndex]) {
              addFromPicker(items[pickerActiveIndex].dataset.slug);
            } else if (items.length) {
              addFromPicker(items[0].dataset.slug);
            }
          } else if (ev.key === "Escape") {
            pickerResults.hidden = true;
            pickerInput.blur();
          }
        });
        pickerResults.addEventListener("mousedown", function (ev) {
          var btn = ev.target.closest(".cpr-item");
          if (btn) { ev.preventDefault(); addFromPicker(btn.dataset.slug); }
        });
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
          var name = r[0], href = r[1], v = r[2], icon = r[3];
          if (v === null || v === undefined) {
            html += '<div class="hbar-row"><span class="hbar-name" title="' + esc(name) +
              '"><a href="' + href + '">' + logoImg(icon) + esc(name) + '</a></span>' +
              '<span class="hbar-track"></span><span class="hbar-val na">非公表</span></div>';
          } else {
            var w = scalePct ? v * 100 : (v / max * 100);
            html += '<div class="hbar-row"><span class="hbar-name" title="' + esc(name) +
              '"><a href="' + href + '">' + logoImg(icon) + esc(name) + '</a></span>' +
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
        renderChips();

        var url = new URL(window.location.href);
        if (slugs.length) { url.searchParams.set("c", slugs.join(",")); } else { url.searchParams.delete("c"); }
        window.history.replaceState(null, "", url.pathname + url.search);

        if (!slugs.length) {
          resultEl.innerHTML = '<p class="lead">上のリストから企業を選ぶと、ここに横比較が表示されます。</p>';
          return;
        }
        var picked = slugs.map(function (s) { return bySlug[s]; }).filter(Boolean);
        var rowsFor = function (key) {
          return picked.map(function (c) { return [c.name, base + "kigyou/" + c.slug + ".html", c[key], c.icon]; });
        };
        var tableRows = picked.map(function (c) {
          return "<tr><th scope=\"row\"><a href=\"" + base + "kigyou/" + c.slug + ".html\">" + logoImg(c.icon) + esc(c.name) + "</a></th>" +
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
  // 学部・やりたい仕事タイプのプロフィール選択（旧 tekisei.html）を12問クイズの前段に統合している。
  function initShindan() {
    var root = document.getElementById("shindan");
    if (!root) return;

    var D = null, qi = 0, answers = [];
    var faculty = null, jobs = [];       // プロフィール（学部は1つ、仕事タイプは最大2つ）
    var elIntro = root.querySelector(".sd-intro");
    var elProfile = root.querySelector(".sd-profile");
    var elQuiz = root.querySelector(".sd-quiz");
    var elRes = root.querySelector(".sd-result");
    var btns = root.querySelectorAll(".sd-choice");
    var back = root.querySelector(".sd-back");
    var profileGo = root.querySelector(".sd-profile-go");

    function show(sec) {
      [elIntro, elProfile, elQuiz, elRes].forEach(function (s) { s.hidden = s !== sec; });
      root.setAttribute("data-state",
        sec === elIntro ? "intro" : sec === elProfile ? "profile" : sec === elQuiz ? "quiz" : "result");
      if (sec !== elIntro) sec.scrollIntoView({ behavior: "smooth", block: "start" });
    }

    root.querySelectorAll('.sd-profile [data-kind="faculty"]').forEach(function (b) {
      b.addEventListener("click", function () {
        faculty = b.dataset.key;
        root.querySelectorAll('.sd-profile [data-kind="faculty"]').forEach(function (x) {
          x.classList.toggle("is-on", x === b);
        });
        profileGo.disabled = !faculty;
      });
    });
    root.querySelectorAll('.sd-profile [data-kind="job"]').forEach(function (b) {
      b.addEventListener("click", function () {
        var key = b.dataset.key;
        var i = jobs.indexOf(key);
        if (i >= 0) {
          jobs.splice(i, 1);
          b.classList.remove("is-on");
        } else {
          if (jobs.length >= 2) {
            var oldest = jobs.shift();
            root.querySelectorAll('.sd-profile [data-kind="job"]').forEach(function (x) {
              if (x.dataset.key === oldest) x.classList.remove("is-on");
            });
          }
          jobs.push(key);
          b.classList.add("is-on");
        }
      });
    });

    // 会社ごとの事実（target_faculty_elig）が false と明記している場合だけ除外する。
    // 未構造化・記載なし（null）は対象外と決めつけず含める。「決めていない」を選んだ場合も絞らない。
    function facultyOk(c) {
      if (!faculty || faculty === "toranai") return true;
      if (!c.target_faculty_elig) return true;
      return c.target_faculty_elig[faculty] !== false;
    }

    function jobOk(c) {
      if (!jobs.length) return true;
      return jobs.some(function (j) { return c.job.indexOf(j) !== -1; });
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

      var facLabel = {}; D.faculty_options.forEach(function (o) { facLabel[o.key] = o.label; });
      var jobLabel = {}; D.job_options.forEach(function (o) { jobLabel[o.key] = o.label; });
      root.querySelector(".sd-profile-note").innerHTML =
        "選んだ学部：<b>" + esc(facLabel[faculty]) + "</b>" +
        (jobs.length ? "／やりたい仕事：<b>" + esc(jobs.map(function (j) { return jobLabel[j]; }).join("、")) + "</b>" : "") +
        "。採用ページに明確な学部制限が書かれている会社だけ、対象外なら一覧から除いています" +
        "（記載が無い・まだ判定できていない会社は除いていません）。" +
        (jobs.length ? "やりたい仕事に一致する業界だけに絞っています。" : "");

      renderTable(w);
      show(elRes);
    }

    function facultyCell(c) {
      if (c.target_faculty_fact) {
        return esc(c.target_faculty_fact) + ' <span class="tk-fact">採用ページに記載</span>';
      }
      var facLabel = {}; D.faculty_options.forEach(function (o) { facLabel[o.key] = o.label; });
      return '<span class="tk-guess">' +
        esc(c.faculty.map(function (f) { return facLabel[f]; }).join("・")) + "（業界の一般的傾向）</span>";
    }

    function renderTable(w) {
      var noHd = root.querySelector(".sd-nohd").checked;
      var group = root.querySelector(".sd-group").value;
      var keys = D.axes.map(function (a) { return a.key; }).filter(function (k) { return w[k] > 0; });
      var labelOf = {}; D.axes.forEach(function (a) { labelOf[a.key] = a.short; });

      var rows = D.companies.filter(function (c) {
        if (noHd && c.h) return false;
        if (group && c.g !== group) return false;
        if (!facultyOk(c)) return false;
        if (!jobOk(c)) return false;
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
          "<td class=\"small\">" + facultyCell(r.c) + "</td>" +
          '<td class="small">' + esc(r.c.g) + "</td></tr>";
      }).join("");
    }

    root.querySelector(".sd-start").addEventListener("click", function () {
      fetch(base + "data/shindan.json").then(function (r) { return r.json(); }).then(function (d) {
        D = d; show(elProfile);
      });
    });
    profileGo.addEventListener("click", function () {
      qi = 0; answers = []; show(elQuiz); paintQuestion();
    });
    btns.forEach(function (b) {
      b.addEventListener("click", function () { answer(b.getAttribute("data-side")); });
    });
    back.addEventListener("click", function () { if (qi > 0) { qi--; paintQuestion("back"); } });
    root.querySelector(".sd-retry").addEventListener("click", function () {
      qi = 0; answers = []; show(elQuiz); paintQuestion();
    });
    root.querySelector(".sd-profile-retry").addEventListener("click", function () {
      show(elProfile);
    });
    root.querySelector(".sd-nohd").addEventListener("change", function () { renderTable(weights()); });
    root.querySelector(".sd-group").addEventListener("change", function () { renderTable(weights()); });
  }

  // ---- 新卒の採用枠（企業ページに実行時で差し込む） v2（新デザイン対応） ----
  // HTMLには焼き込まない。data/saiyo.json をスラッグで引いて、見つかった会社だけ
  // 挿入先を #kihon（基本データ）直前 → <main> 先頭 → .extlinks 直後（旧デザイン互換）
  // の順にフォールバック探索してセクションを追加する。
  // データが増えても data/saiyo.json を更新するだけで全ページに反映される
  // （企業ページ1,500枚超を再生成しなくてよい）。
  var SAIYO_FIELD_LABEL = { conditions: "条件", overview: "概要", timing: "時期" };

  function saiyoEsc(x) {
    return String(x).replace(/[&<>"']/g, function (ch) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch];
    });
  }

  function buildSaiyoSectionHTML(s) {
    var order = ["conditions", "overview", "timing"];

    var tracks = s.tracks.map(function (t) {
      var present = order.filter(function (k) { return t[k]; });
      var lead = present[0] || null;
      var rest = present.slice(1);

      var leadHtml = lead
        ? '<p class="saiyo-field"><b>' + SAIYO_FIELD_LABEL[lead] + "：</b>" + saiyoEsc(t[lead]) + "</p>"
        : "";

      var moreHtml = "";
      if (rest.length) {
        var fields = rest.map(function (k) {
          return '<p class="saiyo-field"><b>' + SAIYO_FIELD_LABEL[k] + "：</b>" + saiyoEsc(t[k]) + "</p>";
        }).join("");
        var label = rest.map(function (k) { return SAIYO_FIELD_LABEL[k]; }).join("・") + "を見る";
        moreHtml = '<details class="saiyo-more"><summary>' + label + "</summary>" + fields + "</details>";
      }

      return '<div class="saiyo-track"><h3>' + saiyoEsc(t.name) + "</h3>" + leadHtml + moreHtml + "</div>";
    }).join("");

    var faculty = s.target_faculty ? "　対象：" + saiyoEsc(s.target_faculty) : "";

    var notes = (s.notes || []).map(function (n) { return "<li>" + saiyoEsc(n) + "</li>"; }).join("");
    var notesHtml = notes ? '<ul class="saiyo-notes">' + notes + "</ul>" : "";

    var srcs = (s.source_urls || []).map(function (u) {
      var host = "";
      try { host = new URL(u).hostname; } catch (e) { host = u; }
      return '<a href="' + saiyoEsc(u) + '" rel="nofollow noopener" target="_blank">' + saiyoEsc(host) + "</a>";
    }).join(" ");

    return (
      '<div class="saiyo-head"><h2>新卒の採用枠</h2>' +
      '<span class="saiyo-badge">出典：公式採用ページ（有価証券報告書ではありません）</span></div>' +
      '<p class="saiyo-lead">公式の採用ページに書かれていた内容だけを要約しています。' + faculty + "</p>" +
      '<div class="saiyo-tracks">' + tracks + "</div>" +
      notesHtml +
      '<p class="caveat">採用ページ（' + srcs + "）を" + saiyoEsc(s.fetched_at || "") + "時点でこのセッションが読み、" +
      "書かれていた内容だけを要約したものです。募集要項は年度ごとに変わります。<b>最新の内容は必ず公式の採用ページで確認してください。</b></p>"
    );
  }

  function injectSaiyoSection(s) {
    if (!s || !s.tracks || !s.tracks.length) return;
    if (document.getElementById("saiyo")) return; // 二重挿入ガード

    var section = document.createElement("section");
    section.id = "saiyo";
    section.className = "saiyo-section";
    section.innerHTML = buildSaiyoSectionHTML(s);

    var kihon = document.getElementById("kihon");
    var mainEl = document.querySelector("main");
    var ext = document.querySelector(".extlinks");
    if (kihon && kihon.parentNode) {
      kihon.parentNode.insertBefore(section, kihon);       // 新デザイン：基本データの直前（冒頭）
    } else if (mainEl) {
      mainEl.insertBefore(section, mainEl.firstChild);      // フォールバック：main 先頭
    } else if (ext) {
      ext.insertAdjacentElement("afterend", section);       // 旧デザイン互換
    } else {
      return;
    }

    // sticky ナビに「採用枠」タブを追加（新デザインのみ：#kihon タブの存在で判定）
    var tab = document.querySelector('a[href="#kihon"]');
    if (tab && !document.querySelector('a[href="#saiyo"]')) {
      var t2 = tab.cloneNode(false); // 属性（インラインstyle含む）ごと複製し、見た目を揃える
      t2.href = "#saiyo";
      t2.textContent = "採用枠";
      tab.parentNode.insertBefore(t2, tab);
    }
  }

  function initSaiyoInject() {
    var m = /\/kigyou\/([^/]+)\.html/.exec(location.pathname);
    var slug = m ? m[1] : null;
    if (!slug) return;

    fetch(base + "data/saiyo.json")
      .then(function (r) { return r.json(); })
      .then(function (all) { injectSaiyoSection(all[slug]); })
      .catch(function () { /* データ取得に失敗しても他の表示は止めない */ });
  }

  // ---- 業界ショーケース（トップページ：横に流れる企業帯） ----
  function initShowcase() {
    var root = document.getElementById("showcase");
    if (!root) return;
    var tabs = root.querySelectorAll(".tab-btn");
    var lanes = root.querySelectorAll(".lane");
    var ctaLink = document.getElementById("showcase-cta-link");
    var reduceMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    var timer = null;
    var pausedLane = null;
    var resumeTimer = null;

    // .lane-track は overflow-x:auto の普通のスクロール領域（ユーザーがドラッグ/スワイプ/
    // ホイールで自由に動かせる）。自動送りはその同じ scrollLeft を一定間隔で
    // チップ1つぶん進めるだけなので、手動スクロールと取り合いにならない。
    // scrollTo({behavior:"smooth"}) はブラウザ・環境によって効かないことがあるため、
    // 自前で requestAnimationFrame により scrollLeft を easing させる（挙動を環境に依存させない）。
    function stride(track) {
      var chip = track.querySelector(".chip");
      if (!chip) return 0;
      var gap = parseFloat(getComputedStyle(track).columnGap) || 0;
      return chip.getBoundingClientRect().width + gap;
    }

    function easeScrollTo(track, target, duration) {
      var startX = track.scrollLeft, delta = target - startX, startTime = null;
      if (!delta) return;
      function step(ts) {
        if (startTime === null) startTime = ts;
        var t = Math.min(1, (ts - startTime) / duration);
        var eased = 1 - Math.pow(1 - t, 3);
        track.scrollLeft = startX + delta * eased;
        if (t < 1) requestAnimationFrame(step);
      }
      requestAnimationFrame(step);
    }

    function advance(track) {
      var st = stride(track);
      if (!st) return;
      var repeat = parseInt(track.dataset.repeat, 10) || 2;
      var one = track.scrollWidth / repeat;          // 複製前1コピーぶんの幅
      var next = track.scrollLeft + st;
      if (next >= one - 1) {
        track.scrollLeft = next - one;                // 継ぎ目を見せずに先頭へジャンプ
      } else {
        easeScrollTo(track, next, 420);
      }
    }

    function stopAuto() {
      if (timer) { clearInterval(timer); timer = null; }
    }

    function startAuto(lane) {
      stopAuto();
      if (reduceMotion || !lane) return;
      var track = lane.querySelector(".lane-track");
      if (!track) return;
      timer = setInterval(function () {
        if (pausedLane === lane) return;
        advance(track);
      }, 2600);                                       // 1社ぶん進めて、次まで数秒止まる
    }

    function activate(tab) {
      tabs.forEach(function (t) {
        var on = t === tab;
        t.classList.toggle("is-active", on);
        t.setAttribute("aria-selected", on ? "true" : "false");
      });
      var activeLane = null;
      lanes.forEach(function (l) {
        var on = l.id === tab.dataset.target;
        l.classList.toggle("is-active", on);
        if (on) activeLane = l;
      });
      if (ctaLink) {
        ctaLink.href = tab.dataset.href;
        ctaLink.textContent = tab.dataset.group + tab.dataset.count + "社を1つの表で比較する →";
      }
      startAuto(activeLane);
    }

    tabs.forEach(function (t) {
      t.addEventListener("click", function () { activate(t); });
    });

    // 触っている間は自動送りを止める。動いたままだと狙った企業をクリック/ドラッグしにくいため
    lanes.forEach(function (l) {
      var pause = function () {
        pausedLane = l;
        if (resumeTimer) { clearTimeout(resumeTimer); resumeTimer = null; }
      };
      var resume = function (delay) {
        if (resumeTimer) clearTimeout(resumeTimer);
        resumeTimer = setTimeout(function () {
          if (pausedLane === l) pausedLane = null;
        }, delay || 0);
      };
      l.addEventListener("mouseenter", pause);
      l.addEventListener("mouseleave", function () { resume(0); });
      l.addEventListener("focusin", pause);
      l.addEventListener("focusout", function () { resume(0); });
      l.addEventListener("wheel", pause, { passive: true });
      l.addEventListener("touchstart", pause, { passive: true });
      l.addEventListener("touchend", function () { resume(2500); }, { passive: true });
    });

    startAuto(root.querySelector(".lane.is-active"));
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

  // ---- トップページ：ヒーローカルーセル（注目企業、自動送り＋矢印） ----
  function initHeroCarousel() {
    var track = document.getElementById("hero-carousel");
    if (!track) return;
    var wrap = track.closest(".hero-carousel-wrap");
    var prevBtn = wrap.querySelector(".hero-car-prev");
    var nextBtn = wrap.querySelector(".hero-car-next");
    var paused = false, resumeT = null, raf = null, timer = null;

    function half() {
      var kids = track.children;
      if (!kids.length) return 0;
      var mid = kids[Math.floor(kids.length / 2)];
      return mid ? mid.offsetLeft - kids[0].offsetLeft : track.scrollWidth / 2;
    }

    function step(dir) {
      var kids = track.children;
      if (!kids.length) return;
      var w = kids[0].offsetWidth + 14;
      var h = half();
      cancelAnimationFrame(raf);
      if (dir < 0 && track.scrollLeft < w) track.scrollLeft += h;
      var target = track.scrollLeft + dir * w;
      var best = null;
      for (var i = 0; i < kids.length; i++) {
        var off = kids[i].offsetLeft - kids[0].offsetLeft;
        if (best === null || Math.abs(off - target) < Math.abs(best - target)) best = off;
      }
      if (best !== null) target = best;
      var from = track.scrollLeft, dist = target - from, dur = 900, t0 = performance.now();
      function tick(now) {
        var t = Math.min(1, (now - t0) / dur);
        var eased = t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
        track.scrollLeft = from + dist * eased;
        if (t < 1) { raf = requestAnimationFrame(tick); }
        else if (track.scrollLeft >= h) { track.scrollLeft -= h; }
      }
      raf = requestAnimationFrame(tick);
    }

    function stopAuto() { if (timer) clearInterval(timer); timer = null; }
    function startAuto() {
      stopAuto();
      if (window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
      timer = setInterval(function () { if (!paused) step(1); }, 3000);
    }
    function pauseFor(ms) {
      paused = true;
      clearTimeout(resumeT);
      resumeT = setTimeout(function () { paused = false; }, ms);
    }

    if (prevBtn) prevBtn.addEventListener("click", function () { pauseFor(4000); step(-1); });
    if (nextBtn) nextBtn.addEventListener("click", function () { pauseFor(4000); step(1); });
    wrap.addEventListener("mouseenter", function () { paused = true; clearTimeout(resumeT); });
    wrap.addEventListener("mouseleave", function () { paused = false; });
    wrap.addEventListener("touchstart", function () { paused = true; clearTimeout(resumeT); }, { passive: true });
    wrap.addEventListener("touchend", function () { pauseFor(2500); }, { passive: true });

    startAuto();
  }

  // ---- 企業ページ：規模の近い同業他社テーブルの列ソート ----
  function initPeerSort() {
    var table = document.getElementById("cp-peer-table");
    if (!table) return;
    var tbody = table.querySelector("tbody");
    var btns = table.querySelectorAll(".cp-sort-btn");
    var state = { key: "emp", dir: -1 };

    function apply() {
      var rows = Array.prototype.slice.call(tbody.querySelectorAll("tr"));
      rows.sort(function (a, b) {
        var aCell = a.querySelector('td[data-key="' + state.key + '"]');
        var bCell = b.querySelector('td[data-key="' + state.key + '"]');
        var av = aCell && aCell.dataset.val !== "" ? parseFloat(aCell.dataset.val) : null;
        var bv = bCell && bCell.dataset.val !== "" ? parseFloat(bCell.dataset.val) : null;
        if (av === null && bv === null) return 0;
        if (av === null) return 1;
        if (bv === null) return -1;
        return state.dir < 0 ? bv - av : av - bv;
      });
      rows.forEach(function (r) { tbody.appendChild(r); });
      btns.forEach(function (b) {
        var on = b.dataset.key === state.key;
        b.classList.toggle("is-active", on);
        var arrow = b.querySelector(".cp-sort-arrow");
        if (arrow) arrow.textContent = on ? (state.dir < 0 ? " ↓" : " ↑") : "";
      });
    }

    btns.forEach(function (b) {
      b.addEventListener("click", function () {
        var key = b.dataset.key;
        if (state.key === key) state.dir = -state.dir;
        else { state.key = key; state.dir = -1; }
        apply();
      });
    });

    apply();
  }

  document.addEventListener("DOMContentLoaded", function () {
    initSearch();
    initCompare();
    initShindan();
    initSaiyoInject();
    initShowcase();
    initHeroCarousel();
    initPeerSort();
    initReveal();
    initChrome();
  });
})();
