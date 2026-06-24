"use strict";

let currentNovelId = 1;
let currentChapterId = null;
let currentVolumeId = null;
let currentNoteId = null;
let currentLocationId = null;
let currentModule = "writing";
let currentOutlineCat = "世界观";
let mdViewing = false;
let isGenesisRunning = false;
let genesisPaused = false;              // 暂停标志：优雅停止，保存进度
let genesisAborted = false;             // 中断标志：立即停止，不保存
let genesisAbortController = null;      // AbortController 用于中断 fetch
let genesisResumeState = null;          // 恢复状态快照（从 localStorage 加载）

/* ═══════════ Genesis State Persistence ═══════════ */
const GENESIS_STATE_KEY = "genesis_state";

function saveGenesisState(state) {
    state.savedAt = Date.now();
    localStorage.setItem(GENESIS_STATE_KEY + "_" + currentNovelId, JSON.stringify(state));
}

function loadGenesisState() {
    var raw = localStorage.getItem(GENESIS_STATE_KEY + "_" + currentNovelId);
    if (!raw) return null;
    try { return JSON.parse(raw); } catch(e) { return null; }
}

function clearGenesisState() {
    localStorage.removeItem(GENESIS_STATE_KEY + "_" + currentNovelId);
}

function formatGenesisResumeInfo(state) {
    if (state.parallel && state.chainStates) {
        var keys = ["outline", "map", "character"];
        var labels = { outline: "大纲", map: "地图", character: "人设" };
        var totalNodes = 0;
        var completedCount = 0;
        var statuses = [];
        keys.forEach(function(k) {
            var cs = state.chainStates[k];
            if (cs) {
                totalNodes += cs.executedNodeCount || 0;
                if (cs.completed) { completedCount++; }
                statuses.push((cs.completed ? "✅" : "⏳") + labels[k]);
            }
        });
        return "并行模式 · " + completedCount + "/3 链完成 · 共 " + totalNodes + " 节点 · " + statuses.join(" ");
    }
    var phaseNames = ["", "🗺️ 地理蓝图", "📖 世界观与大纲", "👤 群像人设"];
    var phaseLabel = phaseNames[state.phase] || "未知阶段";
    var nodeCount = state.executedNodeCount || 0;
    var nodeInfo = state.currentNodeId ? " · 节点 " + state.currentNodeId : "";
    return "阶段 " + state.phase + "/3 · " + phaseLabel + " · 已执行 " + nodeCount + " 步" + nodeInfo;
}

/* ═══════════ Clock Engine ═══════════ */
window.novelTimeConfig = { months_per_year: 12, days_per_month: 30, hours_per_day: 24 };
window.currentTick = 0;
window.locationsCache = [];
window.copilotChatHistory = [];

function tickToDate(tick, cfg) {
    cfg = cfg || window.novelTimeConfig;
    var hpd = cfg.hours_per_day || 24, dpm = cfg.days_per_month || 30, mpy = cfg.months_per_year || 12;
    var hour = tick % hpd, dTot = Math.floor(tick / hpd);
    var day = (dTot % dpm) + 1, mTot = Math.floor(dTot / dpm);
    var month = (mTot % mpy) + 1, year = Math.floor(mTot / mpy) + 1;
    return { year: year, month: month, day: day, hour: hour };
}
function dateToTick(y, m, d, h, cfg) {
    cfg = cfg || window.novelTimeConfig;
    var hpd = cfg.hours_per_day || 24, dpm = cfg.days_per_month || 30, mpy = cfg.months_per_year || 12;
    return (y - 1) * mpy * dpm * hpd + (m - 1) * dpm * hpd + (d - 1) * hpd + parseInt(h);
}

async function loadNovelTime() {
    var res = await fetch("/api/novels/" + currentNovelId + "/time");
    var data = await res.json();
    window.currentTick = data.current_tick || 0;
    window.novelTimeConfig = data.calendar_config || { months_per_year: 12, days_per_month: 30, hours_per_day: 24 };
    updateGlobalClockDisplay();
}
function updateGlobalClockDisplay() {
    var el = document.getElementById("global-clock-display");
    if (el) el.textContent = formatTickToLoreTime(window.currentTick);
}

function formatTickToLoreTime(tick, cfg) {
    cfg = cfg || window.novelTimeConfig;
    var d = tickToDate(tick, cfg);
    var yStr = d.year + "年";
    if (cfg.year_names && cfg.year_names.length > 0) yStr = cfg.year_names[(d.year - 1) % cfg.year_names.length] + "年";
    if (cfg.era_name) yStr = cfg.era_name + " " + yStr;
    var mStr = d.month + "月";
    if (cfg.month_names && cfg.month_names.length >= d.month) mStr = cfg.month_names[d.month - 1];
    var dStr = d.day + "日";
    if (cfg.day_names && cfg.day_names.length >= d.day) dStr = cfg.day_names[d.day - 1];
    var hStr = d.hour + "时";
    if (cfg.hour_names && cfg.hour_names.length > d.hour) hStr = cfg.hour_names[d.hour];
    return yStr + " " + mStr + " " + dStr + " " + hStr;
}

/* ═══════════ Novel ═══════════ */
async function loadNovels() {
    const sel = document.getElementById("novel-select"); sel.innerHTML = "";
    var found = false;
    (await (await fetch("/api/novels")).json()).forEach(n => {
        const o = document.createElement("option"); o.value = n.id; o.textContent = n.title;
        if (n.id === currentNovelId) { o.selected = true; found = true; }
        sel.appendChild(o);
    });
    // 如果当前指向的小说已被删除，自动切到第一本
    if (!found && sel.options.length > 0) {
        sel.options[0].selected = true;
        currentNovelId = Number(sel.options[0].value);
    }
}
async function switchNovel(nid) { currentNovelId = nid; await refreshAll(); }
document.getElementById("novel-select").addEventListener("change", e => switchNovel(Number(e.target.value)));
document.getElementById("novel-add-btn").addEventListener("click", async () => {
    const t = prompt("新小说名称：", "新小说"); if (!t) return;
    await fetch("/api/novels", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title: t }) });
    await loadNovels(); switchNovel(Number(document.getElementById("novel-select").lastElementChild.value));
});
document.getElementById("novel-del-btn").addEventListener("click", async () => {
    const sel = document.getElementById("novel-select");
    const nid = Number(sel.value);
    const name = sel.options[sel.selectedIndex]?.textContent || "未知";
    if (!confirm(`⚠️ 确定要删除整本小说「${name}」吗？\n\n这将永久清除该小说的：\n- 全部章节和分卷\n- 全部人物档案、势力、关系\n- 全部地点和地图\n- 全部大纲和时间线\n- 全部推理链方案\n\n此操作不可撤销！`)) return;
    const r = await fetch(`/api/novels/${nid}`, { method: "DELETE" });
    if (!r.ok) { alert("删除失败：" + (await r.json()).detail); return; }
    await loadNovels();
    const newSel = document.getElementById("novel-select");
    if (newSel.options.length > 0) { switchNovel(Number(newSel.options[0].value)); }
    else { location.reload(); }
});
function nidQ() { return `?novel_id=${currentNovelId}`; }

async function apiDel(url, id) { return await fetch(`${url}/${id}`, { method: "DELETE" }); }
async function delItem(url, id, refreshFn) { if (!confirm("确定删除？")) return; await apiDel(url, id); await refreshFn(); }
function delBtn(url, id, refreshFn) {
    const b = document.createElement("button"); b.className = "del-btn"; b.innerHTML = "&#128465;";
    b.addEventListener("click", e => { e.stopPropagation(); delItem(url, id, refreshFn); }); return b;
}

/* ═══════════ Module switch ═══════════ */
document.querySelectorAll(".nav-btn[data-module]").forEach(b => {
    b.addEventListener("click", () => {
        currentModule = b.dataset.module;
        document.getElementById("copilot-quick-btns").classList.toggle("hidden", currentModule !== "writing");
        document.querySelectorAll(".nav-btn").forEach(x => x.classList.remove("active")); b.classList.add("active");
        document.querySelectorAll(".module-panel").forEach(p => p.classList.add("hidden"));
        document.querySelectorAll(".ws-panel").forEach(p => p.classList.add("hidden"));
        document.getElementById("module-" + currentModule).classList.remove("hidden");
        const ws = document.getElementById("ws-" + currentModule); if (ws) ws.classList.remove("hidden");
        swTimeline();
        if (currentModule === "writing") { loadVolumes(); loadChapters(); }
        else if (currentModule === "outline") loadOutlineNotes();
        else if (currentModule === "characters") loadCharactersModule();
        else if (currentModule === "map") { loadLocationTree(); }
        else if (currentModule === "reasoning") loadReasoningModule();
        else if (currentModule === "sandbox") loadSandboxModule();
        else if (currentModule === "relations") { loadRelationsGraph(); setTimeout(function() { if (window.relationsNetwork) { window.relationsNetwork.redraw(); window.relationsNetwork.fit(); } }, 150); }
    });
});

/* ═══════════ Category nav (outline) ═══════════ */
document.querySelectorAll("#outline-category-nav li").forEach(li => {
    li.addEventListener("click", () => {
        document.querySelectorAll("#outline-category-nav li").forEach(x => x.classList.remove("active"));
        li.classList.add("active"); currentOutlineCat = li.dataset.cat;
        currentNoteId = null; outlineTitle.value = ""; outlineContent.value = ""; outlineCtxToggle.checked = false;
        document.getElementById("outline-pane-title").textContent = currentOutlineCat;
        document.getElementById("new-outline-child-btn").classList.remove("hidden");
        loadOutlineNotes();
    });
});

/* ═══════════ Timeline ═══════════ */
const tlMap = { writing: "MAIN_STORY", outline: "HISTORY", map: "WORLD", reasoning: "MAIN_STORY" };
let currentTimelineType = tlMap.writing;
function swTimeline() {
    currentTimelineType = tlMap[currentModule] || "MAIN_STORY";
    document.getElementById("timeline-title").textContent = { HISTORY: "历史时间线", MAIN_STORY: "正文时间线", WORLD: "世界时间线" }[currentTimelineType] || "时间线";
    loadTimeline();
}
async function loadTimeline() {
    const list = document.getElementById("timeline-list");
    const items = await (await fetch(`/api/timeline?novel_id=${currentNovelId}&event_type=${currentTimelineType}`)).json();
    list.innerHTML = ""; if (!items.length) { list.innerHTML = '<p class="placeholder">暂无事件</p>'; return; }
    items.forEach(it => {
        const d = document.createElement("div"); d.className = "timeline-item";
        d.innerHTML = `<div class="tl-label">${e(it.time_label)}</div><div class="tl-content">${e(it.content)}</div>`;
        const del = document.createElement("button"); del.className = "tl-del"; del.innerHTML = "&#128465;";
        del.addEventListener("click", async () => { if (!confirm("删除？")) return; await apiDel("/api/timeline", it.id); loadTimeline(); });
        d.appendChild(del); list.appendChild(d);
    });
}
document.getElementById("tl-add-btn").addEventListener("click", async () => {
    const label = document.getElementById("tl-time-label").value.trim(), content = document.getElementById("tl-content").value.trim();
    if (!content) return;
    await fetch(`/api/timeline?novel_id=${currentNovelId}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ event_type: currentTimelineType, time_label: label, content }) });
    document.getElementById("tl-time-label").value = ""; document.getElementById("tl-content").value = "";
    loadTimeline();
});

/* ═══════════ Writing ═══════════ */
const volumeList = document.getElementById("volume-list"), titleInput = document.getElementById("title-input"), contentInput = document.getElementById("content-input");
const saveBtn = document.getElementById("save-btn"), continueBtn = document.getElementById("continue-btn");

async function loadVolumes() {
    volumeList.innerHTML = ""; (await (await fetch("/api/volumes" + nidQ())).json()).forEach(v => renderVolumeItem(v));
}
function renderVolumeItem(v) {
    const li = document.createElement("li"); li.className = "outline-item";
    li.innerHTML = `<div class="outline-header"><span class="toggle-icon">&#9660;</span><span class="vol-title">${e(v.title)||"(无)"}</span></div><ul class="outline-children" data-vid="${v.id}"></ul>`;
    const hdr = li.querySelector(".outline-header");
    hdr.addEventListener("click", e => { if (e.target.closest(".del-btn")) return; li.classList.toggle("collapsed"); currentVolumeId = v.id;
        document.querySelectorAll(".outline-header").forEach(x => x.classList.remove("volume-active")); hdr.classList.add("volume-active"); });
    hdr.appendChild(delBtn("/api/volumes", v.id, async () => { currentVolumeId = null; await loadVolumes(); await loadChapters(); }));
    var volTitleSpan = hdr.querySelector(".vol-title");
    volTitleSpan.addEventListener("dblclick", async function(e) {
        e.stopPropagation();
        var newTitle = prompt("请输入新的分卷名称：", v.title);
        if (newTitle && newTitle.trim() !== "" && newTitle.trim() !== v.title) {
            await fetch("/api/volumes/" + v.id, { method: "PUT", headers: {"Content-Type": "application/json"}, body: JSON.stringify({title: newTitle.trim()}) });
            await loadVolumes();
            await loadChapters();
        }
    });
    volumeList.appendChild(li);
}
async function loadChapters() {
    const chs = await (await fetch("/api/chapters" + nidQ())).json();
    document.querySelectorAll(".outline-children").forEach(u => u.innerHTML = "");
    chs.forEach(ch => { const p = document.querySelector(`.outline-children[data-vid="${ch.volume_id}"]`);
        if (p) p.appendChild(createChapterLi(ch)); else volumeList.appendChild(createChapterLi(ch)); });
}
function createChapterLi(ch) {
    const li = document.createElement("li"); li.className = "chapter-li"; li.dataset.id = ch.id;
    li.innerHTML = `<span style="flex:1;overflow:hidden;text-overflow:ellipsis">${e(ch.title)||"(无)"}</span>`;
    li.addEventListener("click", () => selectChapter(ch)); li.appendChild(delBtn("/api/chapters", ch.id, loadChapters)); return li;
}
function selectChapter(ch) { currentChapterId = ch.id; currentVolumeId = ch.volume_id;
    titleInput.value = ch.title; contentInput.value = ch.content;
    const archiveViewer = document.getElementById("archived-content-viewer");
    if (ch.archived_content && ch.archived_content.trim()) {
        archiveViewer.textContent = ch.archived_content;
        archiveViewer.classList.remove("hidden");
    } else {
        archiveViewer.textContent = "";
        archiveViewer.classList.add("hidden");
    }
    document.querySelectorAll(".chapter-li").forEach(li => li.classList.toggle("active", Number(li.dataset.id) === ch.id)); }
async function createNewVolume() {
    await fetch("/api/volumes" + nidQ(), { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title: "新分卷", order_index: document.querySelectorAll(".outline-item").length }) });
    await loadVolumes(); await loadChapters(); }
async function createNewChapter() {
    if (currentVolumeId === null) { const v = await (await fetch("/api/volumes" + nidQ())).json(); if (v.length > 0) currentVolumeId = v[0].id; else { await createNewVolume(); const v2 = await (await fetch("/api/volumes" + nidQ())).json(); if (v2.length > 0) currentVolumeId = v2[v2.length - 1].id; else return; } }
    const body = { title: "新章节", content: "", order_index: document.querySelectorAll(".chapter-li").length, volume_id: currentVolumeId };
    const ch = await (await fetch("/api/chapters" + nidQ(), { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })).json();
    const p = document.querySelector(`.outline-children[data-vid="${ch.volume_id}"]`);
    if (p) p.appendChild(createChapterLi(ch)); else { await loadVolumes(); await loadChapters(); }
}
async function saveCurrentChapter() { if (!currentChapterId) return;
    const archived = document.getElementById("archived-content-viewer").textContent || "";
    await fetch(`/api/chapters/${currentChapterId}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title: titleInput.value, content: contentInput.value, archived_content: archived }) });
    saveBtn.textContent = "已保存"; setTimeout(() => saveBtn.textContent = "保存", 1000); await loadChapters(); }
saveBtn.addEventListener("click", saveCurrentChapter);

// word count + auto-save
let autoSaveTimer = null;
window.autoSaveTimer = autoSaveTimer;
contentInput.addEventListener("input", () => {
    document.getElementById("word-count").textContent = contentInput.value.length;
    document.getElementById("save-status").textContent = "未保存";
    clearTimeout(autoSaveTimer);
    autoSaveTimer = setTimeout(async () => {
        if (currentChapterId !== null) {
            const archived = document.getElementById("archived-content-viewer").textContent || "";
            await fetch(`/api/chapters/${currentChapterId}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title: titleInput.value, content: contentInput.value, archived_content: archived }) });
            document.getElementById("save-status").textContent = "已保存";
        }
    }, 1000);
});

function showArchiveOverlay() { document.getElementById("archive-overlay").classList.remove("hidden"); }
function hideArchiveOverlay() { document.getElementById("archive-overlay").classList.add("hidden"); }
function showToast(msg) { const t = document.getElementById("archive-toast"); if (!t) return; t.textContent = msg; t.classList.add("show"); setTimeout(() => t.classList.remove("show"), 4000); }

document.getElementById("archive-selected-btn").addEventListener("click", async () => {
    if (!currentChapterId) { showToast("请先选中一个章节"); return; }
    const sel = contentInput.value.substring(contentInput.selectionStart, contentInput.selectionEnd).trim();
    if (!sel) { showToast("请先在正文中选中要归档的文字"); return; }
    const s = getSettings();
    if (!s.apiKey) { document.getElementById("settings-modal").classList.remove("hidden"); return; }
    showArchiveOverlay();
    // Freeze auto-save during archive to prevent race conditions
    if (window.autoSaveTimer) { clearTimeout(window.autoSaveTimer); window.autoSaveTimer = null; }
    contentInput.disabled = true;
    try {
        const res = await fetch(`/api/chapters/${currentChapterId}/archive`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text_to_archive: sel, api_key: s.apiKey, base_url: s.baseUrl, model: s.model }) });
        const data = await res.json();
        if (!res.ok) { showToast("归档失败: " + (data.detail || "HTTP " + res.status)); return; }
        // refresh UI
        const archivedViewer = document.getElementById("archived-content-viewer");
        if (archivedViewer.textContent && archivedViewer.textContent.trim()) {
            archivedViewer.textContent += "\n\n" + sel;
        } else {
            archivedViewer.textContent = sel;
        }
        archivedViewer.classList.remove("hidden");
        contentInput.value = contentInput.value.replace(sel, "");
        contentInput.dispatchEvent(new Event("input"));
        // build detailed archive report
        let timeSkipStr = "";
        if (data.elapsed_years) timeSkipStr += data.elapsed_years + " 年 ";
        if (data.elapsed_months) timeSkipStr += data.elapsed_months + " 月 ";
        if (data.elapsed_days) timeSkipStr += data.elapsed_days + " 日 ";
        if (data.elapsed_hours) timeSkipStr += data.elapsed_hours + " 小时";
        if (!timeSkipStr) timeSkipStr = "无时间流逝";
        let report = "📦 【世界归档大成报告】\n";
        report += "-------------------------------------------\n";
        report += "⏰ 世界历时钟流逝：+ " + timeSkipStr + "\n";
        if (data.updated_characters && data.updated_characters.length > 0) {
            report += "\n👤 创世人设同步与生命体征反馈：\n";
            data.updated_characters.forEach(function(line) {
                report += "  • " + line + "\n";
            });
        } else {
            report += "\n👤 角色状态在本次归档中无更新。\n";
        }
        if (data.timeline_events && data.timeline_events.length > 0) {
            report += "\n📅 生成的历史时间线印记：\n";
            data.timeline_events.forEach(function(evt) {
                report += "  • " + evt + "\n";
            });
        }
        alert(report);
        showToast("🌎 归档万年历时钟推进、地理轨迹智能匹配成功！");
        await saveCurrentChapter();
        await loadCharacters();
        await loadTimeline();
        loadNovelTime();
        if (currentChapterId) { selectChapter({ id: currentChapterId, title: titleInput.value, content: contentInput.value, archived_content: document.getElementById("archived-content-viewer").textContent, volume_id: currentVolumeId }); }
    } catch (e) { showToast("归档失败: " + e.message); }
    finally { contentInput.disabled = false; hideArchiveOverlay(); }
});

/* ═══════════ Outline (dual-pane + tree + MD) ═══════════ */
const outlineTitle = document.getElementById("outline-title-input"), outlineContent = document.getElementById("outline-content-input");
const outlineCatInput = document.getElementById("outline-category-input"), outlineCtxToggle = document.getElementById("outline-context-toggle");
const outlineMdPreview = document.getElementById("outline-md-preview"), saveOutlineBtn = document.getElementById("save-outline-btn");
const outlineNoteList = document.getElementById("outline-note-list");

async function loadOutlineNotes() {
    const notes = await (await fetch("/api/outlines" + nidQ())).json();
    const filtered = notes.filter(n => n.category === currentOutlineCat);
    outlineNoteList.innerHTML = "";
    if (currentOutlineCat === "剧情大纲") {
        const tree = buildTree(filtered);
        renderTree(tree, 0, outlineNoteList);
    } else {
        filtered.forEach(n => { outlineNoteList.appendChild(createOutlineLi(n, false)); });
    }
}
function buildTree(items) {
    const map = {}; const roots = [];
    items.forEach(it => { map[it.id] = { ...it, children: [] }; });
    items.forEach(it => { if (it.parent_id && map[it.parent_id]) map[it.parent_id].children.push(map[it.id]); else roots.push(map[it.id]); });
    return roots;
}
function renderTree(nodes, depth, parentEl) {
    nodes.forEach(n => {
        const li = createOutlineLi(n, depth > 0);
        li.style.paddingLeft = (10 + depth * 18) + "px";
        parentEl.appendChild(li);
        if (n.children && n.children.length) renderTree(n.children, depth + 1, parentEl);
    });
}
function createOutlineLi(n, isChild) {
    const li = document.createElement("li"); li.className = "outline-note-li" + (isChild ? " child" : ""); li.dataset.noteId = n.id;
    const dot = document.createElement("span"); dot.className = "context-dot" + (n.is_always_context ? " on" : "");
    li.appendChild(dot);
    const span = document.createElement("span"); span.className = "note-text"; span.textContent = n.title || "(无)";
    li.appendChild(span);
    li.addEventListener("click", () => selectOutlineNote(n));
    li.appendChild(delBtn("/api/outlines", n.id, loadOutlineNotes));
    return li;
}
function selectOutlineNote(n) {
    currentNoteId = n.id; outlineTitle.value = n.title; outlineContent.value = n.description;
    outlineCatInput.value = n.category; outlineCtxToggle.checked = n.is_always_context;
    document.querySelectorAll(".outline-note-li").forEach(x => x.classList.remove("active"));
    document.querySelector(`.outline-note-li[data-note-id="${n.id}"]`)?.classList.add("active");
}
document.getElementById("new-outline-note-btn").addEventListener("click", async () => {
    const body = { title: "新大纲", description: "", category: currentOutlineCat, order_index: 0, is_always_context: false };
    await fetch("/api/outlines" + nidQ(), { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    await loadOutlineNotes();
});
document.getElementById("new-outline-child-btn").addEventListener("click", async () => {
    if (!currentNoteId) { alert("请先在列表中选中一个父条目"); return; }
    const body = { title: "子条目", description: "", category: currentOutlineCat, order_index: 0, is_always_context: false, parent_id: currentNoteId };
    await fetch("/api/outlines" + nidQ(), { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    await loadOutlineNotes();
});
saveOutlineBtn.addEventListener("click", async () => {
    if (!currentNoteId) return;
    await fetch(`/api/outlines/${currentNoteId}`, { method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: outlineTitle.value, description: outlineContent.value, category: outlineCatInput.value, is_always_context: outlineCtxToggle.checked }) });
    await loadOutlineNotes();
});

/* MD tabs */
document.querySelectorAll(".md-tab").forEach(b => b.addEventListener("click", () => {
    document.querySelectorAll(".md-tab").forEach(x => x.classList.remove("active")); b.classList.add("active");
    if (b.dataset.view === "preview") { mdViewing = true; outlineContent.classList.add("hidden"); outlineMdPreview.classList.remove("hidden"); outlineMdPreview.innerHTML = parseMarkdown(outlineContent.value); }
    else { mdViewing = false; outlineContent.classList.remove("hidden"); outlineMdPreview.classList.add("hidden"); }
}));

function parseMarkdown(md) {
    let html = e(md);
    html = html.replace(/^### (.+)$/gm, "<h3>$1</h3>");
    html = html.replace(/^## (.+)$/gm, "<h2>$1</h2>");
    html = html.replace(/^# (.+)$/gm, "<h1>$1</h1>");
    html = html.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
    html = html.replace(/\*(.+?)\*/g, "<em>$1</em>");
    html = html.replace(/`(.+?)`/g, "<code>$1</code>");
    html = html.replace(/^\- (.+)$/gm, "<li>$1</li>");
    html = html.replace(/(<li>.*<\/li>\n?)+/g, m => "<ul>" + m + "</ul>");
    html = html.replace(/^---$/gm, "<hr>");
    html = html.replace(/\n\n/g, "</p><p>");
    html = html.replace(/\n/g, "<br>");
    html = "<p>" + html + "</p>";
    html = html.replace(/<p><\/p>/g, "");
    return html;
}

/* ═══════════ Characters Module ═══════════ */
let currentCharId = null;
let charTemplate = [];
let charEditMode = "card";

async function loadCharactersModule() {
    await loadCharList();
    const novel = await (await fetch("/api/novels")).json();
    const n = novel.find(x => x.id === currentNovelId);
    charTemplate = (n && n.character_template) ? n.character_template : [];
    loadCharEditUI();
}

async function loadCharList() {
    const list = document.getElementById("char-list"); list.innerHTML = "";
    const chars = await (await fetch("/api/characters" + nidQ())).json();
    chars.forEach(ch => {
        const li = document.createElement("li"); li.className = "chapter-li"; li.dataset.cid = ch.id;
        const displayName = (ch.attributes && ch.attributes["基础信息"] && ch.attributes["基础信息"]["姓名"]) || ch.name || "(未)";
        li.innerHTML = `<span style="flex:1">${e(displayName)}</span>`;
        li.addEventListener("click", () => { currentCharId = ch.id; loadCharEditUI(); document.querySelectorAll("#char-list .chapter-li").forEach(x => x.classList.remove("active")); li.classList.add("active"); });
        li.appendChild(delBtn("/api/characters", ch.id, loadCharactersModule));
        list.appendChild(li);
    });
}

function loadCharEditUI() {
    if (charEditMode === "card") renderDynamicForm();
    else if (charEditMode === "template") renderTemplateEditor();
    else if (charEditMode === "snapshot") renderSnapshots();
}

async function renderDynamicForm() {
    const container = document.getElementById("char-dynamic-form"); container.innerHTML = "";
    if (!charTemplate.length) { container.innerHTML = '<p class="placeholder">请先在模板设置中配置字段</p>'; return; }
    let attrs = {};
    if (currentCharId) {
        const ch = await (await fetch(`/api/characters/${currentCharId}?novel_id=${currentNovelId}`)).json();
        attrs = ch.attributes || {};
    }
    charTemplate.forEach(grp => {
        const fs = document.createElement("fieldset"); fs.className = "char-fieldset";
        fs.innerHTML = `<legend>${e(grp.group)}</legend>`;
        if (grp.fields.length === 0) {
            // 空字段组：展示已有的属性子键（AI 可自由填充）
            const existing = attrs[grp.group] || {};
            const keys = Object.keys(existing);
            if (keys.length === 0) {
                const hint = document.createElement("div");
                hint.style.cssText = "font-size:.7rem;color:var(--text-muted);padding:4px 0;";
                hint.textContent = "（此分组字段由 AI 自动填充，无需手动编辑）";
                fs.appendChild(hint);
            }
            keys.forEach(k => {
                const row = document.createElement("div"); row.className = "char-field";
                row.innerHTML = `<label>${e(k)}</label><input type="text" data-group="${e(grp.group)}" data-field="${e(k)}" value="${e(existing[k] || '')}">`;
                fs.appendChild(row);
            });
        } else {
            grp.fields.forEach(fld => {
                const row = document.createElement("div"); row.className = "char-field";
                const val = (attrs[grp.group] && attrs[grp.group][fld]) ? attrs[grp.group][fld] : "";
                row.innerHTML = `<label>${e(fld)}</label><input type="text" data-group="${e(grp.group)}" data-field="${e(fld)}" value="${e(val)}">`;
                fs.appendChild(row);
            });
        }
        container.appendChild(fs);
    });
    // 🏛️ 填充势力下拉
    try {
        const factions = await (await fetch("/api/factions?novel_id=" + currentNovelId)).json();
        const sel = document.getElementById("char-faction-sel");
        if (sel) {
            sel.innerHTML = '<option value="">-- 散修 / 无势力 --</option>';
            factions.forEach(function(f) {
                sel.innerHTML += '<option value="' + f.id + '">' + e(f.name) + '</option>';
            });
            if (currentCharId) {
                const ch = await (await fetch("/api/characters/" + currentCharId + "?novel_id=" + currentNovelId)).json();
                if (ch.faction_id) sel.value = ch.faction_id;
                const roleInp = document.getElementById("char-faction-role-inp");
                if (roleInp) roleInp.value = ch.faction_role || "";
            }
        }
    } catch(e) { /* 势力列表加载失败不影响编辑 */ }
    // 🔗 加载人物关系连线
    await loadCharRelations();
    renderTimeEditor(container);
}

async function loadCharRelations() {
    if (!currentCharId) return;
    var listEl = document.getElementById("char-relations-list");
    var countEl = document.getElementById("char-relations-count");
    var targetSel = document.getElementById("char-rel-target-sel");
    if (!listEl) return;
    try {
        var [rels, chars] = await Promise.all([
            fetch("/api/character_relations?novel_id=" + currentNovelId).then(function(r){return r.json();}),
            fetch("/api/characters?novel_id=" + currentNovelId).then(function(r){return r.json();}),
        ]);
        // Fill target dropdown
        if (targetSel) {
            targetSel.innerHTML = '<option value="">-- 关联角色 --</option>';
            chars.forEach(function(c) {
                if (c.id !== currentCharId) targetSel.innerHTML += '<option value="' + c.id + '">' + e(c.name) + '</option>';
            });
        }
        // Filter relations involving this character
        var myRels = rels.filter(function(r) { return r.source_id === currentCharId || r.target_id === currentCharId; });
        countEl.textContent = myRels.length ? "(" + myRels.length + ")" : "";
        listEl.innerHTML = "";
        myRels.forEach(function(r) {
            var isSource = r.source_id === currentCharId;
            var otherId = isSource ? r.target_id : r.source_id;
            var other = chars.find(function(c) { return c.id === otherId; });
            var otherName = other ? other.name : "未知#" + otherId;
            var dir = isSource ? "→" : "←";
            var color = r.weight < 0 ? "#ef4444" : (r.weight > 50 ? "#22c55e" : "#888");
            var row = document.createElement("div");
            row.style.cssText = "display:flex;align-items:center;gap:6px;padding:4px 0;border-bottom:1px solid var(--border);";
            row.innerHTML = '<span style="color:' + color + ';font-weight:700;">' + dir + '</span>'
                + '<span style="flex:1;"><b>' + e(r.label || "关联") + '</b> ' + e(r.description || "") + '</span>'
                + '<span style="font-size:0.65rem;color:' + color + ';">' + (r.weight > 0 ? "+" : "") + r.weight + '</span>'
                + '<button data-del-rel="' + r.id + '" style="font-size:0.6rem;padding:0 4px;border:none;background:none;cursor:pointer;color:var(--text-muted);">✕</button>';
            listEl.appendChild(row);
        });
        // Delete handlers
        listEl.querySelectorAll("[data-del-rel]").forEach(function(btn) {
            btn.addEventListener("click", async function() {
                var rid = parseInt(btn.dataset.delRel);
                await fetch("/api/character_relations/" + rid, { method: "DELETE" });
                await loadCharRelations();
            });
        });
    } catch(e) { /* 加载失败不阻塞 */ }
}

function renderTimeEditor(container) {
    var timeFs = document.createElement("fieldset");
    timeFs.className = "char-fieldset";
    timeFs.style.cssText = "margin-top:16px;border-color:#93c5fd;background:#eff6ff;";
    timeFs.id = "char-time-editor";
    timeFs.innerHTML = '<legend style="color:#1d4ed8;">⏳ 行动规律与轨迹</legend>'
        + '<div style="margin-bottom:8px;"><strong style="font-size:0.78rem;">📅 周期作息</strong><button onclick="addRoutineRow()" style="margin-left:8px;font-size:0.68rem;padding:2px 8px;" class="sys-btn sys-btn-ghost">+ 新增</button></div>'
        + '<div id="routine-rows"></div>'
        + '<div style="margin-top:12px;margin-bottom:8px;"><strong style="font-size:0.78rem;">🗺️ 行动轨迹</strong><button onclick="addTrajectoryRow()" style="margin-left:8px;font-size:0.68rem;padding:2px 8px;" class="sys-btn sys-btn-ghost">+ 新增</button></div>'
        + '<div id="trajectory-rows"></div>'
        + '<button onclick="saveTimeData()" style="margin-top:8px;font-size:0.75rem;padding:4px 12px;" class="sys-btn sys-btn-primary">💾 保存作息与轨迹</button>';
    container.appendChild(timeFs);
    loadLocationsCache().then(function() { loadTimeData(); });
}

function locationOptionsHtml(selId) {
    var h = '<select class="traj-loc">';
    window.locationsCache.forEach(function(l) { h += '<option value="' + l.id + '"' + (l.id === selId ? " selected" : "") + '>' + e(l.name) + '</option>'; });
    h += '</select>';
    return h;
}

function addRoutineRow(data) {
    data = data || { location_id: null, cycle_type: "日", cycle_value: "", activity: "" };
    var div = document.createElement("div");
    div.className = "time-row";
    div.style.cssText = "display:flex;gap:4px;align-items:center;margin-bottom:3px;font-size:0.72rem;";
    div.innerHTML = '<select class="rt-cycle"><option value="日"' + (data.cycle_type === "日" ? " selected" : "") + '>每天</option><option value="月"' + (data.cycle_type === "月" ? " selected" : "") + '>每月</option><option value="年"' + (data.cycle_type === "年" ? " selected" : "") + '>每年</option></select>'
        + '<input class="rt-value" placeholder="时刻/日期" value="' + e(data.cycle_value || "") + '" style="width:60px;">'
        + '<span>在</span>' + locationOptionsHtml(data.location_id)
        + '<input class="rt-activity" placeholder="做什么" value="' + e(data.activity || "") + '" style="flex:1;">'
        + '<button onclick="this.parentElement.remove()" class="sys-btn sys-btn-danger">✕</button>';
    document.getElementById("routine-rows").appendChild(div);
}

function addTrajectoryRow(data) {
    data = data || { location_id: null, start_tick: window.currentTick, end_tick: null, reason: "" };
    var d = tickToDate(data.start_tick || window.currentTick);
    var div = document.createElement("div");
    div.className = "time-row";
    div.style.cssText = "display:flex;gap:4px;align-items:center;margin-bottom:3px;font-size:0.72rem;flex-wrap:wrap;";
    div.innerHTML = '<span>起:</span>'
        + '<input type="number" class="traj-y" value="' + d.year + '" style="width:40px;" min="1">年'
        + '<input type="number" class="traj-m" value="' + d.month + '" style="width:40px;" min="1">月'
        + '<input type="number" class="traj-d" value="' + d.day + '" style="width:40px;" min="1">日'
        + '<input type="number" class="traj-h" value="' + d.hour + '" style="width:40px;" min="0">时'
        + '<span>→</span>' + locationOptionsHtml(data.location_id)
        + '<input class="traj-reason" placeholder="原因" value="' + e(data.reason || "") + '" style="width:100px;">'
        + '<button onclick="this.parentElement.remove()" class="sys-btn sys-btn-danger">✕</button>';
    document.getElementById("trajectory-rows").appendChild(div);
}

async function loadTimeData() {
    if (!currentCharId) return;
    var res = await fetch("/api/characters/" + currentCharId + "/time_data");
    var data = await res.json();
    document.getElementById("routine-rows").innerHTML = "";
    document.getElementById("trajectory-rows").innerHTML = "";
    (data.routines || []).forEach(function(r) { addRoutineRow(r); });
    (data.trajectories || []).forEach(function(t) { addTrajectoryRow(t); });
}

async function loadLocationsCache() {
    var res = await fetch("/api/locations?novel_id=" + currentNovelId);
    window.locationsCache = await res.json();
}

async function saveTimeData() {
    if (!currentCharId) return;
    var routines = [], trajectories = [];
    document.querySelectorAll("#routine-rows .time-row").forEach(function(row) {
        routines.push({
            cycle_type: row.querySelector(".rt-cycle").value,
            cycle_value: row.querySelector(".rt-value").value.trim(),
            location_id: parseInt(row.querySelector(".traj-loc").value) || 0,
            activity: row.querySelector(".rt-activity").value.trim()
        });
    });
    document.querySelectorAll("#trajectory-rows .time-row").forEach(function(row) {
        var y = parseInt(row.querySelector(".traj-y").value) || 1;
        var m = parseInt(row.querySelector(".traj-m").value) || 1;
        var d = parseInt(row.querySelector(".traj-d").value) || 1;
        var h = parseInt(row.querySelector(".traj-h").value) || 0;
        trajectories.push({
            location_id: parseInt(row.querySelector(".traj-loc").value) || 0,
            start_tick: dateToTick(y, m, d, h),
            end_tick: null,
            reason: row.querySelector(".traj-reason").value.trim()
        });
    });
    await fetch("/api/characters/" + currentCharId + "/time_data", { method: "PUT", headers: {"Content-Type": "application/json"}, body: JSON.stringify({ routines: routines, trajectories: trajectories }) });
    showToast("作息与轨迹已保存");
}

function renderTemplateEditor() {
    const protectedGroups = ["基础信息", "外貌特征"];
    const defaultFields = { "基础信息": ["姓名", "年龄", "性别", "性格"], "外貌特征": ["身高", "发色", "瞳色"] };
    const container = document.getElementById("template-editor"); container.innerHTML = "";
    charTemplate.forEach((grp, gi) => {
        const isProtected = protectedGroups.includes(grp.group);
        const div = document.createElement("div"); div.className = "tmpl-group";
        const delBtn = isProtected ? "" : ` <button data-delgroup="${gi}" class="sys-btn sys-btn-danger">&#10005; 删除分组</button>`;
        div.innerHTML = `<h4>${e(grp.group)}${delBtn}</h4><div class="tmpl-fields"></div>`;
        const fieldsDiv = div.querySelector(".tmpl-fields");
        const defFlds = defaultFields[grp.group] || [];
        grp.fields.forEach((fld, fi) => {
            const isDefField = isProtected && defFlds.includes(fld);
            const sp = document.createElement("span"); sp.className = "tmpl-field";
            sp.innerHTML = `${e(fld)}${isDefField ? "" : ` <button data-delfield="${gi}" data-fi="${fi}" class="sys-btn sys-btn-danger">&#10005;</button>`}`;
            fieldsDiv.appendChild(sp);
        });
        const addRow = document.createElement("div"); addRow.className = "tmpl-add-row";
        addRow.innerHTML = `<input type="text" placeholder="新字段名"><button data-addfield="${gi}" class="sys-btn sys-btn-primary">+ 字段</button>`;
        div.appendChild(addRow);
        container.appendChild(div);
    });
    const addGrp = document.createElement("div"); addGrp.id = "add-group-row";
    addGrp.innerHTML = `<input type="text" placeholder="新分组名"><button id="add-group-btn" class="sys-btn sys-btn-primary">+ 分组</button>`;
    container.appendChild(addGrp);

    container.querySelectorAll("[data-addfield]").forEach(b => {
        b.addEventListener("click", () => {
            const gi = Number(b.dataset.addfield);
            const inp = b.parentElement.querySelector("input");
            const val = inp.value.trim(); if (!val) return;
            charTemplate[gi].fields.push(val); inp.value = ""; renderTemplateEditor();
        });
    });
    container.querySelectorAll("[data-delfield]").forEach(b => {
        b.addEventListener("click", () => {
            const gi = Number(b.dataset.delfield), fi = Number(b.dataset.fi);
            charTemplate[gi].fields.splice(fi, 1); renderTemplateEditor();
        });
    });
    container.querySelectorAll("[data-delgroup]").forEach(b => {
        b.addEventListener("click", () => {
            charTemplate.splice(Number(b.dataset.delgroup), 1); renderTemplateEditor();
        });
    });
    document.getElementById("add-group-btn")?.addEventListener("click", () => {
        const inp = document.querySelector("#add-group-row input");
        const val = inp.value.trim(); if (!val) return;
        charTemplate.push({ group: val, fields: [] }); inp.value = ""; renderTemplateEditor();
    });
}

document.querySelectorAll(".mode-tab").forEach(b => b.addEventListener("click", () => {
    document.querySelectorAll(".mode-tab").forEach(x => x.classList.remove("active")); b.classList.add("active");
    charEditMode = b.dataset.mode;
    document.getElementById("char-card-mode").classList.toggle("hidden", charEditMode !== "card");
    document.getElementById("char-template-mode").classList.toggle("hidden", charEditMode !== "template");
    document.getElementById("char-snapshot-mode").classList.toggle("hidden", charEditMode !== "snapshot");
    loadCharEditUI();
}));

document.getElementById("save-template-btn").addEventListener("click", async () => {
    await fetch(`/api/novels/${currentNovelId}/template`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(charTemplate) });
    loadCharactersModule();
});

document.getElementById("char-rel-add-btn")?.addEventListener("click", async () => {
    if (!currentCharId) return;
    var targetId = parseInt(document.getElementById("char-rel-target-sel")?.value || "0");
    if (!targetId) { showToast("请选择关联角色"); return; }
    var label = document.getElementById("char-rel-label-inp")?.value?.trim() || "关联";
    var weight = parseInt(document.getElementById("char-rel-weight-inp")?.value || "0");
    await fetch("/api/character_relations?novel_id=" + currentNovelId, {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({ source_id: currentCharId, target_id: targetId, label: label, weight: weight, description: "" }),
    });
    document.getElementById("char-rel-label-inp").value = "";
    document.getElementById("char-rel-weight-inp").value = "0";
    await loadCharRelations();
});

document.getElementById("save-char-btn").addEventListener("click", async () => {
    if (!currentCharId) return;
    const ch = await (await fetch(`/api/characters/${currentCharId}?novel_id=${currentNovelId}`)).json();
    const attrs = {};
    document.querySelectorAll("#char-dynamic-form input").forEach(inp => {
        const g = inp.dataset.group, f = inp.dataset.field;
        if (!attrs[g]) attrs[g] = {};
        attrs[g][f] = inp.value;
    });
    await fetch(`/api/characters/${currentCharId}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({
        name: ch.name, aliases: ch.aliases, description: ch.description, status: ch.status,
        attributes: attrs, is_always_context: ch.is_always_context,
        faction_id: document.getElementById("char-faction-sel")?.value ? parseInt(document.getElementById("char-faction-sel").value) : null,
        faction_role: document.getElementById("char-faction-role-inp")?.value || "",
    }) });
    loadCharactersModule();
    loadCharacters();
});

document.getElementById("new-char-btn").addEventListener("click", async () => {
    const name = prompt("人物名称：", "新人物"); if (!name) return;
    await fetch("/api/characters" + nidQ(), { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name, status: "存活", attributes: {} }) });
    await loadCharactersModule();
});

/* ═══════════ Archive + Snapshot ═══════════ */
let currentSnapshotId = null;

document.getElementById("archive-btn").addEventListener("click", async () => {
    if (!currentCharId) return;
    const vname = prompt("版本名称：", `V${Date.now() % 1000}`);
    if (!vname) return;
    const ch = await (await fetch(`/api/characters/${currentCharId}?novel_id=${currentNovelId}`)).json();
    const attrs = {};
    document.querySelectorAll("#char-dynamic-form input").forEach(inp => {
        const g = inp.dataset.group, f = inp.dataset.field;
        if (!attrs[g]) attrs[g] = {};
        attrs[g][f] = inp.value;
    });
    // save current
    await fetch(`/api/characters/${currentCharId}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({
        name: ch.name, aliases: ch.aliases, description: ch.description, status: ch.status,
        attributes: attrs, is_always_context: ch.is_always_context,
        faction_id: document.getElementById("char-faction-sel")?.value ? parseInt(document.getElementById("char-faction-sel").value) : null,
        faction_role: document.getElementById("char-faction-role-inp")?.value || "",
    }) });
    // create snapshot
    await fetch(`/api/characters/${currentCharId}/snapshots`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ version_name: vname, attributes: attrs }) });
    loadCharactersModule();
});

