"use strict";

let currentNovelId = 1;
let currentChapterId = null;
let currentVolumeId = null;
let currentNoteId = null;
let currentLocationId = null;
let currentModule = "writing";
let currentOutlineCat = "世界观";
let mdViewing = false;

/* ═══════════ Clock Engine ═══════════ */
window.novelTimeConfig = { months_per_year: 12, days_per_month: 30, hours_per_day: 24 };
window.currentTick = 0;
window.locationsCache = [];

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
    var d = tickToDate(window.currentTick);
    var el = document.getElementById("global-clock-display");
    if (el) el.textContent = d.year + "年 " + d.month + "月 " + d.day + "日 " + d.hour + "时";
}

/* ═══════════ Novel ═══════════ */
async function loadNovels() {
    const sel = document.getElementById("novel-select"); sel.innerHTML = "";
    (await (await fetch("/api/novels")).json()).forEach(n => {
        const o = document.createElement("option"); o.value = n.id; o.textContent = n.title;
        if (n.id === currentNovelId) o.selected = true; sel.appendChild(o);
    });
}
async function switchNovel(nid) { currentNovelId = nid; await refreshAll(); }
document.getElementById("novel-select").addEventListener("change", e => switchNovel(Number(e.target.value)));
document.getElementById("novel-add-btn").addEventListener("click", async () => {
    const t = prompt("新小说名称：", "新小说"); if (!t) return;
    await fetch("/api/novels", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title: t }) });
    await loadNovels(); switchNovel(Number(document.getElementById("novel-select").lastElementChild.value));
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
    });
});

