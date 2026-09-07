(() => {
  "use strict";

  const CORE_DISTRICTS = ["锦江", "青羊", "金牛", "武侯", "成华"];
  const RANK_ENDPOINT = "/api/v1/display/roughcast-ranked";
  const OPEN_ENDPOINT = (id) =>
    `/api/v1/display/roughcast-score-review/${encodeURIComponent(id)}/open`;
  const SHARE_ENDPOINT = (id) =>
    `/api/v1/display/roughcast-ranked/${encodeURIComponent(id)}/share`;

  const state = {
    group: "scored",
    sort: "quality",
    selectedDistricts: CORE_DISTRICTS.slice(),
    requireCover: true,
    deals: false,
    page: 1,
    pageSize: 30,
    total: 0,
    hasMore: false,
    loading: false,
    items: [],
    runMeta: null,
  };

  const grid = document.querySelector("#grid");
  const empty = document.querySelector("#empty");
  const hint = document.querySelector("#hint");
  const runMeta = document.querySelector("#runMeta");
  const runFooter = document.querySelector("#runFooter");
  const groups = document.querySelector("#groups");
  const districts = document.querySelector("#districts");
  const sortNav = document.querySelector("#sort");
  const dealsToggle = document.querySelector("#dealsToggle");
  const coverFilter = document.querySelector("#coverFilter");
  const openStatus = document.querySelector("#openStatus");
  const loadMoreBox = document.querySelector("#loadMore");
  const loadMoreButton = document.querySelector("#loadMoreButton");

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

  function setHint(text) {
    if (hint) hint.textContent = text || "";
  }

  function setOpenStatus(text, isError) {
    if (!openStatus) return;
    openStatus.textContent = text || "";
    openStatus.classList.toggle("is-error", Boolean(isError));
  }

  function isNearby(item) {
    return item && item.quality_status === "nearby_estimate";
  }

  function tierLabel(item) {
    if (!item.quality_tier) return null;
    return isNearby(item) ? `${item.quality_tier}（周边估算）` : item.quality_tier;
  }

  function renderCard(item) {
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
    if (item.city_rank) badge.append(el("span", "rank", `#${item.city_rank}`));
    if (!isNearby(item)) {
      if (item.quality_score != null) {
        badge.append(el("span", "score", `${item.quality_score}分`));
      }
    } else {
      const label = tierLabel(item);
      if (label) badge.append(el("span", "tier", label));
    }
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
      item.orientation || "",
      item.floor || "",
    ].filter(Boolean).join(" · ");
    body.append(el("p", "meta", meta));

    const priceLine = isNearby(item)
      ? `实际 ${fmt(item.unit_rent, 1)} 元/㎡ · 装修参考 ${fmt(item.reference_unit_rent, 1)} · 合理清水 ${fmt(item.expected_unit_rent, 1)} · ${item.benchmark_mode || ""}`
      : `实际 ${fmt(item.unit_rent, 1)} 元/㎡ · 装修参考 ${fmt(item.reference_unit_rent, 1)} · 合理清水 ${fmt(item.expected_unit_rent, 1)} · 可信 ${item.confidence_score ?? "—"} · ${item.benchmark_mode || ""}`;
    body.append(el("p", "prices", priceLine));
    body.append(el("p", "reason", item.reason || ""));

    const actions = el("div", "actions");
    const ke = el("button", "", "打开工作台");
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
    return card;
  }

  function renderItems(args) {
    const reset = !!(args && args.reset);
    const incoming = (args && args.incoming) || [];
    if (reset) {
      grid.replaceChildren();
      state.items = incoming.slice();
    } else {
      state.items = state.items.concat(incoming);
    }
    const list = state.items;
    if (list.length) {
      empty.hidden = true;
    } else if (state.runMeta) {
      // 200 + 0 行:才显示"这一组没有房源。"。404 / 首次载入由 setHint 兜底。
      empty.hidden = false;
      empty.textContent = "这一组没有房源。点「全部」或关掉「有封面」试试。";
    } else {
      empty.hidden = true;
    }
    const renderList = reset ? list : incoming;
    renderList.forEach((item) => grid.append(renderCard(item)));
    loadMoreBox.hidden = !state.hasMore;
  }

  function renderDistricts(payload) {
    if (!districts) return;
    districts.replaceChildren();
    const items = (payload && payload.districts) || [];
    if (!items.length) return;
    const all = el("button", state.selectedDistricts.length ? "" : "is-active", "全部");
    all.type = "button";
    all.dataset.district = "";
    districts.append(all);
    const coreActive = CORE_DISTRICTS.every((name) => state.selectedDistricts.indexOf(name) >= 0)
      && state.selectedDistricts.length === CORE_DISTRICTS.length;
    const core = el("button", coreActive ? "is-active" : "", "五城区");
    core.type = "button";
    core.dataset.district = "__core__";
    districts.append(core);
    items.forEach((item) => {
      const active = state.selectedDistricts.indexOf(item.name) >= 0;
      const button = el("button", active ? "is-active" : "");
      button.type = "button";
      button.dataset.district = item.name;
      button.append(document.createTextNode(item.name + " "));
      button.append(el("span", "count", String(item.count)));
      districts.append(button);
    });
  }

  function renderRunMeta(payload) {
    if (!runMeta) return;
    if (!payload) {
      runMeta.textContent = "载入最新评分…";
      return;
    }
    const delta = typeof payload.delta_value === "number" ? payload.delta_value.toFixed(3) : "—";
    const groupText = ({ scored: "主榜", nearby_estimate: "周边估算", insufficient: "基准不足", data_error: "数据异常" })[payload.group] || payload.group;
    runMeta.textContent = `run ${payload.score_run_id} · δ ${delta} · ${groupText} ${payload.total} 套`;
    if (runFooter) {
      const finished = payload.scored_at || "—";
      const mv = payload.model_version || "—";
      runFooter.textContent = `score_run ${payload.score_run_id} · 模型 ${mv} · δ ${delta} · ${finished}`;
    }
  }

  function buildParams() {
    const params = new URLSearchParams();
    params.set("page", String(state.page));
    params.set("page_size", String(state.pageSize));
    params.set("sort", state.sort);
    params.set("group", state.group);
    if (state.deals) params.set("deals", "true");
    params.set("require_cover", state.requireCover ? "true" : "false");
    if (state.minConfidence) params.set("min_confidence", String(state.minConfidence));
    state.selectedDistricts.forEach((name) => params.append("district", name));
    return params;
  }

  async function fetchPage(reset) {
    if (state.loading) return;
    state.loading = true;
    setHint(reset ? "正在载入…" : "正在载入更多…");
    loadMoreButton.disabled = true;
    try {
      const url = `${RANK_ENDPOINT}?${buildParams().toString()}`;
      const response = await fetch(url);
      if (response.status === 404) {
        state.runMeta = null;
        state.total = 0;
        state.hasMore = false;
        renderItems({ reset: true, incoming: [] });
        renderRunMeta(null);
        setHint("还没有完成的评分批次");
        return;
      }
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const body = await response.json();
      state.runMeta = body;
      state.total = body.total;
      state.hasMore = Boolean(body.has_more);
      const incoming = (body.items || []).slice();
      renderDistricts(body);
      renderRunMeta(body);
      renderItems({ reset, incoming });
      setHint(groupHint(body));
    } catch (error) {
      setHint(error.message || "载入失败");
    } finally {
      state.loading = false;
      loadMoreButton.disabled = false;
    }
  }

  function groupHint(payload) {
    const map = {
      scored: "主榜按 quality_score_raw 排:无上游,直接看分。",
      nearby_estimate: "周边估算:分数是相对参考值,仅 quality_tier 可作筛选。",
      insufficient: "基准不足:本小区 / 商圈无可用对比,无法给分。",
      data_error: "数据异常:疑似车位 / 字段缺失,统一打 data_error。",
    };
    return map[payload.group] || "";
  }

  function markViewed(listingId, viewCount) {
    state.items.forEach((item) => {
      if (item.listing_id === listingId) item.view_count = viewCount;
    });
  }

  function markShared(listingId, shareCount) {
    let matched = false;
    state.items.forEach((item) => {
      if (item.listing_id === listingId) {
        item.share_count = shareCount;
        matched = true;
      }
    });
    // 命中列表外房源(分页切走)就不重渲,下次 fetchPage 会带新值回来。
    if (!matched) return;
    grid.replaceChildren();
    state.items.forEach((item) => grid.append(renderCard(item)));
  }

  function openWorkbench(item, button) {
    if (button.disabled) return;
    const listingId = item.listing_id || "";
    if (!listingId) {
      setHint("没有房源编号");
      return;
    }
    button.disabled = true;
    const original = button.textContent;
    button.textContent = "正在打开…";
    setHint(`正在注入凭证打开 ${listingId} …`);
    fetch(OPEN_ENDPOINT(listingId), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
    })
      .then((response) => response.json().catch(() => ({})).then((body) => ({ response, body })))
      .then(({ response, body }) => {
        if (!response.ok) {
          const detail = body && body.detail ? body.detail : "打开失败";
          throw new Error(detail);
        }
        button.textContent = "已打开";
        const count = typeof body.view_count === "number" ? body.view_count : (item.view_count || 0) + 1;
        markViewed(listingId, count);
        setHint("已在 CRM 工作台打开（已注入登录凭证）");
      })
      .catch((error) => {
        button.textContent = "打开失败";
        setHint(error.message || "打开失败");
      })
      .finally(() => {
        window.setTimeout(() => {
          button.disabled = false;
          button.textContent = original;
        }, 1600);
      });
  }

  function shareListing(item, button) {
    if (button.disabled) return;
    const listingId = item.listing_id || "";
    if (!listingId) {
      setHint("没有房源编号");
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
          // Web Share API 拒收(权限/手势/无 share target)→ 回退下载
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
        setHint(msg);
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

  function setGroup(name) {
    if (state.group === name) return;
    state.group = name;
    state.page = 1;
    groups.querySelectorAll("button").forEach((button) => {
      button.classList.toggle("is-active", button.dataset.group === name);
    });
    fetchPage(true);
  }

  function setSort(name) {
    if (state.sort === name) return;
    state.sort = name;
    state.page = 1;
    sortNav.querySelectorAll("button").forEach((button) => {
      button.classList.toggle("is-active", button.dataset.sort === name);
    });
    fetchPage(true);
  }

  groups.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-group]");
    if (!button) return;
    setGroup(button.dataset.group);
  });

  sortNav.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-sort]");
    if (!button) return;
    setSort(button.dataset.sort);
  });

  districts.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-district]");
    if (!button) return;
    const name = button.dataset.district || "";
    if (!name) {
      state.selectedDistricts = [];
    } else if (name === "__core__") {
      state.selectedDistricts = CORE_DISTRICTS.slice();
    } else {
      const index = state.selectedDistricts.indexOf(name);
      if (index >= 0) state.selectedDistricts.splice(index, 1);
      else state.selectedDistricts.push(name);
    }
    state.page = 1;
    renderDistricts(state.runMeta);
    fetchPage(true);
  });

  dealsToggle.addEventListener("click", () => {
    state.deals = !state.deals;
    dealsToggle.setAttribute("aria-pressed", state.deals ? "true" : "false");
    state.page = 1;
    fetchPage(true);
  });

  if (coverFilter) {
    coverFilter.addEventListener("click", () => {
      state.requireCover = !state.requireCover;
      coverFilter.classList.toggle("is-active", state.requireCover);
      coverFilter.setAttribute("aria-pressed", state.requireCover ? "true" : "false");
      state.page = 1;
      fetchPage(true);
    });
  }

  loadMoreButton.addEventListener("click", () => {
    if (!state.hasMore || state.loading) return;
    state.page += 1;
    fetchPage(false);
  });

  fetchPage(true);
})();
