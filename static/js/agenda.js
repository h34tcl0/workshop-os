// ── Editor manual de un día puntual (forzar bloqueado/viable, horas custom, tareas forzadas) ──
function toggleDayEditor(dateIso) {
    document.getElementById('day-editor-' + dateIso).classList.toggle('hidden');
}

// ── Panel colapsable "Tareas asignadas" de cada tarjeta de día ──
function toggleAssignedTasks(panelId) {
    document.getElementById(panelId).classList.toggle('hidden');
}
