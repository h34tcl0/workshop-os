// ── Toast (notificación flotante, usada en varias partes de la página) ──
function showToast(msg) {
    const t = document.getElementById('toast'); t.innerText = msg; t.classList.remove('hidden');
    setTimeout(() => t.classList.add('hidden'), 3000);
}

// ── Tabs del backlog (manual / importar JSON) ──
function switchTab(tabName) {
    const indicator = document.getElementById('pill-indicator');
    const btnManual = document.getElementById('tab-btn-manual');
    const btnJson = document.getElementById('tab-btn-json');
    const tabManual = document.getElementById('tab-manual');
    const tabJson = document.getElementById('tab-json');
    if (tabName === 'manual') {
        indicator.classList.remove('right');
        btnManual.classList.replace('text-ink2', 'text-canvas');
        btnJson.classList.replace('text-canvas', 'text-ink2');
        tabManual.classList.remove('hidden'); tabJson.classList.add('hidden');
    } else {
        indicator.classList.add('right');
        btnJson.classList.replace('text-ink2', 'text-canvas');
        btnManual.classList.replace('text-canvas', 'text-ink2');
        tabJson.classList.remove('hidden'); tabManual.classList.add('hidden');
    }
}

// ── Importador de tareas vía JSON (IA) ──
function copyAiPrompt() {
    const prompt = `Actúa como Jefe de proyecto. Genera el desglose de tareas en formato JSON: {"project_name": "...", "tasks": [{"title": "...", "category": "carpentry", "estimated_hours": 1.0, "curing_hours": 0.0}]}`;
    navigator.clipboard.writeText(prompt).then(() => showToast('Prompt copiado'));
}

function importJsonTasks() {
    const jsonText = document.getElementById('json-import-input').value.trim();
    if (!jsonText) return;
    fetch('/tasks/import', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: jsonText })
        .then(res => res.json()).then(data => { if (data.status === 'success') { showToast(data.message); setTimeout(() => window.location.reload(), 800); } });
}

// ── Tareas favoritas ──
function useFavoriteInForm(id, title, category, estimatedHours, curingHours) {
    switchTab('manual');
    document.getElementById('manual-title').value = title;
    document.getElementById('manual-category').value = category;
    document.getElementById('manual-estimated-hours').value = estimatedHours;
    document.getElementById('manual-curing-hours').value = curingHours;
    document.getElementById('manual-title').focus();
}

// ── Panel de edición inline de cada tarea ──
function toggleEditTask(taskId) {
    document.getElementById('edit-task-' + taskId).classList.toggle('hidden');
}

// ── Historial de últimos 7 días (colapsable) ──
function toggleHistory() {
    document.getElementById('history-panel').classList.toggle('hidden');
    document.getElementById('history-chevron').classList.toggle('rotate-180');
}

// ── Mover tareas (subir/bajar) sin recargar la página: el backend igual
// reordena en la BD, pero acá solo reordenamos el DOM localmente. ──
document.querySelectorAll('form[action*="/move-up"], form[action*="/move-down"]').forEach(form => {
    form.addEventListener('submit', function (e) {
        e.preventDefault();
        const card = form.closest('.task-card');
        if (!card) { form.submit(); return; }
        const isUp = form.action.includes('/move-up');
        let sibling = isUp ? card.previousElementSibling : card.nextElementSibling;
        while (sibling && !sibling.classList.contains('task-card')) {
            sibling = isUp ? sibling.previousElementSibling : sibling.nextElementSibling;
        }
        if (!sibling) return; // ya está en el borde, no hay nada que mover

        fetch(form.action, { method: 'POST' })
            .then(res => {
                if (!res.ok && res.status !== 0) throw new Error('move failed');
                if (isUp) {
                    card.parentNode.insertBefore(card, sibling);
                } else {
                    card.parentNode.insertBefore(sibling, card);
                }
            })
            .catch(() => { form.submit(); }); // si falla el fetch, degrada al comportamiento clásico
    });
});

// ── SortableJS: Drag & Drop del backlog ──
function initSortable() {
    const list = document.getElementById('backlog-task-list');
    if (!list || typeof Sortable === 'undefined') return;

    Sortable.create(list, {
        animation: 200,
        ghostClass: 'opacity-40',
        dragClass: 'shadow-2xl',
        // Avoid accidental drags on touch: require 100ms hold before drag starts
        delay: 100,
        delayOnTouchOnly: true,
        // Prevent dragging when interacting with form controls inside cards
        filter: 'button, input, select, textarea, a',
        preventOnFilter: false,

        onEnd: function (evt) {
            const items = list.querySelectorAll('.task-card[data-id]');
            const orderedIds = Array.from(items).map(el => parseInt(el.dataset.id, 10));

            fetch('/tasks/reorder', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ task_ids: orderedIds })
            })
                .then(res => res.json())
                .then(data => {
                    if (data.status === 'ok') {
                        showToast('Orden guardado');
                    }
                })
                .catch(() => {
                    // Non-critical: order persists visually even if the request fails.
                    // Silent fail — no toast — to avoid alarming the user for a cosmetic issue.
                });
        }
    });
}

// ── Modo Enfoque ──
const FOCUS_COLLAPSE_CLASSES = ['-translate-x-full', 'opacity-0', '!max-w-0', '!p-0', 'pointer-events-none'];

function applyFocusModeState(isCollapsed, isInitial = false) {
    const backlogCol = document.getElementById('backlog-container');
    const btnText = document.getElementById('toggle-backlog-text');
    if (!backlogCol) return;

    if (isCollapsed) {
        backlogCol.classList.add(...FOCUS_COLLAPSE_CLASSES);

        const collapseDone = () => {
            if (backlogCol.classList.contains('-translate-x-full')) {
                backlogCol.style.display = 'none';
            }
        };

        if (isInitial) {
            collapseDone();
        } else {
            setTimeout(collapseDone, 300);
        }

        if (btnText) btnText.innerText = 'Mostrar Backlog';
    } else {
        backlogCol.style.display = '';

        if (isInitial) {
            backlogCol.classList.remove(...FOCUS_COLLAPSE_CLASSES);
        } else {
            requestAnimationFrame(() => {
                backlogCol.classList.remove(...FOCUS_COLLAPSE_CLASSES);
            });
        }

        if (btnText) btnText.innerText = 'Modo Enfoque';
    }
}

function toggleFocusMode() {
    const backlogCol = document.getElementById('backlog-container');
    if (!backlogCol) return;
    const currentlyCollapsed = backlogCol.classList.contains('-translate-x-full') ||
        backlogCol.style.display === 'none';
    const newState = !currentlyCollapsed;
    localStorage.setItem('workshop_backlog_collapsed', newState ? 'true' : 'false');
    applyFocusModeState(newState);
}

document.addEventListener('DOMContentLoaded', function () {
    const toggleBtn = document.getElementById('toggle-backlog-btn');
    if (toggleBtn) {
        toggleBtn.addEventListener('click', toggleFocusMode);
    }

    // Restaurar estado guardado
    const savedState = localStorage.getItem('workshop_backlog_collapsed');
    if (savedState === 'true') {
        const backlogCol = document.getElementById('backlog-container');
        if (backlogCol) {
            backlogCol.style.transition = 'none';
            applyFocusModeState(true, true);
            requestAnimationFrame(() => {
                backlogCol.style.transition = '';
            });
        }
    }

    initSortable();
});