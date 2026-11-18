// Tiny to-do list used as a GhostWriter demo file.
import { readFile } from 'node:fs/promises';

const STORAGE_KEY = "todos";

/**
 * A single task.
 * @param {string} title
 */
class Todo {
    constructor(title, done = false) {
        this.title = title;
        this.done = done;
    }

    toggle() {
        this.done = !this.done;
        return this;
    }
}

function load(storage) {
    const raw = storage.getItem(STORAGE_KEY);
    if (!raw) {
        return [];
    }
    return JSON.parse(raw).map((t) => new Todo(t.title, t.done));
}

const summary = (todos) => {
    let open = 0;
    for (const todo of todos) {
        if (!todo.done) {
            open += 1;
        }
    }
    return `${open} of ${todos.length} left`;
};

function render(todos, list) {
    list.innerHTML = '';
    todos.forEach((todo, index) => {
        const item = document.createElement('li');
        item.textContent = todo.title;
        item.addEventListener('click', () => {
            todo.toggle();
            render(todos, list);
        });
        list.appendChild(item);
    });
}

export async function seed(path) {
    try {
        const text = await readFile(path, 'utf8');
        return text.split('\n').filter(Boolean);
    } catch (error) {
        console.warn("Couldn't read", path);
        return [];
    }
}

switch (process.argv[2]) {
    case 'count':
        console.log(summary(load(globalThis.localStorage)));
        break;
    default:
        console.log('usage: todo count');
}

export { Todo, load, summary, render };