/* ═══════════ Category nav (outline) ═══════════ */
document.querySelectorAll("#outline-category-nav li").forEach(li => {
    li.addEventListener("click", () => {
        document.querySelectorAll("#outline-category-nav li").forEach(x => x.classList.remove("active"));
        li.classList.add("active"); currentOutlineCat = li.dataset.cat;
        currentNoteId = null; outlineTitle.value = ""; outlineContent.value = ""; outlineCtxToggle.checked = false;
        document.getElementById("outline-pane-title").textContent = currentOutlineCat;
        const childBtn = document.getElementById("new-outline-child-btn");
        childBtn.classList.toggle("hidden", currentOutlineCat !== "剧情大纲");
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
function showToast(msg) { const t = document.getElementById("archive-toast"); t.textContent = msg; t.classList.add("show"); setTimeout(() => t.classList.remove("show"), 4000); }

document.getElementById("archive-selected-btn").addEventListener("click", async () => {
    if (!currentChapterId) { showToast("请先选中一个章节"); return; }
    const sel = contentInput.value.substring(contentInput.selectionStart, contentInput.selectionEnd).trim();
    if (!sel) { showToast("请先在正文中选中要归档的文字"); return; }
    const s = getSettings();
    if (!s.apiKey) { document.getElementById("settings-modal").classList.remove("hidden"); return; }
    showArchiveOverlay();
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
        // build toast
        let msg = data.message || "归档完成";
        const t = document.getElementById("archive-toast");
        t.style.background = "#333";
        if (data.timeline_added) msg += "\n✅ 时间线已生成";
        if (data.timeline_error) { msg += "\n⚠ 时间线失败: " + data.timeline_error; t.style.background = "#e65100"; }
        if (data.characters_updated && data.characters_updated.length) msg += "\n✅ 已更新人物: " + data.characters_updated.join("、");
        if (data.character_error) { msg += "\n⚠ 人物更新失败: " + data.character_error; t.style.background = "#e65100"; }
        showToast(msg);
        await saveCurrentChapter();
        await loadCharacters();
        await loadTimeline();
    } catch (e) { showToast("归档失败: " + e.message); }
    finally { hideArchiveOverlay(); }
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
        grp.fields.forEach(fld => {
            const row = document.createElement("div"); row.className = "char-field";
            const val = (attrs[grp.group] && attrs[grp.group][fld]) ? attrs[grp.group][fld] : "";
            row.innerHTML = `<label>${e(fld)}</label><input type="text" data-group="${e(grp.group)}" data-field="${e(fld)}" value="${e(val)}">`;
            fs.appendChild(row);
        });
        container.appendChild(fs);
    });
    // Time editor placeholder
    var timeFs = document.createElement("fieldset");
    timeFs.className = "char-fieldset";
    timeFs.style.cssText = "margin-top:16px;border-color:#93c5fd;background:#eff6ff;";
    timeFs.innerHTML = '<legend style="color:#1d4ed8;">⏳ 行动规律与轨迹</legend><p style="font-size:0.75rem;color:#475569;margin-bottom:8px;">设定该角色每天/每月出现在哪里，以及特殊的行程变故。</p>';
    container.appendChild(timeFs);
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

document.getElementById("save-char-btn").addEventListener("click", async () => {
    if (!currentCharId) return;
    const ch = await (await fetch(`/api/characters/${currentCharId}?novel_id=${currentNovelId}`)).json();
    const attrs = {};
    document.querySelectorAll("#char-dynamic-form input").forEach(inp => {
        const g = inp.dataset.group, f = inp.dataset.field;
        if (!attrs[g]) attrs[g] = {};
        attrs[g][f] = inp.value;
    });
    await fetch(`/api/characters/${currentCharId}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: ch.name, aliases: ch.aliases, description: ch.description, status: ch.status, attributes: attrs, is_always_context: ch.is_always_context }) });
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
    await fetch(`/api/characters/${currentCharId}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: ch.name, aliases: ch.aliases, description: ch.description, status: ch.status, attributes: attrs, is_always_context: ch.is_always_context }) });
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
const SCALE_LEVELS = ["大千世界", "宇宙", "星球", "大陆", "国家", "城池", "街区"];
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
    await fetch(`/api/locations/${currentLocationId}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: document.getElementById("loc-name-input").value, description: document.getElementById("loc-desc-input").value, attributes: collectLocationAttributes() }) });
    await loadLocationTree(); });

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
        let attrHtml = "";
        if (ch.attributes && typeof ch.attributes === "object") {
            ["基础信息", "外貌特征"].forEach(g => {
                const fields = ch.attributes[g];
                if (fields && typeof fields === "object") {
                    Object.entries(fields).forEach(([k, v]) => { if (v) attrHtml += `<div class="char-desc"><b>${e(k)}</b>: ${e(String(v))}</div>`; });
                }
            });
        }
        d.innerHTML = `<div class="char-name">${e(ch.name)||"(未)"}${getStatusBadge(ch)}</div>${ch.aliases?`<div class="char-aliases">${e(ch.aliases)}</div>`:""}${attrHtml}${ch.description?`<div class="char-desc">${e(ch.description)}</div>`:""}<label class="char-activate"><input type="checkbox" data-cid="${ch.id}" ${ch.is_always_context?"checked":""}> 启动</label>`;
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

async function copilotChat(instruction) {
    const s = getSettings();
    if (!s.apiKey) { document.getElementById("settings-modal").classList.remove("hidden"); return; }
    const content = getActiveContent();
    const sel = getActiveSelection();

    // show user message
    const userB = document.createElement("div"); userB.className = "copilot-bubble user";
    userB.innerHTML = `<div class="cp-label">你</div>${e(instruction)}`;
    copilotHistory.appendChild(userB); copilotHistory.scrollTop = copilotHistory.scrollHeight;

    const aiB = document.createElement("div"); aiB.className = "copilot-bubble ai streaming";
    aiB.innerHTML = '<div class="cp-label">AI</div>';
    copilotHistory.appendChild(aiB); copilotHistory.scrollTop = copilotHistory.scrollHeight;

    try {
        const res = await fetch("/api/writing/copilot", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ novel_id: currentNovelId, current_chapter_content: content, selected_text: sel, instruction, api_key: s.apiKey, base_url: s.baseUrl, model: s.model, scene: getCopilotScene(instruction) }) });
        for await (const msg of parseSSEStream(res)) {
            if (msg.type === "chunk") { aiB.appendChild(document.createTextNode(msg.content)); copilotHistory.scrollTop = copilotHistory.scrollHeight; }
            else if (msg.type === "error") { aiB.appendChild(document.createTextNode("\n[错误: " + msg.message + "]")); }
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
    } catch (e) { aiB.appendChild(document.createTextNode("\n[失败: " + e.message + "]")); }
    finally { aiB.classList.remove("streaming"); }
}

copilotSend.addEventListener("click", () => { const t = copilotInput.value.trim(); if (t) { copilotInput.value = ""; copilotChat(t); } });
copilotInput.addEventListener("keydown", e => { if (e.key === "Enter") { e.preventDefault(); copilotSend.click(); } });
document.querySelectorAll(".cp-quick-btn").forEach(b => b.addEventListener("click", () => copilotChat(b.dataset.cmd)));

/* ═══════════ Prompt Settings ═══════════ */
const promptModal = document.getElementById("prompt-modal");
const promptTpl = document.getElementById("prompt-templates");
const promptLabels = { chat: "自由对话", continue: "续写", polish: "润色", review: "评价", chain_write: "推演链生成正文", outline_assist: "大纲助手", map_assist: "地图助手", character_assist: "人物助手", archive_timeline: "归档-时间线提取", archive_character: "归档-人物更新", sandbox_sim: "沙盘演武" };

function getCopilotScene(instruction) {
    if (currentModule === "outline") return "outline_assist";
    if (currentModule === "map") return "map_assist";
    if (currentModule === "characters") return "character_assist";
    if (instruction.includes("续写")) return "continue";
    if (instruction.includes("润色")) return "polish";
    if (instruction.includes("评价")) return "review";
    return "chat";
}

document.getElementById("prompt-settings-btn").addEventListener("click", async () => {
    const prompts = await (await fetch("/api/settings/prompts")).json();
    promptTpl.innerHTML = "";
    Object.entries(promptLabels).forEach(([key, label]) => {
        const div = document.createElement("div"); div.style.marginBottom = "12px";
        div.innerHTML = `<label style="font-size:.78rem;font-weight:700;display:block;margin-bottom:3px;">${label}<button class="sys-btn sys-btn-ghost reset-prompt-btn" data-key="${key}" title="恢复此项的默认提示词" style="font-size:.7rem;margin-left:8px;padding:1px 8px;">🔄 恢复默认</button></label><textarea data-scene="${key}" style="width:100%;min-height:80px;font-size:.75rem;font-family:Consolas,monospace;padding:6px 8px;border:1px solid var(--border);border-radius:4px;resize:vertical;">${e(prompts[key]||"")}</textarea>`;
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
    var executedNodeIds = new Set();
    var chapterContent = contentInput.value || "";
    var sel = contentInput.value.substring(contentInput.selectionStart, contentInput.selectionEnd);
    while (currentNodeId) {
        if (executedNodeIds.has(currentNodeId)) { break; }
        executedNodeIds.add(currentNodeId);
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
            var res = await fetch("/api/reasoning/execute", { method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({ novel_id: currentNovelId, premise, prompt: promptT, target: targetT, previous_output: "", api_key: s.apiKey, base_url: s.baseUrl, model: s.model }) });
            var txt = "";
            var gen = parseSSEStream(res);
            var chunkResult;
            while (true) {
                chunkResult = await gen.next();
                if (chunkResult.done) break;
                var msg = chunkResult.value;
                if (msg.type === "chunk") txt += msg.content;
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
function getSettings() { return { apiKey: localStorage.getItem("ai_api_key") || "", baseUrl: localStorage.getItem("ai_base_url") || "", model: localStorage.getItem("ai_model") || "gpt-3.5-turbo" }; }
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
    renderAllNodes();
    renderLines();
}

function renderAllNodes() {
    const canvas = document.getElementById("chain-canvas");
    canvas.querySelectorAll(".node-card").forEach(n => n.remove());
    (currentChain.nodes || []).forEach((node, idx) => {
        if (!node.id) node.id = "n" + (idx + 1);
        if (!node.branches) node.branches = [];
        const card = document.createElement("div"); card.className = "node-card"; card.dataset.nid = node.id;
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
        card.innerHTML = `<div class="node-hdr"><span>${e(node.id)}</span><button class="node-del">&#128465;</button></div>
            <div class="node-body">
                <div class="node-section"><label>传入内容</label><textarea class="premise-ta">${e(node.premise||"")}</textarea></div>
                <div class="node-section"><label>推理提示词</label><textarea class="prompt-ta">${e(node.prompt||"")}</textarea></div>
                <div class="node-section"><label>结果指定</label><div class="target-builder" data-node-id="${node.id}"><div class="builder-controls"><button type="button" class="sys-btn sys-btn-ghost" style="font-size:.65rem;padding:2px 6px;" onclick="addTargetBlock('${node.id}','placeholder')">+ 占位符</button><button type="button" class="sys-btn sys-btn-ghost" style="font-size:.65rem;padding:2px 6px;" onclick="addTargetBlock('${node.id}','text')">+ 文本/要求</button><button type="button" class="sys-btn sys-btn-ghost" style="font-size:.65rem;padding:2px 6px;" onclick="addTargetBlock('${node.id}','bool')">+ 条件判断</button><button type="button" class="sys-btn sys-btn-ghost" style="font-size:.65rem;padding:2px 6px;" onclick="addTargetBlock('${node.id}','enum')">+ 选项分支</button></div><div class="builder-list" data-node-id="${node.id}"></div></div></div>
                <div class="node-section"><label>分支路由</label><div class="branch-list"></div><button class="branch-add-btn">+ 新增分支</button></div>
                <div class="node-section"><label>顺序路由（默认下一节点）</label><select class="next-node-sel"><option value="">-- 无 --</option></select></div>
                <button class="node-run-btn">&#9654; 运行本节点</button>
                <div class="node-output" style="display:none;margin-top:6px;padding:8px;border-top:1px solid var(--border);font-size:.78rem;line-height:1.6;max-height:300px;overflow-y:auto;white-space:pre-wrap;word-break:break-word;">${e(node.output||"")}</div>
            </div>
            <div class="port port-in" data-nid="${node.id}" title="接收连接"><span>&#9664;</span></div>
            ${outPorts}`;
        canvas.appendChild(card);
        bindNodeEvents(card, node, idx);
        initTargetBuilder(card, node);
        renderBranches(card, node);
    });
    bindPortEvents();
}

function bindNodeEvents(card, node, idx) {
    const hdr = card.querySelector(".node-hdr");
    hdr.addEventListener("mousedown", e => { if (e.target.closest("button")) return; startDrag(e, card, node, idx); });
    card.querySelector(".node-del").addEventListener("click", () => { currentChain.nodes.splice(idx, 1); renderAllNodes(); renderLines(); autoSave(); });
    card.querySelector(".premise-ta").addEventListener("input", e => { node.premise = e.target.value; autoSave(); });
    card.querySelector(".prompt-ta").addEventListener("input", e => { node.prompt = e.target.value; autoSave(); });
    card.querySelector(".branch-add-btn").addEventListener("click", () => { node.branches.push({ condition: "", next_node_id: "" }); renderBranches(card, node); autoSave(); });
    card.querySelector(".node-run-btn").addEventListener("click", () => runNode(card, node));
    // sequential route
    const nextSel = card.querySelector(".next-node-sel");
    nextSel.innerHTML = '<option value="">-- 无 --</option>';
    (currentChain.nodes || []).forEach(n => { if (n.id !== node.id) nextSel.innerHTML += `<option value="${e(n.id)}" ${node.next_node_id===n.id?"selected":""}>${e(n.id)}</option>`; });
    nextSel.addEventListener("change", () => { node.next_node_id = nextSel.value; renderLines(); autoSave(); });
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
    const list = card.querySelector(".branch-list"); list.innerHTML = "";
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

const PLACEHOLDER_OPTS = ["{content}","{selection}","{lore}","{outlines_list}","{characters_list}","{locations_list}","{timeline_main}","{timeline_history}","{timeline_world}","{character:}","{outline:}","{location:}","{chain_data}"];

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

function compileTarget(target) {
    if (typeof target === "string") return target;
    if (!Array.isArray(target) || !target.length) return "";
    var compiled = "请严格按照以下【区块顺序】和规则组合最终的输出内容，绝对不要包含任何多余解释：\n\n";
    target.forEach(function(block, index) {
        var n = index + 1;
        if (block.type === "placeholder") compiled += "[区块" + n + "]: 必须原样输出占位符字符串 " + (block.value||"") + " ，不要擅自解析或修改。\n";
        if (block.type === "text") compiled += "[区块" + n + "]: 严格按照以下要求输出：" + (block.value||"") + "\n";
        if (block.type === "bool") compiled += "[区块" + n + "]: 逻辑判断【" + (block.condition||"") + "】，若为真则严格输出：<ROUTE>" + (block.trueVal||"") + "</ROUTE>，否则严格输出：<ROUTE>" + (block.falseVal||"") + "</ROUTE>\n";
        if (block.type === "enum") compiled += "[区块" + n + "]: 根据情景从选项【" + (block.options||"") + "】中选择一项。你必须将最终选择的结果包裹在 <ROUTE> 标签中，例如 <ROUTE>选项1</ROUTE>。\n";
    });
    return compiled;
}

async function runNode(card, node) {
    const s = getSettings();
    if (!s.apiKey) { document.getElementById("settings-modal").classList.remove("hidden"); return; }
    const outDiv = card.querySelector(".node-output");
    outDiv.style.display = "block"; outDiv.textContent = "AI 思考中...\n";
    const btn = card.querySelector(".node-run-btn"); btn.disabled = true; btn.textContent = "执行中...";

    // find previous output from incoming connections
    let prevOutput = "";
    (currentChain.nodes || []).forEach(n => {
        // sequential: next_node_id points to this node
        if (n.next_node_id === node.id && n.output) prevOutput += (prevOutput ? "\n" : "") + n.output;
        // branch: branches point to this node
        (n.branches || []).forEach(br => {
            if (br.next_node_id === node.id && n.output) prevOutput += (prevOutput ? "\n" : "") + n.output;
        });
    });

    try {
        // Merge upstream output into premise BEFORE placeholder resolution
        let rawPremise = node.premise || "";
        if (prevOutput) {
            rawPremise = prevOutput + "\n\n" + rawPremise;
        }
        const chapterContent = contentInput.value || "";
        const sel = contentInput.value.substring(contentInput.selectionStart, contentInput.selectionEnd);
        const premise = rawPremise.replace(/\{content\}/g, chapterContent).replace(/\{selection\}/g, sel);
        const prompt = node.prompt || "";
        const target = compileTarget(node.target);
        const res = await fetch("/api/reasoning/execute", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ novel_id: currentNovelId, premise, prompt, target, previous_output: "", api_key: s.apiKey, base_url: s.baseUrl, model: s.model }),
        });
        let rawResult = "";
        for await (const msg of parseSSEStream(res)) {
            if (msg.type === "chunk") { rawResult += msg.content; outDiv.textContent = "AI 思考中...\n" + rawResult; outDiv.scrollTop = outDiv.scrollHeight; }
            else if (msg.type === "error") { rawResult += "\n[错误: " + msg.message + "]"; }
            else if (msg.type === "done") {
                // Extract ROUTE key and strip tags
                let routeKey = null;
                var rm = rawResult.match(/<ROUTE>([\s\S]*?)<\/ROUTE>/);
                if (rm) { routeKey = rm[1].trim(); }
                var cleanResult = rawResult.replace(/<\/?ROUTE>/g, "").trim();
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

/* ═══════════ Settings ═══════════ */
const sm = document.getElementById("settings-modal");
document.getElementById("settings-btn").addEventListener("click", () => { const s = getSettings(); document.getElementById("settings-apikey").value = s.apiKey; document.getElementById("settings-baseurl").value = s.baseUrl; document.getElementById("settings-model").value = s.model; sm.classList.remove("hidden"); });
document.getElementById("settings-cancel").addEventListener("click", () => sm.classList.add("hidden"));
sm.querySelector(".modal-backdrop").addEventListener("click", () => sm.classList.add("hidden"));
document.getElementById("settings-save").addEventListener("click", () => { localStorage.setItem("ai_api_key", document.getElementById("settings-apikey").value.trim()); localStorage.setItem("ai_base_url", document.getElementById("settings-baseurl").value.trim()); localStorage.setItem("ai_model", document.getElementById("settings-model").value.trim() || "gpt-3.5-turbo"); sm.classList.add("hidden"); });

/* ═══════════ Cheat Sheet ═══════════ */
function showCheatSheet() { document.getElementById("cheat-sheet-modal").classList.remove("hidden"); }
document.getElementById("cheat-sheet-close").addEventListener("click", () => document.getElementById("cheat-sheet-modal").classList.add("hidden"));
document.querySelector("#cheat-sheet-modal .modal-backdrop").addEventListener("click", () => document.getElementById("cheat-sheet-modal").classList.add("hidden"));
document.getElementById("cheat-prompt-btn").addEventListener("click", showCheatSheet);
document.getElementById("placeholder-ref-btn")?.addEventListener("click", showCheatSheet);

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
    loadNovelTime();
    if (currentModule === "writing") { await loadVolumes(); await loadChapters(); }
    else if (currentModule === "outline") await loadOutlineNotes();
    else if (currentModule === "characters") await loadCharactersModule();
    else if (currentModule === "map") { await loadLocationTree(); }
    else if (currentModule === "reasoning") await loadReasoningModule();
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