async function renderSnapshots() {
    if (!currentCharId) { document.getElementById("snapshot-list").innerHTML = '<p class="placeholder">请先选中人物</p>'; return; }
    const snaps = await (await fetch(`/api/characters/${currentCharId}/snapshots`)).json();
    const list = document.getElementById("snapshot-list"); list.innerHTML = "";
    const preview = document.getElementById("snapshot-preview"); preview.innerHTML = '<p class="placeholder">请选择一个快照</p>';
    document.getElementById("restore-snapshot-btn").classList.add("hidden");
    if (!snaps.length) { list.innerHTML = '<p class="placeholder">暂无快照</p>'; return; }
    snaps.forEach(s => {
        const div = document.createElement("div"); div.className = "chapter-li"; div.style.cssText = "padding:8px 10px;cursor:pointer;";
        div.innerHTML = `<span style="flex:1">${e(s.version_name)}</span><span style="font-size:0.65rem;color:var(--text-muted)">${new Date(s.created_at).toLocaleDateString()}</span>`;
        div.addEventListener("click", () => { currentSnapshotId = s.id; renderSnapshotPreview(s); });
        list.appendChild(div);
    });
}

function renderSnapshotPreview(s) {
    const preview = document.getElementById("snapshot-preview"); preview.innerHTML = "";
    if (!charTemplate.length) { preview.innerHTML = '<p class="placeholder">无模板</p>'; return; }
    charTemplate.forEach(grp => {
        const fs = document.createElement("fieldset"); fs.className = "char-fieldset";
        fs.innerHTML = `<legend>${e(grp.group)}</legend>`;
        grp.fields.forEach(fld => {
            const row = document.createElement("div"); row.className = "char-field";
            const val = (s.attributes[grp.group] && s.attributes[grp.group][fld]) ? s.attributes[grp.group][fld] : "";
            row.innerHTML = `<label>${e(fld)}</label><input type="text" value="${e(String(val))}" disabled style="background:#f8f6f2;cursor:default;">`;
            fs.appendChild(row);
        });
        preview.appendChild(fs);
    });
    document.getElementById("restore-snapshot-btn").classList.remove("hidden");
}

document.getElementById("restore-snapshot-btn").addEventListener("click", async () => {
    if (!currentCharId || !currentSnapshotId) return;
    if (!confirm("确定要恢复至此版本吗？当前未保存的修改将丢失。")) return;
    const snaps = await (await fetch(`/api/characters/${currentCharId}/snapshots`)).json();
    const snap = snaps.find(s => s.id === currentSnapshotId);
    if (!snap) return;
    const ch = await (await fetch(`/api/characters/${currentCharId}?novel_id=${currentNovelId}`)).json();
    await fetch(`/api/characters/${currentCharId}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: ch.name, aliases: ch.aliases, description: ch.description, status: ch.status, attributes: snap.attributes }) });
    currentSnapshotId = null;
    loadCharactersModule();
});
/* ═══════════ Map — Grid 25x25 ═══════════ */
const SCALE_LEVELS = ["大千世界", "宇宙", "星球", "大陆", "国家", "城池", "街区", "建筑"];
let mapMode = "list";
let mapParentId = null;
let mapScaleIndex = 0;
let mapBreadcrumbs = [{ id: null, name: "大千世界" }];

document.getElementById("map-list-btn").addEventListener("click", () => {
    mapMode = "list";
    document.getElementById("map-list-btn").classList.add("active");
    document.getElementById("map-grid-btn").classList.remove("active");
    document.getElementById("map-list-mode").classList.remove("hidden");
    document.getElementById("map-grid-mode").classList.add("hidden");
});
document.getElementById("map-grid-btn").addEventListener("click", () => {
    mapMode = "grid";
    document.getElementById("map-grid-btn").classList.add("active");
    document.getElementById("map-list-btn").classList.remove("active");
    document.getElementById("map-list-mode").classList.add("hidden");
    document.getElementById("map-grid-mode").classList.remove("hidden");
    mapParentId = null;
    mapScaleIndex = 0;
    mapBreadcrumbs = [{ id: null, name: "大千世界" }];
    renderMapGrid();
});

function enterLocationGrid(loc) {
    mapMode = "grid";
    document.getElementById("map-grid-btn").classList.add("active");
    document.getElementById("map-list-btn").classList.remove("active");
    document.getElementById("map-list-mode").classList.add("hidden");
    document.getElementById("map-grid-mode").classList.remove("hidden");
    mapParentId = loc.id;
    const parentIdx = SCALE_LEVELS.indexOf(loc.scale_level);
    mapScaleIndex = parentIdx >= 0 ? parentIdx : 0;
    mapBreadcrumbs = [
        { id: null, name: "大千世界" },
        { id: loc.id, name: loc.name },
    ];
    renderMapGrid();
}

function updateBreadcrumbs() {
    const bc = document.getElementById("map-breadcrumbs");
    bc.innerHTML = mapBreadcrumbs.map((b, i) =>
        i < mapBreadcrumbs.length - 1
            ? `<span data-idx="${i}">${e(b.name)}</span> > `
            : `<strong>${e(b.name)}</strong>`
    ).join("");
    bc.querySelectorAll("span").forEach(s => {
        s.addEventListener("click", () => {
            const idx = Number(s.dataset.idx);
            mapBreadcrumbs = mapBreadcrumbs.slice(0, idx + 1);
            mapParentId = mapBreadcrumbs[mapBreadcrumbs.length - 1].id;
            mapScaleIndex = idx;
            renderMapGrid();
        });
    });
}

async function renderMapGrid() {
    const container = document.getElementById("fractal-map-container");
    container.innerHTML = '<div class="map-grid" id="map-grid"></div>';
    updateBreadcrumbs();

    const childLevel = mapScaleIndex + 1 < SCALE_LEVELS.length ? SCALE_LEVELS[mapScaleIndex + 1] : "具体建筑";
    let url = `/api/locations?novel_id=${currentNovelId}&scale_level=${encodeURIComponent(childLevel)}`;
    if (mapParentId !== null) {
        url += `&parent_id=${mapParentId}`;
    } else {
        url += `&filter_null_parent=true`;
    }
    const locs = await (await fetch(url)).json();
    const byCoord = {};
    locs.forEach(l => { if (l.grid_x != null && l.grid_y != null) byCoord[`${l.grid_x},${l.grid_y}`] = l; });

    const grid = document.getElementById("map-grid");
    for (let y = 0; y < 25; y++) {
        for (let x = 0; x < 25; x++) {
            const cell = document.createElement("div");
            cell.className = "map-cell";
            cell.dataset.x = x;
            cell.dataset.y = y;
            const loc = byCoord[`${x},${y}`];
            if (loc) {
                cell.classList.add("has-loc");
                cell.textContent = loc.name.substring(0, 2);
                cell.title = loc.name;
                cell.addEventListener("click", (e) => onMapCellClick(e, loc, x, y));
            } else {
                cell.textContent = "";
                cell.title = `(${x},${y})`;
                cell.addEventListener("click", (e) => onMapCellClick(e, null, x, y));
            }
            grid.appendChild(cell);
        }
    }
}

function closeContextMenu() {
    const m = document.querySelector(".map-context-menu");
    if (m) m.remove();
}

function onMapCellClick(e, loc, x, y) {
    closeContextMenu();
    if (loc) {
        const menu = document.createElement("div");
        menu.className = "map-context-menu";
        menu.style.left = e.clientX + "px";
        menu.style.top = e.clientY + "px";
        menu.innerHTML = `<button data-action="edit">📝 编辑详情</button>`;
        if (mapScaleIndex < SCALE_LEVELS.length - 1) {
            menu.innerHTML += `<button data-action="drill">🔍 进入内部</button>`;
        }
        document.body.appendChild(menu);
        menu.querySelector("[data-action=edit]").addEventListener("click", () => {
            closeContextMenu();
            currentLocationId = loc.id;
            document.getElementById("loc-name-input").value = loc.name;
            document.getElementById("loc-desc-input").value = loc.description;
            renderLocationDetail(loc);
            document.getElementById("map-list-btn").click();
        });
        menu.querySelector("[data-action=drill]")?.addEventListener("click", () => {
            closeContextMenu();
            mapParentId = loc.id;
            mapScaleIndex += 1;
            mapBreadcrumbs.push({ id: loc.id, name: loc.name });
            renderMapGrid();
        });
        document.addEventListener("click", closeContextMenu, { once: true });
        e.stopPropagation();
    } else {
        const nextLevel = mapScaleIndex + 1 < SCALE_LEVELS.length ? SCALE_LEVELS[mapScaleIndex + 1] : "具体建筑";
        const name = prompt(`在此创建 ${nextLevel}：`, "");
        if (!name) return;
        createMapLocation(name, x, y);
    }
}

async function createMapLocation(name, x, y) {
    const nextLevel = mapScaleIndex + 1 < SCALE_LEVELS.length ? SCALE_LEVELS[mapScaleIndex + 1] : "具体建筑";
    await fetch("/api/locations" + nidQ(), { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name, scale_level: nextLevel, parent_id: mapParentId, grid_x: x, grid_y: y }) });
    await loadLocationTree();
    renderMapGrid();
}

/* ═══════════ Location Templates ═══════════ */
let locTemplates = {};

document.getElementById("loc-tmpl-btn").addEventListener("click", async () => {
    locTemplates = await (await fetch(`/api/novels/${currentNovelId}/location_templates`)).json() || {};
    renderLocTmplEditor();
    document.getElementById("loc-tmpl-modal").classList.remove("hidden");
});
document.getElementById("loc-tmpl-cancel").addEventListener("click", () => document.getElementById("loc-tmpl-modal").classList.add("hidden"));
document.querySelector("#loc-tmpl-modal .modal-backdrop").addEventListener("click", () => document.getElementById("loc-tmpl-modal").classList.add("hidden"));
document.getElementById("loc-tmpl-scale-sel").addEventListener("change", renderLocTmplEditor);
document.getElementById("loc-tmpl-add-group").addEventListener("click", () => {
    const scale = document.getElementById("loc-tmpl-scale-sel").value;
    const arr = locTemplates[scale] || [];
    arr.push({ group: "新分组", fields: [] });
    locTemplates[scale] = arr;
    renderLocTmplEditor();
});
document.getElementById("loc-tmpl-save").addEventListener("click", async () => {
    collectLocTmpl();
    await fetch(`/api/novels/${currentNovelId}/location_templates`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(locTemplates) });
    document.getElementById("loc-tmpl-modal").classList.add("hidden");
});

function collectLocTmpl() {
    const scale = document.getElementById("loc-tmpl-scale-sel").value;
    const groups = [];
    document.querySelectorAll("#loc-tmpl-editor .tmpl-group").forEach(g => {
        const name = g.querySelector(".tmpl-group-name").value.trim() || "未命名";
        const fields = [];
        g.querySelectorAll(".tmpl-field-row").forEach(r => {
            const fn = r.querySelector(".tmpl-fname").value.trim();
            if (fn) fields.push({ name: fn, type: "text", inheritable: r.querySelector(".tmpl-inherit").checked });
        });
        groups.push({ group: name, fields });
    });
    locTemplates[scale] = groups;
}

function renderLocTmplEditor() {
    const scale = document.getElementById("loc-tmpl-scale-sel").value;
    const groups = locTemplates[scale] || [];
    const container = document.getElementById("loc-tmpl-editor");
    container.innerHTML = "";
    groups.forEach((g, gi) => {
        const div = document.createElement("div"); div.className = "tmpl-group";
        div.innerHTML = `<div style="display:flex;gap:4px;align-items:center;margin-bottom:4px;"><input class="tmpl-group-name" value="${e(g.group)}" style="flex:1;font-weight:600;"><button data-delgroup="${gi}" class="sys-btn sys-btn-danger">🗑️</button></div>`;
        const fieldsDiv = document.createElement("div");
        (g.fields || []).forEach((f, fi) => {
            const r = document.createElement("div"); r.className = "tmpl-field-row";
            r.style.cssText = "display:flex;gap:4px;align-items:center;margin-bottom:3px;padding-left:12px;";
            r.innerHTML = `<input class="tmpl-fname" value="${e(f.name)}" placeholder="字段名" style="flex:1;font-size:.72rem;"><label style="font-size:.68rem;display:flex;align-items:center;gap:2px;white-space:nowrap;"><input type="checkbox" class="tmpl-inherit" ${f.inheritable?"checked":""}>可被子地点继承</label><button data-delfield="${fi}" class="sys-btn sys-btn-danger">🗑️</button>`;
            fieldsDiv.appendChild(r);
        });
        const addRow = document.createElement("div");
        addRow.innerHTML = `<button data-addfield="${gi}" class="sys-btn sys-btn-secondary" style="font-size:.68rem;margin-left:12px;">+ 字段</button>`;
        fieldsDiv.appendChild(addRow);
        div.appendChild(fieldsDiv);
        container.appendChild(div);
    });
    if (!groups.length) container.innerHTML = '<p class="placeholder">尚无模板分组，请点击"+ 添加分组"</p>';
    // Bind events
    container.querySelectorAll("[data-delgroup]").forEach(b => b.addEventListener("click", () => {
        const groups2 = locTemplates[scale] || [];
        groups2.splice(Number(b.dataset.delgroup), 1);
        locTemplates[scale] = groups2;
        renderLocTmplEditor();
    }));
    container.querySelectorAll("[data-delfield]").forEach(b => b.addEventListener("click", () => {
        collectLocTmpl();
        const groups2 = locTemplates[scale] || [];
        const gi = groups2.findIndex(g => g.fields);  // find the right group
        // simpler: just re-render after collection
        container.querySelectorAll(".tmpl-group").forEach((gDiv, idx) => {
            const fRows = gDiv.querySelectorAll(".tmpl-field-row");
            fRows.forEach((r, fi) => { if (r.contains(b)) { groups2[idx].fields.splice(fi, 1); } });
        });
        locTemplates[scale] = groups2;
        renderLocTmplEditor();
    }));
    container.querySelectorAll("[data-addfield]").forEach(b => {
        b.addEventListener("click", () => {
            collectLocTmpl();
            const groups2 = locTemplates[scale] || [];
            const gi = Number(b.dataset.addfield);
            if (groups2[gi]) {
                groups2[gi].fields.push({ name: "", type: "text", inheritable: false });
            }
            locTemplates[scale] = groups2;
            renderLocTmplEditor();
        });
    });
}

/* ═══════════ Location Detail with Inherited Attributes ═══════════ */
function renderLocationDetail(loc) {
    document.getElementById("loc-name-input").value = loc.name || "";
    document.getElementById("loc-desc-input").value = loc.description || "";
    document.getElementById("loc-scale-select").value = loc.scale_level || "城池";
    document.getElementById("loc-grid-x").value = (loc.grid_x != null) ? loc.grid_x : "";
    document.getElementById("loc-grid-y").value = (loc.grid_y != null) ? loc.grid_y : "";

    var parentSelect = document.getElementById("loc-parent-select");
    parentSelect.innerHTML = '<option value="">-- 无上级 (根节点) --</option>';
    fetch("/api/locations?novel_id=" + currentNovelId).then(function(r){return r.json();}).then(function(locList){
        locList.forEach(function(l){
            if (l.id !== loc.id) {
                var opt = document.createElement("option");
                opt.value = l.id;
                opt.textContent = l.name + " [" + (l.scale_level||"") + "]";
                if (l.id === loc.parent_id) opt.selected = true;
                parentSelect.appendChild(opt);
            }
        });
    }).catch(function(){});

    const container = document.getElementById("loc-attrs-editor");
    container.innerHTML = "";
    if (!loc) return;
    const comp = loc.computed_attributes || {};
    const own = loc.attributes || {};
    const tmpl = (locTemplates[loc.scale_level] || []);

    // Collect all keys: template fields + computed attributes
    var allKeys = {};
    tmpl.forEach(function(g) {
        (g.fields || []).forEach(function(f) { allKeys[f.name] = { inheritable: f.inheritable, group: g.group }; });
    });
    Object.keys(comp).forEach(function(k) { if (!allKeys[k]) allKeys[k] = {}; });

    // Group template keys by group, non-template keys go to "其他"
    var groups = {};
    tmpl.forEach(function(g) {
        groups[g.group] = [];
        (g.fields || []).forEach(function(f) { groups[g.group].push(f.name); });
    });
    var otherKeys = [];
    Object.keys(allKeys).forEach(function(k) {
        var found = false;
        tmpl.forEach(function(g) {
            (g.fields || []).forEach(function(f) { if (f.name === k) found = true; });
        });
        if (!found && comp[k]) otherKeys.push(k);
    });

    // Render groups
    Object.keys(groups).forEach(function(grpName) {
        var keys = groups[grpName];
        if (!keys.length) return;
        var fs = document.createElement("fieldset"); fs.className = "char-fieldset";
        fs.innerHTML = "<legend>" + e(grpName) + "</legend>";
        keys.forEach(function(k) {
            var meta = allKeys[k] || {};
            var inheritedVal = comp[k] || "";
            var ownVal = own[k] || "";
            var isInherited = inheritedVal && !ownVal;
            var row = document.createElement("div"); row.style.cssText = "display:flex;align-items:center;gap:4px;margin-bottom:3px;";
            row.innerHTML = "<label style='font-size:.73rem;width:60px;flex-shrink:0;'>" + e(k) + "</label>"
                + "<input type='text' data-field='" + e(k) + "' value='" + e(ownVal) + "'"
                + " placeholder='" + (isInherited ? "继承: " + e(String(inheritedVal)) : "") + "'"
                + " style='flex:1;font-size:.73rem;padding:3px 6px;border:1px solid var(--border);border-radius:4px;"
                + (isInherited ? "background:#f5fff5;" : "") + "'>"
                + (meta.inheritable ? "<span style='font-size:.6rem;color:var(--accent);' title='可被子地点继承'>🔄</span>" : "")
                + (isInherited ? "<span style='font-size:.62rem;color:var(--muted);' title='继承自上级'>↳</span>" : "");
            fs.appendChild(row);
        });
        container.appendChild(fs);
    });

    // Non-template inherited keys
    if (otherKeys.length) {
        var fs = document.createElement("fieldset"); fs.className = "char-fieldset";
        fs.style.cssText = "background:#f9f9f9;";
        fs.innerHTML = "<legend>🛡️ 继承自上级的属性</legend>";
        otherKeys.forEach(function(k) {
            var inheritedVal = comp[k] || "";
            var ownVal = own[k] || "";
            var isInherited = inheritedVal && !ownVal;
            var row = document.createElement("div"); row.style.cssText = "display:flex;align-items:center;gap:4px;margin-bottom:3px;";
            row.innerHTML = "<label style='font-size:.73rem;width:60px;'>" + e(k) + "</label>"
                + "<input type='text' data-field='" + e(k) + "' value='" + e(ownVal) + "'"
                + " placeholder='" + (isInherited ? "继承: " + e(String(inheritedVal)) : "") + "'"
                + " style='flex:1;font-size:.73rem;padding:3px 6px;border:1px solid var(--border);border-radius:4px;"
                + (isInherited ? "background:#f5fff5;" : "") + "'>"
                + (isInherited ? "<span style='font-size:.62rem;color:var(--muted);' title='继承自上级'>↳</span>" : "");
            fs.appendChild(row);
        });
        container.appendChild(fs);
    }

    if (!Object.keys(groups).length && !otherKeys.length && !Object.keys(own).length) {
        container.innerHTML = '<p class="placeholder">暂无属性（请先在模板设置中配置此尺度的字段）</p>';
    }
    // Presence radar
    fetch("/api/locations/" + loc.id + "/presence?novel_id=" + currentNovelId)
        .then(function(r) { return r.json(); })
        .then(function(chars) {
            var div = document.createElement("div");
            div.className = "tmpl-group";
            div.style.cssText = "margin-top:16px;border-color:#8b5cf6;background:#f5f3ff;padding:12px 14px;border-radius:6px;";
            var h = '<h4 style="color:#6d28d9;margin-bottom:8px;">📍 当前在此地的人物 (基于主线时间)</h4>';
            if (chars.length === 0) {
                h += '<div style="font-size:0.8rem;color:#6b7280;">当前无活跃人物在此。</div>';
            } else {
                chars.forEach(function(c) {
                    h += '<div style="font-size:0.82rem;margin-bottom:4px;"><b>🏃 ' + e(c.name) + '</b> <span style="color:#6b7280;">(' + e(c.status_desc) + ')</span></div>';
                });
            }
            div.innerHTML = h;
            container.appendChild(div);
        });
}

function collectLocationAttributes() {
    var attrs = {};
    document.querySelectorAll("#loc-attrs-editor input[data-field]").forEach(function(inp) {
        var k = inp.dataset.field;
        var v = inp.value.trim();
        if (k && v !== "") attrs[k] = v;
    });
    return attrs;
}

async function loadLocationTree() {
    // Always refresh templates so renderLocationDetail works
    locTemplates = await (await fetch(`/api/novels/${currentNovelId}/location_templates`)).json() || {};
    const list = document.getElementById("location-list-tree");
    const locs = await (await fetch("/api/locations" + nidQ())).json();
    list.innerHTML = "";
    const children = {};
    const roots = [];
    locs.forEach(l => {
        if (l.parent_id == null) roots.push(l);
        else {
            if (!children[l.parent_id]) children[l.parent_id] = [];
            children[l.parent_id].push(l);
        }
    });
    function renderTree(node, depth) {
        const li = document.createElement("li");
        li.className = "chapter-li";
        li.dataset.cid = node.id;
        li.style.paddingLeft = (8 + depth * 16) + "px";
        li.style.display = "flex";
        li.style.alignItems = "center";
        const scaleLabel = node.scale_level ? ` [${node.scale_level}]` : "";
        li.innerHTML = `<span style="flex:1;overflow:hidden;text-overflow:ellipsis">${e(node.name)}<span style="font-size:.65rem;color:var(--muted);margin-left:6px;">${scaleLabel}</span></span><button class="loc-drill-btn" title="进入内部地图">🔍</button>`;
        li.querySelector(".loc-drill-btn").addEventListener("click", (e) => {
            e.stopPropagation();
            enterLocationGrid(node);
        });
        li.addEventListener("click", () => {
            currentLocationId = node.id;
            document.getElementById("loc-name-input").value = node.name;
            document.getElementById("loc-desc-input").value = node.description;
            renderLocationDetail(node);
            document.querySelectorAll("#location-list-tree .chapter-li").forEach(x => x.classList.remove("active"));
            li.classList.add("active");
        });
        li.appendChild(delBtn("/api/locations", node.id, () => { loadLocationTree(); }));
        list.appendChild(li);
        (children[node.id] || []).forEach(child => renderTree(child, depth + 1));
    }
    roots.forEach(r => renderTree(r, 0));
}
document.getElementById("save-location-btn").addEventListener("click", async () => { if (!currentLocationId) return;
    var parentVal = document.getElementById("loc-parent-select").value;
    var gx = document.getElementById("loc-grid-x").value;
    var gy = document.getElementById("loc-grid-y").value;
    var payload = {
        name: document.getElementById("loc-name-input").value,
        description: document.getElementById("loc-desc-input").value,
        scale_level: document.getElementById("loc-scale-select").value,
        parent_id: parentVal ? parseInt(parentVal) : null,
        grid_x: gx !== "" ? parseInt(gx) : null,
        grid_y: gy !== "" ? parseInt(gy) : null,
        attributes: collectLocationAttributes(),
    };
    var res = await fetch(`/api/locations/${currentLocationId}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    if (res.ok) { showToast("📍 地点元数据保存成功！"); await loadLocationTree(); } else { var e2 = await res.json(); alert("失败: " + (e2.detail||"未知")); }
});

/* ═══════════ Right bar ═══════════ */
async function loadCharacters() {
    function getStatusBadge(ch) {
        if (ch.is_active === false) return '<span class="status-badge inactive">🚪 退场</span>';
        if (ch.is_active === true) return '<span class="status-badge alive">🏃 活跃</span>';
        return "";
    }
    const c = document.getElementById("character-list"), chars = await (await fetch("/api/characters" + nidQ())).json(); c.innerHTML = "";
    if (!chars.length) { c.innerHTML = '<p class="placeholder">暂无人物</p>'; return; }
    const activated = chars.filter(ch => ch.is_always_context).map(ch => ch.id);
    localStorage.setItem("activated_chars", JSON.stringify(activated));
    chars.forEach(ch => {
        const d = document.createElement("div"); d.className = "character-card"; d.style.cursor = "pointer";
        var attrs = ch.attributes || {};
        var basic = attrs["基础信息"] || {};
        var appearance = attrs["外貌特征"] || {};
        // 精选六要素：姓名、年龄、性别、性格、身高、一句话描述
        var lines = [];
        if (basic["年龄"]) lines.push('<b>年龄</b>: ' + e(String(basic["年龄"])));
        if (basic["性别"]) lines.push('<b>性别</b>: ' + e(String(basic["性别"])));
        if (basic["性格"]) lines.push('<b>性格</b>: ' + e(String(basic["性格"])));
        if (appearance["身高"]) lines.push('<b>身高</b>: ' + e(String(appearance["身高"])));
        var summary = lines.length ? lines.map(function(l){ return '<div>' + l + '</div>'; }).join("") : "";
        d.innerHTML = '<div class="char-name">' + e(ch.name||"(未)") + getStatusBadge(ch) + '</div>'
            + (summary ? '<div style="font-size:0.78rem;line-height:1.5;margin:4px 0;">' + summary + '</div>' : '')
            + (ch.description ? '<div style="font-size:0.7rem;color:var(--text-muted);line-height:1.3;">' + e(ch.description) + '</div>' : '')
            + '<label class="char-activate"><input type="checkbox" data-cid="' + ch.id + '" ' + (ch.is_always_context?"checked":"") + '> 启动</label>';
        d.addEventListener("click", (e) => { if (e.target.tagName === "INPUT") return; switchToCharactersModule(ch.id); });
        c.appendChild(d);
    });
    document.querySelectorAll(".char-activate input").forEach(cb => {
        cb.addEventListener("change", async () => {
            const cid = Number(cb.dataset.cid);
            const ch = await (await fetch(`/api/characters/${cid}?novel_id=${currentNovelId}`)).json();
            await fetch(`/api/characters/${cid}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: ch.name, aliases: ch.aliases, description: ch.description, status: ch.status, attributes: ch.attributes, is_always_context: cb.checked }) });
            const act = JSON.parse(localStorage.getItem("activated_chars") || "[]");
            if (cb.checked) { if (!act.includes(cid)) act.push(cid); }
            else { const idx = act.indexOf(cid); if (idx >= 0) act.splice(idx, 1); }
            localStorage.setItem("activated_chars", JSON.stringify(act));
        });
    });
}

async function switchToCharactersModule(charId) {
    document.querySelectorAll(".nav-btn").forEach(b => b.classList.remove("active"));
    document.querySelector(".nav-btn[data-module=characters]").classList.add("active");
    document.querySelectorAll(".module-panel").forEach(p => p.classList.add("hidden"));
    document.querySelectorAll(".ws-panel").forEach(p => p.classList.add("hidden"));
    document.getElementById("module-characters").classList.remove("hidden");
    document.getElementById("ws-characters").classList.remove("hidden");
    currentModule = "characters";
    document.getElementById("copilot-quick-btns").classList.add("hidden");
    await loadCharactersModule();
    if (charId) {
        currentCharId = charId;
        loadCharEditUI();
        document.querySelectorAll("#char-list .chapter-li").forEach(x => x.classList.remove("active"));
        const li = document.querySelector(`#char-list .chapter-li[data-cid="${charId}"]`);
        if (li) li.classList.add("active");
    }
    swTimeline();
}
async function createNewCharacter() { const n = prompt("人物名称："); if (!n) return;
    await fetch("/api/characters" + nidQ(), { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: n, status: "存活" }) }); await loadCharacters(); }
/* ═══════════ Tabs ═══════════ */
document.querySelectorAll(".tab-btn").forEach(b => b.addEventListener("click", () => {
    document.querySelectorAll(".tab-btn").forEach(x => x.classList.remove("active")); b.classList.add("active");
    document.querySelectorAll("#right-sidebar .tab-content").forEach(x => x.classList.add("hidden"));
    document.getElementById(b.dataset.tab).classList.remove("hidden"); }));

/* ═══════════ Writing Copilot ═══════════ */
const copilotHistory = document.getElementById("copilot-history");
const copilotInput = document.getElementById("copilot-input");
const copilotSend = document.getElementById("copilot-send");

window.handleCopilotSend = function() {
    var t = copilotInput.value.trim();
    if (!t) return;
    copilotInput.value = "";
    var scene = document.getElementById("chat-scene-select")?.value || "chat";
    if (scene.startsWith("chain_")) {
        var chainId = parseInt(scene.replace("chain_", ""));
        if (chainId) {
            var userB = document.createElement("div"); userB.className = "copilot-bubble user";
            userB.innerHTML = '<div class="cp-label">你</div>' + e(t) + '<div style="text-align:right;margin-top:6px;"><span class="undo-capsule" onclick="truncateChat(this)">✕ 撤回</span></div>';
            copilotHistory.appendChild(userB); copilotHistory.scrollTop = copilotHistory.scrollHeight;
            var aiB = document.createElement("div"); aiB.className = "copilot-bubble ai streaming";
            aiB.innerHTML = '<div class="cp-label">AI</div>';
            copilotHistory.appendChild(aiB); copilotHistory.scrollTop = copilotHistory.scrollHeight;
            runHeadlessChain(chainId, t, aiB);
        }
        return;
    }
    copilotChat(t);
};

function getActiveTextarea() {
    if (currentModule === "writing") return contentInput;
    if (currentModule === "outline") return outlineContent;
    if (currentModule === "map") return document.getElementById("loc-desc-input");
    return contentInput;
}

function getActiveContent() {
    return getActiveTextarea().value || "";
}

function getActiveSelection() {
    const sel = window.getSelection();
    if (sel && sel.toString().trim()) return sel.toString();
    const ta = getActiveTextarea();
    return ta.value.substring(ta.selectionStart, ta.selectionEnd);
}

function insertToActiveTextarea(text) {
    const ta = getActiveTextarea();
    const start = ta.selectionStart;
    ta.value = ta.value.substring(0, start) + text + ta.value.substring(start);
    ta.focus();
    ta.selectionStart = start + text.length;
    ta.selectionEnd = start + text.length;
    ta.dispatchEvent(new Event("input"));
}

function replaceActiveTextarea(text) {
    const ta = getActiveTextarea();
    ta.value = text;
    ta.focus();
    ta.dispatchEvent(new Event("input"));
}

async function copilotChat(instruction, forceScene) {
    const s = getSettings();
    if (!s.apiKey) { document.getElementById("settings-modal").classList.remove("hidden"); return; }
    const content = getActiveContent();
    const sel = getActiveSelection();

    // show user message
    const userB = document.createElement("div"); userB.className = "copilot-bubble user";
    userB.innerHTML = `<div class="cp-label">你</div>${e(instruction)}<div style="text-align:right;margin-top:6px;"><span class="undo-capsule" onclick="truncateChat(this)">✕ 撤回</span></div>`;
    copilotHistory.appendChild(userB); copilotHistory.scrollTop = copilotHistory.scrollHeight;

    const aiB = document.createElement("div"); aiB.className = "copilot-bubble ai streaming";
    aiB.innerHTML = '<div class="cp-label">AI</div>';
    copilotHistory.appendChild(aiB); copilotHistory.scrollTop = copilotHistory.scrollHeight;

    try {
        var currentAiResponse = "";
        const res = await fetch("/api/writing/copilot", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ novel_id: currentNovelId, current_chapter_content: content, selected_text: sel, instruction, api_key: s.apiKey, base_url: s.baseUrl, model: s.model, scene: forceScene || document.getElementById("chat-scene-select")?.value || "chat", history: window.copilotChatHistory }) });
        for await (const msg of parseSSEStream(res)) {
            if (msg.type === "chunk") { currentAiResponse += msg.content; aiB.appendChild(document.createTextNode(msg.content)); copilotHistory.scrollTop = copilotHistory.scrollHeight; }
            else if (msg.type === "error") { aiB.appendChild(document.createTextNode("\n[错误: " + msg.message + "]")); }
            else if (msg.type === "tool_proposal") {
                var autoExec = document.getElementById("agent-auto-execute").checked;
                renderToolProposal(aiB, msg, autoExec);
            }
            else if (msg.type === "done") {
                const text = aiB.textContent.replace(/^AI/, "").trim();
                const row = document.createElement("div"); row.className = "cp-action-row";

                const insBtn = document.createElement("button"); insBtn.className = "cp-insert-btn";
                insBtn.textContent = "\u2B05 插入到当前光标处";
                insBtn.addEventListener("click", () => insertToActiveTextarea(text));

                const repBtn = document.createElement("button"); repBtn.className = "cp-insert-btn";
                repBtn.textContent = "\uD83D\uDD04 替换当前文本";
                repBtn.addEventListener("click", () => { if (confirm("替换当前编辑器全部内容？")) replaceActiveTextarea(text); });

                row.appendChild(insBtn);
                row.appendChild(repBtn);
                aiB.appendChild(row);
            }
        }
        if (instruction.trim() || currentAiResponse.trim()) {
            window.copilotChatHistory.push({ role: "user", content: instruction });
            window.copilotChatHistory.push({ role: "assistant", content: currentAiResponse });
        }
    } catch (e) { aiB.appendChild(document.createTextNode("\n[失败: " + e.message + "]")); }
    finally { aiB.classList.remove("streaming"); }
}

