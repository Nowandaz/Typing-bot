// Small progressive enhancements for the Notes app.
"use strict";

const API = "/api/notes";

function debounce(fn, wait) {
    let timer = null;
    return (...args) => {
        clearTimeout(timer);
        timer = setTimeout(() => fn(...args), wait);
    };
}

async function fetchNotes(query) {
    const url = query ? `${API}?q=${encodeURIComponent(query)}` : API;
    const response = await fetch(url, { headers: { Accept: "application/json" } });
    if (!response.ok) {
        throw new Error(`Request failed: ${response.status}`);
    }
    return response.json();
}

function render(list, notes) {
    list.innerHTML = "";
    if (notes.length === 0) {
        list.innerHTML = '<p class="empty">No matches.</p>';
        return;
    }
    for (const note of notes) {
        const card = document.createElement("article");
        card.className = "card" + (note.pinned ? " pinned" : "");
        card.dataset.id = note.id;
        const tags = note.tags.map((t) => `<span class="pill">${t}</span>`).join("");
        card.innerHTML = `
            <h3><a href="/note/${note.id}">${note.title}</a></h3>
            <p class="excerpt">${(note.body || "").slice(0, 140)}</p>
            <div class="meta">${tags}</div>`;
        list.appendChild(card);
    }
}

function init() {
    const search = document.querySelector(".search input");
    const list = document.getElementById("note-list");
    if (!search || !list) {
        return;
    }
    const run = debounce(async (event) => {
        try {
            const notes = await fetchNotes(event.target.value.trim());
            render(list, notes);
        } catch (error) {
            console.error(error);
        }
    }, 250);
    search.addEventListener("input", run);
}

document.addEventListener("DOMContentLoaded", init);
