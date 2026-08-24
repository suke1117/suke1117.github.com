/* えらび手帳 — 記事を読むときの補助だけを担当する。
   設計方針は docs/ux-target.md を参照。
   JavaScript が動かなくても、記事の内容と導線はすべて使えるようにしてある。 */

(function () {
  "use strict";

  var reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* ── 状況セレクター ──────────────────────
     選んだ商品まで移動して、その場所を一時的に目立たせる。
     どこに飛んだのかが分からないと、かえって迷うため。 */
  function setupPicker() {
    var options = document.querySelectorAll(".picker-option");
    if (!options.length) return;

    Array.prototype.forEach.call(options, function (option) {
      option.addEventListener("click", function (event) {
        var target = document.getElementById(option.dataset.target);
        if (!target) return;   // 見つからなければ通常のアンカーとして動く
        event.preventDefault();

        target.scrollIntoView({
          behavior: reduceMotion ? "auto" : "smooth",
          block: "start"
        });
        // スクリーンリーダーとキーボードの現在地も移す
        target.focus({ preventScroll: true });
        history.replaceState(null, "", "#" + option.dataset.target);

        Array.prototype.forEach.call(
          document.querySelectorAll(".is-highlighted"),
          function (el) { el.classList.remove("is-highlighted"); }
        );
        target.classList.add("is-highlighted");
        window.setTimeout(function () {
          target.classList.remove("is-highlighted");
        }, 2600);
      });
    });
  }

  /* ── 記事内タブの現在地 ──────────────────
     細切れの時間で読まれるので、いまどこかが常に分かるようにする。 */
  function setupSectionNav() {
    var nav = document.querySelector(".section-nav");
    if (!nav || !("IntersectionObserver" in window)) return;

    var links = {};
    var sections = [];
    Array.prototype.forEach.call(nav.querySelectorAll("a[href^='#']"), function (link) {
      var id = link.getAttribute("href").slice(1);
      var section = document.getElementById(id);
      if (!section) {
        link.remove();       // 対応するセクションが無い記事ではタブごと消す
        return;
      }
      links[id] = link;
      sections.push(section);
    });
    if (!sections.length) { nav.remove(); return; }

    var current = null;
    function setCurrent(id) {
      if (id === current) return;
      current = id;
      Object.keys(links).forEach(function (key) {
        links[key].classList.toggle("is-current", key === id);
      });
      // 選択中のタブが画面外にあると気づけないので、横スクロールを追従させる
      var active = links[id];
      if (active && active.scrollIntoView) {
        active.scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth",
                                block: "nearest", inline: "nearest" });
      }
    }

    var visible = {};
    var observer = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        visible[entry.target.id] = entry.isIntersecting;
      });
      for (var i = 0; i < sections.length; i++) {
        if (visible[sections[i].id]) { setCurrent(sections[i].id); return; }
      }
    }, { rootMargin: "-72px 0px -55% 0px", threshold: 0 });

    sections.forEach(function (section) { observer.observe(section); });
  }

  /* ── 上に戻る ────────────────────────────
     記事が長いため、片手で先頭に戻れるようにする。 */
  function setupToTop() {
    var button = document.querySelector(".to-top");
    if (!button) return;

    var ticking = false;
    function update() {
      var show = window.scrollY > window.innerHeight * 1.5;
      button.hidden = !show;
      button.classList.toggle("is-visible", show);
      ticking = false;
    }
    window.addEventListener("scroll", function () {
      if (ticking) return;
      ticking = true;
      window.requestAnimationFrame(update);
    }, { passive: true });

    button.addEventListener("click", function () {
      window.scrollTo({ top: 0, behavior: reduceMotion ? "auto" : "smooth" });
      var brand = document.querySelector(".brand");
      if (brand) brand.focus({ preventScroll: true });
    });
    update();
  }

  /* ── 比較表からのジャンプ ────────────────
     表の商品名を押したときも、飛んだ先を目立たせる。 */
  function setupTableJump() {
    Array.prototype.forEach.call(
      document.querySelectorAll(".compare-table a[href^='#item-']"),
      function (link) {
        link.addEventListener("click", function () {
          var target = document.getElementById(link.getAttribute("href").slice(1));
          if (!target) return;
          target.classList.add("is-highlighted");
          window.setTimeout(function () {
            target.classList.remove("is-highlighted");
          }, 2600);
        });
      }
    );
  }

  setupPicker();
  setupSectionNav();
  setupToTop();
  setupTableJump();
})();