function clearCopilotChat() {
    window.copilotChatHistory = [];
    copilotHistory.innerHTML = "";
    showToast("对话记忆已清空");
}

function truncateChat(btnElement) {
    var bubble = btnElement.closest(".copilot-bubble");
    if (!bubble) return;
    var idx = Array.from(copilotHistory.children).indexOf(bubble);
    while (copilotHistory.children.length > idx) {
        copilotHistory.removeChild(copilotHistory.lastChild);
    }
    window.copilotChatHistory = window.copilotChatHistory.slice(0, Math.floor(idx / 2) * 2);
    showToast("已从此处悔棋截断");
}

document.getElementById("clear-memory-btn")?.addEventListener("click", clearCopilotChat);
document.getElementById("outline-export-btn")?.addEventListener("click", async function() {
    if (!currentNovelId) return;
    showToast("⏳ 正在编译全量小说设定集...");
    try {
        var res2 = await fetch("/api/outlines/export-markdown", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ novel_id: currentNovelId }) });
        if (res2.ok) {
            var d2 = await res2.json();
            var blob = new Blob([d2.markdown], { type: "text/markdown;charset=utf-8;" });
            var url = URL.createObjectURL(blob);
            var link = document.createElement("a"); link.href = url; link.setAttribute("download", "全景大纲设定集.md");
            document.body.appendChild(link); link.click(); document.body.removeChild(link);
            showToast("📤 设定集 Markdown 下载成功！");
        }
    } catch (e2) { console.error(e2); }
});
document.getElementById("import-close-btn")?.addEventListener("click", function() {
    if (isGenesisRunning) return;  // 运行中由中断按钮处理
    document.getElementById("import-modal").classList.add("hidden");
});
document.getElementById("genesis-backdrop")?.addEventListener("click", function() {
    if (isGenesisRunning) return;  // 运行中禁止遮罩关闭
    document.getElementById("import-modal").classList.add("hidden");
});

document.addEventListener("keydown", function(e) {
    if (e.key === "Escape" && isGenesisRunning) {
        e.preventDefault();
        e.stopPropagation();
        triggerGenesisPause();  // ESC 触发暂停（安全行为）
    }
});

/* ═══════════ 暂停 / 中断逻辑 ═══════════ */
function triggerGenesisPause() {
    if (!isGenesisRunning || genesisPaused) return;
    genesisPaused = true;
    showToast("⏸ 已发出暂停信号，当前节点执行完毕后将保存进度...");
    var pauseBtn = document.getElementById("genesis-pause-btn");
    if (pauseBtn) { pauseBtn.disabled = true; pauseBtn.textContent = "⏳ 暂停中..."; }
    var abortBtn = document.getElementById("genesis-abort-btn");
    if (abortBtn) abortBtn.disabled = true;
}

function triggerGenesisAbort() {
    if (!isGenesisRunning || genesisAborted) return;
    genesisAborted = true;
    // 中止所有进行中的 fetch 请求
    if (genesisAbortController) {
        try { genesisAbortController.abort(); } catch(e) {}
    }
    showToast("⏹ 已发出中断信号，正在强制停止...");
    var pauseBtn = document.getElementById("genesis-pause-btn");
    if (pauseBtn) { pauseBtn.disabled = true; pauseBtn.textContent = "⏳ 中断中..."; }
    var abortBtn = document.getElementById("genesis-abort-btn");
    if (abortBtn) { abortBtn.disabled = true; abortBtn.textContent = "⏳ 中断中..."; }
}

document.getElementById("genesis-pause-btn")?.addEventListener("click", function() {
    triggerGenesisPause();
});

document.getElementById("genesis-abort-btn")?.addEventListener("click", function() {
    if (!confirm("⚠️ 中断建纲将丢弃当前进度且不可恢复，确定中断？")) return;
    triggerGenesisAbort();
});

/* ═══════════ 模式选择器交互 ═══════════ */
(function setupModeSelector() {
    var labels = document.querySelectorAll(".genesis-mode-btn");
    if (!labels.length) return;
    labels.forEach(function(label) {
        label.addEventListener("click", function() {
            labels.forEach(function(l) { l.classList.remove("active"); });
            label.classList.add("active");
            // 同步选中 radio
            var radio = label.querySelector("input[type=radio]");
            if (radio) radio.checked = true;
            // 更新启动按钮文案
            updateGenesisSubmitLabel();
        });
    });
})();

function getGenesisMode() {
    var checked = document.querySelector("input[name='genesis-mode']:checked");
    return checked ? checked.value : "full";
}

function updateGenesisSubmitLabel() {
    var btn = document.getElementById("genesis-submit-btn");
    if (!btn) return;
    var mode = getGenesisMode();
    var labels = { full: "🌌 启动完整建纲", incremental: "📎 启动分段建纲", overwrite: "💥 启动覆写重建" };
    btn.textContent = labels[mode] || "🌌 启动建纲";
}

/* ⚡ 并行执行开关 */
function getGenesisParallel() {
    var cb = document.getElementById("genesis-parallel-checkbox");
    return cb ? cb.checked : false;
}

(function setupParallelToggle() {
    var cb = document.getElementById("genesis-parallel-checkbox");
    var label = document.getElementById("genesis-parallel-label");
    if (!cb || !label) return;
    cb.addEventListener("change", function() {
        label.textContent = cb.checked ? "并行执行" : "依次执行";
        label.style.color = cb.checked ? "#a5b4fc" : "var(--text-muted)";
    });
})();

/* ═══════════ 断点恢复 ═══════════ */
function checkAndShowResume() {
    var state = loadGenesisState();
    var banner = document.getElementById("genesis-resume-banner");
    if (!banner) return;
    if (state && state.status === "interrupted") {
        genesisResumeState = state;
        banner.style.display = "flex";
        document.getElementById("genesis-resume-info").textContent = formatGenesisResumeInfo(state);
    } else {
        genesisResumeState = null;
        banner.style.display = "none";
    }
}

document.getElementById("genesis-resume-btn")?.addEventListener("click", function() {
    if (!genesisResumeState) return;
    var state = genesisResumeState;
    genesisResumeState = null;
    document.getElementById("genesis-resume-banner").style.display = "none";
    // 恢复输入文本和模式
    document.getElementById("import-textarea").value = state.inputText || "";
    var modeRadio = document.querySelector("input[name='genesis-mode'][value='" + (state.mode || "full") + "']");
    if (modeRadio) {
        modeRadio.checked = true;
        document.querySelectorAll(".genesis-mode-btn").forEach(function(l) { l.classList.remove("active"); });
        modeRadio.closest(".genesis-mode-btn").classList.add("active");
        updateGenesisSubmitLabel();
    }
    // 恢复并行开关状态
    var parallelCb = document.getElementById("genesis-parallel-checkbox");
    var parallelLabel = document.getElementById("genesis-parallel-label");
    if (parallelCb) {
        parallelCb.checked = state.parallel || false;
        if (parallelLabel) {
            parallelLabel.textContent = parallelCb.checked ? "并行执行" : "依次执行";
            parallelLabel.style.color = parallelCb.checked ? "#a5b4fc" : "var(--text-muted)";
        }
    }
    // 重建进度条状态
    restoreProgressFromState(state);
    // 启动恢复执行
    executeGenesisFromResume(state);
});

document.getElementById("genesis-discard-btn")?.addEventListener("click", function() {
    clearGenesisState();
    genesisResumeState = null;
    document.getElementById("genesis-resume-banner").style.display = "none";
    showToast("🗑 已放弃中断的建纲任务");
});

function restoreProgressFromState(state) {
    var stepIds = ["step-map", "step-outline", "step-char"];
    if (state.parallel && state.chainStates) {
        var keys = ["outline", "map", "character"];
        keys.forEach(function(key, idx) {
            var el = document.getElementById(stepIds[idx]);
            if (!el) return;
            var cs = state.chainStates[key];
            if (cs && cs.completed) {
                el.className = "step-completed";
            } else if (state.status === "interrupted") {
                el.className = "step-interrupted";
            } else {
                el.className = "";
            }
        });
        return;
    }
    stepIds.forEach(function(id, idx) {
        var el = document.getElementById(id);
        if (!el) return;
        var phaseIdx = idx + 1;
        if (state.completedPhases && state.completedPhases[idx]) {
            el.className = "step-completed";
        } else if (state.phase === phaseIdx && state.status === "interrupted") {
            el.className = "step-interrupted";
        } else {
            el.className = "";
        }
    });
}

/* ═══════════ 🌌 智能创世大重构引擎 ═══════════ */
document.getElementById("outline-genesis-btn")?.addEventListener("click", function() {
    var modal = document.getElementById("import-modal");
    document.getElementById("import-textarea").value = "";
    document.getElementById("genesis-status-badge").textContent = "待命中";
    document.getElementById("genesis-status-badge").style.color = "var(--text-muted)";
    ["step-outline", "step-map", "step-char"].forEach(function(id) {
        var el = document.getElementById(id);
        if (el) el.className = "";
    });
    document.getElementById("genesis-console").textContent = "[System] 创世核心就绪。请输入文字指令并启动...\n";
    document.getElementById("import-textarea").disabled = false;
    document.getElementById("genesis-submit-btn").disabled = false;
    document.getElementById("import-close-btn").disabled = false;
    document.getElementById("genesis-pause-btn").style.display = "none";
    document.getElementById("genesis-abort-btn").style.display = "none";
    updateGenesisSubmitLabel();
    // 重置模式选择为完整建纲
    document.querySelectorAll(".genesis-mode-btn").forEach(function(l) { l.classList.remove("active"); });
    var fullLabel = document.getElementById("mode-full-label");
    if (fullLabel) { fullLabel.classList.add("active"); fullLabel.querySelector("input[type=radio]").checked = true; }
    updateGenesisSubmitLabel();
    // 检查断点恢复
    checkAndShowResume();
    modal.classList.remove("hidden");
});

/* ═══════════ 文件拖拽导入 ═══════════ */
(function setupFileImport() {
    var dropZone = document.getElementById("import-drop-zone");
    var fileInput = document.getElementById("import-file-input");
    var textarea = document.getElementById("import-textarea");
    if (!dropZone || !fileInput || !textarea) return;

    function readFile(file) {
        if (!file) return;
        var ext = (file.name || "").split(".").pop().toLowerCase();
        if (ext !== "txt" && ext !== "md" && ext !== "text") {
            alert("仅支持 .txt 和 .md 文本文件");
            return;
        }
        var reader = new FileReader();
        reader.onload = function(e) {
            textarea.value = e.target.result;
            textarea.dispatchEvent(new Event("input"));
            dropZone.style.borderColor = "#22c55e";
            dropZone.style.background = "rgba(34,197,94,0.06)";
            var info = dropZone.querySelector("p");
            if (info) info.textContent = "✅ 已加载: " + file.name + " (" + (file.size/1024).toFixed(1) + " KB)";
        };
        reader.onerror = function() { alert("文件读取失败"); };
        reader.readAsText(file, "UTF-8");
    }

    dropZone.addEventListener("click", function() { fileInput.click(); });
    fileInput.addEventListener("change", function() { readFile(fileInput.files[0]); });

    dropZone.addEventListener("dragover", function(e) { e.preventDefault(); dropZone.style.borderColor = "#9333ea"; dropZone.style.background = "rgba(147,51,234,0.06)"; });
    dropZone.addEventListener("dragleave", function(e) { e.preventDefault(); dropZone.style.borderColor = "var(--border)"; dropZone.style.background = "var(--bg)"; });
    dropZone.addEventListener("drop", function(e) { e.preventDefault(); dropZone.style.borderColor = "var(--border)"; dropZone.style.background = "var(--bg)"; readFile(e.dataTransfer.files[0]); });
})();

document.getElementById("genesis-submit-btn")?.addEventListener("click", async function() {
    var text = document.getElementById("import-textarea").value.trim();
    if (!text) { alert("请输入需要重构或提炼的文本设定！"); return; }
    var mode = getGenesisMode();

    // 覆写模式：先清空
    if (mode === "overwrite") {
        if (!confirm("⚠️ 覆写模式将清空当前小说的全部大纲/地点/人物/势力数据，确定继续？")) return;
        var clearRes = await fetch("/api/novels/" + currentNovelId + "/genesis/clear", { method: "POST" });
        if (!clearRes.ok) { alert("清空数据失败，建纲取消。"); return; }
    }

    clearGenesisState();
    genesisPaused = false;
    genesisAborted = false;
    genesisAbortController = new AbortController();

    // 构建初始状态快照
    var initialState = {
        phase: 1,
        completedPhases: [false, false, false],
        currentNodeId: null,
        currentChainId: null,
        fullChainContext: "",
        executedNodeCount: 0,
        inputText: text,
        mode: mode,
        parallel: getGenesisParallel(),
        chainStates: null,
        status: "running",
        startedAt: Date.now(),
    };

    await executeGenesisCore(initialState, 1, [false, false, false]);
});

/* ═══════════ 建纲核心执行引擎 ═══════════ */
async function executeGenesisCore(initialState, startPhase, completedPhases) {
    var text = initialState.inputText;
    var mode = initialState.mode;
    var submitBtn = document.getElementById("genesis-submit-btn");
    var closeBtn = document.getElementById("import-close-btn");
    var pauseBtn = document.getElementById("genesis-pause-btn");
    var abortBtn = document.getElementById("genesis-abort-btn");
    var textarea = document.getElementById("import-textarea");
    var backdrop = document.getElementById("genesis-backdrop");
    var consoleEl = document.getElementById("genesis-console");
    var statusBadge = document.getElementById("genesis-status-badge");

    // UI 锁定
    submitBtn.disabled = true;
    submitBtn.style.display = "none";
    closeBtn.disabled = true;
    pauseBtn.style.display = "inline-block";
    pauseBtn.disabled = false;
    pauseBtn.textContent = "⏸ 暂停建纲";
    abortBtn.style.display = "inline-block";
    abortBtn.disabled = false;
    abortBtn.textContent = "⏹ 中断建纲";
    textarea.disabled = true;
    backdrop.classList.add("genesis-running-backdrop");
    document.getElementById("genesis-resume-banner").style.display = "none";

    var state = {
        phase: startPhase,
        completedPhases: completedPhases.slice(),
        currentNodeId: initialState.currentNodeId || null,
        currentChainId: initialState.currentChainId || null,
        fullChainContext: initialState.fullChainContext || "",
        executedNodeCount: initialState.executedNodeCount || 0,
        inputText: text,
        mode: mode,
        status: "running",
        startedAt: initialState.startedAt || Date.now(),
    };

    statusBadge.textContent = "创世大重构执行中...";
    statusBadge.style.color = "#38bdf8";

    // 恢复已完成的阶段进度
    restoreProgressFromState(state);
    if (startPhase > 1) {
        // 恢复模式：不清空控制台；追加说明
        var existingConsole = consoleEl.innerHTML;
        consoleEl.innerHTML = existingConsole || "";
        logToConsole("\n🔄 [断点恢复] 从阶段 " + startPhase + " 继续...\n", "step");
    } else {
        consoleEl.innerHTML = "";
    }

    function logToConsole(content, type) {
        var color = "#38bdf8";
        if (type === "step") color = "#eab308";
        if (type === "tool") color = "#22c55e";
        if (type === "error") color = "#ef4444";
        if (type === "warning") color = "#f59e0b";
        if (type === "interrupted") color = "#f59e0b";
        var span = document.createElement("span");
        span.style.color = color;
        span.textContent = content;
        consoleEl.appendChild(span);
        consoleEl.scrollTop = consoleEl.scrollHeight;
    }

    try {
        // ⚡ 并行模式分流
        if (initialState.parallel) {
            await executeGenesisParallel(initialState, startPhase, completedPhases, state, logToConsole, consoleEl, statusBadge, submitBtn, closeBtn, pauseBtn, abortBtn, textarea, backdrop);
            return;
        }

        isGenesisRunning = true;
        genesisPaused = false;

        // 增量模式的上下文设置
        var incCtx = (mode === "incremental") ? { use_outlines: true, use_characters: true, use_timeline: true } : null;

        // 📝 日志实时写入
        var genesisFullLog = "";
        var genesisLogPath = "";
        var genesisLogTimer = null;
        var genesisLogChainTitle = "";

        async function flushGenesisLog(isFinal) {
            if (!genesisFullLog) return;
            var chunk = genesisFullLog;
            genesisFullLog = "";
            try {
                var body = {logs: chunk, chain_title: genesisLogChainTitle, append: !!genesisLogPath, log_path: genesisLogPath};
                var resp = await fetch("/api/novels/" + currentNovelId + "/genesis/logs", {
                    method: "POST",
                    headers: {"Content-Type": "application/json"},
                    body: JSON.stringify(body)
                });
                var data = await resp.json();
                if (data.path) genesisLogPath = data.path;
            } catch(e) { /* 日志保存失败不阻塞 */ }
            if (isFinal) {
                genesisLogPath = "";
                if (genesisLogTimer) { clearInterval(genesisLogTimer); genesisLogTimer = null; }
            }
        }

        function startLogFlusher(chainTitle) {
            genesisLogChainTitle = chainTitle;
            genesisLogPath = "";
            genesisFullLog = "";
            if (genesisLogTimer) clearInterval(genesisLogTimer);
            genesisLogTimer = setInterval(function() { flushGenesisLog(false); }, 5000);
        }

        function stopLogFlusher() {
            if (genesisLogTimer) { clearInterval(genesisLogTimer); genesisLogTimer = null; }
        }
        window.__genStopFlusher = stopLogFlusher;
        window.__genFlushLog = flushGenesisLog;

        logToConsole("[System] 正在寻址预设创世推理方案...\n", "step");
        var chainsRes = await fetch("/api/reasoning_chains?novel_id=" + currentNovelId);
        var chains = await chainsRes.json();

        var chainOutline = chains.find(function(c) { return c.title === "📖 全维世界观与大纲流水线"; });
        var chainMap = chains.find(function(c) { return c.title === "🗺️ 蓝图规划与逐层搭建流"; });
        var chainChar = chains.find(function(c) { return c.title === "👤 严谨人设与模板质检流"; });

        if (!chainOutline || !chainMap || !chainChar) {
            throw new Error("系统缺失预设的 3 大创世推理方案，请点击顶部的 [恢复默认] 按钮初始化方案！");
        }

        // 阶段 1
        if (startPhase <= 1 && !state.completedPhases[0]) {
            // 非恢复：从头开始
            if (startPhase === 1 && !initialState.fullChainContext) {
                state.phase = 1;
                state.currentChainId = chainMap.id;
                state.currentNodeId = null;
                state.executedNodeCount = 0;
                saveGenesisState(state);
            }
            startLogFlusher(chainMap.title);
            logToConsole("[System] \n===== 🚀 阶段 1: 空间地理蓝图搭建启动 =====\n", "step");
            document.getElementById("step-map").className = "step-active";
            var resumeData1 = (state.currentChainId === chainMap.id && state.currentNodeId) ? {
                currentNodeId: state.currentNodeId,
                fullChainContext: state.fullChainContext,
                executedNodeCount: state.executedNodeCount || 0,
            } : null;
            var res1 = await runHeadlessChain(chainMap.id, text, null, function(txt, typ) {
                genesisFullLog += "[" + typ + "] " + txt + "\n";
                if (typ !== "chunk") logToConsole(txt, typ);
            }, incCtx, resumeData1);
            if (genesisPaused) {
                var pausedState = loadGenesisState();
                if (pausedState) {
                    state.currentNodeId = pausedState.currentNodeId;
                    state.currentChainId = pausedState.currentChainId;
                    state.fullChainContext = pausedState.fullChainContext;
                    state.executedNodeCount = pausedState.executedNodeCount || 0;
                }
                handleGenesisPaused(state, statusBadge, submitBtn, closeBtn, pauseBtn, textarea, backdrop);
                return;
            }
            if (genesisAborted) {
                handleGenesisAborted(statusBadge, submitBtn, closeBtn, pauseBtn, abortBtn, textarea, backdrop);
                return;
            }
            if (!res1.success) throw new Error("阶段 1 蓝图搭建失败: " + (res1.reason || "未知错误"));
            await flushGenesisLog(true);
            state.completedPhases[0] = true;
            state.phase = 2;
            state.currentChainId = null;
            state.currentNodeId = null;
            state.fullChainContext = "";
            state.executedNodeCount = 0;
            document.getElementById("step-map").className = "step-completed";
            saveGenesisState(state);
        } else if (state.completedPhases[0]) {
            document.getElementById("step-map").className = "step-completed";
        }

        // 🌍 获取地图数据注入到阶段2大纲上下文
        var outlineCtx = incCtx ? Object.assign({}, incCtx) : {};
        try {
            var locs = await (await fetch("/api/locations?novel_id=" + currentNovelId)).json();
            outlineCtx.locations_full_detail = JSON.stringify(locs);
        } catch(e) { /* 获取失败不阻塞 */ }

        // 阶段 2
        if (startPhase <= 2 && !state.completedPhases[1]) {
            if (!state.currentChainId || state.currentChainId !== chainOutline.id) {
                state.phase = 2;
                state.currentChainId = chainOutline.id;
                state.currentNodeId = null;
                state.executedNodeCount = 0;
                state.fullChainContext = "";
                saveGenesisState(state);
            }
            startLogFlusher(chainOutline.title);
            logToConsole("[System] \n===== 📖 阶段 2: 全维世界大纲推演启动 =====\n", "step");
            document.getElementById("step-outline").className = "step-active";
            var resumeData2 = (state.currentChainId === chainOutline.id && state.currentNodeId) ? {
                currentNodeId: state.currentNodeId,
                fullChainContext: state.fullChainContext,
                executedNodeCount: state.executedNodeCount || 0,
            } : null;
            var res2 = await runHeadlessChain(chainOutline.id, text, null, function(txt, typ) {
                genesisFullLog += "[" + typ + "] " + txt + "\n";
                if (typ !== "chunk") logToConsole(txt, typ);
            }, outlineCtx, resumeData2);
            if (genesisPaused) {
                var pausedState = loadGenesisState();
                if (pausedState) {
                    state.currentNodeId = pausedState.currentNodeId;
                    state.currentChainId = pausedState.currentChainId;
                    state.fullChainContext = pausedState.fullChainContext;
                    state.executedNodeCount = pausedState.executedNodeCount || 0;
                }
                handleGenesisPaused(state, statusBadge, submitBtn, closeBtn, pauseBtn, textarea, backdrop);
                return;
            }
            if (genesisAborted) {
                handleGenesisAborted(statusBadge, submitBtn, closeBtn, pauseBtn, abortBtn, textarea, backdrop);
                return;
            }
            if (!res2.success) throw new Error("阶段 2 全维世界大纲推演失败: " + (res2.reason || "未知错误"));
            await flushGenesisLog(true);
            state.completedPhases[1] = true;
            state.phase = 3;
            state.currentChainId = null;
            state.currentNodeId = null;
            state.fullChainContext = "";
            state.executedNodeCount = 0;
            document.getElementById("step-outline").className = "step-completed";
            saveGenesisState(state);
        } else if (state.completedPhases[1]) {
            document.getElementById("step-outline").className = "step-completed";
        }

        // 阶段 3
        if (startPhase <= 3 && !state.completedPhases[2]) {
            if (!state.currentChainId || state.currentChainId !== chainChar.id) {
                state.phase = 3;
                state.currentChainId = chainChar.id;
                state.currentNodeId = null;
                state.executedNodeCount = 0;
                state.fullChainContext = "";
                saveGenesisState(state);
            }
            startLogFlusher(chainChar.title);
            logToConsole("[System] \n===== 👤 阶段 3: 群像人设质检与繁衍启动 =====\n", "step");
            document.getElementById("step-char").className = "step-active";
            var resumeData3 = (state.currentChainId === chainChar.id && state.currentNodeId) ? {
                currentNodeId: state.currentNodeId,
                fullChainContext: state.fullChainContext,
                executedNodeCount: state.executedNodeCount || 0,
            } : null;
            var res3 = await runHeadlessChain(chainChar.id, text, null, function(txt, typ) {
                genesisFullLog += "[" + typ + "] " + txt + "\n";
                if (typ !== "chunk") logToConsole(txt, typ);
            }, incCtx, resumeData3);
            if (genesisPaused) {
                var pausedState = loadGenesisState();
                if (pausedState) {
                    state.currentNodeId = pausedState.currentNodeId;
                    state.currentChainId = pausedState.currentChainId;
                    state.fullChainContext = pausedState.fullChainContext;
                    state.executedNodeCount = pausedState.executedNodeCount || 0;
                }
                handleGenesisPaused(state, statusBadge, submitBtn, closeBtn, pauseBtn, textarea, backdrop);
                return;
            }
            if (genesisAborted) {
                handleGenesisAborted(statusBadge, submitBtn, closeBtn, pauseBtn, abortBtn, textarea, backdrop);
                return;
            }
            if (!res3.success) throw new Error("阶段 3 人设质检失败: " + (res3.reason || "未知错误"));
            await flushGenesisLog(true);
            state.completedPhases[2] = true;
            document.getElementById("step-char").className = "step-completed";
        } else if (state.completedPhases[2]) {
            document.getElementById("step-char").className = "step-completed";
        }

        // 全部完成
        stopLogFlusher();
        window.__genStopFlusher = null;
        window.__genFlushLog = null;
        clearGenesisState();
        logToConsole("[System] \n\n🎉🎉 [创世神迹已成] 世界大纲推演、地理蓝图落地、群像角色繁衍全部圆满成功！\n", "tool");
        statusBadge.textContent = "创世圆满成功";
        statusBadge.style.color = "#22c55e";
        showToast("🌌 智能创世三链大重构圆满成功！");
        if (typeof refreshAll === "function") await refreshAll();
        if (typeof loadLocationsCache === "function") loadLocationsCache();
        if (typeof loadLocationTree === "function") loadLocationTree();
    } catch (err) {
        console.error(err);
        logToConsole("[System] \n\n❌ 创世中途流产: " + (err.message || String(err)) + "\n", "error");
        statusBadge.textContent = "执行失败";
        statusBadge.style.color = "#ef4444";
        // 失败时清除状态，不保留恢复点（因为不是用户主动中断）
        clearGenesisState();
    } finally {
        genesisFinishedCleanup(submitBtn, closeBtn, pauseBtn, textarea, backdrop);
    }
}

