(() => {
  "use strict";

  const SHARE_ENDPOINT = (id) =>
    `/api/v1/display/roughcast-ranked/${encodeURIComponent(id)}/share`;

  const hints = {
    top: "先看封面和月租：是不是真住房、租金有没有少写一个 0。",
    bottom: "是不是合租/商办/面积填小了，被当成整租清水。",
    extreme: "算法认为便宜或贵过了头。对照贝壳页判断数据是否录错。",
    mid: "高可信且未打极端标记。如果这组也不像真价，主榜不能上。",
  };

  const grid = document.querySelector("#grid");
  const empty = document.querySelector("#empty");
  const runMeta = document.querySelector("#runMeta");
  const groupHint = document.querySelector("#groupHint");
  const tabs = document.querySelector("#tabs");
  const districts = document.querySelector("#districts");
  const coverFilter = document.querySelector("#coverFilter");
  const openStatus = document.querySelector("#openStatus");
  const CORE_DISTRICTS = ["锦江", "青羊", "金牛", "武侯", "成华"];

  let feed = null;
  let currentGroup = "top";
  let selectedDistricts = CORE_DISTRICTS.slice();
  let requireCover = true;

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function safeHttpUrl(value) {
    if (typeof value !== "string" || !value.trim()) return null;
    try {
      const parsed = new URL(value, window.location.origin);
      return parsed.protocol === "http:" || parsed.protocol === "https:" ? parsed.href : null;
    } catch {
      return null;
    }
  }

  function fmt(value, digits) {
    if (typeof value !== "number" || !Number.isFinite(value)) return "—";
    return value.toFixed(digits);
  }

  function renderGroup(name) {
    currentGroup = name;
    tabs.querySelectorAll("button").forEach((button) => {
      button.classList.toggle("is-active", button.dataset.group === name);
    });
    groupHint.textContent = hints[name] || "";
    const cards = (feed && feed.groups && feed.groups[name]) || [];
    grid.replaceChildren();
    empty.hidden = cards.length > 0;
    cards.forEach((item) => {
      const seen = (item.view_count || 0) > 0;
      const shared = (item.share_count || 0) > 0;
      const card = el("article", seen ? "card is-seen" : "card");
      const cover = el("div", "cover");
      const imageUrl = safeHttpUrl(item.image);
      if (imageUrl) {
        const img = document.createElement("img");
        img.alt = item.community || "房源封面";
        img.loading = "lazy";
        img.src = imageUrl;
        cover.append(img);
      } else {
        cover.append(el("div", "ph", "无封面"));
      }
      const badge = el("div", "badge");
      badge.append(el("span", "rank", item.city_rank ? `#${item.city_rank}` : "无排名"));
      badge.append(el("span", "score", item.quality_score == null ? "无分" : `${item.quality_score}分`));
      if (item.extreme_price) badge.append(el("span", "ext", "待核"));
      if (seen) badge.append(el("span", "seen", `已看${item.view_count}次`));
      if (shared) badge.append(el("span", "shared", `已分享${item.share_count}次`));
      cover.append(badge);

      const body = el("div", "body");
      body.append(el("h2", "", item.community || "未知小区"));
      const place = [item.district || "未知", item.bizcircle].filter(Boolean).join(" · ");
      if (place) body.append(el("p", "place", place));
      const meta = [
        item.layout,
        item.area_sqm != null ? `${fmt(item.area_sqm, 0)}㎡` : "",
        item.monthly_rent_yuan != null ? `${fmt(item.monthly_rent_yuan, 0)}元/月` : "",
      ].filter(Boolean).join(" · ");
      body.append(el("p", "meta", meta));
      body.append(el(
        "p",
        "prices",
        `实际 ${fmt(item.unit_rent, 1)} 元/㎡ · 装修参考 ${fmt(item.reference_unit_rent, 1)} · 合理清水 ${fmt(item.expected_unit_rent, 1)} · 可信 ${item.confidence_score ?? "—"} · ${item.benchmark_mode || ""}`,
      ));
      body.append(el("p", "reason", item.reason || ""));
      const actions = el("div", "actions");
      const ke = el("button", "", "打开贝壳");
      ke.type = "button";
      ke.addEventListener("click", () => openWorkbench(item, ke));
      const share = el("button", "ghost", "分享");
      share.type = "button";
      share.addEventListener("click", () => shareListing(item, share));
      const copy = el("button", "ghost", "复制编号");
      copy.type = "button";
      copy.addEventListener("click", () => {
        if (navigator.clipboard) navigator.clipboard.writeText(item.listing_id || "");
      });
      actions.append(ke, share, copy);
      body.append(actions);
      card.append(cover, body);
      grid.append(card);
    });
  }

  function shareListing(item, button) {
    if (button.disabled) return;
    const listingId = item.listing_id || "";
    if (!listingId) {
      setOpenStatus("没有房源编号", true);
      return;
    }
    button.disabled = true;
    const original = button.textContent;
    button.textContent = "正在生成…";
    const progressTimer = window.setTimeout(() => {
      if (button.disabled) button.textContent = "下载实勘图中…";
    }, 1000);
    const controller = new AbortController();
    const abortTimer = window.setTimeout(() => controller.abort(), 30000);
    fetch(SHARE_ENDPOINT(listingId), { signal: controller.signal })
      .then((response) => {
        if (!response.ok) {
          return response.json().catch(() => ({})).then((body) => {
            const detail = typeof body.detail === "string" ? body.detail : "分享失败";
            throw new Error(detail);
          });
        }
        const header = response.headers.get("X-Share-Count");
        const count = header != null ? Number.parseInt(header, 10) : NaN;
        return response.blob().then((blob) => ({ blob, shareCount: count }));
      })
      .then(({ blob, shareCount }) => {
        if (Number.isFinite(shareCount) && shareCount > 0) {
          markShared(listingId, shareCount);
        }
        const file = new File([blob], `roughcast-${listingId}.png`, { type: "image/png" });
        const fallbackDownload = () => {
          const href = URL.createObjectURL(blob);
          const link = document.createElement("a");
          link.href = href;
          link.download = `roughcast-${listingId}.png`;
          link.rel = "noopener";
          document.body.append(link);
          link.click();
          link.remove();
          window.setTimeout(() => URL.revokeObjectURL(href), 4000);
          button.textContent = "已保存";
        };
        if (navigator.canShare && navigator.canShare({ files: [file] })) {
          return navigator.share({
            files: [file],
            title: item.community || "清水房",
          }).then(() => {
            button.textContent = "已分享";
          }).catch((err) => {
            console.warn("share: navigator.share rejected, falling back to download", err);
            fallbackDownload();
          });
        }
        fallbackDownload();
      })
      .catch((error) => {
        const msg = error && error.name === "AbortError"
          ? "生成超时(>30s),请稍后重试"
          : (error && error.message) || "分享失败";
        button.textContent = "分享失败";
        setOpenStatus(msg, true);
      })
      .finally(() => {
        window.clearTimeout(progressTimer);
        window.clearTimeout(abortTimer);
        window.setTimeout(() => {
          button.disabled = false;
          button.textContent = original;
        }, 2000);
      });
  }

  function markViewed(listingId, viewCount) {
    if (!feed || !feed.groups) return;
    Object.keys(feed.groups).forEach((name) => {
      (feed.groups[name] || []).forEach((item) => {
        if (item.listing_id === listingId) item.view_count = viewCount;
      });
    });
    renderGroup(currentGroup);
  }

  function markShared(listingId, shareCount) {
    if (!feed || !feed.groups) return;
    Object.keys(feed.groups).forEach((name) => {
      (feed.groups[name] || []).forEach((item) => {
        if (item.listing_id === listingId) item.share_count = shareCount;
      });
    });
    renderGroup(currentGroup);
  }

  function setOpenStatus(text, isError) {
    if (!openStatus) return;
    openStatus.textContent = text || "";
    openStatus.classList.toggle("is-error", Boolean(isError));
  }

  function detailMessage(body) {
    if (!body || typeof body !== "object") return "";
    if (typeof body.detail === "string") return body.detail;
    if (body.detail && typeof body.detail.message === "string") return body.detail.message;
    return "";
  }

  function openWorkbench(item, button) {
    if (button.disabled) return;
    const listingId = item.listing_id || "";
    if (!listingId) {
      setOpenStatus("没有房源编号", true);
      return;
    }
    button.disabled = true;
    const original = button.textContent;
    button.textContent = "正在打开…";
    setOpenStatus(`正在注入凭证打开 ${listingId} …`, false);
    fetch(`/api/v1/display/roughcast-score-review/${encodeURIComponent(listingId)}/open`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
    })
      .then((response) => response.json().catch(() => ({})).then((body) => ({ response, body })))
      .then(({ response, body }) => {
        if (!response.ok) {
          throw new Error(detailMessage(body) || "打开失败");
        }
        button.textContent = "已打开";
        setOpenStatus("已在 CRM 工作台打开（已注入登录凭证）", false);
        const count = typeof body.view_count === "number" ? body.view_count : (item.view_count || 0) + 1;
        markViewed(listingId, count);
      })
      .catch((error) => {
        button.textContent = "打开失败";
        setOpenStatus(error.message || "打开失败", true);
      })
      .finally(() => {
        window.setTimeout(() => {
          button.disabled = false;
          button.textContent = original;
        }, 1600);
      });
  }

  function renderDistricts() {
    if (!districts) return;
    districts.replaceChildren();
    const items = (feed && feed.districts) || [];
    if (!items.length) return;
    const all = el("button", selectedDistricts.length ? "" : "is-active", "全部");
    all.type = "button";
    all.dataset.district = "";
    districts.append(all);
    const coreActive = CORE_DISTRICTS.every((name) => selectedDistricts.indexOf(name) >= 0)
      && selectedDistricts.length === CORE_DISTRICTS.length;
    const core = el("button", coreActive ? "is-active" : "", "五城区");
    core.type = "button";
    core.dataset.district = "__core__";
    districts.append(core);
    items.forEach((item) => {
      const active = selectedDistricts.indexOf(item.name) >= 0;
      const button = el("button", active ? "is-active" : "");
      button.type = "button";
      button.dataset.district = item.name;
      button.append(document.createTextNode(item.name + " "));
      button.append(el("span", "count", String(item.count)));
      districts.append(button);
    });
  }

  function applyFeed(payload) {
    feed = payload;
    selectedDistricts = Array.isArray(payload.selected_districts)
      ? payload.selected_districts.filter((name) => typeof name === "string" && name)
      : [];
    requireCover = Boolean(payload.require_cover);
    if (coverFilter) coverFilter.classList.toggle("is-active", requireCover);
    const delta = typeof payload.delta_value === "number" ? payload.delta_value.toFixed(3) : "—";
    const filtered = payload.filtered_count;
    const parts = [];
    if (selectedDistricts.length) parts.push(selectedDistricts.join("、"));
    if (requireCover) parts.push("有封面");
    const scoped = parts.length && typeof filtered === "number"
      ? ` · ${parts.join(" · ")} ${filtered} 套`
      : "";
    runMeta.textContent = `score_run ${payload.score_run_id} · δ ${delta} · 主榜 ${payload.scored_count} 套${scoped}`;
    renderDistricts();
    renderGroup(currentGroup);
  }

  function loadFeed() {
    const params = new URLSearchParams();
    selectedDistricts.forEach((name) => params.append("district", name));
    params.set("require_cover", requireCover ? "1" : "0");
    const query = params.toString();
    const url = "/api/v1/display/roughcast-score-review" + (query ? `?${query}` : "");
    return fetch(url).then((response) => {
      if (!response.ok) throw new Error(response.status === 404 ? "还没有评分批次" : "载入失败");
      return response.json();
    });
  }

  tabs.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-group]");
    if (!button) return;
    renderGroup(button.dataset.group);
  });

  if (districts) {
    districts.addEventListener("click", (event) => {
      const button = event.target.closest("button[data-district]");
      if (!button) return;
      const name = button.dataset.district || "";
      if (!name) {
        selectedDistricts = [];
      } else if (name === "__core__") {
        selectedDistricts = CORE_DISTRICTS.slice();
      } else {
        const index = selectedDistricts.indexOf(name);
        if (index >= 0) selectedDistricts.splice(index, 1);
        else selectedDistricts.push(name);
      }
      loadFeed().then(applyFeed).catch((error) => {
        setOpenStatus(error.message || "筛选失败", true);
      });
    });
  }

  if (coverFilter) {
    coverFilter.addEventListener("click", () => {
      requireCover = !requireCover;
      loadFeed().then(applyFeed).catch((error) => {
        setOpenStatus(error.message || "筛选失败", true);
      });
    });
  }

  loadFeed()
    .then(applyFeed)
    .catch((error) => {
      runMeta.textContent = error.message || "载入失败";
      empty.hidden = false;
      empty.textContent = runMeta.textContent;
    });
})();
