"use strict";

let currentChapterId = null;

const listEl = document.getElementById("chapter-list");
const titleInput = document.getElementById("title-input");
const contentInput = document.getElementById("content-input");
const saveBtn = document.getElementById("save-btn");
const newBtn = document.getElementById("new-btn");

async function loadChapters() {
    const res = await fetch("/api/chapters");
    const chapters = await res.json();
    listEl.innerHTML = "";
    chapters.forEach(ch => {
        const li = document.createElement("li");
        li.textContent = ch.title || "(无标题)";
        li.dataset.id = ch.id;
        li.addEventListener("click", () => selectChapter(ch));
        listEl.appendChild(li);
    });
}

function selectChapter(chapter) {
    currentChapterId = chapter.id;
    titleInput.value = chapter.title;
    contentInput.value = chapter.content;
    document.querySelectorAll("#chapter-list li").forEach(li => {
        li.classList.toggle("active", Number(li.dataset.id) === chapter.id);
    });
}

async function createNewChapter() {
    const nextIndex = listEl.children.length;
    const res = await fetch("/api/chapters", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: "新章节", content: "", order_index: nextIndex }),
    });
    const chapter = await res.json();
    await loadChapters();
    selectChapter(chapter);
}

async function saveCurrentChapter() {
    if (currentChapterId === null) return;
    const title = titleInput.value;
    const content = contentInput.value;
    await fetch(`/api/chapters/${currentChapterId}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title, content }),
    });
    saveBtn.textContent = "已保存";
    setTimeout(() => { saveBtn.textContent = "保存"; }, 1000);
    await loadChapters();
}

saveBtn.addEventListener("click", saveCurrentChapter);
newBtn.addEventListener("click", createNewChapter);

document.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === "s") {
        e.preventDefault();
        saveCurrentChapter();
    }
});

document.addEventListener("DOMContentLoaded", () => {
    loadChapters();
});