/* ═══════════ 并行建纲执行引擎 ═══════════ */
async function executeGenesisParallel(initialState, startPhase, completedPhases, state, logToConsole, consoleEl, statusBadge, submitBtn, closeBtn, pauseBtn, abortBtn, textarea, backdrop) {
    var text = initialState.inputText;
    var mode = initialState.mode;
    var incCtx = (mode === "incremental") ? { use_outlines: true, use_characters: true, use_timeline: true } : null;

    isGenesisRunning = true;
    genesisPaused = false;

    logToConsole("[System] ⚡ 并行建纲模式启动 — 三条推理链将同时执行...\n", "step");
    logToConsole("[System] 正在寻址预设创世推理方案...\n", "step");
    var chainsRes = await fetch("/api/reasoning_chains?novel_id=" + currentNovelId);
    var chains = await chainsRes.json();

    var chainDefs = [
        { key: "outline", title: "📖 全维世界观与大纲流水线", stepId: "step-outline", label: "📖 大纲" },
        { key: "map", title: "🗺️ 蓝图规划与逐层搭建流", stepId: "step-map", label: "🗺️ 地图" },
        { key: "character", title: "👤 严谨人设与模板质检流", stepId: "step-char", label: "👤 人设" },
    ];

    // 解析链 ID
    var chainMap = {};
    chainDefs.forEach(function(d) {
        var found = chains.find(function(c) { return c.title === d.title; });
        if (found) chainMap[d.key] = found;
    });
    if (Object.keys(chainMap).length < 3) {
        throw new Error("系统缺失预设的 3 大创世推理方案！");
    }

    // 初始化/恢复 chainStates
    if (!initialState.chainStates) {
        initialState.chainStates = {};
        chainDefs.forEach(function(d) {
            var isCompleted = completedPhases && completedPhases[chainDefs.indexOf(d)];
            initialState.chainStates[d.key] = {
                chainId: chainMap[d.key].id,
                currentNodeId: null,
                fullChainContext: "",
                executedNodeCount: 0,
                completed: isCompleted || false,
            };
        });
    }
    state.chainStates = initialState.chainStates;
    state.parallel = true;
    state.phase = 0; // 并行模式不使用 phase
    saveGenesisState(state);

    // 启动所有未完成的链的进度指示
    chainDefs.forEach(function(d) {
        var el = document.getElementById(d.stepId);
        if (el && !state.chainStates[d.key].completed) {
            el.className = "step-active";
        } else if (el && state.chainStates[d.key].completed) {
            el.className = "step-completed";
        }
    });

    // 为每条链创建带前缀的日志回调
    function makeLogger(prefix, color) {
        return function(txt, typ) {
            var prefixed = "[" + prefix + "] " + txt;
            // 对于 chunk 类型（流式输出），只在行首加前缀
            if (typ === "chunk") {
                // 流式块：检测是否为新行开始
                if (txt.indexOf("\n") >= 0 && txt.indexOf("\n") < txt.length - 1) {
                    prefixed = txt.replace(/\n/g, "\n[" + prefix + "] ");
                } else if (txt.indexOf("\n") === txt.length - 1) {
                    prefixed = txt.slice(0, -1) + "\n";
                }
                // 简单追加块（不加额外前缀）
                prefixed = txt;
            }
            logToConsole(prefixed, typ);
        };
    }

    var loggers = {
        outline: makeLogger("大纲", "#eab308"),
        map: makeLogger("地图", "#38bdf8"),
        character: makeLogger("人设", "#22c55e"),
    };

    // 为每条链准备 resume data
    function getResumeData(key) {
        var cs = state.chainStates[key];
        if (cs && cs.currentNodeId) {
            return {
                currentNodeId: cs.currentNodeId,
                fullChainContext: cs.fullChainContext,
                executedNodeCount: cs.executedNodeCount || 0,
            };
        }
        return null;
    }

    // 并行启动所有链
    var parallelFullLogs = {};
    var promises = chainDefs.map(function(d) {
        var cs = state.chainStates[d.key];
        if (cs.completed) {
            logToConsole("[System] ⏭ " + d.label + " 链已完成，跳过。\n", "step");
            return Promise.resolve({ key: d.key, success: true, skipped: true });
        }
        logToConsole("[System] 🚀 " + d.label + " 链启动...\n", "step");
        var resumeData = getResumeData(d.key);
        return runHeadlessChain(
            chainMap[d.key].id, text, null,
            function(txt, typ) {
                if (!parallelFullLogs[d.key]) parallelFullLogs[d.key] = "";
                parallelFullLogs[d.key] += "[" + typ + "] " + txt + "\n";
                if (typ !== "chunk") loggers[d.key](txt, typ);
            },
            incCtx,
            resumeData,
            d.key  // chainKey for state persistence
        ).then(function(result) {
            if (result.success) {
                state.chainStates[d.key].completed = true;
                saveGenesisState(state);
                var el = document.getElementById(d.stepId);
                if (el) el.className = "step-completed";
                logToConsole("[System] ✅ " + d.label + " 链执行完毕！\n", "tool");
            }
            return { key: d.key, success: result.success, reason: result.reason, skipped: false };
        }).catch(function(err) {
            logToConsole("[System] ❌ " + d.label + " 链崩溃: " + (err.message || String(err)) + "\n", "error");
            return { key: d.key, success: false, reason: err.message, skipped: false };
        });
    });

    var results = await Promise.allSettled(promises);

    // 提取实际结果（Promise.allSettled 包装了一层）
    var outcomes = [];
    for (var i = 0; i < results.length; i++) {
        if (results[i].status === "fulfilled") {
            outcomes.push(results[i].value);
        } else {
            outcomes.push({ key: chainDefs[i].key, success: false, reason: results[i].reason });
        }
    }

    // 检查暂停
    if (genesisPaused) {
        handleGenesisPaused(state, statusBadge, submitBtn, closeBtn, pauseBtn, textarea, backdrop);
        return;
    }
    // 检查中断
    if (genesisAborted) {
        handleGenesisAborted(statusBadge, submitBtn, closeBtn, pauseBtn, abortBtn, textarea, backdrop);
        return;
    }

    // 检查是否全部成功
    var allOk = outcomes.every(function(o) { return o.success || o.skipped; });

    // 保存各链日志到文件
    for (var d of chainDefs) {
        if (parallelFullLogs[d.key]) {
            try {
                await fetch("/api/novels/" + currentNovelId + "/genesis/logs", {
                    method: "POST",
                    headers: {"Content-Type": "application/json"},
                    body: JSON.stringify({logs: parallelFullLogs[d.key], chain_title: chainMap[d.key].title})
                });
            } catch(e) {}
        }
    }

    if (allOk) {
        clearGenesisState();
        logToConsole("[System] \n\n🎉🎉 [创世神迹已成] 三链并行建纲全部圆满成功！\n", "tool");
        statusBadge.textContent = "创世圆满成功";
        statusBadge.style.color = "#22c55e";
        showToast("🌌 智能创世三链并行大重构圆满成功！");
        if (typeof refreshAll === "function") await refreshAll();
        if (typeof loadLocationsCache === "function") loadLocationsCache();
        if (typeof loadLocationTree === "function") loadLocationTree();
    } else {
        var failed = outcomes.filter(function(o) { return !o.success && !o.skipped; });
        var failedNames = failed.map(function(o) { return o.key; }).join(", ");
        throw new Error("并行建纲部分失败: " + failedNames);
    }
}

function handleGenesisPaused(state, statusBadge, submitBtn, closeBtn, pauseBtn, textarea, backdrop) {
    state.status = "interrupted";
    saveGenesisState(state);
    if (window.__genStopFlusher) { window.__genStopFlusher(); }
    if (window.__genFlushLog) { window.__genFlushLog(true); }
    statusBadge.textContent = "已暂停 · 可恢复";
    statusBadge.style.color = "#f59e0b";
    var consoleEl = document.getElementById("genesis-console");
    if (consoleEl) {
        var span = document.createElement("span");
        span.style.color = "#f59e0b";
        span.textContent = "\n⏸ [已暂停] 节点 " + (state.currentNodeId || "?") + " · 进度已保存，下次打开智能建纲时可继续执行。\n";
        consoleEl.appendChild(span);
        consoleEl.scrollTop = consoleEl.scrollHeight;
    }
    // 标记当前阶段为 interrupted
    var stepIds = ["step-map", "step-outline", "step-char"];
    if (state.parallel && state.chainStates) {
        // 并行模式：标记所有未完成的链为 interrupted
        var keys = ["outline", "map", "character"];
        keys.forEach(function(key, idx) {
            var cs = state.chainStates[key];
            if (cs && !cs.completed) {
                document.getElementById(stepIds[idx]).className = "step-interrupted";
            }
        });
    } else if (state.phase >= 1 && state.phase <= 3) {
        document.getElementById(stepIds[state.phase - 1]).className = "step-interrupted";
    }
    genesisFinishedCleanup(submitBtn, closeBtn, pauseBtn, textarea, backdrop);
}

function handleGenesisAborted(statusBadge, submitBtn, closeBtn, pauseBtn, abortBtn, textarea, backdrop) {
    if (window.__genStopFlusher) { window.__genStopFlusher(); }
    if (window.__genFlushLog) { window.__genFlushLog(true); }
    clearGenesisState();
    statusBadge.textContent = "已中断 · 不可恢复";
    statusBadge.style.color = "#ef4444";
    var consoleEl = document.getElementById("genesis-console");
    if (consoleEl) {
        var span = document.createElement("span");
        span.style.color = "#ef4444";
        span.textContent = "\n⏹ [已中断] 建纲已被强制终止，进度未保存。\n";
        consoleEl.appendChild(span);
        consoleEl.scrollTop = consoleEl.scrollHeight;
    }
    // 所有步骤标为 interrupted
    var stepIds = ["step-map", "step-outline", "step-char"];
    stepIds.forEach(function(id) {
        var el = document.getElementById(id);
        if (el && el.className === "step-active") el.className = "step-interrupted";
    });
    genesisFinishedCleanup(submitBtn, closeBtn, pauseBtn, textarea, backdrop);
}

function genesisFinishedCleanup(submitBtn, closeBtn, pauseBtn, textarea, backdrop) {
    // 幂等保护：防止双重调用
    if (!isGenesisRunning && !genesisPaused && !genesisAborted) return;
    isGenesisRunning = false;
    genesisPaused = false;
    genesisAborted = false;
    submitBtn.disabled = false;
    submitBtn.style.display = "";
    closeBtn.disabled = false;
    if (pauseBtn) pauseBtn.style.display = "none";
    var abortBtn = document.getElementById("genesis-abort-btn");
    if (abortBtn) abortBtn.style.display = "none";
    textarea.disabled = false;
    backdrop.classList.remove("genesis-running-backdrop");

    var chatInput = document.getElementById("copilot-input");
    if (chatInput) {
        chatInput.removeAttribute("disabled");
        chatInput.style.pointerEvents = "auto";
        chatInput.style.opacity = "1";
    }
    var copilotSend = document.getElementById("copilot-send");
    if (copilotSend) {
        copilotSend.removeAttribute("disabled");
        copilotSend.style.pointerEvents = "auto";
        copilotSend.style.opacity = "1";
    }
    console.log("[System] 全局创世流结束，AI 助手聊天控制权已完美归还用户。");
}

/* ═══════════ 断点恢复执行 ═══════════ */
async function executeGenesisFromResume(state) {
    var text = state.inputText;
    var mode = state.mode || "full";
    var startPhase = state.phase || 1;
    var completedPhases = state.completedPhases || [false, false, false];

    // 如果覆写模式但之前已清空过数据，则不再清空
    // 恢复时不重复清空操作

    clearGenesisState();
    genesisPaused = false;
    genesisAborted = false;
    genesisAbortController = new AbortController();

    var initialState = {
        phase: startPhase,
        completedPhases: completedPhases,
        currentNodeId: state.currentNodeId || null,
        currentChainId: state.currentChainId || null,
        fullChainContext: state.fullChainContext || "",
        executedNodeCount: state.executedNodeCount || 0,
        inputText: text,
        mode: mode,
        parallel: state.parallel || false,
        chainStates: state.chainStates || null,
        status: "running",
        startedAt: Date.now(),
    };

    await executeGenesisCore(initialState, startPhase, completedPhases);
}

copilotInput.addEventListener("keydown", e => { if (e.key === "Enter") { e.preventDefault(); handleCopilotSend(); } });
document.querySelectorAll(".cp-quick-btn").forEach(b => b.addEventListener("click", () => copilotChat(b.dataset.cmd)));

function renderToolProposal(parentBubble, proposal, autoExecute) {
    var argsText = JSON.stringify(proposal.arguments || {}, null, 2);
    var toolLabel = proposal.tool_name === "advance_world_time" ? "🌏 推进世界时间" :
                    proposal.tool_name === "manage_world_element" ? "⚙️ 元素操作" : "🔧 " + proposal.tool_name;
    var tp = document.createElement("div");
    tp.className = "tool-proposal";
    tp.innerHTML = '<div class="tp-title">📋 提议操作: ' + toolLabel + '</div>' +
        '<div class="tp-args">参数: ' + e(argsText) + '</div>' +
        '<div class="tp-actions">' +
            '<button class="tp-approve">✅ 批准并执行</button>' +
            '<button class="tp-reject">❌ 拒绝</button>' +
        '</div>';
    parentBubble.appendChild(tp);
    copilotHistory.scrollTop = copilotHistory.scrollHeight;

    if (autoExecute) {
        tp.querySelector(".tp-actions").innerHTML = '<span style="font-size:.7rem;color:#7c3aed;">⚡ 自动执行中...</span>';
        executeTool(proposal.tool_name, proposal.arguments, tp);
        return;
    }

    tp.querySelector(".tp-approve").addEventListener("click", function() {
        tp.querySelector(".tp-actions").innerHTML = '<span style="font-size:.7rem;color:#7c3aed;">⏳ 执行中...</span>';
        executeTool(proposal.tool_name, proposal.arguments, tp);
    });
    tp.querySelector(".tp-reject").addEventListener("click", function() {
        tp.classList.add("tp-rejected");
        tp.querySelector(".tp-title").textContent = "❌ 已拒绝: " + toolLabel;
    });
}

async function executeTool(toolName, args, card) {
    try {
        var res = await fetch("/api/agent/execute/" + currentNovelId, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ tool_name: toolName, arguments: args }),
        });
        var result = await res.json();
        if (result.status === "success") {
            card.classList.add("tp-executed");
            card.querySelector(".tp-title").textContent = "✅ 已执行: " + result.msg;
            if (typeof loadNovelTime === "function") loadNovelTime();
            if (currentModule === "characters" && typeof loadCharacters === "function") loadCharacters(currentNovelId);
            if (toolName === "manage_world_element") {
                if (typeof loadOutlineNotes === "function") loadOutlineNotes();
                if (typeof loadLocationsCache === "function") loadLocationsCache();
                if (typeof loadLocationTree === "function") loadLocationTree();
                if (typeof loadTimeline === "function") loadTimeline();
            }
            if (toolName === "generate_reasoning_chain") {
                if (typeof loadReasoningModule === "function") loadReasoningModule();
            }
            return true;
        } else {
            card.querySelector(".tp-title").textContent = "❌ 执行失败: " + (result.msg || "未知错误");
            card.classList.add("tp-rejected");
            return false;
        }
    } catch (e) {
        card.querySelector(".tp-title").textContent = "❌ 网络错误: " + e.message;
        card.classList.add("tp-rejected");
        return false;
    }
}

async function runHeadlessChain(chainId, userInput, bubbleEl, onLogCallback = null, contextSettings = null, resumeState = null, chainKey = null) {
    return new Promise(async (resolve) => {
        var s = getSettings();
        try {
            var rc = await (await fetch("/api/reasoning_chains/" + chainId + "?novel_id=" + currentNovelId)).json();
            var nodes = rc.nodes || [];
            if (nodes.length === 0) {
                if (onLogCallback) onLogCallback("❌ 此方案无节点，执行终止。\n", "error");
                resolve({ success: false, reason: "no_nodes" });
                return;
            }

            nodes.forEach(function(n) { n.output = ""; });
            var currentNodeId, loopCount, fullChainContext;
            if (resumeState) {
                // 断点恢复：从保存的节点继续，使用已有的上下文
                currentNodeId = resumeState.currentNodeId || nodes[0].id;
                loopCount = resumeState.executedNodeCount || 0;
                fullChainContext = resumeState.fullChainContext || ("【初始指令】: " + userInput + "\n");
                if (onLogCallback) onLogCallback("\n🔄 [断点恢复] 从节点 " + currentNodeId + " 继续执行（已执行 " + loopCount + " 步）\n", "step");
            } else {
                currentNodeId = nodes[0].id;
                loopCount = 0;
                fullChainContext = "【初始指令】: " + userInput + "\n";
            }

            while (currentNodeId) {
                loopCount++;
                if (loopCount > 100) {
                    if (onLogCallback) onLogCallback("⚠️ 触发安全硬熔断 (超过100个循环)。\n", "warning");
                    break;
                }
                // 🛑 暂停检查：优雅停止，保存进度
                if (genesisPaused && isGenesisRunning) {
                    if (onLogCallback) onLogCallback("\n⏸ [用户暂停] 已收到暂停信号，正在保存进度...\n", "warning");
                    var gs = loadGenesisState();
                    if (gs) {
                        if (chainKey && gs.chainStates) {
                            gs.chainStates[chainKey] = { currentNodeId: currentNodeId, fullChainContext: fullChainContext, executedNodeCount: loopCount, chainId: chainId };
                        } else {
                            gs.currentNodeId = currentNodeId;
                            gs.fullChainContext = fullChainContext;
                            gs.executedNodeCount = loopCount;
                            gs.currentChainId = chainId;
                        }
                        gs.status = "interrupted";
                        gs.inputText = userInput;
                        saveGenesisState(gs);
                    }
                    resolve({ success: false, reason: "paused" });
                    return;
                }
                // 🛑 中断检查：立即停止，不保存
                if (genesisAborted && isGenesisRunning) {
                    if (onLogCallback) onLogCallback("\n⏹ [用户中断] 强制终止，进度未保存。\n", "error");
                    resolve({ success: false, reason: "aborted" });
                    return;
                }
                var node = nodes.find(function(n) { return n.id === currentNodeId; });
                if (!node) break;

                var progressEl = document.createElement("div");
                progressEl.style.cssText = "font-size:0.72rem;color:var(--text-muted);margin:4px 0;";
                progressEl.textContent = "⚙️ 执行: " + ((node.prompt || "").split("\n")[0] || "").substring(0, 60);
                if (bubbleEl) { bubbleEl.appendChild(progressEl); copilotHistory.scrollTop = copilotHistory.scrollHeight; }

                if (onLogCallback) {
                    onLogCallback("\n▶️ [执行节点] " + (node.premise || node.id) + "\n", "step");
                }

                var nodeToolCalls = "";
                try {
                    // ⚙️ 程序节点：执行预设动作（capture_template 等）
                    if (node.type === "program") {
                        var progAction = node.program_action || "";
                        if (progAction === "capture_template") {
                            try {
                                var novelData = await (await fetch("/api/novels/" + currentNovelId)).json();
                                rc._template_snapshot = novelData.character_template || [];
                                if (onLogCallback) onLogCallback("\n📸 [模板快照] 已冻结 " + (rc._template_snapshot.length || 0) + " 个分组\n", "tool");
                            } catch(e) {
                                rc._template_snapshot = [];
                            }
                        }
                        node.output = "✅ 程序执行完毕";
                        currentNodeId = node.next_node_id || null;
                        continue;
                    }

                    // ❓ 交互节点：建纲模式下自动回应「否」，无人值守模式下跳过
                    if (node.type === "ask") {
                        if (isGenesisRunning) {
                            if (onLogCallback) onLogCallback("\n❓ 交互节点（自动回应「否」）: " + (node.premise || "(无提问内容)") + "\n", "warning");
                            node.output = "【自动回应】否\n" + (node.premise || "");
                        } else {
                            if (onLogCallback) onLogCallback("\n❓ 交互节点（无人值守模式）: " + (node.premise || "(无提问内容)") + "\n", "warning");
                            node.output = "【自动跳过交互】\n" + (node.premise || "");
                        }
                        var askNextId = node.next_node_id || null;
                        currentNodeId = askNextId;
                        continue;
                    }

                    // 🔀 路由节点
                    if (node.type === "router") {
                        var rv = "";
                        var allRoutes = fullChainContext.match(/<ROUTE>([\s\S]*?)<\/ROUTE>/g);
                        if (allRoutes && allRoutes.length) {
                            var lastRoute = allRoutes[allRoutes.length - 1];
                            var rvm2 = lastRoute.match(/<ROUTE>([\s\S]*?)<\/ROUTE>/);
                            if (rvm2) rv = rvm2[1].trim();
                        }
                        var rNextId = null;
                        if (node.branches && node.branches.length && rv) {
                            for (var rbi = 0; rbi < node.branches.length; rbi++) {
                                if (node.branches[rbi].condition && rv.includes(node.branches[rbi].condition)) {
                                    rNextId = node.branches[rbi].next_node_id; break;
                                }
                            }
                        }
                        if (!rNextId) rNextId = node.next_node_id || null;
                        node.output = "🔀 路由: [" + (rv || "无值") + "] → " + (rNextId || "终止");
                        fullChainContext += "\n--- 【路由判定】 ---\n" + node.output;
                        if (onLogCallback) onLogCallback("\n🔀 [路由判定] " + (rv || "无路由值") + " → " + (rNextId || "终止") + "\n", "step");
                        currentNodeId = rNextId;
                        continue;
                    }

                    // 🔍 校验节点：程序验证
                    if (node.type === "validate") {
                        var vFmt = node.format || "";
                        var vParser = REASONING_FORMATS[vFmt] && REASONING_FORMATS[vFmt].parser;
                        var vResult = { valid: true };
                        if (vParser) {
                            var vItems = vParser(lastReasoning || "");
                            vResult = validateStructuredOutput(vFmt, vItems, rc.nodes || null, rc._template_snapshot || null);
                        }
                        if (vResult.valid) {
                            fullChainContext += "\n\n--- 【校验通过】 ---\n" + lastReasoning;
                            node.output = "✅ 校验通过";
                            // 转跳到下一节点
                            var writeTarget = (node.branches || []).find(function(b) { return b.condition === "VALID"; });
                            if (writeTarget && writeTarget.next_node_id) {
                                currentNodeId = writeTarget.next_node_id;
                            } else {
                                currentNodeId = node.next_node_id || null;
                            }
                        } else {
                            fullChainContext += "\n\n--- 【校验失败】 ---\n" + vResult.errors.join("; ");
                            node.output = "⛔ 校验失败: " + vResult.errors.join("; ");
                            // 转跳回创建节点
                            var retryTarget = (node.branches || []).find(function(b) { return b.condition === "INVALID"; });
                            if (retryTarget && retryTarget.next_node_id) {
                                fullChainContext += "\n[系统指令] 上一个节点的输出未通过数据校验，请根据以下错误修正后重新输出：\n" + vResult.errors.map(function(e) { return "- " + e; }).join("\n");
                                currentNodeId = retryTarget.next_node_id;
                            } else {
                                currentNodeId = node.next_node_id || null;
                            }
                        }
                        continue;
                    }

                    // ✅ 校验-确认节点：纯程序检查待办清单，不依赖 LLM
                    if (node.type === "verify") {
                        // 强制刷新 todos
                        try {
                            var freshV = await (await fetch("/api/reasoning_chains/" + chainId + "?novel_id=" + currentNovelId)).json();
                            rc.todos = freshV.todos || [];
                        } catch(e) {}
                        var vTodos = rc.todos || [];
                        var vFilter = node.verify_filter || "";
                        var filters = vFilter ? vFilter.split('|') : [];
                        var hasPending = false;
                        if (filters.length > 0) {
                            for (var vti = 0; vti < vTodos.length; vti++) {
                                if ((vTodos[vti].status === "pending" || vTodos[vti].status === "in_progress") && vTodos[vti].content) {
                                    for (var fi = 0; fi < filters.length; fi++) {
                                        if (vTodos[vti].content.indexOf(filters[fi]) >= 0) {
                                            hasPending = true; break;
                                        }
                                    }
                                    if (hasPending) break;
                                }
                            }
                        } else {
                            // 无过滤：检查是否有任何 pending 项
                            for (var vti2 = 0; vti2 < vTodos.length; vti2++) {
                                if (vTodos[vti2].status === "pending" || vTodos[vti2].status === "in_progress") {
                                    hasPending = true; break;
                                }
                            }
                        }
                        var vBranch = hasPending ? "CONTINUE" : ((node.branches || []).some(function(b){return b.condition==="NEXT";}) ? "NEXT" : "DONE");
                        var vTarget = null;
                        for (var vbi = 0; vbi < (node.branches || []).length; vbi++) {
                            if (node.branches[vbi].condition === vBranch) { vTarget = node.branches[vbi].next_node_id; break; }
                        }
                        if (!vTarget) vTarget = node.next_node_id || null;
                        node.output = (hasPending ? "⏳ 仍有待办" : "✅ 全部完成") + " → " + vBranch;
                        fullChainContext += "\n--- 【校验确认】 ---\n" + node.output;
                        if (onLogCallback) onLogCallback("\n✅ [校验确认] " + node.output + "\n", "tool");
                        currentNodeId = vTarget;
                        continue;
                    }

                    // 🎯 取待办节点：程序顺序选取第一个未完成待办
                    if (node.type === "pick_todo") {
                        try {
                            var freshP = await (await fetch("/api/reasoning_chains/" + chainId + "?novel_id=" + currentNovelId)).json();
                            rc.todos = freshP.todos || [];
                        } catch(e) {}
                        var pFilter = node.pick_filter || "";
                        var filters = pFilter ? pFilter.split('|') : [];
                        var picked = null;
                        for (var pti = 0; pti < (rc.todos || []).length; pti++) {
                            var td = rc.todos[pti];
                            if (td.status === "pending" || td.status === "in_progress") {
                                if (filters.length > 0 && td.content) {
                                    for (var pfi = 0; pfi < filters.length; pfi++) {
                                        if (td.content.indexOf(filters[pfi]) >= 0) { picked = td; break; }
                                    }
                                } else {
                                    picked = td;
                                }
                                if (picked) break;
                            }
                        }
                        if (picked) {
                            node.output = picked.content || "";
                            rc._last_picked_todo_id = picked.id;  // 存储选取的待办ID，供 mark_done 使用
                            fullChainContext += "\n--- 【选取待办】 ---\n" + node.output;
                            if (onLogCallback) onLogCallback("\n🎯 [选取待办] " + (picked.content || "").substring(0, 80) + "\n", "tool");
                            var pNext = (node.branches || []).find(function(b){return b.condition==="HAS_TODO";});
                            currentNodeId = pNext ? pNext.next_node_id : (node.next_node_id || null);
                        } else {
                            node.output = "ALL_DONE";
                            fullChainContext += "\n--- 【选取待办】 ---\n无待办项";
                            if (onLogCallback) onLogCallback("\n🎯 [选取待办] 无待办项 → ALL_DONE\n", "tool");
                            var pNone = (node.branches || []).find(function(b){return b.condition==="NO_TODO";});
                            currentNodeId = pNone ? pNone.next_node_id : (node.next_node_id || null);
                        }
                        continue;
                    }

                    // 📝 写入节点：程序直接写入
                    if (node.type === "write") {
                        var wContent = fullChainContext.replace(/<\/?ROUTE>/g, "").trim();
                        // 取最后一段推理输出作为写入内容
                        var lastReasoning = wContent.split("--- 【节点输出】 ---").pop() || "";
                        lastReasoning = lastReasoning.replace(/--- 【路由判定】 ---[\s\S]*/, "").replace(/--- 【写入结果】 ---[\s\S]*/, "").replace(/--- 【写入失败】 ---[\s\S]*/, "").trim();
                        if (!lastReasoning) lastReasoning = (node.premise || "");
                        var wt = node.write_to || {};
                        if (wt.entity && lastReasoning) {
                            var wToolName = "manage_world_element";
                            var wArgs = {};
                            if (wt.entity === "chain_todos") {
                                wToolName = "manage_chain_todos";
                                if (wt.action === "update" && wt.field === "status") {
                                    // 强制刷新 todos，确保拿到最新数据
                                    try {
                                        var freshRc2 = await (await fetch("/api/reasoning_chains/" + chainId + "?novel_id=" + currentNovelId)).json();
                                        rc.todos = freshRc2.todos || [];
                                    } catch(e) {}
                                    var todos = (rc && rc.todos) ? rc.todos : [];
                                    // 优先使用 pick_todo 阶段存储的 ID
                                    var targetId = rc._last_picked_todo_id;
                                    var pending = null;
                                    if (targetId) {
                                        for (var ti = 0; ti < todos.length; ti++) {
                                            if (todos[ti].id === targetId) { pending = todos[ti]; break; }
                                        }
                                    }
                                    // 回退：按 filter 匹配第一个 pending 项（而非全表第一个）
                                    if (!pending) {
                                        var markFilter = node.verify_filter || node.pick_filter || "";
                                        var mFilters = markFilter ? markFilter.split('|') : [];
                                        for (var ti = 0; ti < todos.length; ti++) {
                                            if (todos[ti].status === "pending" || todos[ti].status === "in_progress") {
                                                if (mFilters.length > 0 && todos[ti].content) {
                                                    for (var pfi = 0; pfi < mFilters.length; pfi++) {
                                                        if (todos[ti].content.indexOf(mFilters[pfi]) >= 0) { pending = todos[ti]; break; }
                                                    }
                                                } else {
                                                    pending = todos[ti];
                                                }
                                                if (pending) break;
                                            }
                                        }
                                    }
                                    wArgs = { action: "update_status", todo_id: pending ? pending.id : "none", status: "done" };
                                } else {
                                    // 按行拆分 planning 输出，每条 `- [ ]` 行创建一个独立任务
                                    var rawLines = lastReasoning.split('\n');
                                    var todoLines = [];
                                    for (var li = 0; li < rawLines.length; li++) {
                                        var line = rawLines[li].trim();
                                        if (line && (line.indexOf('- [ ]') >= 0 || line.indexOf('- []') >= 0 || line.indexOf('* [ ]') >= 0)) {
                                            todoLines.push(line);
                                        }
                                    }
                                    if (todoLines.length > 1) {
                                        var createdCount = 0;
                                        for (var tli = 0; tli < todoLines.length; tli++) {
                                            try {
                                                var addRes = await fetch("/api/agent/execute/" + currentNovelId, { method: "POST", headers: {"Content-Type":"application/json"}, body: JSON.stringify({ tool_name: wToolName, arguments: { action: "add", content: todoLines[tli] }, chain_id: chainId }) });
                                                if (addRes.ok) createdCount++;
                                            } catch(e) {}
                                        }
                                        try {
                                            var freshRc3 = await (await fetch("/api/reasoning_chains/" + chainId + "?novel_id=" + currentNovelId)).json();
                                            rc.todos = freshRc3.todos || [];
                                        } catch(e) {}
                                        node.output = "✅ 写入成功：已创建 " + createdCount + " 个待办任务";
                                        fullChainContext += "\n--- 【写入结果】 ---\n" + node.output;
                                        if (onLogCallback) onLogCallback("\n📝 [写入成功] 已创建 " + createdCount + " 个待办任务\n", "tool");
                                        var wNextId2 = node.next_node_id || null;
                                        currentNodeId = wNextId2;
                                        continue;
                                    } else {
                                        wArgs = { action: "add", content: lastReasoning };
                                    }
                                }
                            } else {
                                wArgs = { element_type: wt.entity, action: wt.action === "create" ? "add" : (wt.action === "update" ? "update" : "add"), data: {} };
                                if (wt.entity === "character_template") {
                                    var tmplData = null;
                                    try { tmplData = JSON.parse(lastReasoning); } catch(e) {
                                        var m2 = lastReasoning.match(/\[[\s\S]*\]/);
                                        if (m2) try { tmplData = JSON.parse(m2[0]); } catch(e2) {}
                                    }
                                    wArgs.data["attributes"] = tmplData || lastReasoning;
                                } else if (wt.entity === "location" && wt.field === "description") {
                                    var lParser = REASONING_FORMATS["location"] && REASONING_FORMATS["location"].parser;
                                    if (lParser) {
                                        var lItems = lParser(lastReasoning);
                                        if (lItems && lItems.length) {
                                            var loc = lItems[0];
                                            wArgs.data["name"] = loc.name || "";
                                            wArgs.data["scale_level"] = loc.scale_level || "";
                                            wArgs.data["parent_name"] = loc.parent_name || "";
                                            wArgs.data["scale_enum"] = loc.scale_enum || "REGION";
                                            if (loc.grid_x !== undefined) wArgs.data["grid_x"] = loc.grid_x;
                                            if (loc.grid_y !== undefined) wArgs.data["grid_y"] = loc.grid_y;
                                            if (loc.map_x !== undefined) wArgs.data["map_x"] = loc.map_x;
                                            if (loc.map_y !== undefined) wArgs.data["map_y"] = loc.map_y;
                                            wArgs.data["description"] = loc.description || "";
                                            wArgs.data["attributes"] = loc.attributes || {};
                                        } else {
                                            wArgs.data["description"] = lastReasoning;
                                        }
                                    } else {
                                        wArgs.data["description"] = lastReasoning;
                                    }
                                } else if (wt.entity === "character" && wt.field === "attributes") {
                                    var cParser = REASONING_FORMATS["character"] && REASONING_FORMATS["character"].parser;
                                    if (cParser) {
                                        var cItems = cParser(lastReasoning);
                                        if (cItems && cItems.length) {
                                            var ch = cItems[0];
                                            wArgs.data["name"] = ch.name || "";
                                            if (ch.aliases) wArgs.data["aliases"] = ch.aliases;
                                            if (ch.status) wArgs.data["status"] = ch.status;
                                            if (ch.is_active !== undefined) wArgs.data["is_active"] = ch.is_active;
                                            if (ch.faction_name) wArgs.data["faction_name"] = ch.faction_name;
                                            if (ch.faction_role) wArgs.data["faction_role"] = ch.faction_role;
                                            if (ch.is_always_context !== undefined) wArgs.data["is_always_context"] = ch.is_always_context;
                                            wArgs.data["description"] = ch.description || "";
                                            wArgs.data["attributes"] = ch.attributes || {};
                                        } else {
                                            wArgs.data["attributes"] = lastReasoning;
                                        }
                                    } else {
                                        wArgs.data["attributes"] = lastReasoning;
                                    }
                                } else if (wt.field === "category" && wt.sub_field) {
                                    // 大纲分类由节点配置锁定，解析上游结构化输出取 title/description
                                    var oFmt = (node.format || "").replace("outline","outline") || "outline";
                                    var oParser = REASONING_FORMATS["outline"] && REASONING_FORMATS["outline"].parser;
                                    if (oParser) {
                                        var oItems = oParser(lastReasoning);
                                        if (oItems && oItems.length) {
                                            wArgs.data["title"] = oItems[0].title || "";
                                            wArgs.data["description"] = oItems[0].description || lastReasoning;
                                            if (oItems[0].parent_name && oItems[0].parent_name !== "无") wArgs.data["parent_name"] = oItems[0].parent_name;
                                            if (oItems[0].order_index !== undefined) wArgs.data["order_index"] = oItems[0].order_index;
                                        }
                                    }
                                    wArgs.data["category"] = wt.sub_field;
                                } else if (wt.field === "attributes" && wt.sub_field) {
                                    wArgs.data["attributes"] = {}; wArgs.data["attributes"][wt.sub_field] = lastReasoning;
                                } else if (wt.field) {
                                    wArgs.data[wt.field] = lastReasoning;
                                } else {
                                    wArgs.data["description"] = lastReasoning;
                                }
                            }
                            try {
                                var wRes = await fetch("/api/agent/execute/" + currentNovelId, { method: "POST", headers: {"Content-Type":"application/json"}, body: JSON.stringify({ tool_name: wToolName, arguments: wArgs, write_to: wt.entity === "chain_todos" ? null : wt, chain_id: chainId }) });
                                if (wRes.ok) {
                                    var wJson = await wRes.json();
                                    node.output = "✅ 写入成功：" + wJson.msg;
                                    fullChainContext += "\n--- 【写入结果】 ---\n" + node.output;
                                    if (onLogCallback) onLogCallback("\n📝 [写入成功] " + wJson.msg + "\n", "tool");
                                    // 🔄 chain_todos 操作后刷新内存中的 todos，防止后续 update 找不到待办
                                    if (wt.entity === "chain_todos") {
                                        try {
                                            var freshRc = await (await fetch("/api/reasoning_chains/" + chainId + "?novel_id=" + currentNovelId)).json();
                                            rc.todos = freshRc.todos || [];
                                        } catch(e) {}
                                    }
                                } else {
                                    var wErr = await wRes.json().catch(function() { return {detail:"未知错误"}; });
                                    node.output = "⛔ 写入失败：" + (wErr.detail || "");
                                    fullChainContext += "\n--- 【写入失败】 ---\n" + node.output;
                                    if (onLogCallback) onLogCallback("\n⛔ [写入失败] " + (wErr.detail || "") + "\n", "error");
                                }
                            } catch(we) { node.output = "⛔ 写入异常：" + we.message; }
                        } else {
                            node.output = "⛔ 写入跳过：缺少写入目标或上游无内容";
                        }
                        var wNextId = node.next_node_id || null;
                        // 写入失败时尝试匹配分支
                        if (node.output.indexOf("⛔") >= 0 && node.branches && node.branches.length) {
                            for (var wbi = 0; wbi < node.branches.length; wbi++) {
                                if (node.branches[wbi].condition && node.output.includes(node.branches[wbi].condition)) {
                                    wNextId = node.branches[wbi].next_node_id; break;
                                }
                            }
                        }
                        currentNodeId = wNextId;
                        continue;
                    }

                    var targetT = compileTarget(node.target);
                    var res = await fetch("/api/reasoning/execute", {
                        method: "POST", headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({
                            novel_id: currentNovelId,
                            premise: node.premise || "",
                            prompt: (node.prompt || "") + getFormatPrompt(node),
                            target: targetT,
                            previous_output: fullChainContext,
                            api_key: s.apiKey, base_url: s.baseUrl, model: s.model,
                            context_settings: contextSettings || rc.context_settings || {},
                            response_format: (/deepseek/i.test(s.model) && node.format) ? {type: "json_object"} : undefined,
                            no_tools: true,
                        }),
                        signal: genesisAbortController ? genesisAbortController.signal : undefined,
                    });
                    var txt = "";
                    var gen = parseSSEStream(res);
                    while (true) {
                        var cr = await gen.next();
                        if (cr.done) break;
                        var msg = cr.value;
                        if (msg.type === "chunk") {
                            txt += msg.content;
                            if (bubbleEl) { bubbleEl.appendChild(document.createTextNode(msg.content)); copilotHistory.scrollTop = copilotHistory.scrollHeight; }
                            if (onLogCallback) onLogCallback(msg.content, "chunk");
                        } else if (msg.type === "tool_proposal") {
                            if (bubbleEl) {
                                var card = document.createElement("div");
                                card.className = "tool-proposal";
                                card.innerHTML = '<div class="tp-title">🛠 自动执行: ' + e(msg.tool_name) + '</div>'
                                    + '<div class="tp-args">' + e(JSON.stringify(msg.arguments, null, 2)) + '</div>'
                                    + '<div class="tp-actions"><span style="font-size:.7rem;color:#7c3aed;">⏳ 链式执行中...</span></div>';
                                bubbleEl.appendChild(card);
                                copilotHistory.scrollTop = copilotHistory.scrollHeight;
                            }
                            if (onLogCallback) {
                                var toolName = msg.tool_name || msg.arguments?.element_type || "";
                                var toolTitle = (msg.arguments?.data?.title || msg.arguments?.data?.name || "");
                                onLogCallback("\n⚙️ 自动录入实体: [" + toolName + "] -> " + toolTitle + "\n", "tool");
                            }
                            var execRes = await fetch("/api/agent/execute/" + currentNovelId, {
                                method: "POST", headers: { "Content-Type": "application/json" },
                                body: JSON.stringify({ tool_name: msg.tool_name, arguments: msg.arguments, write_to: node.write_to || null, chain_id: chainId }),
                            });
                            if (!execRes.ok) {
                                var errBody = await execRes.json().catch(function() { return {detail: "未知错误"}; });
                                if (onLogCallback) onLogCallback("\n⛔ 写入校验拦截: " + (errBody.detail || "") + "\n", "error");
                                nodeToolCalls += "\n[⛔ 校验拦截: " + (errBody.detail || "") + "]";
                            } else {
                                if (bubbleEl) {
                                    card.classList.add("tp-executed");
                                    card.querySelector(".tp-title").textContent = "✅ 已执行: " + msg.tool_name;
                                }
                                nodeToolCalls += "\n[✅ 系统已记录：成功调用 '" + msg.tool_name + "' 归档数据: " + toolTitle + "]";
                            }
                        }
                    }
                    var routeKey = null;
                    // 🛡️ 输出格式强制校验
                    var cleanTxt = txt;
                    // 剥离 AI 可能输出的 markdown 代码块包裹
                    var codeBlockMatch = cleanTxt.match(/```[\s\S]*?```/);
                    if (codeBlockMatch) {
                        cleanTxt = cleanTxt.replace(/```[\s\S]*?```/g, function(m) { return m.replace(/```\w*\n?/g, "").replace(/```/g, ""); });
                        if (onLogCallback) onLogCallback("\n⚠️ 校验器剥离了 markdown 代码块包裹\n", "warning");
                    }
                    var rm = cleanTxt.match(/<ROUTE>([\s\S]*?)<\/ROUTE>/);
                    if (rm) routeKey = rm[1].trim();
                    
                    var cleanOutput = cleanTxt.replace(/<\/?ROUTE>/g, "").trim();
                    node.output = cleanOutput;

                    fullChainContext += "\n\n--- 【节点输出】 ---\n" + cleanOutput;
                    if (nodeToolCalls) {
                        fullChainContext += nodeToolCalls;
                    }

                    var nextId = null;
                    if (node.branches && node.branches.length > 0) {
                        for (var bi = 0; bi < node.branches.length; bi++) {
                            var br = node.branches[bi];
                            if (br.condition && (routeKey === br.condition || cleanOutput.includes(br.condition))) {
                                nextId = br.next_node_id;
                                break;
                            }
                        }
                    }
                    if (!nextId) nextId = node.next_node_id || null;
                    currentNodeId = nextId;
                } catch (e) {
                    if (bubbleEl) bubbleEl.appendChild(document.createTextNode("\n[节点错误: " + e.message + "]"));
                    if (onLogCallback) onLogCallback("\n❌ 节点执行错误: " + e.message + "\n", "error");
                    currentNodeId = null;
                }
                // 💾 建纲状态持久化：每节点完成后保存
                if (isGenesisRunning && !genesisAborted && currentNodeId) {
                    var gsNow = loadGenesisState();
                    if (gsNow) {
                        if (chainKey && gsNow.chainStates) {
                            gsNow.chainStates[chainKey] = { currentNodeId: currentNodeId, fullChainContext: fullChainContext, executedNodeCount: loopCount, chainId: chainId };
                        } else {
                            gsNow.currentNodeId = currentNodeId;
                            gsNow.fullChainContext = fullChainContext;
                            gsNow.executedNodeCount = loopCount;
                            gsNow.currentChainId = chainId;
                        }
                        gsNow.inputText = userInput;
                        saveGenesisState(gsNow);
                    }
                }
            }
            if (bubbleEl) bubbleEl.appendChild(document.createTextNode("\n\n✅ 推理链执行完毕。"));
            // 执行完毕自动清空任务清单
            try { await fetch("/api/reasoning_chains/" + chainId + "/todos/clear", { method: "POST" }); } catch(e) {}
            resolve({ success: true });
        } catch (e) {
            if (bubbleEl) bubbleEl.appendChild(document.createTextNode("\n[链加载失败: " + e.message + "]"));
            if (onLogCallback) onLogCallback("\n❌ 执行中途崩溃: " + e.message + "\n", "error");
            resolve({ success: false, reason: e.message });
        } finally {
            if (bubbleEl) bubbleEl.classList.remove("streaming");
        }
    });
}

/* ═══════════ Prompt Settings ═══════════ */
const promptModal = document.getElementById("prompt-modal");
const promptTpl = document.getElementById("prompt-templates");
const promptLabels = { system_agent: "系统代理神谕", intent_classifier: "意图预检分类器", chat: "自由对话", map_gen: "地图自动生成", char_gen: "角色自动生成", outline_gen: "大纲自动生成", extract_outline: "正文逆向提炼", continue: "续写", polish: "润色", review: "评价", chain_write: "推演链生成正文", outline_assist: "大纲助手", map_assist: "地图助手", character_assist: "人物助手", archive_timeline: "归档-时间线提取", archive_character: "归档-人物更新", sandbox_sim: "沙盘演武", import_data: "智能导入解析" };

function getCopilotScene() {
    return "auto";
}

document.getElementById("prompt-settings-btn").addEventListener("click", async () => {
    const prompts = await (await fetch("/api/settings/prompts")).json();
    promptTpl.innerHTML = '<div style="text-align:right;margin-bottom:10px;"><button class="sys-btn sys-btn-danger" style="font-size:.72rem;" onclick="if(confirm(\'确定恢复所有提示词为系统默认吗？您的修改将丢失！\')){fetch(\'/api/settings/reset_prompts\',{method:\'POST\'}).then(function(){location.reload();});}">🔄 恢复全部出厂默认</button></div>';
    Object.entries(promptLabels).forEach(([key, label]) => {
        var warning = "";
        if (key === "chat" || key === "import_data" || key === "system_agent" || key === "intent_classifier") {
            warning = ' <span style="color:#ef4444;font-size:0.75rem;">(⚠️ 核心动作系统词，勿随意删减指令)</span>';
        }
        const div = document.createElement("div"); div.style.marginBottom = "12px";
        div.innerHTML = `<label style="font-size:.78rem;font-weight:700;display:block;margin-bottom:3px;">${label}${warning}<button class="sys-btn sys-btn-ghost reset-prompt-btn" data-key="${key}" title="恢复此项的默认提示词" style="font-size:.7rem;margin-left:8px;padding:1px 8px;">🔄 恢复默认</button></label><textarea data-scene="${key}" style="width:100%;min-height:80px;font-size:.75rem;font-family:Consolas,monospace;padding:6px 8px;border:1px solid var(--border);border-radius:4px;resize:vertical;">${e(prompts[key]||"")}</textarea>`;
        promptTpl.appendChild(div);
    });
    promptModal.classList.remove("hidden");
});
document.getElementById("prompt-cancel").addEventListener("click", () => promptModal.classList.add("hidden"));
promptModal.querySelector(".modal-backdrop").addEventListener("click", () => promptModal.classList.add("hidden"));
document.getElementById("prompt-save").addEventListener("click", async () => {
    const data = {};
    promptTpl.querySelectorAll("textarea").forEach(ta => { data[ta.dataset.scene] = ta.value; });
    await fetch("/api/settings/prompts", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) });
    promptModal.classList.add("hidden");
});
// Reset individual prompt to default
promptTpl.addEventListener("click", async (e) => {
    const btn = e.target.closest(".reset-prompt-btn");
    if (!btn) return;
    const key = btn.dataset.key;
    if (!confirm("确定要将此提示词恢复为出厂默认吗？")) return;
    const defaults = await (await fetch("/api/settings/prompts/defaults")).json();
    const ta = promptTpl.querySelector(`textarea[data-scene="${key}"]`);
    if (ta && defaults[key]) {
        ta.value = defaults[key];
        const data = {};
        promptTpl.querySelectorAll("textarea").forEach(t => { data[t.dataset.scene] = t.value; });
        await fetch("/api/settings/prompts", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) });
        showToast((promptLabels[key] || key) + " 已恢复默认");
    }
});

/* ═══════════ Chain Tab ═══════════ */
const chainSelect = document.getElementById("chain-select");
const chainBubbles = document.getElementById("chain-bubbles");
const chainGenBtn = document.getElementById("chain-generate-btn");
let currentChainNodes = [];
let currentChainRunId = null;

document.getElementById("tab-chain").closest(".tab-content")?.addEventListener("DOMNodeInserted", () => {});

async function loadChainSelect() {
    chainSelect.innerHTML = '<option value="">-- 选择推演方案 --</option>';
    const chains = await (await fetch("/api/reasoning_chains" + nidQ())).json();
    chains.forEach(rc => { chainSelect.innerHTML += `<option value="${rc.id}">${e(rc.title)}</option>`; });
    // Also populate chat scene dropdown with SOP chains
    var sceneSel = document.getElementById("chat-scene-select");
    if (sceneSel) {
        sceneSel.querySelectorAll("option[value^='chain_']").forEach(function(o) { o.remove(); });
        chains.forEach(function(rc) {
            sceneSel.innerHTML += '<option value="chain_' + rc.id + '">🔗 SOP: ' + e(rc.title) + '</option>';
        });
    }
}

chainSelect.addEventListener("change", async () => {
    const id = chainSelect.value;
    if (!id) { chainBubbles.innerHTML = ""; chainGenBtn.classList.add("hidden"); document.getElementById("chain-run-btn").classList.add("hidden"); return; }
    currentChainRunId = id;
    const rc = await (await fetch(`/api/reasoning_chains/${id}?novel_id=${currentNovelId}`)).json();
    renderChainBubbles(rc.nodes || []);
});

function renderChainBubbles(nodes) {
    chainBubbles.innerHTML = "";
    const hasOutput = nodes.filter(n => n.output && n.output.trim());
    nodes.forEach(n => {
        const b = document.createElement("div"); b.className = "chain-bubble"; b.style.borderLeftColor = n.output && n.output.trim() ? "var(--accent)" : "#ccc";
        const out = n.output || "";
        b.innerHTML = `<div class="cb-header">${e(n.id)} · ${e(n.premise||"").substring(0,40)}${out?"":" ⏳"}</div>${out?`<div class="cb-output">${e(out).substring(0,300)}${out.length>300?"...":""}</div>`:""}`;
        chainBubbles.appendChild(b);
    });
    currentChainNodes = hasOutput;
    document.getElementById("chain-run-btn").classList.toggle("hidden", nodes.length === 0);
    chainGenBtn.classList.toggle("hidden", !hasOutput.length);
}

document.getElementById("chain-run-btn").addEventListener("click", async function() {
    if (!currentChainRunId) return;
    var s = getSettings();
    if (!s.apiKey) { document.getElementById("settings-modal").classList.remove("hidden"); return; }
    var btn = document.getElementById("chain-run-btn");
    btn.disabled = true;
    btn.textContent = "运行中...";
    var rc = await (await fetch("/api/reasoning_chains/" + currentChainRunId + "?novel_id=" + currentNovelId)).json();
    var nodes = rc.nodes || [];
    if (nodes.length === 0) { btn.disabled = false; btn.textContent = "\u25B6 运行推演链"; return; }
    // Clear all outputs before run
    nodes.forEach(function(n) { n.output = ""; });
    // State machine: start from first node
    var currentNodeId = nodes[0].id;
    var loopCount = 0;
    var chapterContent = contentInput.value || "";
    var sel = contentInput.value.substring(contentInput.selectionStart, contentInput.selectionEnd);
    while (currentNodeId) {
        loopCount++;
        if (loopCount > 20) { console.warn("推理链循环熔断"); break; }
        var node = nodes.find(function(n) { return n.id === currentNodeId; });
        if (!node) break;
        // Collect upstream output
        var prevOutput = "";
        nodes.forEach(function(n) {
            if ((n.next_node_id === node.id && n.output) || (n.branches || []).some(function(br) { return br.next_node_id === node.id && n.output; })) {
                prevOutput += (prevOutput ? "\n" : "") + n.output;
            }
        });
        try {
            var rawPremise2 = node.premise || "";
            if (prevOutput) rawPremise2 = prevOutput + "\n\n" + rawPremise2;
            var premise = rawPremise2.replace(/\{content\}/g, chapterContent).replace(/\{selection\}/g, sel);
            var promptT = node.prompt || "";
            var targetT = compileTarget(node.target);
            var res = await fetch("/api/reasoning/execute", { method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({ novel_id: currentNovelId, premise, prompt: promptT, target: targetT, previous_output: "", api_key: s.apiKey, base_url: s.baseUrl, model: s.model, context_settings: rc.context_settings || {}, write_to: node.write_to || null, chain_id: chainId, response_format: /deepseek/i.test(s.model) ? {type: "json_object"} : undefined }) });
            var txt = "";
            var gen = parseSSEStream(res);
            var chunkResult;
            while (true) {
                chunkResult = await gen.next();
                if (chunkResult.done) break;
                var msg = chunkResult.value;
                if (msg.type === "chunk") txt += msg.content;
                else if (msg.type === "tool_proposal") {
                    await fetch("/api/agent/execute/" + currentNovelId, {
                        method: "POST", headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ tool_name: msg.tool_name, arguments: msg.arguments, write_to: node.write_to || null, chain_id: currentChainId || null }),
                    });
                    txt += "\n[🛠 工具已自动执行: " + msg.tool_name + "]";
                }
            }
            // Extract ROUTE key and strip tags
            var routeKey = null;
            var rm = txt.match(/<ROUTE>([\s\S]*?)<\/ROUTE>/);
            if (rm) routeKey = rm[1].trim();
            var cleanOutput = txt.replace(/<\/?ROUTE>/g, "").trim();
            node.output = cleanOutput;
            // Determine next node via branch routing
            var nextId = null;
            if (node.branches && node.branches.length > 0) {
                for (var bi = 0; bi < node.branches.length; bi++) {
                    var br = node.branches[bi];
                    var compareText = (routeKey !== null ? routeKey : cleanOutput).replace(/<\/?ROUTE>/g, "");
                    if (br.condition && compareText.includes(br.condition)) {
                        nextId = br.next_node_id;
                        break;
                    }
                }
            }
            if (!nextId) nextId = node.next_node_id || null;
            currentNodeId = nextId;
        } catch (e) { node.output = "[错误: " + e.message + "]"; currentNodeId = null; }
    }
    // Persist all nodes and refresh bubbles once
    await fetch("/api/reasoning_chains/" + currentChainRunId, { method: "PUT", headers: {"Content-Type": "application/json"}, body: JSON.stringify({ title: rc.title, nodes: nodes, context_settings: rc.context_settings || {} }) });
    var latest = await (await fetch("/api/reasoning_chains/" + currentChainRunId + "?novel_id=" + currentNovelId)).json();
    renderChainBubbles(latest.nodes || []);
    // 执行完毕自动清空任务清单
    try { await fetch("/api/reasoning_chains/" + currentChainRunId + "/todos/clear", { method: "POST" }); } catch(e) {}
    btn.disabled = false;
    btn.textContent = "\u25B6 运行推演链";
});

chainGenBtn.addEventListener("click", async () => {
    const chainData = currentChainNodes.map(n => `[${n.id}] ${n.premise}\n→ ${n.output}`).join("\n\n");
    // switch to copilot tab
    document.querySelectorAll(".tab-btn").forEach(b => b.classList.remove("active"));
    const aiTab = document.querySelector('.tab-btn[data-tab="tab-ai"]');
    aiTab.classList.add("active");
    document.querySelectorAll("#right-sidebar .tab-content").forEach(x => x.classList.add("hidden"));
    document.getElementById("tab-ai").classList.remove("hidden");
    // send as special scene
    const s = getSettings();
    if (!s.apiKey) { document.getElementById("settings-modal").classList.remove("hidden"); return; }
    const title = chainSelect.options[chainSelect.selectedIndex].textContent;
    const userB = document.createElement("div"); userB.className = "copilot-bubble user";
    userB.innerHTML = `<div class="cp-label">你</div>依据推演链【${e(title)}】生成正文`;
    document.getElementById("copilot-history").appendChild(userB);
    const aiB = document.createElement("div"); aiB.className = "copilot-bubble ai streaming";
    aiB.innerHTML = '<div class="cp-label">AI</div>';
    document.getElementById("copilot-history").appendChild(aiB);
    chainGenBtn.disabled = true; chainGenBtn.textContent = "生成中...";
    try {
        const res = await fetch("/api/writing/copilot", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ novel_id: currentNovelId, current_chapter_content: getActiveContent(), instruction: "根据推演链结果生成正文", api_key: s.apiKey, base_url: s.baseUrl, model: s.model, scene: "chain_write", chain_data: chainData }) });
        for await (const msg of parseSSEStream(res)) {
            if (msg.type === "chunk") { aiB.appendChild(document.createTextNode(msg.content)); aiB.scrollIntoView(false); }
            else if (msg.type === "error") { aiB.appendChild(document.createTextNode("\n[错误: " + msg.message + "]")); }
            else if (msg.type === "tool_proposal") { renderToolProposal(aiB, msg, false); }
            else if (msg.type === "done") {
                const txt = aiB.textContent.replace(/^AI/, "").trim();
                const row = document.createElement("div"); row.className = "cp-action-row";
                const insBtn = document.createElement("button"); insBtn.className = "cp-insert-btn"; insBtn.textContent = "\u2B05 插入到当前光标处";
                insBtn.addEventListener("click", () => insertToActiveTextarea(txt));
                const repBtn = document.createElement("button"); repBtn.className = "cp-insert-btn"; repBtn.textContent = "\uD83D\uDD04 替换当前文本";
                repBtn.addEventListener("click", () => { if (confirm("替换当前编辑器全部内容？")) replaceActiveTextarea(txt); });
                row.appendChild(insBtn);
                row.appendChild(repBtn);
                aiB.appendChild(row);
            }
        }
    } catch (e) { aiB.appendChild(document.createTextNode("\n[失败: " + e.message + "]")); }
    finally { aiB.classList.remove("streaming"); chainGenBtn.disabled = false; chainGenBtn.textContent = "\u270D 根据此推演链生成正文"; }
});

/* ═══════════ AI Settings ═══════════ */
function getSettings() { return { apiKey: localStorage.getItem("ai_api_key") || "", baseUrl: localStorage.getItem("ai_base_url") || "", model: localStorage.getItem("ai_model") || "gpt-3.5-turbo", streamOutput: localStorage.getItem("ai_stream") !== "false" }; }
function hasApiKey() { return getSettings().apiKey.trim().length > 0; }
function requireApiKey() { if (!hasApiKey()) { document.getElementById("settings-modal").classList.remove("hidden"); return false; } return true; }
continueBtn.addEventListener("click", async () => { if (!requireApiKey()) return; const s = getSettings(); const content = contentInput.value; continueBtn.disabled = true; continueBtn.textContent = "生成中...";
    try { const res = await fetch("/api/writing/copilot", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ novel_id: currentNovelId, current_chapter_content: content, selected_text: "", instruction: "请根据前文顺着语气直接续写约200字正文，不要输出任何解释，不要换行分段，紧接上文。", api_key: s.apiKey, base_url: s.baseUrl, model: s.model }) });
        for await (const msg of parseSSEStream(res)) { if (msg.type === "chunk") { contentInput.value += msg.content; contentInput.scrollTop = contentInput.scrollHeight; } }
        contentInput.dispatchEvent(new Event("input")); await saveCurrentChapter(); }
    catch (e) { contentInput.value += `\n\n[出错: ${e.message}]`; } finally { continueBtn.disabled = false; continueBtn.textContent = "\u2728 续写"; } });

/* ═══════════ Reasoning 2D Graph ═══════════ */
let currentChainId = null;
let currentChain = null;
let saveTimer = null;
let dragNode = null;
let dragStartMouseX = 0;
let dragStartMouseY = 0;
let dragStartNodeX = 0;
let dragStartNodeY = 0;
let connectingFrom = null;
window.canvasScale = 1;
window.canvasPanX = 0;
window.canvasPanY = 0;
var isCanvasPanning = false;
var canvasPanStartX = 0;
var canvasPanStartY = 0;

async function loadReasoningModule() { await loadChainList(); }

async function loadChainList() {
    const list = document.getElementById("chain-list"); list.innerHTML = "";
    const chains = await (await fetch("/api/reasoning_chains" + nidQ())).json();
    chains.forEach(rc => {
        const li = document.createElement("li"); li.className = "chapter-li"; li.dataset.cid = rc.id;
        li.innerHTML = `<span style="flex:1">${e(rc.title)||"(未)"}</span>`;
        li.addEventListener("click", () => { currentChainId = rc.id; loadGraph(); document.querySelectorAll("#chain-list .chapter-li").forEach(x => x.classList.remove("active")); li.classList.add("active"); });
        li.appendChild(delBtn("/api/reasoning_chains", rc.id, () => { if (currentChainId === rc.id) { currentChainId = null; clearGraph(); } loadReasoningModule(); }));
        list.appendChild(li);
    });
}

function clearGraph() {
    document.getElementById("chain-canvas").querySelectorAll(".node-card").forEach(n => n.remove());
    document.getElementById("canvas-lines").innerHTML = "";
}

async function loadGraph() {
    if (!currentChainId) { clearGraph(); return; }
    const res = await fetch(`/api/reasoning_chains/${currentChainId}?novel_id=${currentNovelId}`);
    if (!res.ok) return;
    currentChain = await res.json();
    var systemChains = ["🗺️ 蓝图规划与逐层搭建流","👤 严谨人设与模板质检流","📖 全维世界观与大纲流水线"];
    var isSys = systemChains.indexOf(currentChain.title) >= 0;
    var rbtn = document.getElementById("chain-restore-btn");
    if (rbtn) rbtn.classList.toggle("hidden", !isSys);
    var ti = document.getElementById("cs-title");
    if (ti) { ti.disabled = isSys; if (isSys) ti.setAttribute("title","系统预设链不可重命名"); else ti.removeAttribute("title"); }
    renderAllNodes();
    renderLines();
    renderTodos();
}

function renderAllNodes() {
    const canvas = document.getElementById("chain-canvas");
    canvas.querySelectorAll(".node-card").forEach(n => n.remove());
    var nodes = currentChain.nodes || [];
    var sysTitles = ["🗺️ 蓝图规划与逐层搭建流","👤 严谨人设与模板质检流","📖 全维世界观与大纲流水线"];
    var isSys = sysTitles.indexOf((currentChain && currentChain.title) || "") >= 0;
    for (var idx = 0; idx < nodes.length; idx++) {
        var node = nodes[idx];
        try {
        if (!node.id) node.id = "n" + (idx + 1);
        if (!node.branches) node.branches = [];
        var ntype = node.type || "reasoning";
        var cardCls = "node-card" + (ntype === "router" ? " node-type-router" : "") + (ntype === "ask" ? " node-type-ask" : "") + (ntype === "write" ? " node-type-write" : "");
        const card = document.createElement("div"); card.className = cardCls; card.dataset.nid = node.id;
        card.style.left = (node.x || 50 + idx * 60) + "px";
        card.style.top = (node.y || 50 + idx * 80) + "px";
        const bcount = (node.branches || []).length;
        let outPorts = "";
        if (bcount === 0) {
            outPorts = `<div class="port port-out" data-nid="${node.id}" data-port="0" title="拖线到其他节点"><span>&#9654;</span></div>`;
        } else {
            for (let pi = 0; pi < bcount; pi++) {
                outPorts += `<div class="port port-out" data-nid="${node.id}" data-port="${pi}" style="top:${20+pi*16}px" title="分支${pi+1}"><span>&#9654;</span></div>`;
            }
            outPorts += `<div class="port port-out port-add" data-nid="${node.id}" data-port="${bcount}" style="top:${20+bcount*16}px;background:#888" title="新增连接">+</div>`;
        }
        var ntype = node.type || "reasoning";
        var isRouter = ntype === "router";
        var isAsk = ntype === "ask";
        var isWrite = ntype === "write";
        var isValidate = ntype === "validate";
        var isVerify = ntype === "verify";
        var isPickTodo = ntype === "pick_todo";
        var runLabel = isRouter ? "🔀 判定" : (isAsk ? "❓ 提问" : (isWrite ? "📝 写入" : (isValidate ? "🔍 校验" : (isVerify ? "✅ 校验确认" : (isPickTodo ? "🎯 取待办" : "▶ 运行")))));
        var promptHTML = (isRouter || isWrite || isValidate || isVerify || isPickTodo) ? "" : `<div class="node-section"><label>推理提示词</label><textarea class="prompt-ta">${e(node.prompt||"")}</textarea>${buildFormatSelector(node)}</div>`;
        var targetHTML = (isRouter || isAsk || isWrite || isValidate || isVerify || isPickTodo) ? "" : `<div class="node-section"><label>结果指定</label><div class="target-builder" data-node-id="${node.id}"><div class="builder-controls"><button type="button" class="sys-btn sys-btn-ghost" style="font-size:.65rem;padding:2px 6px;" onclick="addTargetBlock('${node.id}','placeholder')">+ 占位符</button><button type="button" class="sys-btn sys-btn-ghost" style="font-size:.65rem;padding:2px 6px;" onclick="addTargetBlock('${node.id}','text')">+ 文本/要求</button><button type="button" class="sys-btn sys-btn-ghost" style="font-size:.65rem;padding:2px 6px;" onclick="addTargetBlock('${node.id}','bool')">+ 条件判断</button><button type="button" class="sys-btn sys-btn-ghost" style="font-size:.65rem;padding:2px 6px;" onclick="addTargetBlock('${node.id}','enum')">+ 选项分支</button></div><div class="builder-list" data-node-id="${node.id}"></div></div></div>`;
        var writeHTML = (isRouter || isAsk || isValidate || isVerify || isPickTodo) ? "" : `<div class="node-section"><label>🎯 写入目标</label><div class="write-to-row" data-node-id="${node.id}">${buildWriteToHTML(node)}</div></div>`;
        var verifyFilterHTML = isVerify ? `<div class="node-section"><label>🔎 待办过滤关键词${isSys?' <span style="color:var(--text-muted);font-size:.65rem;">(系统预设，只读)</span>':''}</label><input class="verify-filter-inp" value="${e(node.verify_filter||'')}" ${isSys?'readonly':''} style="width:100%;font-size:.7rem;padding:4px 6px;border:1px solid var(--border);border-radius:4px;background:var(--bg);" placeholder="用 | 分隔多关键词，留空=检查全部待办"></div>` : "";
        var pickFilterHTML = isPickTodo ? `<div class="node-section"><label>🎯 待办过滤关键词${isSys?' <span style="color:var(--text-muted);font-size:.65rem;">(系统预设，只读)</span>':''}</label><input class="pick-filter-inp" value="${e(node.pick_filter||'')}" ${isSys?'readonly':''} style="width:100%;font-size:.7rem;padding:4px 6px;border:1px solid var(--border);border-radius:4px;background:var(--bg);" placeholder="用 | 分隔多关键词"></div>` : "";
        var branchHTML = (isAsk || isWrite) ? "" : `<div class="node-section"><label>分支路由${isValidate ? "（VALID→写入 / INVALID→打回）" : ""}${isVerify ? "（CONTINUE→继续 / NEXT→总检）" : ""}${isPickTodo ? "（HAS_TODO→创建 / NO_TODO→跳过）" : ""}</label><div class="branch-list"></div><button class="branch-add-btn">+ 新增分支</button></div>`;
        var typeOpts = '<option value="reasoning" ' + (ntype==="reasoning"?"selected":"") + '>🧠 推理</option>'
            + '<option value="validate" ' + (ntype==="validate"?"selected":"") + '>🔍 校验</option>'
            + '<option value="verify" ' + (ntype==="verify"?"selected":"") + '>✅ 校验确认</option>'
            + '<option value="pick_todo" ' + (ntype==="pick_todo"?"selected":"") + '>🎯 取待办</option>'
            + '<option value="write" ' + (ntype==="write"?"selected":"") + '>📝 写入</option>'
            + '<option value="router" ' + (ntype==="router"?"selected":"") + '>🔀 路由</option>'
            + '<option value="ask" ' + (ntype==="ask"?"selected":"") + '>❓ 交互</option>';

        card.innerHTML = `<div class="node-hdr"><span>${e(node.id)}</span><span style="display:flex;gap:4px;align-items:center;"><select class="node-type-sel" style="font-size:.65rem;padding:1px 3px;border:1px solid var(--border);border-radius:3px;background:var(--bg);color:var(--text);">${typeOpts}</select><button class="node-del">&#128465;</button></span></div>
            <div class="node-body">
                <div class="node-section"><label>${isRouter ? "判定依据" : (isWrite || isValidate || isPickTodo ? "注释" : "传入内容")}</label><textarea class="premise-ta">${e(node.premise||"")}</textarea></div>
                ${promptHTML}
                ${targetHTML}
                ${writeHTML}
                ${verifyFilterHTML}
                ${pickFilterHTML}
                ${branchHTML}
                <div class="node-section"><label>顺序路由（默认下一节点）</label><select class="next-node-sel"><option value="">-- 无 --</option></select></div>
                <button class="node-run-btn">${runLabel}</button>
                <div class="node-output" style="display:none;margin-top:6px;padding:8px;border-top:1px solid var(--border);font-size:.78rem;line-height:1.6;max-height:300px;overflow-y:auto;white-space:pre-wrap;word-break:break-word;">${e(node.output||"")}</div>
            </div>
            <div class="port port-in" data-nid="${node.id}" title="接收连接"><span>&#9664;</span></div>
            ${outPorts}`;
        canvas.appendChild(card);
        bindNodeEvents(card, node, idx);
        if (!isAsk && !isRouter && !isWrite && !isPickTodo) initTargetBuilder(card, node);
        if (!isAsk && !isWrite) renderBranches(card, node);
        } catch(e) {
            var errCard = document.createElement("div");
            errCard.className = "node-card node-type-write";
            errCard.style.cssText = "position:absolute;left:" + (node.x||30) + "px;top:" + (node.y||0) + "px;width:320px;";
            errCard.innerHTML = '<div class="node-hdr" style="background:#7f1d1d;color:#fca5a5;">⚠ ' + (node.id||'?') + '</div><div class="node-body"><pre style="font-size:.65rem;color:#fca5a5;">' + e.message + '</pre></div>';
            canvas.appendChild(errCard);
        }
    }
    bindPortEvents();
}

function bindNodeEvents(card, node, idx) {
    const hdr = card.querySelector(".node-hdr");
    hdr.addEventListener("mousedown", e => { if (e.target.closest("button") || e.target.closest("select")) return; startDrag(e, card, node, idx); });
    card.querySelector(".node-del").addEventListener("click", () => { currentChain.nodes.splice(idx, 1); renderAllNodes(); renderLines(); autoSave(); });
    var typeSel = card.querySelector(".node-type-sel");
    if (typeSel) { typeSel.addEventListener("change", function() { node.type = typeSel.value; renderAllNodes(); renderLines(); autoSave(); }); }
    var premiseTa = card.querySelector(".premise-ta");
    if (premiseTa) premiseTa.addEventListener("input", e => { node.premise = e.target.value; autoSave(); });
    var promptTa = card.querySelector(".prompt-ta");
    if (promptTa) promptTa.addEventListener("input", e => { node.prompt = e.target.value; autoSave(); });
    var branchAddBtn = card.querySelector(".branch-add-btn");
    if (branchAddBtn) branchAddBtn.addEventListener("click", () => { node.branches.push({ condition: "", next_node_id: "" }); renderBranches(card, node); autoSave(); });
    var formatSel = card.querySelector(".format-sel");
    if (formatSel) { formatSel.addEventListener("change", function() { node.format = formatSel.value || ""; autoSave(); }); }
    var verifyFilterInp = card.querySelector(".verify-filter-inp");
    if (verifyFilterInp) verifyFilterInp.addEventListener("input", function(e) { node.verify_filter = e.target.value; autoSave(); });
    var pickFilterInp = card.querySelector(".pick-filter-inp");
    if (pickFilterInp) pickFilterInp.addEventListener("input", function(e) { node.pick_filter = e.target.value; autoSave(); });
    card.querySelector(".node-run-btn").addEventListener("click", () => runNode(card, node));
    // sequential route
    const nextSel = card.querySelector(".next-node-sel");
    if (nextSel) {
    nextSel.innerHTML = '<option value="">-- 无 --</option>';
    (currentChain.nodes || []).forEach(n => { if (n.id !== node.id) nextSel.innerHTML += `<option value="${e(n.id)}" ${node.next_node_id===n.id?"selected":""}>${e(n.id)}</option>`; });
    nextSel.addEventListener("change", () => { node.next_node_id = nextSel.value; renderLines(); autoSave(); });
    }
    // write target events
    bindWriteToEvents(card, node);
}

function bindPortEvents() {
    document.querySelectorAll(".port-out").forEach(port => {
        port.addEventListener("mousedown", e => {
            e.stopPropagation(); e.preventDefault();
            connectingFrom = port.dataset.nid;
            port.classList.add("connecting");
            const srcCard = document.querySelector(`.node-card[data-nid="${connectingFrom}"]`);
            if (srcCard) {
                const sx = srcCard.offsetLeft + srcCard.offsetWidth;
                const sy = parseInt(port.style.top) || 20;
                const pl = document.getElementById("preview-line");
                pl.style.opacity = "1";
                pl.setAttribute("d", `M${sx},${srcCard.offsetTop+sy+7} L${e.clientX},${e.clientY}`);
            }
            document.addEventListener("mousemove", onPortMove);
        });
    });
    document.querySelectorAll(".port-in").forEach(port => {
        port.addEventListener("mouseup", e => {
            e.stopPropagation(); e.preventDefault();
            const pl = document.getElementById("preview-line"); pl.style.opacity = "0";
            if (!connectingFrom || connectingFrom === port.dataset.nid) { cancelConnect(); return; }
            const src = currentChain.nodes.find(n => n.id === connectingFrom);
            const tgt = currentChain.nodes.find(n => n.id === port.dataset.nid);
            if (src && tgt) {
                const cond = prompt("分支条件（如：是/否/成功/失败）：", "是");
                if (cond !== null) {
                    if (!src.branches) src.branches = [];
                    src.branches.push({ condition: cond, next_node_id: tgt.id });
                    renderAllNodes(); renderLines(); autoSave();
                }
            }
            cancelConnect();
        });
    });
    document.addEventListener("mouseup", () => { if (connectingFrom) { document.getElementById("preview-line").style.opacity = "0"; cancelConnect(); } });
}

function cancelConnect() {
    document.querySelectorAll(".port-out.connecting").forEach(p => p.classList.remove("connecting"));
    connectingFrom = null;
    document.removeEventListener("mousemove", onPortMove);
}

function onPortMove(e) {
    if (!connectingFrom) return;
    var srcCard = document.querySelector('.node-card[data-nid="' + connectingFrom + '"]');
    if (!srcCard) return;
    var port = document.querySelector(".port-out.connecting");
    var sy = port ? (parseInt(port.style.top) || 20) : 20;
    var sx = srcCard.offsetLeft + srcCard.offsetWidth;
    var canvas = document.getElementById("reasoning-canvas"); // viewport for bounding rect
    var rect = canvas.getBoundingClientRect();
    var tx = (e.clientX - rect.left) / window.canvasScale;
    var ty = (e.clientY - rect.top) / window.canvasScale;
    var pl = document.getElementById("preview-line");
    pl.setAttribute("d", "M" + sx + "," + (srcCard.offsetTop + sy + 7) + " L" + tx + "," + ty);
}

function renderBranches(card, node) {
    const list = card.querySelector(".branch-list");
    if (!list) return;
    list.innerHTML = "";
    (node.branches || []).forEach((br, bi) => {
        const row = document.createElement("div"); row.className = "branch-row";
        const inp = document.createElement("input"); inp.type = "text"; inp.placeholder = "条件"; inp.value = br.condition || "";
        inp.addEventListener("input", () => { br.condition = inp.value; autoSave(); });
        const sel = document.createElement("select");
        sel.innerHTML = '<option value="">-- 目标 --</option>';
        (currentChain.nodes || []).forEach(n => { if (n.id !== node.id) sel.innerHTML += `<option value="${e(n.id)}" ${br.next_node_id===n.id?"selected":""}>${e(n.id)}</option>`; });
        sel.addEventListener("change", () => { br.next_node_id = sel.value; renderLines(); autoSave(); });
        const del = document.createElement("button"); del.textContent = "✕";
        del.addEventListener("click", () => { node.branches.splice(bi, 1); renderBranches(card, node); renderLines(); autoSave(); });
        row.appendChild(inp); row.appendChild(sel); row.appendChild(del); list.appendChild(row);
    });
}

function startDrag(e, card, node, idx) {
    if (e.target.closest(".port")) return;
    if (e.button === 2) return; // right-click reserved for pan
    e.preventDefault();
    dragNode = { card, node, idx };
    dragStartMouseX = e.clientX;
    dragStartMouseY = e.clientY;
    dragStartNodeX = node.x || 0;
    dragStartNodeY = node.y || 0;
    card.classList.add("dragging");
    document.addEventListener("mousemove", onDrag);
    document.addEventListener("mouseup", onDragEnd);
}
function onDrag(e) {
    if (!dragNode) return;
    var dx = (e.clientX - dragStartMouseX) / window.canvasScale;
    var dy = (e.clientY - dragStartMouseY) / window.canvasScale;
    dragNode.card.style.left = (dragStartNodeX + dx) + "px";
    dragNode.card.style.top = (dragStartNodeY + dy) + "px";
    dragNode.node.x = dragStartNodeX + dx;
    dragNode.node.y = dragStartNodeY + dy;
    renderLines();
}
function onDragEnd() {
    if (!dragNode) return;
    dragNode.card.classList.remove("dragging");
    document.removeEventListener("mousemove", onDrag);
    document.removeEventListener("mouseup", onDragEnd);
    autoSave();
    dragNode = null;
}

function renderTodos() {
    var panel = document.getElementById("chain-todos-panel");
    var list = document.getElementById("chain-todos-list");
    if (!panel || !list) return;
    var todos = (currentChain && currentChain.todos) ? currentChain.todos : [];
    if (!todos.length) { panel.style.display = "none"; return; }
    panel.style.display = "block";
    var html = "";
    todos.forEach(function(t) {
        var icon = {"pending":"⏳","in_progress":"🔄","done":"✅"}[t.status] || "❓";
        var color = {"pending":"#f59e0b","in_progress":"#3b82f6","done":"#22c55e"}[t.status] || "#888";
        html += '<div style="display:flex;align-items:flex-start;gap:6px;padding:3px 0;border-bottom:1px solid var(--border);">';
        html += '<span style="color:' + color + ';flex-shrink:0;">' + icon + '</span>';
        html += '<span style="flex:1;word-break:break-all;">' + e(t.content||"") + '</span>';
        html += '<span style="font-size:.6rem;color:var(--text-muted);flex-shrink:0;">' + e(t.id||"") + '</span>';
        html += '</div>';
        if (t.notes && t.notes.length) {
            t.notes.forEach(function(n) {
                html += '<div style="margin-left:22px;font-size:.65rem;color:var(--text-muted);padding:1px 0;">📝 ' + e(n.text||"") + '</div>';
            });
        }
    });
    list.innerHTML = html;
}

function renderLines() {
    const svg = document.getElementById("canvas-lines"); svg.innerHTML = "";
    // preview line placeholder
    const pl = document.createElementNS("http://www.w3.org/2000/svg", "path");
    pl.id = "preview-line"; pl.setAttribute("stroke", "#c7512e"); pl.setAttribute("stroke-width", "2");
    pl.setAttribute("stroke-dasharray", "5,5"); pl.setAttribute("fill", "none"); pl.style.opacity = "0";
    svg.appendChild(pl);
    // defs for arrow
    const defs = document.createElementNS("http://www.w3.org/2000/svg", "defs");
    const marker = document.createElementNS("http://www.w3.org/2000/svg", "marker");
    marker.id = "arrowhead"; marker.setAttribute("markerWidth", "8"); marker.setAttribute("markerHeight", "6");
    marker.setAttribute("refX", "8"); marker.setAttribute("refY", "3"); marker.setAttribute("orient", "auto");
    const poly = document.createElementNS("http://www.w3.org/2000/svg", "polygon");
    poly.setAttribute("points", "0 0, 8 3, 0 6"); poly.setAttribute("fill", "#c7512e");
    marker.appendChild(poly); defs.appendChild(marker);
    const marker2 = document.createElementNS("http://www.w3.org/2000/svg", "marker");
    marker2.id = "arrowhead-seq"; marker2.setAttribute("markerWidth", "8"); marker2.setAttribute("markerHeight", "6");
    marker2.setAttribute("refX", "8"); marker2.setAttribute("refY", "3"); marker2.setAttribute("orient", "auto");
    const poly2 = document.createElementNS("http://www.w3.org/2000/svg", "polygon");
    poly2.setAttribute("points", "0 0, 8 3, 0 6"); poly2.setAttribute("fill", "#888");
    marker2.appendChild(poly2); defs.appendChild(marker2);
    svg.appendChild(defs);

    const nodes = currentChain?.nodes || [];
    // sequential lines (dashed)
    nodes.forEach(node => {
        if (!node.next_node_id) return;
        const target = nodes.find(n => n.id === node.next_node_id);
        if (!target) return;
        const srcCard = document.querySelector(`.node-card[data-nid="${node.id}"]`);
        const tgtCard = document.querySelector(`.node-card[data-nid="${target.id}"]`);
        if (!srcCard || !tgtCard) return;
        const x1 = srcCard.offsetLeft + srcCard.offsetWidth;
        const y1 = srcCard.offsetTop + 80;
        const x2 = tgtCard.offsetLeft;
        const y2 = tgtCard.offsetTop + 80;
        const dx = Math.abs(x2 - x1) * 0.3;
        const d = `M${x1},${y1} C${x1+dx},${y1} ${x2-dx},${y2} ${x2},${y2}`;
        const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
        path.setAttribute("d", d); path.setAttribute("stroke", "#888");
        path.setAttribute("stroke-width", "1.5"); path.setAttribute("stroke-dasharray", "6,4");
        path.setAttribute("fill", "none"); path.setAttribute("marker-end", "url(#arrowhead-seq)");
        svg.appendChild(path);
    });
    // branch lines (solid)
    nodes.forEach(node => {
        (node.branches || []).forEach((br, bi) => {
            if (!br.next_node_id) return;
            const target = nodes.find(n => n.id === br.next_node_id);
            if (!target) return;
            const srcCard = document.querySelector(`.node-card[data-nid="${node.id}"]`);
            const tgtCard = document.querySelector(`.node-card[data-nid="${target.id}"]`);
            if (!srcCard || !tgtCard) return;
            const srcY = srcCard.offsetTop + 27 + bi * 16;
            const tgtY = tgtCard.offsetTop + 27;
            const x1 = srcCard.offsetLeft + srcCard.offsetWidth;
            const x2 = tgtCard.offsetLeft;
            const dx = Math.abs(x2 - x1) * 0.3;
            const d = `M${x1},${srcY} C${x1+dx},${srcY} ${x2-dx},${tgtY} ${x2},${tgtY}`;
            const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
            path.setAttribute("d", d); path.setAttribute("stroke", "#c7512e");
            path.setAttribute("stroke-width", "1.5"); path.setAttribute("fill", "none");
            path.setAttribute("marker-end", "url(#arrowhead)");
            svg.appendChild(path);
            const label = document.createElementNS("http://www.w3.org/2000/svg", "text");
            const midX = (x1 + x2) / 2; const midY = (srcY + tgtY) / 2 - 6;
            label.setAttribute("x", midX); label.setAttribute("y", midY);
            label.setAttribute("text-anchor", "middle"); label.setAttribute("font-size", "10");
            label.setAttribute("fill", "#888"); label.textContent = br.condition || "";
            svg.appendChild(label);
        });
    });
}

/* ═══════════ Target Builder ═══════════ */

const PLACEHOLDER_OPTS = ["{content}","{selection}","{lore}","{outlines_list}","{outline_cat_world}","{outline_cat_faction}","{outline_cat_geo}","{outline_cat_power}","{outline_cat_story}","{characters_list}","{characters_inactive}","{factions_list}","{relations_graph}","{locations_list}","{locations_full_detail}","{character_template}","{timeline}","{timeline_main}","{timeline_history}","{timeline_world}","{current_time}","{novel_name}","{chapter_title}","{character:}","{outline:}","{location:}","{chain_data}"];

function getNodeById(nid) { return (currentChain && currentChain.nodes) ? currentChain.nodes.find(function(n) { return n.id === nid; }) : null; }

function initTargetBuilder(card, node) {
    var list = card.querySelector(".builder-list");
    if (!list) return;
    var raw = node.target;
    var blocks = [];
    if (typeof raw === "string" && raw.trim()) {
        blocks = [{ type: "text", value: raw }];
    } else if (Array.isArray(raw)) {
        blocks = raw;
    }
    node.target = blocks;
    list.innerHTML = "";
    blocks.forEach(function(b, i) { renderTargetBlock(list, b, i, node.id); });
}

function addTargetBlock(nodeId, type, data) {
    var node = getNodeById(nodeId);
    if (!node) return;
    if (!Array.isArray(node.target)) node.target = [];
    var block = data || {};
    block.type = type;
    if (type === "placeholder" && !block.value) block.value = "{content}";
    if (type === "text" && block.value === undefined) block.value = "";
    if (type === "bool" && !block.condition) { block.condition = ""; block.trueVal = ""; block.falseVal = ""; }
    if (type === "enum" && !block.options) { block.options = ""; block.format = ""; }
    node.target.push(block);
    var list = document.querySelector('.builder-list[data-node-id="' + nodeId + '"]');
    if (list) { renderTargetBlock(list, block, node.target.length - 1, nodeId); list.scrollTop = list.scrollHeight; }
    autoSave();
}

function renderTargetBlock(listEl, block, index, nodeId) {
    var el = document.createElement("div");
    el.className = "target-block";
    el.draggable = true;
    el.dataset.index = index;
    el.dataset.nodeId = nodeId;

    var handle = '<span class="drag-handle" title="拖拽排序">&#9776;</span>';
    var content = "";
    var node = getNodeById(nodeId);
    if (block.type === "placeholder") {
        var opts = PLACEHOLDER_OPTS.map(function(o) { return '<option value="' + e(o) + '"' + (block.value === o ? " selected" : "") + '>' + e(o) + '</option>'; }).join("");
        content = '<select onchange="updateBlockField(\'' + nodeId + '\',' + index + ',\'value\',this.value)">' + opts + '</select>';
    } else if (block.type === "text") {
        content = '<textarea placeholder="输入模型输出要求" oninput="updateBlockField(\'' + nodeId + '\',' + index + ',\'value\',this.value)">' + e(block.value||"") + '</textarea>';
    } else if (block.type === "bool") {
        content = '<div class="bool-row"><label>条件</label><input value="' + e(block.condition||"") + '" placeholder="判断条件" oninput="updateBlockField(\'' + nodeId + '\',' + index + ',\'condition\',this.value)"></div>';
        content += '<div class="bool-row"><label>真→</label><input value="' + e(block.trueVal||"") + '" placeholder="满足时输出" oninput="updateBlockField(\'' + nodeId + '\',' + index + ',\'trueVal\',this.value)"></div>';
        content += '<div class="bool-row"><label>假→</label><input value="' + e(block.falseVal||"") + '" placeholder="不满足时输出" oninput="updateBlockField(\'' + nodeId + '\',' + index + ',\'falseVal\',this.value)"></div>';
    } else if (block.type === "enum") {
        content = '<input value="' + e(block.options||"") + '" placeholder="选项（逗号分隔）" oninput="updateBlockField(\'' + nodeId + '\',' + index + ',\'options\',this.value)">';
        content += '<input value="' + e(block.format||"") + '" placeholder="结果要求" oninput="updateBlockField(\'' + nodeId + '\',' + index + ',\'format\',this.value)">';
    }

    el.innerHTML = handle + '<div class="block-content">' + content + '</div><button class="block-delete" onclick="removeTargetBlock(\'' + nodeId + '\',' + index + ')">&#10005;</button>';

    el.addEventListener("dragstart", function(e) { e.dataTransfer.setData("text/plain", index + ":" + nodeId); el.classList.add("dragging"); });
    el.addEventListener("dragend", function() { el.classList.remove("dragging"); });
    el.addEventListener("dragover", function(e) { e.preventDefault(); el.classList.add("drag-over"); });
    el.addEventListener("dragleave", function() { el.classList.remove("drag-over"); });
    el.addEventListener("drop", function(e) {
        e.preventDefault(); el.classList.remove("drag-over");
        var data = e.dataTransfer.getData("text/plain").split(":");
        var fromIdx = parseInt(data[0]), fromNodeId = data[1];
        if (fromNodeId !== nodeId) return;
        var arr = (getNodeById(nodeId) || {}).target;
        if (!arr || !arr.length) return;
        var toIdx = parseInt(el.dataset.index);
        var item = arr.splice(fromIdx, 1)[0];
        arr.splice(toIdx, 0, item);
        initTargetBuilder(document.querySelector('.node-card[data-nid="' + nodeId + '"]'), getNodeById(nodeId));
        autoSave();
    });
    listEl.appendChild(el);
}

function updateBlockField(nodeId, index, field, value) {
    var node = getNodeById(nodeId);
    if (!node || !Array.isArray(node.target) || !node.target[index]) return;
    node.target[index][field] = value;
    autoSave();
}

function removeTargetBlock(nodeId, index) {
    var node = getNodeById(nodeId);
    if (!node || !Array.isArray(node.target)) return;
    node.target.splice(index, 1);
    var card = document.querySelector('.node-card[data-nid="' + nodeId + '"]');
    if (card) initTargetBuilder(card, node);
    autoSave();
}

/* ═══════════ 写入目标 (Write Target) ═══════════ */
const WRITE_FIELDS = {
    character: ["name","aliases","description","status","is_active","faction_name","faction_role","is_always_context","attributes"],
    location: ["name","description","scale_level","parent_name","grid_x","grid_y","attributes"],
    outline: ["title","description","category","parent_name","is_always_context","order_index"],
    faction: ["name","description","base_location_name"],
    timeline: ["time_label","content","event_type"],
    chain_todos: ["content","status"],
    character_template: ["attributes"],
};

function buildWriteToHTML(node) {
    var wt = node.write_to || {};
    var entity = wt.entity || "";
    var field = wt.field || "";
    var sub = wt.sub_field || "";
    var action = wt.action || "create";
    var entityOpts = '<option value="">-- 不写入 --</option>'
        + '<option value="character"' + (entity==="character"?" selected":"") + '>👤 人物</option>'
        + '<option value="location"' + (entity==="location"?" selected":"") + '>📍 地点</option>'
        + '<option value="outline"' + (entity==="outline"?" selected":"") + '>📖 大纲</option>'
        + '<option value="faction"' + (entity==="faction"?" selected":"") + '>🏛️ 势力</option>'
        + '<option value="timeline"' + (entity==="timeline"?" selected":"") + '>⏳ 时间线</option>'
        + '<option value="chain_todos"' + (entity==="chain_todos"?" selected":"") + '>📋 待办清单</option>'
        + '<option value="character_template"' + (entity==="character_template"?" selected":"") + '>📐 人物模板</option>';
    var fieldsList = WRITE_FIELDS[entity] || [];
    var fieldLabels = { name:"名称", aliases:"别名", description:"描述", status:"状态", is_active:"活跃", faction_name:"势力名", faction_role:"职位", is_always_context:"常驻上下文", attributes:"属性", scale_level:"层级", parent_name:"父级名称", grid_x:"网格X", grid_y:"网格Y", title:"标题", category:"分类", order_index:"排序", content:"内容", time_label:"时间标签", event_type:"事件类型", base_location_name:"驻地" };
    var fieldOpts = '<option value="">-- 字段 --</option>';
    fieldsList.forEach(function(f) {
        fieldOpts += '<option value="' + f + '"' + (field===f?" selected":"") + '>' + (fieldLabels[f]||f) + '</option>';
    });
    var subInput = "";
    if (entity === "character" && field === "attributes") {
        subInput = '<input class="write-sub" value="' + e(sub) + '" placeholder="如: 能力数据.当前境界" style="width:100px;">';
    } else if (entity === "outline" && field === "category") {
        var cats = ["世界观","世界势力","地理","人物设定","能力体系设定","剧情大纲"];
        subInput = '<select class="write-sub">';
        cats.forEach(function(c) { subInput += '<option value="' + c + '"' + (sub===c?" selected":"") + '>' + c + '</option>'; });
        subInput += '</select>';
    } else {
        subInput = '<input class="write-sub" placeholder="子字段(可选)" value="' + e(sub) + '" style="width:70px;">';
    }
    var actOpts = '<option value="create"' + (action==="create"?" selected":"") + '>新建</option>'
        + '<option value="update"' + (action==="update"?" selected":"") + '>更新</option>';
    return '<select class="write-entity">' + entityOpts + '</select>'
        + '<span style="font-size:.65rem;margin:0 2px;">→</span>'
        + '<select class="write-field">' + fieldOpts + '</select>'
        + subInput
        + '<select class="write-action">' + actOpts + '</select>';
}

function bindWriteToEvents(card, node) {
    var row = card.querySelector(".write-to-row");
    if (!row) return;
    var entitySel = row.querySelector(".write-entity");
    var fieldSel = row.querySelector(".write-field");
    var actionSel = row.querySelector(".write-action");
    if (!entitySel || !fieldSel || !actionSel) return;

    function save() {
        if (!node.write_to) node.write_to = {};
        node.write_to.entity = entitySel.value || "";
        node.write_to.field = fieldSel.value || "";
        var subInput = row.querySelector(".write-sub");
        node.write_to.sub_field = subInput ? subInput.value : "";
        node.write_to.action = actionSel.value || "create";
        if (!node.write_to.entity) { node.write_to = {}; }
        autoSave();
    }

    entitySel.addEventListener("change", function() {
        node.write_to = node.write_to || {};
        node.write_to.entity = entitySel.value;
        node.write_to.field = "";
        node.write_to.sub_field = "";
        row.innerHTML = buildWriteToHTML(node);
        bindWriteToEvents(card, node);
        save();
    });
    fieldSel.addEventListener("change", function() {
        node.write_to = node.write_to || {};
        node.write_to.field = fieldSel.value;
        if (fieldSel.value !== "attributes") node.write_to.sub_field = "";
        row.innerHTML = buildWriteToHTML(node);
        bindWriteToEvents(card, node);
        save();
    });
    actionSel.addEventListener("change", save);
    var subInput = row.querySelector(".write-sub");
    if (subInput) { subInput.addEventListener("input", function() { node.write_to.sub_field = subInput.value; autoSave(); }); }
}

/* ═══════════ 推理节点结构化输出格式 ═══════════ */
const REASONING_FORMATS = {
    "": { label: "通用", desc: "" },
    "location": {
        label: "🗺️ 地点",
        template: "\n\n【结构化输出格式 — 必须严格遵循，字段名必须与下方完全一致】\n" +
            "每条地点一个完整块，格式如下：\n\n" +
            "【name】地点名（必填）\n" +
            "【scale_level】大千世界/宇宙/星球/大陆/国家/城池/街区/建筑\n" +
            "【parent_name】父地点全名（如'东大陆/青云国'，无父级则写'顶级'）\n" +
            "【scale】REGION 或 GRID_25\n" +
            "【grid_x】0-24 整数\n" +
            "【grid_y】0-24 整数\n" +
            "【map_x】0-24 整数（可选）\n" +
            "【map_y】0-24 整数（可选）\n" +
            "【description】详细 Markdown 描述\n" +
            "【attributes】JSON 对象，如 {\"气候\":\"极寒\",\"特产\":\"寒铁\"}\n" +
            "<ROUTE>DONE</ROUTE>",
        jsonExample: '\n\n【JSON Output — 必须严格输出JSON】\n{"name":"地点名","scale_level":"国家","parent_name":"父地点名","scale":"REGION","grid_x":5,"grid_y":3,"map_x":5,"map_y":3,"description":"简短描述","attributes":{"气候":"温暖"}}\n禁止使用任何其他格式（包括【】标记），必须输出合法JSON。',
        parser: function(text) {
            var locs = [];
            // 优先 JSON 解析
            var cleaned = text.replace(/<ROUTE>.*?<\/ROUTE>/g, "").replace(/<ROUTE>\w+<\/ROUTE>/g, "").trim();
            try {
                var parsed = JSON.parse(cleaned);
                if (!Array.isArray(parsed)) parsed = [parsed];
                parsed.forEach(function(obj) { locs.push(obj); });
                if (locs.length) return locs;
            } catch(e) {}
            // 回退 【】 格式
            var blocks = text.split(/【name】/g).slice(1);
            blocks.forEach(function(b) {
                var m = {};
                m.name = (b.match(/^(.+)/) || ["",""])[0].trim();
                var sl = b.match(/【scale_level】(.+)/); if (sl) m.scale_level = sl[1].trim();
                var pn = b.match(/【parent_name】(.+)/); if (pn) m.parent_name = pn[1].trim();
                var sc = b.match(/【scale】(.+)/); if (sc) m.scale_enum = sc[1].trim();
                var gx = b.match(/【grid_x】(\d+)/); if (gx) m.grid_x = parseInt(gx[1]);
                var gy = b.match(/【grid_y】(\d+)/); if (gy) m.grid_y = parseInt(gy[1]);
                var mx = b.match(/【map_x】(\d+)/); if (mx) m.map_x = parseInt(mx[1]);
                var my = b.match(/【map_y】(\d+)/); if (my) m.map_y = parseInt(my[1]);
                var d = b.match(/【description】([\s\S]+?)(?=【attributes】|<ROUTE>|$)/); if (d) m.description = d[1].trim();
                var a = b.match(/【attributes】([\s\S]+?)(?=<ROUTE>|$)/); if (a) {
                    try { m.attributes = JSON.parse(a[1].trim()); } catch(e) { m.attributes = {}; }
                }
                locs.push(m);
            });
            return locs;
        }
    },
    "character": {
        label: "👤 人设",
        template: "\n\n【结构化输出格式 — 必须严格遵循】\n" +
            "系统当前人物属性模板如下（你必须严格按照此模板的每个分组和每个字段输出 attributes）：\n" +
            "{character_template}\n\n" +
            "每个角色一个完整块，格式如下：\n\n" +
            "【name】角色名（必填）\n" +
            "【aliases】别名，逗号分隔\n" +
            "【status】存活/阵亡/飞升/失踪\n" +
            "【is_active】true 或 false\n" +
            "【faction_name】所属势力全名（无则写'散修'）\n" +
            "【faction_role】在势力中的职位\n" +
            "【is_always_context】true 或 false\n" +
            "【attributes】\n" +
            "[必须严格按上方模板的每个分组逐个输出，每个字段都填满值]\n" +
            "分组名:\n" +
            "  字段1: 值1\n" +
            "  字段2: 值2\n" +
            "分组名2:\n" +
            "  字段1: 值1\n" +
            "【description】详细 Markdown 描述\n" +
            "<ROUTE>DONE</ROUTE>",
        jsonExample: null,  // 由 getFormatPrompt 根据当前模板动态生成
        parser: function(text) {
            var chars = [];
            // 优先 JSON 解析
            var cleaned = text.replace(/<ROUTE>.*?<\/ROUTE>/g, "").replace(/<ROUTE>\w+<\/ROUTE>/g, "").trim();
            try {
                var parsed = JSON.parse(cleaned);
                if (!Array.isArray(parsed)) parsed = [parsed];
                parsed.forEach(function(obj) { chars.push(obj); });
                if (chars.length) return chars;
            } catch(e) {}
            // 回退 【】 格式
            var blocks = text.split(/【name】/g).slice(1);
            blocks.forEach(function(b) {
                var m = {};
                m.name = (b.match(/^(.+)/) || ["",""])[0].trim();
                var al = b.match(/【aliases】(.+)/); if (al) m.aliases = al[1].trim();
                var st = b.match(/【status】(.+)/); if (st) m.status = st[1].trim();
                var ia = b.match(/【is_active】(true|false)/i); if (ia) m.is_active = ia[1].toLowerCase() === "true";
                var fn = b.match(/【faction_name】(.+)/); if (fn) m.faction_name = fn[1].trim();
                var fr = b.match(/【faction_role】(.+)/); if (fr) m.faction_role = fr[1].trim();
                var iac = b.match(/【is_always_context】(true|false)/i); if (iac) m.is_always_context = iac[1].toLowerCase() === "true";
                var attrs = {};
                var attrSec = b.match(/【attributes】([\s\S]+?)(?=【description】|<ROUTE>|$)/);
                if (attrSec) {
                    var lines = attrSec[1].trim().split('\n');
                    var curGroup = "";
                    lines.forEach(function(l) {
                        var trimmed = l.trim();
                        if (!trimmed) return;
                        if (/^[^:]+:$/.test(trimmed) && !/:/.test(trimmed.slice(0, -1))) {
                            curGroup = trimmed.slice(0, -1).trim();
                            attrs[curGroup] = {};
                        } else if (curGroup) {
                            var colonIdx = trimmed.indexOf(':');
                            if (colonIdx > 0) {
                                var key = trimmed.substring(0, colonIdx).trim();
                                var val = trimmed.substring(colonIdx + 1).trim();
                                if (key && val) attrs[curGroup][key] = val;
                            }
                        }
                    });
                }
                m.attributes = attrs;
                var d = b.match(/【description】([\s\S]+?)(?=<ROUTE>|$)/); if (d) m.description = d[1].trim();
                chars.push(m);
            });
            return chars;
        }
    },
    "outline": {
        label: "📖 大纲",
        template: "\n\n【结构化输出格式 — 必须严格遵循，字段名必须与下方完全一致】\n" +
            "每条大纲一个完整块，格式如下：\n\n" +
            "【title】条目标题（必填）\n" +
            "【category】世界观/世界势力/地理/人物设定/能力体系设定/剧情大纲\n" +
            "【parent_name】父大纲标题（顶级写'无'；多级路径用/分隔，如'第一卷/第一章'）\n" +
            "【is_always_context】true 或 false\n" +
            "【order_index】排序序号（整数）\n" +
            "【description】详细 Markdown 描述\n" +
            "<ROUTE>DONE</ROUTE>",
        jsonExample: '\n\n【JSON Output — 必须严格输出JSON】\n{"title":"条目标题","category":"世界观","parent_name":"无","is_always_context":true,"order_index":1,"description":"简短描述"}\n禁止使用任何其他格式（包括【】标记），必须输出合法JSON。',
        parser: function(text) {
            var outlines = [];
            // 优先 JSON 解析
            var cleaned = text.replace(/<ROUTE>.*?<\/ROUTE>/g, "").replace(/<ROUTE>\w+<\/ROUTE>/g, "").trim();
            try {
                var parsed = JSON.parse(cleaned);
                if (!Array.isArray(parsed)) parsed = [parsed];
                parsed.forEach(function(obj) { outlines.push(obj); });
                if (outlines.length) return outlines;
            } catch(e) {}
            // 回退 【】 格式
            var blocks = text.split(/【title】/g).slice(1);
            blocks.forEach(function(b) {
                var m = {};
                m.title = (b.match(/^(.+)/) || ["",""])[0].trim();
                var c = b.match(/【category】(.+)/); if (c) m.category = c[1].trim();
                var pn = b.match(/【parent_name】(.+)/); if (pn) m.parent_name = pn[1].trim();
                var iac = b.match(/【is_always_context】(true|false)/i); if (iac) m.is_always_context = iac[1].toLowerCase() === "true";
                var oi = b.match(/【order_index】(\d+)/); if (oi) m.order_index = parseInt(oi[1]);
                var d = b.match(/【description】([\s\S]+?)(?=<ROUTE>|$)/); if (d) m.description = d[1].trim();
                outlines.push(m);
            });
            return outlines;
        }
    }
};

function buildFormatSelector(node) {
    var fmt = node.format || "";
    var opts = '<option value="">🔵 通用输出</option>';
    Object.keys(REASONING_FORMATS).forEach(function(k) {
        if (!k) return;
        opts += '<option value="' + k + '" ' + (fmt === k ? "selected" : "") + '>' + REASONING_FORMATS[k].label + '</option>';
    });
    return '<div style="margin-top:4px;font-size:.68rem;"><span style="color:var(--text-muted);">输出格式：</span><select class="format-sel" style="font-size:.65rem;padding:1px 3px;border:1px solid var(--border);border-radius:3px;background:var(--bg);color:var(--text);">' + opts + '</select></div>';
}

function getFormatPrompt(node) {
    var fmt = REASONING_FORMATS[node.format || ""];
    if (!fmt) return "";
    if (fmt.jsonExample) {
        // 有 JSON 示例时只输出 JSON 格式，不输出【】模板
        return fmt.jsonExample;
    }
    if (node.format === "character" && typeof charTemplate !== "undefined" && Array.isArray(charTemplate) && charTemplate.length) {
        // 根据当前模板动态生成 JSON 示例，不输出【】模板
        var attrsExample = {};
        charTemplate.forEach(function(g) {
            var fields = {};
            (g.fields || []).forEach(function(f) { fields[f] = "值"; });
            if (Object.keys(fields).length === 0) fields = {};
            attrsExample[g.group] = fields;
        });
        return '\n\n【JSON Output — 必须严格输出JSON，attributes 严格只含以下分组】\n{"name":"角色名","aliases":"别名","status":"存活","is_active":true,"faction_name":"势力名","faction_role":"职位","is_always_context":false,"attributes":' + JSON.stringify(attrsExample) + ',"description":"一句话概述"}\n禁止使用任何其他格式（包括【】标记），必须输出合法JSON。';
    }
    return fmt.template || "";
}

/* ═══════════ 校验节点：程序验证结构化输出 ═══════════ */
function validateStructuredOutput(format, items, chainNodes, templateSnapshot) {
    // 返回 { valid: true } 或 { valid: false, errors: [...] }
    if (!items || !items.length) return { valid: false, errors: ["上游无结构化数据可校验"] };
    var item = items[0];
    var errors = [];

    if (format === "location") {
        if (!item.name || item.name.trim() === "" || item.name === "未知") errors.push("name 为空或无效");
        var validScales = ["大千世界","宇宙","星球","大陆","国家","城池","街区","建筑"];
        if (item.scale_level && validScales.indexOf(item.scale_level) === -1) errors.push("scale_level 不在有效值中: " + item.scale_level);
        if (item.grid_x !== undefined && (item.grid_x < 0 || item.grid_x > 24)) errors.push("grid_x 超出 0-24");
        if (item.grid_y !== undefined && (item.grid_y < 0 || item.grid_y > 24)) errors.push("grid_y 超出 0-24");
        if (!item.description || item.description.trim().length < 3) errors.push("description 过短或缺失");
    } else if (format === "character") {
        if (!item.name || item.name.trim() === "" || item.name === "未知") errors.push("name 为空或无效");
        // 优先使用快照模板，无快照则回退到 charTemplate
        var tmpl = (templateSnapshot && templateSnapshot.length) ? templateSnapshot : charTemplate;
        if (!item.attributes || Object.keys(item.attributes).length === 0) {
            errors.push("attributes 为空，未按模板填写");
        } else if (tmpl && Array.isArray(tmpl)) {
            // 建立模板索引：分组名 → 字段集合
            var tmplGroups = {};
            tmpl.forEach(function(g) {
                tmplGroups[g.group] = new Set(g.fields || []);
            });
            // 检查缺少的分组和字段
            tmpl.forEach(function(g) {
                var gname = g.group;
                var fields = g.fields || [];
                var groupData = item.attributes[gname];
                if (!groupData || Object.keys(groupData).length === 0) {
                    errors.push("缺少模板分组: " + gname);
                } else {
                    fields.forEach(function(f) {
                        if (!groupData[f] || String(groupData[f]).trim() === "") {
                            errors.push("分组「" + gname + "」缺少字段: " + f);
                        }
                    });
                }
            });
            // 检查多余的分组和字段
            Object.keys(item.attributes).forEach(function(actualGroup) {
                if (!tmplGroups[actualGroup]) {
                    errors.push("多余分组（模板中不存在）: " + actualGroup);
                } else {
                    var tmplFields = tmplGroups[actualGroup];
                    var groupData = item.attributes[actualGroup];
                    if (groupData && typeof groupData === "object") {
                        Object.keys(groupData).forEach(function(actualField) {
                            if (!tmplFields.has(actualField)) {
                                errors.push("分组「" + actualGroup + "」多余字段: " + actualField);
                            }
                        });
                    }
                }
            });
        }
    } else if (format === "outline") {
        if (!item.title || item.title.trim() === "") errors.push("title 为空");
        var validCats = ["世界观","世界势力","地理","人物设定","能力体系设定","剧情大纲"];
        if (item.category && validCats.indexOf(item.category) === -1) errors.push("category 不在有效值中: " + item.category);
        if (!item.description || item.description.trim().length < 3) errors.push("description 过短或缺失");
    }

    return errors.length ? { valid: false, errors: errors } : { valid: true };
}

function compileTarget(target) {
    if (typeof target === "string") return target;
    if (!Array.isArray(target) || !target.length) return "";
    var compiled = "【输出铁律 — 必须逐字遵守，违反将导致流程失败】\n\n请严格按照以下【区块顺序】输出，每个区块单独一行，绝对不要添加任何解释、标记或额外文字：\n\n";
    target.forEach(function(block, index) {
        var n = index + 1;
        if (block.type === "placeholder") compiled += "[区块" + n + " — 强制占位符输出]: 仅输出这一行占位符字符串：" + (block.value||"") + " ，不要添加任何前缀或后缀。\n";
        if (block.type === "text") compiled += "[区块" + n + " — 强制文本输出]: 仅输出以下要求的文本内容：" + (block.value||"") + "\n";
        if (block.type === "bool") compiled += "[区块" + n + " — 强制布尔路由]: 仅输出 <ROUTE>" + (block.trueVal||"") + "</ROUTE> 或 <ROUTE>" + (block.falseVal||"") + "</ROUTE> 中的一个。\n";
        if (block.type === "enum") compiled += "[区块" + n + " — 强制选项路由]: 从以下选项中精确选择一个，仅输出 <ROUTE>选项值</ROUTE> 格式。可选值为: " + (block.options||"") + "\n";
    });
    compiled += "\n⚠️ 再次强调：只输出上述区块的内容，禁止添加任何解释、前言、后缀或 markdown 代码块。";
    return compiled;
}

async function runNode(card, node) {
    const s = getSettings();
    if (!s.apiKey) { document.getElementById("settings-modal").classList.remove("hidden"); return; }
    const outDiv = card.querySelector(".node-output");
    outDiv.style.display = "block";
    const btn = card.querySelector(".node-run-btn"); btn.disabled = true;

    // 按上游节点类型分离传递数据
    let prevOutput = "";    // 全文（传给推理/交互节点）
    let prevContent = "";   // 纯内容（传给写入节点）
    let prevRoute = "";     // 路由值（传给路由节点）
    (currentChain.nodes || []).forEach(n => {
        var passesToThis = n.next_node_id === node.id;
        (n.branches || []).forEach(br => { if (br.next_node_id === node.id) passesToThis = true; });
        if (passesToThis && n.output) {
            var nRoute = "";
            var rm3 = (n.output || "").match(/<ROUTE>([\s\S]*?)<\/ROUTE>/);
            if (rm3) nRoute = rm3[1].trim();
            var nContent = (n.output || "").replace(/<\/?ROUTE>/g, "").trim();
            prevOutput += (prevOutput ? "\n" : "") + n.output;
            if (nContent) prevContent += (prevContent ? "\n" : "") + nContent;
            if (nRoute) prevRoute = nRoute;
        }
    });

    if (node.type === "ask") {
        // ❓ 提问节点：AI 生成问题 → 弹窗 → 用户回答
        btn.textContent = "生成问题中...";
        try {
            var askPrompt = (node.premise || "请根据上下文") + "\n\n请基于以上内容生成一个精准的、需要向用户提问的问题。只输出问题本身，不要任何解释或前缀。";
            var res = await fetch("/api/reasoning/execute", {
                method: "POST", headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ novel_id: currentNovelId, premise: askPrompt, prompt: node.prompt || "", target: "", previous_output: prevOutput, api_key: s.apiKey, base_url: s.baseUrl, model: s.model, context_settings: (currentChain || {}).context_settings || {}, chain_id: currentChainId || null, response_format: /deepseek/i.test(s.model) ? {type: "json_object"} : undefined }),
            });
            var question = "";
            for await (const msg of parseSSEStream(res)) {
                if (msg.type === "chunk") { question += msg.content; outDiv.textContent = "生成问题中...\n" + question; }
            }
            question = question.replace(/<\/?ROUTE>/g, "").trim();
            outDiv.textContent = "❓ " + question;
            var answer = prompt("🤖 AI 提问:\n\n" + question + "\n\n请输入你的回答（将传递给后续节点）:");
            if (answer !== null) {
                node.output = "【用户对问题的回答】\n问题: " + question + "\n回答: " + answer;
                outDiv.innerHTML = '<span>❓ <b>' + e(question) + '</b></span><br><span style="color:#22c55e;">✅ 用户已回答: ' + e(answer) + '</span>';
            } else {
                node.output = "【用户跳过了提问】\n问题: " + question;
                outDiv.innerHTML = '<span>❓ <b>' + e(question) + '</b></span><br><span style="color:#f59e0b;">⚠️ 用户跳过</span>';
            }
            autoSave();
        } catch(e) { outDiv.textContent += "\n[错误: " + e.message + "]"; }
        finally { btn.disabled = false; btn.textContent = "❓ 提问"; }
        return;
    }

    if (node.type === "router") {
        // 🔀 路由节点：使用上游的结构化路由值
        btn.textContent = "判定中...";
        try {
            var routeVal = prevRoute;
            var matchedBranch = null;
            if (node.branches && node.branches.length && routeVal) {
                for (var ri = 0; ri < node.branches.length; ri++) {
                    if (node.branches[ri].condition && routeVal.includes(node.branches[ri].condition)) {
                        matchedBranch = node.branches[ri];
                        break;
                    }
                }
            }
            if (matchedBranch) {
                node.output = "🔀 路由命中: [" + routeVal + "] → " + matchedBranch.next_node_id;
                outDiv.innerHTML = '<span style="color:#f59e0b;">🔀 路由命中: <b>' + e(routeVal) + '</b></span><br><span>➡️ 流转至 ' + e(matchedBranch.next_node_id) + '</span>';
            } else if (node.next_node_id) {
                node.output = "🔀 默认路由: [" + (routeVal || "无路由值") + "] → " + node.next_node_id;
                outDiv.innerHTML = '<span style="color:#888;">🔀 无匹配分支，默认流转至 <b>' + e(node.next_node_id) + '</b></span>';
            } else {
                node.output = "🔀 路由终止: [" + (routeVal || "无路由值") + "] → 无下文";
                outDiv.innerHTML = '<span style="color:#ef4444;">🛑 路由终止</span>';
            }
            autoSave();
        } catch(e) { outDiv.textContent += "\n[错误: " + e.message + "]"; }
        finally { btn.disabled = false; btn.textContent = "🔀 判定"; }
        return;
    }

    if (node.type === "write") {
        // 📝 写入节点：使用上游的纯内容（无ROUTE标签）
        btn.textContent = "写入中...";
        try {
            var content = prevContent;
            if (!content) {
                node.output = "⛔ 写入失败：上游节点无有效内容";
                outDiv.innerHTML = '<span style="color:#ef4444;">⛔ 写入失败：上游无内容</span>';
                autoSave();
                btn.disabled = false; btn.textContent = "📝 写入";
                return;
            }
            var wt = node.write_to || {};
            if (!wt.entity) {
                node.output = "⛔ 写入失败：未配置写入目标";
                outDiv.innerHTML = '<span style="color:#ef4444;">⛔ 未配置写入目标</span>';
                autoSave();
                btn.disabled = false; btn.textContent = "📝 写入";
                return;
            }
            var toolArgs = {
                element_type: wt.entity,
                action: wt.action === "create" ? "add" : (wt.action === "update" ? "update" : "add"),
            };
            // 🧩 尝试从上游推理节点获取结构化解析
            var parsedItems = null;
            var upstreamFormat = "";
            (currentChain.nodes || []).forEach(function(n) {
                var passes = n.next_node_id === node.id;
                (n.branches || []).forEach(function(br) { if (br.next_node_id === node.id) passes = true; });
                if (passes && n.format && REASONING_FORMATS[n.format] && REASONING_FORMATS[n.format].parser) {
                    upstreamFormat = n.format;
                    parsedItems = REASONING_FORMATS[n.format].parser(n.output || "");
                }
            });
            // 构建 data 对象
            var dataObj = {};
            var toolName = "manage_world_element";
            if (wt.entity === "chain_todos") {
                toolName = "manage_chain_todos";
                if (wt.action === "update" && wt.field === "status") {
                    // 📋 标记待办为完成：找第一个 pending 项
                    var todos = (currentChain && currentChain.todos) ? currentChain.todos : [];
                    var pending = null;
                    for (var i = 0; i < todos.length; i++) {
                        if (todos[i].status === "pending" || todos[i].status === "in_progress") {
                            pending = todos[i]; break;
                        }
                    }
                    if (pending) {
                        toolArgs = { action: "update_status", todo_id: pending.id, status: "done" };
                    } else {
                        toolArgs = { action: "update_status", todo_id: "none", status: "done" };
                    }
                } else {
                    // 新增待办项
                    toolArgs = { action: "add", content: content };
                }
            } else if (wt.entity === "character" || wt.entity === "location" || wt.entity === "outline" || wt.entity === "faction" || wt.entity === "timeline" || wt.entity === "character_template") {
                var item = (parsedItems && parsedItems.length > 0) ? parsedItems[0] : null;
                if (wt.entity === "character_template") {
                    // 模板写入：尝试从上游内容提取 JSON 数组
                    var tmplData = null;
                    try { tmplData = JSON.parse(content); } catch(e) {
                        var m = content.match(/\[[\s\S]*\]/);
                        if (m) try { tmplData = JSON.parse(m[0]); } catch(e2) {}
                    }
                    if (tmplData && Array.isArray(tmplData)) {
                        dataObj["attributes"] = tmplData;
                    } else {
                        dataObj["attributes"] = content;
                    }
                } else if (item && upstreamFormat === "location") {
                    dataObj["name"] = item.name || "";
                    dataObj["scale_level"] = item.scale_level || "";
                    if (item.parent_name) dataObj["parent_name"] = item.parent_name;
                    if (item.scale) dataObj["scale_enum"] = item.scale;
                    if (item.grid_x !== undefined) dataObj["grid_x"] = item.grid_x;
                    if (item.grid_y !== undefined) dataObj["grid_y"] = item.grid_y;
                    if (item.map_x !== undefined) dataObj["map_x"] = item.map_x;
                    if (item.map_y !== undefined) dataObj["map_y"] = item.map_y;
                    if (item.attributes) dataObj["attributes"] = item.attributes;
                    dataObj["description"] = item.description || content;
                } else if (item && upstreamFormat === "character") {
                    dataObj["name"] = item.name || "";
                    if (item.aliases) dataObj["aliases"] = item.aliases;
                    if (item.status) dataObj["status"] = item.status;
                    if (item.is_active !== undefined) dataObj["is_active"] = item.is_active;
                    if (item.faction_name) dataObj["faction_name"] = item.faction_name;
                    if (item.faction_role) dataObj["faction_role"] = item.faction_role;
                    if (item.is_always_context !== undefined) dataObj["is_always_context"] = item.is_always_context;
                    dataObj["attributes"] = item.attributes || {};
                    dataObj["description"] = item.description || "";
                } else if (item && upstreamFormat === "outline") {
                    dataObj["title"] = item.title || "";
                    dataObj["category"] = item.category || "";
                    if (item.parent_name && item.parent_name !== "无") dataObj["parent_name"] = item.parent_name;
                    if (item.is_always_context !== undefined) dataObj["is_always_context"] = item.is_always_context;
                    if (item.order_index !== undefined) dataObj["order_index"] = item.order_index;
                    dataObj["description"] = item.description || content;
                }
                // 🎯 若 write_to.field===category 且有 sub_field，则锁定分类（覆盖解析值）
                if (wt.field === "category" && wt.sub_field) {
                    dataObj["category"] = wt.sub_field;
                } else if (wt.field === "attributes") {
                    // 尝试解析 content 为 JSON，否则存入嵌套字段
                    dataObj[wt.field] = {};
                    if (wt.sub_field) {
                        // 设置嵌套值: data.attributes.sub_field = content
                        dataObj[wt.field][wt.sub_field] = content;
                    } else {
                        dataObj[wt.field] = { "_content": content };
                    }
                } else if (wt.field) {
                    dataObj[wt.field] = content;
                } else {
                    dataObj["description"] = content;
                }
            }
            toolArgs.data = dataObj;
            // 如果是更新且知道 entity name，尝试通过 name 查找
            if (toolArgs.action === "update" && wt.entity) {
                // 尝试从上游内容提取名称
                var nameMatch = content.match(/【(.+?)】|^(.+?)[\n：:]|名称[:：]\s*(.+)/);
                if (nameMatch) {
                    dataObj["name"] = (nameMatch[1] || nameMatch[2] || nameMatch[3] || "").trim();
                }
            }
            var execRes = await fetch("/api/agent/execute/" + currentNovelId, {
                method: "POST", headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ tool_name: toolName, arguments: toolArgs, write_to: wt.entity === "chain_todos" ? null : (wt || null), chain_id: currentChainId || null }),
            });
            if (execRes.ok) {
                var result = await execRes.json();
                node.output = "✅ 写入成功：" + result.msg;
                outDiv.innerHTML = '<span style="color:#22c55e;">✅ 写入成功</span><br><span style="font-size:.7rem;">' + e(result.msg || "") + '</span>';
            } else {
                var errBody = await execRes.json().catch(function() { return {detail: "未知错误"}; });
                node.output = "⛔ 写入失败：" + (errBody.detail || "");
                outDiv.innerHTML = '<span style="color:#ef4444;">⛔ 写入失败</span><br><span style="font-size:.7rem;">' + e(errBody.detail || "") + '</span>';
            }
            autoSave();
        } catch(e) { outDiv.textContent += "\n[错误: " + e.message + "]"; }
        finally { btn.disabled = false; btn.textContent = "📝 写入"; }
        return;
    }

    if (node.type === "verify") {
        btn.textContent = "校验中...";
        try {
            var fresh = await (await fetch("/api/reasoning_chains/" + currentChainId + "?novel_id=" + currentNovelId)).json();
            var vTodos = fresh.todos || [];
            var vFilter = node.verify_filter || "";
            var filters = vFilter ? vFilter.split('|') : [];
            var hasPending = false;
            if (filters.length > 0) {
                for (var vti = 0; vti < vTodos.length; vti++) {
                    if ((vTodos[vti].status === "pending" || vTodos[vti].status === "in_progress") && vTodos[vti].content) {
                        for (var fi = 0; fi < filters.length; fi++) {
                            if (vTodos[vti].content.indexOf(filters[fi]) >= 0) { hasPending = true; break; }
                        }
                        if (hasPending) break;
                    }
                }
            } else {
                for (var vti2 = 0; vti2 < vTodos.length; vti2++) {
                    if (vTodos[vti2].status === "pending" || vTodos[vti2].status === "in_progress") { hasPending = true; break; }
                }
            }
            node.output = hasPending ? "⏳ 仍有待办 → CONTINUE" : "✅ 全部完成 → " + ((node.branches || []).some(function(b){return b.condition==="NEXT";}) ? "NEXT" : "DONE");
            outDiv.innerHTML = '<span style="color:' + (hasPending ? '#eab308' : '#22c55e') + ';">' + node.output + '</span>';
        } catch(e) {
            node.output = "⛔ 校验异常: " + e.message;
            outDiv.innerHTML = '<span style="color:#ef4444;">⛔ ' + e.message + '</span>';
        }
        autoSave();
        btn.disabled = false; btn.textContent = "✅ 校验确认";
        return;
    }

    if (node.type === "validate") {
        // 校验节点：程序验证上游结构化输出
        btn.textContent = "校验中...";
        var fmt = node.format || "";
        var parser = REASONING_FORMATS[fmt] && REASONING_FORMATS[fmt].parser;
        if (!parser) {
            node.output = "✅ 无结构化格式，跳过校验";
            outDiv.innerHTML = '<span style="color:#22c55e;">✅ 无格式，直接通过</span>';
            autoSave();
            btn.disabled = false; btn.textContent = "🔍 校验";
            return;
        }
        if (!prevContent) {
            node.output = "⛔ 校验失败：上游无内容";
            outDiv.innerHTML = '<span style="color:#ef4444;">⛔ 上游无内容</span>';
            autoSave();
            btn.disabled = false; btn.textContent = "🔍 校验";
            return;
        }
        var items = parser(prevContent);
        var result = validateStructuredOutput(fmt, items, currentChain ? currentChain.nodes : null);
        if (result.valid) {
            node.output = "✅ 校验通过";
            outDiv.innerHTML = '<span style="color:#22c55e;">✅ 校验通过 → ' + (node.branches && node.branches[0] ? node.branches[0].next_node_id : '写入') + '</span>';
        } else {
            node.output = "⛔ 校验不通过:\n" + result.errors.map(function(e) { return "- " + e; }).join("\n");
            outDiv.innerHTML = '<span style="color:#ef4444;">⛔ 校验不通过</span><pre style="font-size:.65rem;margin-top:4px;color:var(--text-muted);">' + e(node.output) + '</pre>';
        }
        autoSave();
        btn.disabled = false; btn.textContent = "🔍 校验";
        return;
    }

    outDiv.textContent = "AI 思考中...\n";
    btn.textContent = "执行中...";

    try {
        // Merge upstream output into premise BEFORE placeholder resolution
        let rawPremise = node.premise || "";
        if (prevOutput) {
            rawPremise = prevOutput + "\n\n" + rawPremise;
        }
        const chapterContent = contentInput.value || "";
        const sel = contentInput.value.substring(contentInput.selectionStart, contentInput.selectionEnd);
        const premise = rawPremise.replace(/\{content\}/g, chapterContent).replace(/\{selection\}/g, sel);
        const prompt = (node.prompt || "") + getFormatPrompt(node);
        const target = compileTarget(node.target);
        const res = await fetch("/api/reasoning/execute", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ novel_id: currentNovelId, premise, prompt, target, previous_output: "", api_key: s.apiKey, base_url: s.baseUrl, model: s.model, context_settings: (currentChain || {}).context_settings || {}, write_to: node.write_to || null, chain_id: currentChainId || null, response_format: /deepseek/i.test(s.model) ? {type: "json_object"} : undefined }),
        });
        let rawResult = "";
        for await (const msg of parseSSEStream(res)) {
            if (msg.type === "chunk") { rawResult += msg.content; outDiv.textContent = "AI 思考中...\n" + rawResult; outDiv.scrollTop = outDiv.scrollHeight; }
            else if (msg.type === "error") { rawResult += "\n[错误: " + msg.message + "]"; }
            else if (msg.type === "tool_proposal") {
                var safeArgs = encodeURIComponent(JSON.stringify(msg.arguments));
                outDiv.innerHTML += '<div class="tool-proposal-card" style="margin-top:10px;padding:10px;border:1px solid #c084fc;border-radius:8px;background:#faf5ff;">'
                    + '<p style="margin:0 0 5px 0;color:#9333ea;"><strong>🛠 提议执行: ' + e(msg.tool_name) + '</strong></p>'
                    + '<pre style="font-size:0.75rem;background:#fff;padding:5px;border-radius:4px;max-height:100px;overflow-y:auto;">' + e(JSON.stringify(msg.arguments, null, 2)) + '</pre>'
                    + '<button class="sys-btn sys-btn-primary" style="margin-top:5px;width:100%;" onclick="var a=JSON.parse(decodeURIComponent(\'' + safeArgs + '\'));executeTool(\'' + e(msg.tool_name) + '\',a,this)">批准执行</button>'
                    + '</div>';
            }
            else if (msg.type === "done") {
                // 🛡️ 输出格式强制校验
                var cleanPreValidate = rawResult;
                var codeBlockMatch = cleanPreValidate.match(/```[\s\S]*?```/);
                if (codeBlockMatch) {
                    cleanPreValidate = cleanPreValidate.replace(/```[\s\S]*?```/g, function(m) { return m.replace(/```\w*\n?/g, "").replace(/```/g, ""); });
                }
                // Extract ROUTE key and strip tags
                let routeKey = null;
                var rm = cleanPreValidate.match(/<ROUTE>([\s\S]*?)<\/ROUTE>/);
                if (rm) { routeKey = rm[1].trim(); }
                var cleanResult = cleanPreValidate.replace(/<\/?ROUTE>/g, "").trim();
                node.output = cleanResult;
                autoSave();
                // Route matching & visual feedback
                var branchRows = card.querySelectorAll(".branch-row");
                branchRows.forEach(function(r) { r.classList.remove("route-matched"); });
                var matchedCondition = null, matchedTargetId = null, matchedRow = null;
                if (node.branches && node.branches.length) {
                    for (var bi = 0; bi < node.branches.length; bi++) {
                        var br = node.branches[bi];
                        var compareText = (routeKey !== null ? routeKey : cleanResult).replace(/<\/?ROUTE>/g, "");
                        if (br.condition && compareText.includes(br.condition)) {
                            matchedCondition = br.condition;
                            matchedTargetId = br.next_node_id;
                            matchedRow = branchRows[bi];
                            break;
                        }
                    }
                }
                // Build route log
                var logHtml = "";
                if (matchedRow) {
                    matchedRow.classList.add("route-matched");
                    logHtml = '<div class="route-log">🎯 [路由流转]: 命中分支 \'' + e(matchedCondition) + '\' ➡️ 前往节点 ' + e(matchedTargetId||"未知") + '</div>';
                } else if (node.next_node_id) {
                    var tgt = (currentChain.nodes||[]).find(function(n) { return n.id === node.next_node_id; });
                    logHtml = '<div class="route-log">➡️ [路由流转]: 默认顺序执行 ➡️ 前往节点 ' + e(node.next_node_id) + (tgt ? " (" + e(tgt.premise||"").substring(0,20) + ")" : "") + '</div>';
                } else {
                    logHtml = '<div class="route-log">🛑 [路由流转]: 无下文，推演在此终止。</div>';
                }
                outDiv.innerHTML = "<span>AI 思考中...\n" + e(cleanResult) + "</span>" + logHtml;
                outDiv.scrollTop = outDiv.scrollHeight;
            }
        }
    } catch (e) { outDiv.textContent += "\n[连接失败: " + e.message + "]"; }
    finally { btn.disabled = false; btn.textContent = "\u25B6 运行本节点"; }
}

async function* parseSSEStream(r) {
    const reader = r.body.getReader(); const dec = new TextDecoder(); let buf = "";
    while (true) {
        const { done, value } = await reader.read(); if (done) break;
        buf += dec.decode(value, { stream: true });
        const parts = buf.split("\n\n"); buf = parts.pop() || "";
        for (const p of parts) { for (const l of p.split("\n")) { if (l.startsWith("data: ")) { try { yield JSON.parse(l.slice(6)); } catch (e) { } } } }
    }
}

function autoSave() {
    if (!currentChainId || !currentChain) return;
    clearTimeout(saveTimer);
    saveTimer = setTimeout(async () => {
        await fetch(`/api/reasoning_chains/${currentChainId}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title: currentChain.title, nodes: currentChain.nodes, context_settings: currentChain.context_settings || {} }) });
    }, 600);
}

document.getElementById("new-chain-btn").addEventListener("click", async () => {
    const title = prompt("方案名称：", "新推演方案"); if (!title) return;
    const rc = await (await fetch("/api/reasoning_chains" + nidQ(), { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title }) })).json();
    currentChainId = rc.id; currentChain = rc; await loadReasoningModule(); loadGraph();
});

document.getElementById("add-node-btn").addEventListener("click", () => {
    if (!currentChain) { alert("请先选择或新建推演方案"); return; }
    if (!currentChain.nodes) currentChain.nodes = [];
    const idx = currentChain.nodes.length;
    const node = { id: "n" + (idx + 1), x: 100 + (idx % 4) * 200, y: 80 + Math.floor(idx / 4) * 280, premise: "", prompt: "", target: [], branches: [], next_node_id: "", output: "" };
    currentChain.nodes.push(node);
    renderAllNodes(); renderLines(); autoSave();
});

/* ═══════════ Chain Settings Modal ═══════════ */
var csm = document.getElementById("chain-settings-modal");
document.getElementById("chain-settings-btn")?.addEventListener("click", function() {
    if (!currentChain) { alert("请先选择或新建推演方案"); return; }
    if (!currentChain.context_settings) currentChain.context_settings = { use_outlines: true, use_characters: true, use_timeline: true };
    document.getElementById("cs-title").value = currentChain.title || "";
    document.getElementById("cs-use-outlines").checked = !!currentChain.context_settings.use_outlines;
    document.getElementById("cs-use-characters").checked = !!currentChain.context_settings.use_characters;
    document.getElementById("cs-use-timeline").checked = !!currentChain.context_settings.use_timeline;
    csm.classList.remove("hidden");
});
document.getElementById("cs-cancel-btn").addEventListener("click", function() { csm.classList.add("hidden"); });
csm.querySelector(".modal-backdrop").addEventListener("click", function() { csm.classList.add("hidden"); });
document.getElementById("cs-save-btn").addEventListener("click", async function() {
    if (!currentChain || !currentChainId) return;
    currentChain.title = document.getElementById("cs-title").value.trim() || currentChain.title;
    currentChain.context_settings = {
        use_outlines: document.getElementById("cs-use-outlines").checked,
        use_characters: document.getElementById("cs-use-characters").checked,
        use_timeline: document.getElementById("cs-use-timeline").checked,
    };
    await fetch("/api/reasoning_chains/" + currentChainId, {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: currentChain.title, nodes: currentChain.nodes, context_settings: currentChain.context_settings }),
    });
    await loadReasoningModule();
    csm.classList.add("hidden");
});

document.getElementById("chain-restore-btn")?.addEventListener("click", async function() {
    if (!currentChainId) return;
    if (!confirm("确定要将当前内置推理链恢复默认吗？这将清空所有自定义节点且无法撤销！")) return;
    try {
        var res = await fetch("/api/reasoning_chains/" + currentChainId + "/restore", { method: "POST" });
        var data = await res.json();
        if (res.ok) { showToast(data.message); loadGraph(); } else { alert("恢复失败: " + (data.detail || "未知")); }
    } catch (e) { alert("请求失败: " + e.message); }
});

/* ═══════════ Settings ═══════════ */
const sm = document.getElementById("settings-modal");
document.getElementById("settings-btn").addEventListener("click", () => { const s = getSettings(); document.getElementById("settings-apikey").value = s.apiKey; document.getElementById("settings-baseurl").value = s.baseUrl; document.getElementById("settings-model").value = s.model; document.getElementById("settings-stream").checked = s.streamOutput; sm.classList.remove("hidden"); });
document.getElementById("settings-cancel").addEventListener("click", () => sm.classList.add("hidden"));
sm.querySelector(".modal-backdrop").addEventListener("click", () => sm.classList.add("hidden"));
document.getElementById("settings-save").addEventListener("click", () => { localStorage.setItem("ai_api_key", document.getElementById("settings-apikey").value.trim()); localStorage.setItem("ai_base_url", document.getElementById("settings-baseurl").value.trim()); localStorage.setItem("ai_model", document.getElementById("settings-model").value.trim() || "gpt-3.5-turbo"); localStorage.setItem("ai_stream", document.getElementById("settings-stream").checked ? "true" : "false"); sm.classList.add("hidden"); });

/* ═══════════ Cheat Sheet ═══════════ */
function showCheatSheet() { document.getElementById("cheat-sheet-modal").classList.remove("hidden"); }
document.getElementById("cheat-sheet-close").addEventListener("click", () => document.getElementById("cheat-sheet-modal").classList.add("hidden"));
document.querySelector("#cheat-sheet-modal .modal-backdrop").addEventListener("click", () => document.getElementById("cheat-sheet-modal").classList.add("hidden"));
document.getElementById("cheat-prompt-btn").addEventListener("click", showCheatSheet);
document.getElementById("placeholder-ref-btn")?.addEventListener("click", showCheatSheet);
document.getElementById("chain-todos-toggle")?.addEventListener("click", function() {
    var list = document.getElementById("chain-todos-list");
    var btn = document.getElementById("chain-todos-toggle");
    if (list.style.display === "none") { list.style.display = ""; btn.textContent = "收起"; }
    else { list.style.display = "none"; btn.textContent = "展开"; }
});

// Click to copy on cheat sheet table cells
document.querySelector("#cheat-sheet-modal table")?.addEventListener("click", function(e) {
    var td = e.target.closest("td:first-child");
    if (td && td.textContent) {
        navigator.clipboard.writeText(td.textContent).then(function() {
            var orig = td.style.background;
            td.style.background = "#c8e6c9";
            setTimeout(function() { td.style.background = orig; }, 800);
        });
    }
});

/* ═══════════ Sandbox ═══════════ */
let sandboxLogText = "";
let sandboxSelectedLoc = "";
let sandboxSelectedChars = [];

async function loadSandboxModule() {
    // Load locations
    var locSelect = document.getElementById("sandbox-location");
    locSelect.innerHTML = '<option value="">-- 选择场地 --</option>';
    var locs = await (await fetch("/api/locations" + nidQ())).json();
    locs.forEach(function(l) {
        var sel = l.name === sandboxSelectedLoc ? " selected" : "";
        locSelect.innerHTML += '<option value="' + e(l.name) + '"' + sel + '>' + e(l.name) + '</option>';
    });
    locSelect.addEventListener("change", function() { sandboxSelectedLoc = locSelect.value; });

    // Load characters as checkboxes
    var charsDiv = document.getElementById("sandbox-characters");
    charsDiv.innerHTML = "";
    var chars = await (await fetch("/api/characters" + nidQ())).json();
    chars.forEach(function(ch) {
        var name = ch.name;
        if (ch.attributes && ch.attributes["基础信息"] && ch.attributes["基础信息"]["姓名"]) name = ch.attributes["基础信息"]["姓名"];
        var checked = sandboxSelectedChars.includes(name) ? " checked" : "";
        var label = document.createElement("label");
        label.className = "sandbox-char-item";
        label.innerHTML = '<input type="checkbox" value="' + e(String(name)) + '" data-cid="' + ch.id + '"' + checked + '> ' + e(String(name));
        label.querySelector("input").addEventListener("change", function() {
            sandboxSelectedChars = [];
            document.querySelectorAll("#sandbox-characters input:checked").forEach(function(cb) { sandboxSelectedChars.push(cb.value); });
        });
        charsDiv.appendChild(label);
    });
    // Restore scenario
    document.getElementById("sandbox-scenario").value = document.getElementById("sandbox-scenario").value || "";
}

document.getElementById("sandbox-start-btn").addEventListener("click", async function() {
    var s = getSettings();
    if (!s.apiKey) { document.getElementById("settings-modal").classList.remove("hidden"); return; }
    var locName = document.getElementById("sandbox-location").value;
    if (!locName) { showToast("请选择场地"); return; }
    var checked = document.querySelectorAll("#sandbox-characters input:checked");
    if (checked.length === 0) { showToast("请选择至少一位参战人员"); return; }
    var scenario = document.getElementById("sandbox-scenario").value.trim();
    if (!scenario) { showToast("请输入推演情景"); return; }

    var charPlaceholders = [];
    checked.forEach(function(cb) { charPlaceholders.push("{character:" + cb.value + "}"); });

    var content = "【演武场地】\n{location:" + locName + "}\n\n【参战人员】\n" + charPlaceholders.join("\n") + "\n\n【推演情景】\n" + scenario;
    var logArea = document.getElementById("sandbox-log-content");
    sandboxLogText = "";
    logArea.innerHTML = '<em style="color:var(--muted);">推演中...</em>';

    var btn = document.getElementById("sandbox-start-btn");
    btn.disabled = true; btn.textContent = "推演中...";
    try {
        var res = await fetch("/api/writing/copilot", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ novel_id: currentNovelId, current_chapter_content: content, selected_text: "", instruction: scenario, api_key: s.apiKey, base_url: s.baseUrl, model: s.model, scene: "sandbox_sim" }) });
        logArea.innerHTML = "";
        var gen = parseSSEStream(res);
        for (;;) {
            var chunkResult = await gen.next();
            if (chunkResult.done) break;
            var msg = chunkResult.value;
            if (msg.type === "chunk") { sandboxLogText += msg.content; logArea.textContent = sandboxLogText; logArea.scrollTop = logArea.scrollHeight; }
            else if (msg.type === "error") { sandboxLogText += "\n[错误: " + msg.message + "]"; logArea.textContent = sandboxLogText; }
        }
        document.getElementById("sandbox-copy-btn").disabled = false;
        document.getElementById("sandbox-next-btn").disabled = false;
    } catch (e) { logArea.textContent = "[失败: " + e.message + "]"; }
    finally { btn.disabled = false; btn.textContent = "\u25B6\uFE0F 开始兵棋推演"; }
});

document.getElementById("sandbox-copy-btn").addEventListener("click", function() {
    if (!sandboxLogText) return;
    var ta = getActiveTextarea();
    var start = ta.selectionStart;
    ta.value = ta.value.substring(0, start) + sandboxLogText + ta.value.substring(start);
    ta.focus(); ta.selectionStart = start + sandboxLogText.length; ta.selectionEnd = start + sandboxLogText.length;
    ta.dispatchEvent(new Event("input"));
    showToast("战报已复制到当前编辑器");
});

document.getElementById("sandbox-next-btn").addEventListener("click", function() {
    var scenario = document.getElementById("sandbox-scenario");
    scenario.value = "【接续上一回合战报继续推演】\n\n" + (sandboxLogText || "");
    document.getElementById("sandbox-start-btn").click();
});

/* ═══════════ Relations Graph ═══════════ */

async function loadRelationsGraph() {
    if (!currentNovelId) return;
    var container = document.getElementById("relation-network");
    if (container) container.innerHTML = '<div style="padding:20px;color:#3b82f6;">正在探测江湖动态，请稍候...</div>';

    try {
        var res = await Promise.all([
            fetch("/api/characters" + nidQ()),
            fetch("/api/factions" + nidQ()),
            fetch("/api/character_relations" + nidQ()),
        ]);

        if (!res[0].ok) throw new Error("无法获取人物数据");

        var chars = await res[0].json();
        var factions = await res[1].json();
        var rels = await res[2].json();

        if (!Array.isArray(chars) || chars.length === 0) {
            if (container) container.innerHTML = '<div style="padding:20px;color:#eab308;">当前世界尚无活跃人物，无法绘制网络。</div>';
            return;
        }

        renderVisNetwork(chars, factions, rels);

        var ul = document.getElementById("factions-list-ul");
        if (ul) {
            ul.innerHTML = (factions || []).map(function(_, i) {
                var f = factions[i];
                return '<li style="padding:8px;border-bottom:1px solid var(--border);cursor:pointer;" data-fid="' + f.id + '" onclick="openFactionModal(' + f.id + ')"><strong>' + e(f.name) + '</strong><br><span style="font-size:0.75rem;color:var(--text-muted);">' + e(f.description || "无描述") + '</span></li>';
            }).join("") || "<li>(无势力)</li>";
        }
    } catch (err) {
        console.error("图谱加载失败:", err);
        if (container) container.innerHTML = '<div style="padding:20px;color:#ef4444;"><b>网络加载崩溃:</b> ' + e(err.message || String(err)) + '</div>';
    }
}

function renderVisNetwork(chars, factions, rels) {
    var container = document.getElementById("relation-network");
    if (!container) return;

    try {
        if (typeof vis === "undefined") {
            container.innerHTML = "<div style='color:red;padding:20px;'>错误: vis-network 库未加载。请检查网络。</div>";
            return;
        }
        container.innerHTML = "";

        var factionMap = {};
        if (Array.isArray(factions)) {
            factions.forEach(function(f) { if (f && f.id != null) factionMap[f.id] = f.name; });
        }

        var factionColors = ["#fef3c7","#dbeafe","#d1fae5","#fce7f3","#e0e7ff","#fef9c3","#ede9fe","#ccfbf1","#fee2e2","#e5e7eb"];
        var factionColorIdx = 0;
        var factionColorMap = {};
        (factions || []).forEach(function(f) {
            var name = f.name || "未命名";
            factionColorMap[name] = factionColors[factionColorIdx % factionColors.length];
            factionColorIdx++;
        });
        factionColorMap["散修/无势力"] = "#f1f5f9";

        var validCharIds = new Set();
        (chars || []).forEach(function(c) { if (c && c.id != null) validCharIds.add(String(c.id)); });

        var nodesArray = (chars || []).map(function(c) {
            var groupName = (c.faction_id && factionMap[c.faction_id]) ? factionMap[c.faction_id] : "散修/无势力";
            var roleStr = c.faction_role ? "\n(" + c.faction_role + ")" : "";
            var cfg = {
                id: String(c.id),
                label: (c.name || "未知") + roleStr,
                group: groupName,
                title: "状态: " + (c.status || "正常") + "\n阵营: " + groupName,
                shape: "dot",
                size: c.is_active ? 20 : 10,
            };
            if (!c.is_active) cfg.color = { background: "#ccc", border: "#999" };
            return cfg;
        });

        var validRels = (Array.isArray(rels) ? rels : []).filter(function(r) {
            return r && validCharIds.has(String(r.source_id)) && validCharIds.has(String(r.target_id));
        });

        var edgesArray = validRels.map(function(r) {
            var edgeColor = "#94a3b8";
            if (r.weight < 0) edgeColor = "#ef4444";
            if (r.weight > 50) edgeColor = "#10b981";
            return {
                from: String(r.source_id),
                to: String(r.target_id),
                label: r.label || "",
                arrows: "to",
                color: { color: edgeColor },
                font: { size: 10, align: "middle" },
                title: "好感度: " + (r.weight || 0) + "\n原因: " + (r.description || "无"),
            };
        });

        var data = { nodes: new vis.DataSet(nodesArray), edges: new vis.DataSet(edgesArray) };

        var options = {
            physics: {
                solver: "repulsion",
                repulsion: { nodeDistance: 200, springLength: 200 },
            },
            groups: (function() {
                var g = {};
                Object.keys(factionColorMap).forEach(function(name) {
                    g[name] = { color: { background: factionColorMap[name], border: "#94a3b8" } };
                });
                return g;
            })(),
        };

        if (window.relationsNetwork) window.relationsNetwork.destroy();
        window.relationsNetwork = new vis.Network(container, data, options);

    } catch (err) {
        console.error("Vis.js 渲染崩溃:", err);
        container.innerHTML = "<div style='color:red;padding:20px;font-size:14px;line-height:1.5;'><b>🚨 渲染引擎崩溃!</b><br><br>错误信息: " + (err.message || String(err)) + "</div>";
    }
}
document.getElementById("btn-refresh-graph")?.addEventListener("click", loadRelationsGraph);

/* ═══════════ Faction Modal CRUD ═══════════ */
var activeFactionId = null;

window.openFactionModal = async function(fid) {
    var modal = document.getElementById("faction-modal");
    var nameInput = document.getElementById("faction-name-input");
    var descInput = document.getElementById("faction-desc-input");
    var locSelect = document.getElementById("faction-loc-select");
    var delBtn = document.getElementById("faction-delete-btn");
    document.getElementById("faction-modal-title").textContent = fid ? "⚙️ 编辑势力设定" : "🏛️ 创建全新世界势力";
    locSelect.innerHTML = '<option value="">-- 选择总部地点 --</option>';
    try {
        var locList = await (await fetch("/api/locations?novel_id=" + currentNovelId)).json();
        locList.forEach(function(l) {
            var opt = document.createElement("option");
            opt.value = l.id;
            opt.textContent = l.name + " [" + (l.scale_level||"") + "]";
            locSelect.appendChild(opt);
        });
    } catch(e) {}

    if (fid) {
        activeFactionId = fid;
        var factions = await (await fetch("/api/factions?novel_id=" + currentNovelId)).json();
        var f = factions.find(function(x) { return x.id == fid; });
        if (f) {
            nameInput.value = f.name || "";
            descInput.value = f.description || "";
            locSelect.value = f.base_location_id || "";
        }
        delBtn.style.display = "inline-block";
    } else {
        activeFactionId = null;
        nameInput.value = "";
        descInput.value = "";
        locSelect.value = "";
        delBtn.style.display = "none";
    }
    modal.classList.remove("hidden");
};

document.getElementById("new-faction-btn")?.addEventListener("click", function() { openFactionModal(null); });
document.getElementById("faction-cancel-btn")?.addEventListener("click", function() { document.getElementById("faction-modal").classList.add("hidden"); });
document.getElementById("faction-save-btn")?.addEventListener("click", async function() {
    var nameInput = document.getElementById("faction-name-input").value.trim();
    if (!nameInput) { showToast("势力名称不能为空"); return; }
    var payload = { novel_id: currentNovelId, name: nameInput, description: document.getElementById("faction-desc-input").value, base_location_id: document.getElementById("faction-loc-select").value ? parseInt(document.getElementById("faction-loc-select").value) : null };
    var url = activeFactionId ? "/api/factions/" + activeFactionId : "/api/factions";
    var method = activeFactionId ? "PUT" : "POST";
    try {
        var res = await fetch(url, { method: method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
        if (res.ok) { showToast("🏛️ 势力数据更新成功！"); document.getElementById("faction-modal").classList.add("hidden"); loadRelationsGraph(); }
    } catch(e) { console.error(e); }
});
document.getElementById("faction-delete-btn")?.addEventListener("click", async function() {
    if (!activeFactionId) return;
    if (!confirm("确定要将当前势力物理删除吗？\n删除后，属于该势力的所有人物其所属势力属性将自动脱钩，且该操作无法撤销！")) return;
    try {
        var res = await fetch("/api/factions/" + activeFactionId, { method: "DELETE" });
        if (res.ok) { showToast("💥 势力已成功移除！"); document.getElementById("faction-modal").classList.add("hidden"); loadRelationsGraph(); }
    } catch(e) { console.error(e); }
});
document.querySelector("#faction-modal .modal-backdrop")?.addEventListener("click", function() { document.getElementById("faction-modal").classList.add("hidden"); });

/* ═══════════ Export ═══════════ */
document.getElementById("export-btn").addEventListener("click", async () => {
    var res = await fetch("/api/novels/" + currentNovelId + "/export");
    var text = await res.text();
    var novel = (await (await fetch("/api/novels")).json()).find(function(n) { return n.id === currentNovelId; });
    var title = (novel && novel.title) || "小说";
    var blob = new Blob([text], { type: "text/markdown;charset=utf-8;" });
    var url = URL.createObjectURL(blob);
    var link = document.createElement("a");
    link.href = url;
    link.download = title + "_设定与正文.md";
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
    showToast("导出完成: " + title + "_设定与正文.md");
});

/* ═══════════ Utils + Init ═══════════ */
document.addEventListener("keydown", e => { if ((e.ctrlKey || e.metaKey) && e.key === "s") { e.preventDefault(); saveCurrentChapter(); } });
function e(t) { const d = document.createElement("div"); d.textContent = t; return d.innerHTML; }
async function refreshAll() {
    await loadNovelTime();
    if (currentModule === "writing") { await loadVolumes(); await loadChapters(); }
    else if (currentModule === "outline") await loadOutlineNotes();
    else if (currentModule === "characters") await loadCharactersModule();
    else if (currentModule === "map") { await loadLocationTree(); }
    else if (currentModule === "reasoning") await loadReasoningModule();
    else if (currentModule === "relations") await loadRelationsGraph();
    await loadCharacters(); swTimeline();
}
document.addEventListener("DOMContentLoaded", async () => {
    await loadNovels(); await loadChainSelect(); await refreshAll();
    document.getElementById("new-volume-btn").addEventListener("click", createNewVolume);
    document.getElementById("new-chapter-btn").addEventListener("click", createNewChapter);
    document.getElementById("new-character-btn").addEventListener("click", createNewCharacter);
    // ZIP export
    var zipBtn = document.getElementById("export-zip-btn");
    if (zipBtn) zipBtn.addEventListener("click", function() {
        window.location.href = "/api/novels/" + currentNovelId + "/export-zip";
    });
    // Time settings modal
    document.getElementById("global-clock-btn").addEventListener("click", function() {
        var d = tickToDate(window.currentTick);
        document.getElementById("ts-year").value = d.year;
        document.getElementById("ts-month").value = d.month;
        document.getElementById("ts-day").value = d.day;
        document.getElementById("ts-hour").value = d.hour;
        document.getElementById("ts-mpy").value = window.novelTimeConfig.months_per_year || 12;
        document.getElementById("ts-dpm").value = window.novelTimeConfig.days_per_month || 30;
        document.getElementById("ts-hpd").value = window.novelTimeConfig.hours_per_day || 24;
        document.getElementById("ts-era").value = window.novelTimeConfig.era_name || "";
        document.getElementById("ts-year-names").value = (window.novelTimeConfig.year_names || []).join(",");
        document.getElementById("ts-month-names").value = (window.novelTimeConfig.month_names || []).join(",");
        document.getElementById("ts-day-names").value = (window.novelTimeConfig.day_names || []).join(",");
        document.getElementById("ts-hour-names").value = (window.novelTimeConfig.hour_names || []).join(",");
        document.getElementById("time-settings-modal").classList.remove("hidden");
    });
    document.getElementById("ts-cancel-btn").addEventListener("click", function() { document.getElementById("time-settings-modal").classList.add("hidden"); });
    document.querySelector("#time-settings-modal .modal-backdrop").addEventListener("click", function() { document.getElementById("time-settings-modal").classList.add("hidden"); });
    document.getElementById("ts-save-btn").addEventListener("click", async function() {
        var y = parseInt(document.getElementById("ts-year").value) || 1;
        var m = parseInt(document.getElementById("ts-month").value) || 1;
        var d = parseInt(document.getElementById("ts-day").value) || 1;
        var h = parseInt(document.getElementById("ts-hour").value) || 0;
        window.novelTimeConfig.months_per_year = parseInt(document.getElementById("ts-mpy").value) || 12;
        window.novelTimeConfig.days_per_month = parseInt(document.getElementById("ts-dpm").value) || 30;
        window.novelTimeConfig.hours_per_day = parseInt(document.getElementById("ts-hpd").value) || 24;
        window.novelTimeConfig.era_name = document.getElementById("ts-era").value.trim();
        window.novelTimeConfig.year_names = document.getElementById("ts-year-names").value.split(",").map(function(s) { return s.trim(); }).filter(Boolean);
        window.novelTimeConfig.month_names = document.getElementById("ts-month-names").value.split(",").map(function(s) { return s.trim(); }).filter(Boolean);
        window.novelTimeConfig.day_names = document.getElementById("ts-day-names").value.split(",").map(function(s) { return s.trim(); }).filter(Boolean);
        window.novelTimeConfig.hour_names = document.getElementById("ts-hour-names").value.split(",").map(function(s) { return s.trim(); }).filter(Boolean);
        window.currentTick = dateToTick(y, m, d, h, window.novelTimeConfig);
        await fetch("/api/novels/" + currentNovelId + "/time", { method: "PUT", headers: {"Content-Type": "application/json"}, body: JSON.stringify({ current_tick: window.currentTick, calendar_config: window.novelTimeConfig }) });
        updateGlobalClockDisplay();
        document.getElementById("time-settings-modal").classList.add("hidden");
    });
});

/* ═══════════ Canvas Pan & Zoom ═══════════ */
(function setupCanvasPanZoom() {
    var canvasLayer = document.getElementById("chain-canvas");
    var canvasViewport = document.getElementById("reasoning-canvas");
    if (!canvasLayer || !canvasViewport) return;
    // Wheel zoom
    canvasViewport.addEventListener("wheel", function(e) {
        if (!document.getElementById("ws-reasoning") || document.getElementById("ws-reasoning").classList.contains("hidden")) return;
        e.preventDefault();
        var zoomAmount = e.deltaY > 0 ? 0.9 : 1.1;
        var newScale = Math.min(Math.max(0.2, window.canvasScale * zoomAmount), 3);
        var rect = canvasViewport.getBoundingClientRect();
        var mouseX = e.clientX - rect.left;
        var mouseY = e.clientY - rect.top;
        window.canvasPanX = mouseX - (mouseX - window.canvasPanX) * (newScale / window.canvasScale);
        window.canvasPanY = mouseY - (mouseY - window.canvasPanY) * (newScale / window.canvasScale);
        window.canvasScale = newScale;
        canvasLayer.style.transform = "translate(" + window.canvasPanX + "px, " + window.canvasPanY + "px) scale(" + window.canvasScale + ")";
        canvasViewport.style.backgroundPosition = window.canvasPanX + "px " + window.canvasPanY + "px";
        canvasViewport.style.backgroundSize = (20 * window.canvasScale) + "px " + (20 * window.canvasScale) + "px";
    }, {passive: false});
    // Right-click pan
    canvasViewport.addEventListener("contextmenu", function(e) { e.preventDefault(); });
    canvasViewport.addEventListener("mousedown", function(e) {
        if (e.button === 2) {
            isCanvasPanning = true;
            canvasPanStartX = e.clientX - window.canvasPanX;
            canvasPanStartY = e.clientY - window.canvasPanY;
            canvasViewport.style.cursor = "grabbing";
        }
    });
    document.addEventListener("mousemove", function(e) {
        if (isCanvasPanning) {
            window.canvasPanX = e.clientX - canvasPanStartX;
            window.canvasPanY = e.clientY - canvasPanStartY;
            canvasLayer.style.transform = "translate(" + window.canvasPanX + "px, " + window.canvasPanY + "px) scale(" + window.canvasScale + ")";
            canvasViewport.style.backgroundPosition = window.canvasPanX + "px " + window.canvasPanY + "px";
            canvasViewport.style.backgroundSize = (20 * window.canvasScale) + "px " + (20 * window.canvasScale) + "px";
        }
    });
    var stopPan = function(e) { if (e.button === 2 || e.type === "mouseleave") { isCanvasPanning = false; canvasViewport.style.cursor = ""; } };
    document.addEventListener("mouseup", stopPan);
    canvasViewport.addEventListener("mouseleave", stopPan);
})();

(function bindSend() {
    if (document.readyState === "loading") { document.addEventListener("DOMContentLoaded", bindSend); return; }
    var btn = document.getElementById("copilot-send");
    var inp = document.getElementById("copilot-input");
    if (btn && inp) { btn.onclick = function() { var t = inp.value.trim(); if (t) { inp.value = ""; copilotChat(t); } }; }
})();
